import json
from pathlib import Path
import subprocess

import pytest

from test_vibelove import server
from vibelove import template_manager


@pytest.fixture
def project_api(server, monkeypatch):
    def switch(name):
        server.CURRENT_PROJECT = name
    monkeypatch.setattr(server, 'switch_project', switch)
    original = template_manager.subprocess.run
    def run(cmd, **kwargs):
        if cmd[0] == 'npm':
            return subprocess.CompletedProcess(cmd, 0, '', '')
        return original(cmd, **kwargs)
    monkeypatch.setattr(template_manager.subprocess, 'run', run)
    return server.app.test_client()


def test_template_catalog(project_api):
    items = project_api.get('/project-templates').json['templates']
    assert [t['id'] for t in items] == ['empty', 'vite-business']


@pytest.mark.parametrize('template', ['empty', 'vite-business'])
def test_create_template_project(server, project_api, template):
    response = project_api.post('/projects', json={'name': 'new-app', 'template': template})
    assert response.status_code == 200, response.json
    root = Path(server.PROJEKTE_ROOT, 'new-app')
    assert (root / '.git').is_dir()
    assert server.project_head(str(root))
    assert not list(Path(server.PROJEKTE_ROOT).glob('.new-*'))
    if template != 'empty':
        assert (root / 'frontend/package-lock.json').is_file()
        assert (root / 'DESIGN.md').is_file()
        assert not (root / 'frontend/node_modules').exists()
        assert 'DESIGN.md' in template_manager.project_hint(root)
    else:
        assert not (root / 'frontend').exists()


def test_existing_project_is_never_overwritten(server, project_api):
    root = Path(server.PROJEKTE_ROOT, 'existing')
    root.mkdir(parents=True)
    (root / '.gitignore').write_text('important')
    response = project_api.post('/projects', json={'name': 'existing', 'template': 'vite-business'})
    assert response.status_code == 409
    assert (root / '.gitignore').read_text() == 'important'


@pytest.mark.parametrize('payload', [
    {'name': '../escape'}, {'name': 'workspace'}, {'name': 'bad name'},
    {'name': None}, ['invalid'],
    {'name': 'ok', 'template': '../../workspace'}, {'name': 'ok', 'template': 'missing'},
])
def test_invalid_creation(server, project_api, payload):
    assert project_api.post('/projects', json=payload).status_code == 400
    assert not Path(server.PROJEKTE_ROOT, 'ok').exists()


def test_prepare_failure_leaves_no_partial_project(server, project_api, monkeypatch):
    original = server.CURRENT_PROJECT
    def fail(*args):
        raise ValueError('Installation fehlgeschlagen')
    monkeypatch.setattr(template_manager, 'prepare', fail)
    response = project_api.post('/projects', json={'name': 'failed', 'template': 'vite-business'})
    assert response.status_code == 400
    assert not Path(server.PROJEKTE_ROOT, 'failed').exists()
    assert not list(Path(server.PROJEKTE_ROOT).glob('.new-*'))
    assert server.CURRENT_PROJECT == original


def test_projects_have_separate_browser_storage(tmp_path):
    keys = []
    for name in ['one', 'two']:
        root = tmp_path / name
        root.mkdir()
        template_manager.populate(root, 'vite-business')
        keys.append(json.loads((root / 'frontend/src/project.json').read_text())['storageKey'])
    assert keys[0] != keys[1]


def test_prepare_commands_are_fixed(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(template_manager.subprocess, 'run', lambda cmd, **kw: calls.append((cmd, kw)))
    template_manager.prepare(tmp_path, 'vite-business')
    assert calls[0][0] == ['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund']
    assert calls[1][0] == ['npm', 'run', 'build']
    assert all(call[1]['check'] and call[1]['timeout'] for call in calls)
