"""Vibelove request isolation and persistent rollback references, without LLMs."""
import importlib.util
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
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
    monkeypatch.setattr(module, 'PROJEKTE_ROOT', str(tmp_path / 'projects'))
    monkeypatch.setattr(module, 'SETTINGS_FILE_PATH', str(tmp_path / 'settings.json'))
    monkeypatch.setattr(module, 'GLOBAL_SKILLS_DIR', str(tmp_path / 'global_skills'))
    monkeypatch.setattr(module, 'SHARED_SKILLS_DIR', str(tmp_path / 'shared_skills'))
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


def test_stop_build_while_waiting_for_output(server, monkeypatch):
    original_popen = subprocess.Popen
    processes = []

    def launch(*args, **kwargs):
        proc = original_popen(['python3', '-u', '-c',
                              'import time; print("partial", end="", flush=True); time.sleep(30)'], **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(server.subprocess, 'Popen', launch)
    client = server.app.test_client()
    assert client.post('/build/stop').status_code == 409
    response = client.post('/build', data={'instruction': 'test'}, buffered=False)
    try:
        assert server.app.test_client().post('/build/stop').status_code == 200
        assert b'Bauauftrag gestoppt.' in response.get_data()
    finally:
        response.close()
    assert processes[0].poll() is not None
    assert not server.PROJECT_OPERATION_LOCK.locked()
    assert not server.BUILD_STATUS['laeuft']
    assert server.BUILD_HISTORY[-1]['result_summary'].startswith('Bauauftrag gestoppt.')


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


def test_profiles_migrate_legacy_and_hide_keys(server, tmp_path):
    saved = dict(server.MC_SETTINGS, model='legacy-model', api_key='secret-value', max_steps=200, max_tokens=16000)
    Path(server.SETTINGS_FILE_PATH).write_text(json.dumps(saved))
    server.load_settings()
    server.apply_project_profile()
    server.save_settings()
    response = server.app.test_client().get('/profiles')
    assert b'secret-value' not in response.data
    profile = response.get_json()['profiles'][0]
    assert profile['model'] == 'legacy-model'
    assert profile['api_key_gesetzt'] is True
    assert profile['api_key_masked'] == 'secrxxxxxxxxxxxx'
    assert profile['max_tokens'] == 16000
    assert json.loads(Path(server.SETTINGS_FILE_PATH).read_text())['profiles']['default']['api_key'] == 'secret-value'
    assert Path(server.SETTINGS_FILE_PATH).stat().st_mode & 0o777 == 0o600


def test_mask_api_key_handles_empty_and_short_keys(server):
    assert server.mask_api_key('') == ''
    assert server.mask_api_key('abcd') == 'xxxxxxxxxxxx'
    assert server.mask_api_key('abcde') == 'abcdxxxxxxxxxxxx'


def test_profile_selection_persists_per_project(server):
    client = server.app.test_client()
    payload = dict(name='Remote', model='remote-model', base_url='https://example.invalid/v1',
                   api_key='secret', max_steps=50, max_tokens=8000)
    profile_id = client.post('/profiles', json=payload).get_json()['id']
    assert client.post('/projects/profile', json={'project': 'workspace', 'id': profile_id}).status_code == 200
    assert server.MC_SETTINGS['model'] == 'remote-model'
    server.switch_project('second', start_vite=False)
    assert server.MC_SETTINGS['model'] == server.DEFAULT_MODEL
    server.switch_project('workspace', start_vite=False)
    assert server.MC_SETTINGS['max_steps'] == 50
    assert server.MC_SETTINGS['api_key'] == 'secret'
    server.load_settings()
    assert server.selected_profile() == profile_id
    assert client.post('/profiles', json={'id': profile_id, 'delete': True}).status_code == 409
    assert client.post('/projects/profile', json={'project': 'second', 'id': profile_id}).status_code == 409


def test_profile_edit_keeps_or_clears_key_and_validates(server):
    client = server.app.test_client()
    payload = dict(name='Test', model='test', base_url='http://localhost:1234/v1',
                   api_key='secret', max_steps=200, max_tokens=16000)
    profile_id = client.post('/profiles', json=payload).get_json()['id']
    payload.update(id=profile_id, api_key='')
    assert client.post('/profiles', json=payload).status_code == 200
    assert server.MC_SETTINGS['profiles'][profile_id]['api_key'] == 'secret'
    payload['clear_api_key'] = True
    assert client.post('/profiles', json=payload).status_code == 200
    assert server.MC_SETTINGS['profiles'][profile_id]['api_key'] == ''
    payload['max_tokens'] = 0
    assert client.post('/profiles', json=payload).status_code == 400
    assert client.post('/profiles', json={'id': profile_id, 'delete': True}).status_code == 200


def test_skill_crud_and_project_override_used_by_refine(server):
    client = server.app.test_client()
    payload = dict(name='review.md', scope='global', content='Global $ARGUMENTS', create=True)
    assert client.post('/skills', json=payload).status_code == 200
    payload.update(scope='project', project='workspace', content='---\nanalyse: true\n---\nProject $ARGUMENTS')
    assert client.post('/skills', json=payload).status_code == 200
    assert client.post('/skills', json=payload).status_code == 409
    skills = client.get('/skills').get_json()['skills']
    assert len(skills) == 2
    result = client.post('/refine', json={'message': '/review backend'}).get_json()
    assert result['instruction'] == 'Project backend'
    assert result['analyse'] is True
    assert client.post('/skills', json={**payload, 'delete': True}).status_code == 200
    result = client.post('/refine', json={'message': '/review backend'}).get_json()
    assert result['instruction'] == 'Global backend'


def test_skill_path_and_project_guards(server):
    client = server.app.test_client()
    payload = dict(name='../bad.md', scope='project', project='workspace', content='text')
    assert client.post('/skills', json=payload).status_code == 400
    payload.update(name='good.md', project='other')
    assert client.post('/skills', json=payload).status_code == 409


def test_shared_skills_are_listed_editable_and_usable(server):
    client = server.app.test_client()
    payload = dict(name='seo.md', scope='shared', content='Pruefe $ARGUMENTS', create=True)
    assert client.post('/skills', json=payload).status_code == 200
    skills = client.get('/skills').get_json()['skills']
    assert skills[0]['scope'] == 'shared'
    assert skills[0]['name'] == 'seo.md'
    result = client.post('/refine', json={'message': '/seo example.com'}).get_json()
    assert result['instruction'] == 'Pruefe example.com'
    payload.update(create=False, content='SEO fuer $ARGUMENTS')
    assert client.post('/skills', json=payload).status_code == 200
    assert Path(server.SHARED_SKILLS_DIR, 'seo.md').read_text() == 'SEO fuer $ARGUMENTS'


def test_three_views_render(server):
    response = server.app.test_client().get('/')
    assert response.status_code == 200
    for name in ('buildView', 'setupView', 'skillsView', 'projectProfileSelect'):
        assert name.encode() in response.data
    assert b'id="settingsModal"' not in response.data


def test_build_uses_profile_limits_and_skill_analysis(server, monkeypatch):
    captured = {}
    original_popen = subprocess.Popen

    def launch(command, **kwargs):
        captured['command'] = command
        captured['env'] = kwargs['env']
        return original_popen(['python3', '-c', 'print("done")'], **kwargs)

    monkeypatch.setattr(server.subprocess, 'Popen', launch)
    monkeypatch.setenv('MC_API_KEY', 'unrelated-inherited-key')
    server.MC_SETTINGS.update(model='chosen', max_steps=123, max_tokens=4567, api_key='')
    response = server.app.test_client().post('/build', data={'instruction': 'test', 'analyse': 'true'}, buffered=True)
    response.close()
    command = captured['command']
    assert command[2] == server.MC_PATH
    assert '--analyse' in command[3:]
    assert command[command.index('--max-steps') + 1] == '123'
    assert command[command.index('--model') + 1] == 'chosen'
    assert captured['env']['MC_MAX_TOKENS'] == '4567'
    assert captured['env']['MC_API_KEY'] == ''


def test_po_uses_profile_token_limit(server, monkeypatch):
    captured = {}

    def fake_llm(messages, base_url, model, api_key, **kwargs):
        captured.update(kwargs)
        return '```decision\n{"type":"question"}\n```\n```question\nWelche Farbe?\n```'

    monkeypatch.setattr(server.po, '_call_llm', fake_llm)
    server.MC_SETTINGS['max_tokens'] = 4321
    response = server.app.test_client().post('/refine', json={'message': 'Baue eine App'})
    assert response.get_json()['type'] == 'question'
    assert captured['max_tokens'] == 4321


@pytest.fixture
def preview_restart(server, monkeypatch):
    calls = []
    process = SimpleNamespace(poll=lambda: None, returncode=None)
    monkeypatch.setattr(server, 'stop_vite_processes', lambda: calls.append('stop-preview'))
    monkeypatch.setattr(server, 'stop_backend_server', lambda: calls.append('stop-backend'))
    monkeypatch.setattr(server, '_kill_port', lambda port, *args: calls.append(('kill', port, args)))
    monkeypatch.setattr(server, 'wait_preview_stopped', lambda *args: True)
    monkeypatch.setattr(server, '_backend_manifest', lambda *args: ('backend', 'start'))

    def start_backend():
        calls.append('start-backend')
        server.backend_process = process

    def start_preview():
        calls.append('start-preview')
        server.vite_process = process

    monkeypatch.setattr(server, 'start_backend_server', start_backend)
    monkeypatch.setattr(server, 'start_vite_server', start_preview)
    monkeypatch.setattr(server, 'preview_http_status', lambda port: 200)
    return server, calls


def test_restart_reports_ready_only_after_both_http_checks(preview_restart, monkeypatch):
    server, calls = preview_restart
    client = server.app.test_client()

    def probe(port):
        with ThreadPoolExecutor(max_workers=1) as pool:
            state = pool.submit(lambda: server.app.test_client().get('/preview-status').get_json()).result()
            blocked = pool.submit(lambda: server.app.test_client().post('/projects/aktiv', json={'name': 'other'}).status_code).result()
        calls.append(('http', port, state['phase']))
        assert state['running']
        assert blocked == 409
        return 404 if port == server.BACKEND_PORT else 200

    monkeypatch.setattr(server, 'preview_http_status', probe)
    assert client.post('/restart-vite').status_code == 200
    assert calls.index(('http', server.BACKEND_PORT, 'backend')) < calls.index('start-preview')
    assert ('http', server.PORT_VITE, 'preview') in calls
    state = client.get('/preview-status').get_json()
    assert state['phase'] == 'ready' and not state['running']
    assert not server.PROJECT_OPERATION_LOCK.locked()


def test_restart_refuses_occupied_ports(preview_restart, monkeypatch):
    server, calls = preview_restart
    monkeypatch.setattr(server, 'wait_preview_stopped', lambda *args: False)
    response = server.app.test_client().post('/restart-vite')
    assert response.status_code == 500
    assert 'start-backend' not in calls and 'start-preview' not in calls
    assert server.PREVIEW_STATE['phase'] == 'error'
    assert not server.PROJECT_OPERATION_LOCK.locked()


def test_restart_timeout_exposes_logs_and_can_retry(preview_restart, monkeypatch):
    server, calls = preview_restart
    monkeypatch.setattr(server, 'PREVIEW_START_TIMEOUT', 0)
    monkeypatch.setattr(server, 'preview_http_status', lambda port: 503)
    original_start = server.start_backend_server

    def failing_backend():
        original_start()
        server.PREVIEW_LOG.append('backend failed to load config')

    monkeypatch.setattr(server, 'start_backend_server', failing_backend)
    client = server.app.test_client()
    response = client.post('/restart-vite')
    assert response.status_code == 500
    state = client.get('/preview-status').get_json()
    assert 'HTTP 503' in state['message']
    assert 'failed to load config' in state['log']
    monkeypatch.setattr(server, 'preview_http_status', lambda port: 200)
    assert client.post('/restart-vite').status_code == 200


def test_preview_readiness_detects_process_exit(server):
    proc = SimpleNamespace(poll=lambda: 1, returncode=1)
    with pytest.raises(RuntimeError, match='Exit 1'):
        server.wait_preview_ready(proc, server.PORT_VITE, 'Vorschau')


def test_restart_static_preview_needs_no_backend(preview_restart, monkeypatch):
    server, calls = preview_restart
    monkeypatch.setattr(server, '_backend_manifest', lambda *args: None)
    assert server.app.test_client().post('/restart-vite').status_code == 200
    assert 'start-backend' not in calls
    assert 'start-preview' in calls
