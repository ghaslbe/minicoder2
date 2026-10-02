"""Project-local conversation state with bounded, replayable event deltas."""
from collections import deque
import copy
import json
import os
from pathlib import Path
import threading
import time
import uuid

ACTIVE = {'queued', 'running', 'stopping'}
MAX_TEXT = 2000000


class ChatStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.events = deque(maxlen=2000)
        self.saved = 0
        self.state = {'messages': [], 'seq': 0, 'po_history': []}
        if self.path.exists():
            self.state = json.loads(self.path.read_text(encoding='utf-8'))
        for message in self.state['messages']:
            if message['status'] in ACTIVE:
                message.update(status='interrupted', phase='Server wurde neu gestartet; Auftrag unterbrochen.', updated=time.time())
                self.state['seq'] += 1
                if message['role'] == 'build' and message.get('source_id'):
                    for source in self.state['messages']:
                        if source['id'] == message['source_id']:
                            source.update(actionable=True, updated=time.time())
        self.persist()

    def persist(self):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.state, ensure_ascii=False), encoding='utf-8')
            os.replace(tmp, self.path)
            self.saved = time.monotonic()

    def snapshot(self):
        with self.lock:
            return copy.deepcopy({k: self.state[k] for k in ('messages', 'seq')})

    def poll(self, after):
        with self.lock:
            if after > self.state['seq'] or (after < self.state['seq'] and
                    (not self.events or after < self.events[0]['seq'] - 1)):
                return {'reset': self.snapshot()}
            return {'events': copy.deepcopy([e for e in self.events if e['seq'] > after]), 'seq': self.state['seq']}

    def _event(self, event):
        self.state['seq'] += 1
        event['seq'] = self.state['seq']
        self.events.append(event)

    def add(self, role, text='', **fields):
        with self.lock:
            message = dict(id=uuid.uuid4().hex, role=role, text=text, raw='', status='complete',
                           phase='', created=time.time(), updated=time.time())
            message.update(fields)
            self.state['messages'].append(message)
            self._event({'type': 'message', 'message': copy.deepcopy(message)})
            self.persist()
            return message['id']

    def get(self, ident):
        with self.lock:
            return copy.deepcopy(next(m for m in self.state['messages'] if m['id'] == ident))

    def update(self, ident, **fields):
        with self.lock:
            msg = next(m for m in self.state['messages'] if m['id'] == ident)
            fields['updated'] = time.time()
            msg.update(fields)
            self._event({'type': 'update', 'id': ident, 'fields': copy.deepcopy(fields)})
            if msg['status'] not in ACTIVE or time.monotonic() - self.saved > 1:
                self.persist()

    def append(self, ident, text):
        with self.lock:
            msg = next(m for m in self.state['messages'] if m['id'] == ident)
            msg['raw'] = (msg['raw'] + text)[-MAX_TEXT:]
            msg['updated'] = time.time()
            self._event({'type': 'delta', 'id': ident, 'text': text, 'updated': msg['updated']})
            if time.monotonic() - self.saved > 1:
                self.persist()

    def history(self, value=None):
        with self.lock:
            if value is not None:
                self.state['po_history'] = value
                self.persist()
            return copy.deepcopy(self.state['po_history'])

    def followup_context(self):
        with self.lock:
            previous = [m for m in self.state['messages'] if m['role'] in ('po', 'build') and m['status'] == 'complete'][-4:]
            lines = []
            for m in previous:
                decision = m.get('decision') or {}
                text = decision.get('instruction') or m['text']
                lines.append(m['role'] + ': ' + text[:2400])
            if not lines:
                return ''
            return ('\nLETZTE AUFTRAEGE UND ERGEBNISSE:\n' + '\n'.join(lines) +
                    '\nArbeite am vorhandenen Projekt weiter. Nicht neu generieren. '
                    'Die aktuelle Nutzeranweisung hat Vorrang vor frueheren Anforderungen.\n')
