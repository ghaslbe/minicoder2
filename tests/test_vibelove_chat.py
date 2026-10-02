import io
import json
import threading
import time
import subprocess
import sys

import pytest

from test_vibelove import server
from vibelove.chat_store import ChatStore
import po


def wait_until(predicate):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Job did not complete')


def test_store_replay_reload_and_project_isolation(tmp_path):
    store = ChatStore(tmp_path / 'one.json')
    ident = store.add('po', status='running')
    seq = store.snapshot()['seq']
    store.append(ident, 'partial')
    assert store.poll(seq)['events'][0]['text'] == 'partial'
    store.history([{'role': 'user', 'content': 'change existing'}])
    old_seq = store.snapshot()['seq']
    reopened = ChatStore(tmp_path / 'one.json')
    assert reopened.get(ident)['status'] == 'interrupted'
    assert reopened.get(ident)['raw'] == 'partial'
    assert reopened.history()[0]['content'] == 'change existing'
    assert 'reset' in reopened.poll(0)
    assert 'reset' in reopened.poll(old_seq)
    assert ChatStore(tmp_path / 'two.json').snapshot()['messages'] == []


def test_followup_context_preserves_spec_and_result(tmp_path):
    store = ChatStore(tmp_path / 'one.json')
    store.add('po', decision={'instruction': 'Keep CRUD and add export'})
    store.add('build', 'Export implemented')
    context = store.followup_context()
    assert 'Keep CRUD and add export' in context
    assert 'Export implemented' in context
    assert 'Nicht neu generieren' in context


