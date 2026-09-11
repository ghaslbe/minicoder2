import base64
import time

import pytest
from flask import Flask

from vibelove.terminal_backend import TerminalManager, TerminalSession


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv('SHELL', '/bin/sh')
    (tmp_path / 'alpha').mkdir()
    (tmp_path / 'beta').mkdir()
    manager = TerminalManager(lambda name: str(tmp_path / name))
    yield manager
    manager.close()


def wait_for(session, expected):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        output = base64.b64decode(session.read(0)['data'])
        if expected in output:
            return output
        time.sleep(0.05)
    pytest.fail(f'Missing {expected!r} in terminal output: {output!r}')


def test_real_shell_directory_resize_and_interrupt(manager, tmp_path):
    app = Flask(__name__)
    app.register_blueprint(manager.blueprint)
    client = app.test_client()
    assert not manager.sessions
    opened = client.post('/terminal/open', json={'project': 'alpha'}).get_json()
    session = manager.sessions['alpha']
    client.post('/terminal/input', json={**opened, 'data': 'pwd\r'})
    wait_for(session, str(tmp_path / 'alpha').encode())
    assert client.post('/terminal/resize', json={**opened, 'cols': 110, 'rows': 37}).status_code == 200
    session.write('stty size\r')
    wait_for(session, b'37 110')
    session.write('sleep 30\r')
    time.sleep(0.2)
    session.write('\x03')
    session.write("printf 'AFTER_%s\\n' 'INTERRUPT'\r")
    wait_for(session, b'AFTER_INTERRUPT')
    assert client.post('/terminal/open', json={'project': 'alpha'}).get_json()['session'] == opened['session']
    beta = client.post('/terminal/open', json={'project': 'beta'}).get_json()
    assert beta['session'] != opened['session']
    assert client.post('/terminal/input', json={**opened, 'project': 'beta', 'data': 'x'}).status_code == 400


def test_terminal_replay_exit_restart_and_paths(manager):
    app = Flask(__name__)
    app.register_blueprint(manager.blueprint)
    client = app.test_client()
    assert client.post('/terminal/open', json={'project': '../alpha'}).status_code == 400
    assert client.post('/terminal/open', json={'project': 'missing'}).status_code == 400
    opened = client.post('/terminal/open', json={'project': 'alpha'}).get_json()
    session = manager.sessions['alpha']
    session.write("printf 'TERMINAL_%s\\n' 'READY'\r")
    wait_for(session, b'TERMINAL_READY')
    first = client.get('/terminal/output', query_string=opened).get_json()
    assert b'TERMINAL_READY' in base64.b64decode(first['data'])
    assert client.get('/terminal/output', query_string={**opened, 'cursor': first['cursor']}).get_json()['cursor'] >= first['cursor']
    session.write('exit\r')
    session.proc.wait(timeout=3)
    restarted = client.post('/terminal/open', json={**opened, 'restart': True})
    assert restarted.status_code == 200
    assert restarted.get_json()['session'] != opened['session']
