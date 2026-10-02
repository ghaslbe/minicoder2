"""Browser-independent PO/build jobs. The existing build generator stays the engine."""
import json
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import threading
import time
import uuid

from flask import g, jsonify, request
from vibelove.chat_store import ACTIVE, ChatStore

ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


class ChatRuntime:
    def __init__(self, app, server):
        self.app, self.s = app, server
        self.stores = {}
        self.lock = threading.RLock()
        self.active = None

    def store(self):
        project = self.s['CURRENT_PROJECT']
        path = Path(self.s['SETTINGS_FILE_PATH']).parent / 'chat_state' / (project + '.json')
        with self.lock:
            key = str(path)
            if key not in self.stores:
                store = ChatStore(path)
                if not store.snapshot()['messages']:
                    for entry in self.s['lade_verlauf'](self.s['projekt_dir'](project)):
                        store.add('user', entry.get('instruction', ''))
                        store.add('build', entry.get('summary', ''), rollback_to=entry.get('rollback_to'))
                self.stores[key] = store
            return self.stores[key]

    def shutdown(self):
        with self.lock:
            active = self.active
        if active:
            active['stop'].set()
            self.s['BUILD_STOP'].set()
            proc = active.get('proc')
            if proc:
                self.s['terminate_build_process'](proc)

    def new_project(self, project):
        path = Path(self.s['SETTINGS_FILE_PATH']).parent / 'chat_state' / (project + '.json')
        with self.lock:
            self.stores.pop(str(path), None)
            if path.exists():
                path.rename(path.with_suffix('.' + uuid.uuid4().hex + '.archive'))

    def run_po(self, job, payload):
        store, ident = job['store'], job['id']
        proc = subprocess.Popen([sys.executable, '-u', str(Path(__file__).with_name('po_worker.py'))],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
        job['proc'] = proc
        result = None
        deadline = time.monotonic() + 1800
        buffer = b''
        try:
            proc.stdin.write(json.dumps(payload).encode('utf-8'))
            proc.stdin.close()
            while True:
                if job['stop'].is_set():
                    return None
                if time.monotonic() > deadline:
                    raise TimeoutError('Product Owner hat das Gesamtlimit von 30 Minuten erreicht.')
                ready, _, _ = select.select([proc.stdout], [], [], .25)
                if not ready:
                    continue
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    break
                buffer += chunk
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    event = json.loads(line)
                    if event['type'] == 'delta':
                        store.append(ident, event['text'])
                        store.update(ident, phase='Antwort wird geschrieben')
                    elif event['type'] == 'phase':
                        phase = 'Modell denkt' if event['phase'] == 'reasoning' else 'Warte auf Modelldaten'
                        store.update(ident, phase=phase)
                    elif event['type'] == 'attempt':
                        store.update(ident, attempt=event['attempt'], phase='Anfrage wird vorbereitet', raw='')
                    elif event['type'] == 'result':
                        result = event
                    elif event['type'] == 'error':
                        raise RuntimeError(event['error'])
            if result is None:
                raise RuntimeError('PO-Prozess ohne Ergebnis beendet.')
            return result
        finally:
            self.s['terminate_build_process'](proc)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream and not stream.closed:
                    stream.close()
            job['proc'] = None

    def run_build(self, job, decision):
        store, ident = job['store'], job['id']
        steps = decision.get('steps') or [{'num': 1, 'instruction': decision['instruction']}]
        final = None
        for index, step in enumerate(steps):
            if job['stop'].is_set():
                return None
            store.update(ident, phase=f'Bauen: Auftrag {index + 1}/{len(steps)}')
            data = {'instruction': step['instruction'], 'acceptance': decision.get('acceptance', ''),
                    'analyse': 'true' if decision.get('analyse') else 'false'}
            with self.app.test_request_context('/build', method='POST', data=data):
                g.chat_job = job
                response = self.s['build']()
                if isinstance(response, str):
                    raise RuntimeError(response)
                try:
                    for chunk in response.response:
                        if isinstance(chunk, bytes):
                            chunk = chunk.decode('utf-8', 'replace')
                        store.append(ident, chunk)
                        job['tail'] = (job.get('tail', '') + chunk)[-4000:]
                        steps_seen = re.findall(r'Schritt (\d+)', ANSI.sub('', job['tail']))
                        if steps_seen and job.get('step') != steps_seen[-1]:
                            job['step'] = steps_seen[-1]
                            store.update(ident, phase='Bauen: Schritt ' + steps_seen[-1])
                        if job['stop'].is_set():
                            self.s['BUILD_STOP'].set()
                    final = getattr(g, 'chat_build_result', None)
                finally:
                    response.close()
            if not final:
                raise RuntimeError('Bauprozess lieferte keinen Abschlussstatus.')
            if final['status'] != 'complete':
                break
        return final

    def worker(self, job, payload, kind):
        store, ident = job['store'], job['id']
        try:
            store.update(ident, status='running')
            if kind == 'po':
                result = {'decision': payload['skill'], 'history': []} if 'skill' in payload else self.run_po(job, payload)
                if result and not job['stop'].is_set():
                    decision = result['decision']
                    raw = decision.get('raw') or store.get(ident)['raw']
                    if decision['type'] == 'error':
                        store.update(ident, status='error', text=decision.get('error', 'PO-Fehler'),
                                     decision=decision, raw=raw, phase='Fehlgeschlagen')
                    else:
                        store.history(result['history'])
                        store.update(ident, status='complete', decision=decision, raw=raw, actionable=True,
                                     text=decision.get('summary') or decision.get('question', ''), phase='Antwort bereit')
            else:
                result = self.run_build(job, payload)
                if result and not job['stop'].is_set():
                    store.update(ident, **result)
                    store.history([])
            if job['stop'].is_set():
                store.update(ident, status='stopped', phase='Gestoppt', text='Auftrag gestoppt. Vorhandene Ausgaben bleiben erhalten.')
        except Exception as exc:
            store.update(ident, status='stopped' if job['stop'].is_set() else 'error',
                         text=str(exc), phase='Gestoppt' if job['stop'].is_set() else 'Fehlgeschlagen')
        finally:
            try:
                current = store.get(ident)
                if kind == 'build' and current['status'] != 'complete' and current.get('source_id'):
                    store.update(current['source_id'], actionable=True)
                store.persist()
            finally:
                with self.lock:
                    self.active = None
                self.s['PROJECT_OPERATION_LOCK'].release()

    def start(self):
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify(error='Ungueltige Anfrage'), 400
        kind, request_id = data.get('kind'), data.get('request_id')
        if kind not in ('po', 'build') or not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9-]{8,80}', request_id):
            return jsonify(error='Ungueltiger Auftrag'), 400
        store = self.store()
        previous = next((m for m in store.snapshot()['messages'] if m.get('request_id') == request_id), None)
        if previous:
            return jsonify(id=previous['id']), 200
        if kind == 'po':
            text = data.get('text')
            if not isinstance(text, str) or not text.strip() or len(text) > 50000:
                return jsonify(error='Nachricht fehlt oder ist zu lang.'), 400
            context = self.s['_po_project_context'](mit_verlauf=bool(data.get('mit_verlauf')))
            context += store.followup_context()
            settings = self.s['MC_SETTINGS']
            payload = dict(user_message=text, project_context=context, history=store.history(),
                           base_url=settings['base_url'], model=settings['model'], api_key=settings['api_key'],
                           max_tokens=settings['max_tokens'], think=settings.get('think', True))
            first, _, arguments = text.partition(' ')
            skills = {os.path.splitext(skill['name'])[0].lower(): skill for skill in self.s['available_skills']()}
            if first.startswith('/') and first[1:].lower() in skills:
                skill = skills[first[1:].lower()]
                payload = {'skill': {'type': 'spec', 'summary': skill['description'] or first,
                                    'instruction': self.s['mc_terminal'].render_skill(skill, arguments),
                                    'analyse': self.s['mc_terminal'].skill_flags(skill)['analyse']}}
            store.add('user', text)
        else:
            try:
                source = store.get(data.get('source_id'))
            except StopIteration:
                return jsonify(error='Auftrag nicht gefunden.'), 404
            payload = source.get('decision') or {}
            if not source.get('actionable') or payload.get('type') not in ('spec', 'plan'):
                return jsonify(error='Dieser Auftrag ist nicht mehr aktuell. Bitte erneut mit dem PO abstimmen.'), 409
        for msg in store.snapshot()['messages']:
            if msg.get('actionable'):
                store.update(msg['id'], actionable=False)
        ident = store.add('po' if kind == 'po' else 'build', status='queued', phase='Wird gestartet',
                          model=self.s['MC_SETTINGS']['model'], request_id=request_id,
                          request_text=data.get('text') if kind == 'po' else None,
                          source_id=data.get('source_id'))
        job = {'id': ident, 'store': store, 'stop': threading.Event(), 'proc': None}
        with self.lock:
            self.active = job
        thread = threading.Thread(target=self.worker, args=(job, payload, kind), daemon=True)
        # Transfer the already-reserved operation lock from HTTP to the worker.
        g.project_operation_reserved = False
        try:
            thread.start()
        except Exception:
            with self.lock:
                self.active = None
            g.project_operation_reserved = True
            store.update(ident, status='error', text='Auftrag konnte nicht gestartet werden.')
            raise
        return jsonify(id=ident), 202


def register_chat(app, server):
    runtime = ChatRuntime(app, server)
    app.extensions['chat_runtime'] = runtime
    app.add_url_rule('/chat/start', 'chat_start', runtime.start, methods=['POST'])

    @app.get('/chat/state')
    def chat_state():
        return jsonify(project=server['CURRENT_PROJECT'], **runtime.store().snapshot())

    @app.get('/chat/events')
    def chat_events():
        after = request.args.get('after', type=int)
        if after is None or after < 0:
            return jsonify(error='Ungueltige Ereignisnummer'), 400
        return jsonify(project=server['CURRENT_PROJECT'], **runtime.store().poll(after))

    @app.post('/chat/stop')
    def chat_stop():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify(error='Ungueltige Anfrage'), 400
        with runtime.lock:
            job = runtime.active
            if not job or job['id'] != data.get('id'):
                return jsonify(error='Auftrag ist nicht mehr aktiv.'), 409
            job['stop'].set()
            server['BUILD_STOP'].set()
            job['store'].update(job['id'], status='stopping', phase='Wird gestoppt')
        return jsonify(ok=True)