def test_po_stream_reports_reasoning_and_partial_text():
    events = []
    chunks = [
        {'choices': [{'delta': {'reasoning_content': 'thinking'}}]},
        {'choices': [{'delta': {'content': '**Hello**'}}]},
        {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
    ]
    raw = ': keepalive\n\n' + ''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n'
    assert po._read_stream(io.BytesIO(raw.encode()), events.append) == '**Hello**'
    assert events[0]['phase'] == 'reasoning'
    assert events[1]['text'] == '**Hello**'


@pytest.mark.parametrize('reason', ['length', None])
def test_po_incomplete_stream_never_looks_successful(reason):
    data = {'choices': [{'delta': {'content': 'partial'}, 'finish_reason': reason}]}
    events = []
    with pytest.raises(ValueError):
        po._read_stream(io.BytesIO(('data: ' + json.dumps(data) + '\n\n').encode()), events.append)
    assert events[0]['text'] == 'partial'


def test_po_done_does_not_wait_for_connection_close():
    def stream():
        yield b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n'
        yield b'\n'
        yield b'data: [DONE]\n'
        yield b'\n'
        raise AssertionError('Client kept reading after DONE')
    assert po._read_stream(stream(), lambda event: None) == 'ok'


def test_po_job_survives_request_and_replays_on_reload(server, monkeypatch):
    runtime = server.app.extensions['chat_runtime']
    started, release = threading.Event(), threading.Event()
    def work(job, payload):
        job['store'].append(job['id'], '## Live output')
        started.set()
        release.wait(3)
        return {'decision': {'type': 'question', 'question': '**Which color?**'},
                'history': [{'role': 'user', 'content': payload['user_message']}]}
    monkeypatch.setattr(runtime, 'run_po', work)
    client = server.app.test_client()
    try:
        response = client.post('/chat/start', json={'kind': 'po', 'text': 'Build it', 'request_id': 'request-123'})
        assert response.status_code == 202
        assert started.wait(2)
        assert server.PROJECT_OPERATION_LOCK.locked()
        assert client.post('/projects/aktiv', json={'name': 'elsewhere'}).status_code == 409
        state = client.get('/chat/state').json
        assert state['messages'][-1]['raw'] == '## Live output'
        assert state['messages'][-1]['status'] == 'running'
    finally:
        release.set()
        wait_until(lambda: not server.PROJECT_OPERATION_LOCK.locked())
    state = client.get('/chat/state').json
    assert state['messages'][-1]['decision']['question'] == '**Which color?**'
    assert client.post('/chat/start', json={'kind': 'po', 'text': 'Build it', 'request_id': 'request-123'}).status_code == 200
    assert len(client.get('/chat/state').json['messages']) == 2


def test_po_stop_and_followup_context(server, monkeypatch):
    runtime = server.app.extensions['chat_runtime']
    store = runtime.store()
    store.add('po', decision={'instruction': 'Original customer management'})
    store.add('build', 'Customer management completed')
    captured = {}
    def work(job, payload):
        captured.update(payload)
        job['stop'].wait(3)
    monkeypatch.setattr(runtime, 'run_po', work)
    client = server.app.test_client()
    response = client.post('/chat/start', json={'kind': 'po', 'text': 'Now add search', 'request_id': 'followup-123'})
    assert response.status_code == 202
    try:
        wait_until(lambda: bool(captured))
        assert 'Original customer management' in captured['project_context']
        assert 'Customer management completed' in captured['project_context']
        assert client.post('/chat/stop', json={'id': 'wrong'}).status_code == 409
        assert client.post('/chat/stop', json={'id': response.json['id']}).status_code == 200
    finally:
        runtime.shutdown()
        wait_until(lambda: not server.PROJECT_OPERATION_LOCK.locked())
    assert store.get(response.json['id'])['status'] == 'stopped'


def test_job_failure_releases_controls_and_keeps_output(server, monkeypatch):
    runtime = server.app.extensions['chat_runtime']
    def fail(job, payload):
        job['store'].append(job['id'], 'Already received')
        raise TimeoutError('provider idle timeout')
    monkeypatch.setattr(runtime, 'run_po', fail)
    client = server.app.test_client()
    response = client.post('/chat/start', json={'kind': 'po', 'text': 'Build', 'request_id': 'failure-123'})
    wait_until(lambda: not server.PROJECT_OPERATION_LOCK.locked())
    item = runtime.store().get(response.json['id'])
    assert item['status'] == 'error'
    assert item['raw'] == 'Already received'
    assert 'timeout' in item['text']


def test_build_uses_saved_decision_and_invalidates_old_actions(server, monkeypatch):
    runtime = server.app.extensions['chat_runtime']
    store = runtime.store()
    source = store.add('po', decision={'type': 'spec', 'instruction': 'Actual saved instruction'}, actionable=True)
    captured = {}
    def build(job, decision):
        captured.update(decision)
        return {'status': 'complete', 'text': 'done'}
    monkeypatch.setattr(runtime, 'run_build', build)
    client = server.app.test_client()
    response = client.post('/chat/start', json={'kind': 'build', 'source_id': source, 'instruction': 'tampered', 'request_id': 'build-123'})
    assert response.status_code == 202
    wait_until(lambda: not server.PROJECT_OPERATION_LOCK.locked())
    assert captured['instruction'] == 'Actual saved instruction'
    assert not store.get(source)['actionable']
    assert client.post('/chat/start', json={'kind': 'build', 'source_id': source, 'request_id': 'build-456'}).status_code == 409


@pytest.mark.parametrize('success', [True, False])
def test_real_build_generator_reports_status_and_plan_stops(server, monkeypatch, success):
    runtime = server.app.extensions['chat_runtime']
    original = subprocess.Popen
    calls = []
    def launch(cmd, **kwargs):
        if server.MC_PATH in cmd:
            calls.append(cmd)
            script = 'print("✓ Done\\nΣ 1 Requests")' if success else 'print("build failed"); raise SystemExit(1)'
            cmd = [sys.executable, '-u', '-c', script]
        return original(cmd, **kwargs)
    monkeypatch.setattr(server.subprocess, 'Popen', launch)
    source = runtime.store().add('po', actionable=True, decision={'type': 'plan', 'steps': [
        {'num': 1, 'instruction': 'first'}, {'num': 2, 'instruction': 'second'}]})
    client = server.app.test_client()
    response = client.post('/chat/start', json={'kind': 'build', 'source_id': source, 'request_id': 'real-build-123'})
    wait_until(lambda: not server.PROJECT_OPERATION_LOCK.locked())
    message = runtime.store().get(response.json['id'])
    assert message['status'] == ('complete' if success else 'incomplete'), message
    assert len(calls) == (2 if success else 1)
    assert bool(message['raw'])


def test_real_po_process_can_be_stopped_without_waiting_for_provider(server, monkeypatch):
    runtime = server.app.extensions['chat_runtime']
    original = subprocess.Popen
    processes = []
    def launch(cmd, **kwargs):
        if any('po_worker.py' in str(part) for part in cmd):
            cmd = [sys.executable, '-u', '-c',
                   'import json,sys,time; json.load(sys.stdin); print(json.dumps({"type":"phase","phase":"waiting"})); time.sleep(30)']
        proc = original(cmd, **kwargs)
        processes.append(proc)
        return proc
    monkeypatch.setattr(server.subprocess, 'Popen', launch)
    client = server.app.test_client()
    response = client.post('/chat/start', json={'kind': 'po', 'text': 'slow', 'request_id': 'slow-process-123'})
    try:
        wait_until(lambda: bool(processes))
        assert client.post('/chat/stop', json={'id': response.json['id']}).status_code == 200
        wait_until(lambda: not server.PROJECT_OPERATION_LOCK.locked())
        assert processes[0].poll() is not None
        assert runtime.store().get(response.json['id'])['status'] == 'stopped'
    finally:
        runtime.shutdown()
