"""Vibelove request isolation and persistent rollback references, without LLMs."""
import importlib.util
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest


@pytest.fixture
def server(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'vibelove' / 'server.py'
    spec = importlib.util.spec_from_file_location('vibelove_test_server', path)
    module = importlib.util.module_from_spec(spec)
    # Importing the app must never register process cleanup for the user's servers.
    with patch('atexit.register'), patch('signal.signal'):
        spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'WORKSPACE_DIR', str(tmp_path))
    monkeypatch.setattr(module, 'ensure_backend_running', lambda: None)
    monkeypatch.setattr(module, 'ensure_vite_running', lambda: None)
    monkeypatch.setattr(module, '_preflight_key_modell_fehler', lambda *args: None)
    module.app.config['TESTING'] = True
    return module


def test_stream_blocks_other_mutations_until_closed(server, monkeypatch):
    original_popen = subprocess.Popen
    monkeypatch.setattr(server.subprocess, 'Popen', lambda *args, **kwargs: original_popen(
        ['python3', '-u', '-c', 'import time; print("working", flush=True); time.sleep(30)'],
        **kwargs))
    response = server.app.test_client().post('/build', data={'instruction': 'test'}, buffered=False)
    try:
        assert server.PROJECT_OPERATION_LOCK.locked()
        client = server.app.test_client()
        for route in ['/build', '/projects/aktiv', '/projects/rollback', '/reset', '/projects/file']:
            assert client.post(route).status_code == 409
        assert client.get('/projects').status_code == 200
    finally:
        response.close()
    assert not server.PROJECT_OPERATION_LOCK.locked()
    assert not server.BUILD_STATUS['laeuft']
    assert server.app.test_client().post('/reset').status_code == 200


def test_failed_process_start_releases_build(server, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('test launch failure')
    monkeypatch.setattr(server.subprocess, 'Popen', fail)
    response = server.app.test_client().post('/build', data={'instruction': 'test'}, buffered=True)
    assert b'test launch failure' in response.data
    response.close()
    assert not server.PROJECT_OPERATION_LOCK.locked()
    assert not server.BUILD_STATUS['laeuft']


def test_validation_error_does_not_hold_lock(server):
    client = server.app.test_client()
    assert client.post('/projects/aktiv', json={'name': ''}).status_code == 404
    assert client.post('/reset').status_code == 200
    assert not server.PROJECT_OPERATION_LOCK.locked()


def test_history_keeps_exact_before_and_after_commits(server, tmp_path):
    def git(*args):
        return subprocess.run(['git', *args], cwd=tmp_path, check=True,
                              capture_output=True, text=True).stdout.strip()
    git('init')
    git('config', 'user.name', 'Test')
    git('config', 'user.email', 'test@example.invalid')
    git('commit', '--allow-empty', '-m', 'before')
    before = server.project_head(tmp_path)
    git('commit', '--allow-empty', '-m', 'after')
    server.schreibe_verlauf_eintrag(tmp_path, 'task', 'done', 'model', before)
    entry = server.app.test_client().get('/bauverlauf').get_json()['eintraege'][0]
    assert entry['rollback_to'] == before
    assert entry['commit'] == server.project_head(tmp_path)
    server.schreibe_verlauf_eintrag(tmp_path, 'no change', 'done', 'model', entry['commit'])
    assert server.lade_verlauf(tmp_path)[1]['rollback_to'] is None


def test_edit_file_and_reject_stale_save(server, tmp_path):
    path = tmp_path / 'app.js'
    path.write_bytes(b'first\r\n')
    path.chmod(0o755)
    client = server.app.test_client()
    loaded = client.get('/projects/file?path=app.js').get_json()
    payload = {**loaded, 'content': 'second\r\n'}
    response = client.post('/projects/file', json=payload)
    assert response.status_code == 200
    assert path.read_bytes() == b'second\r\n'
    assert path.stat().st_mode & 0o777 == 0o755
    assert response.get_json()['revision'] != loaded['revision']
    assert client.post('/projects/file', json=payload).status_code == 409
    assert path.read_bytes() == b'second\r\n'
    payload['project'] = 'other'
    assert client.post('/projects/file', json=payload).status_code == 409


def test_editor_rejects_external_symlink_git_and_binary(server, tmp_path):
    client = server.app.test_client()
    outside = tmp_path.parent / (tmp_path.name + '-outside')
    outside.write_text('unchanged')
    (tmp_path / 'link.txt').symlink_to(outside)
    (tmp_path / '.git').mkdir()
    (tmp_path / '.git' / 'config').write_text('unchanged')
    (tmp_path / 'binary').write_bytes(b'a\x00b')
    assert client.get('/projects/file?path=binary').get_json()['binary']
    for path in ['link.txt', '../' + outside.name, '.git/config', 'binary']:
        response = client.post('/projects/file', json={
            'project': 'workspace', 'path': path, 'content': 'changed', 'revision': 'invalid'})
        assert response.status_code == 400
    assert outside.read_text() == 'unchanged'


def test_editor_rejects_truncated_files(server, tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'FILE_PREVIEW_LIMIT', 8)
    (tmp_path / 'large.txt').write_text('123456789')
    client = server.app.test_client()
    loaded = client.get('/projects/file?path=large.txt').get_json()
    assert loaded['truncated'] and loaded['revision'] is None
    assert client.post('/projects/file', json={**loaded, 'content': 'short'}).status_code == 400
