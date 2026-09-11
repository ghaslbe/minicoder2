"""Project-scoped PTY sessions with bounded output replay."""
import base64
import fcntl
import os
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import threading
import uuid

from flask import Blueprint, jsonify, request


class TerminalSession:
    def __init__(self, directory, cols=80, rows=24):
        self.id = uuid.uuid4().hex
        self.lock = threading.RLock()
        self.output = bytearray()
        self.offset = 0
        self.closed = False
        self.eof = False
        self.master, slave = pty.openpty()
        shell = os.environ.get('SHELL', '/bin/zsh')
        if not os.path.isfile(shell):
            shell = '/bin/sh'
        env = dict(os.environ, TERM='xterm-256color', COLORTERM='truecolor')
        try:
            self.resize(cols, rows)
            self.proc = subprocess.Popen(
                [sys.executable, os.path.join(os.path.dirname(__file__), 'terminal_shell.py'), shell],
                cwd=directory, env=env, stdin=slave, stdout=slave, stderr=slave,
                start_new_session=True, close_fds=True)
        except Exception:
            os.close(self.master)
            raise
        finally:
            os.close(slave)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            while not self.closed:
                ready, _, _ = select.select([self.master], [], [], 0.2)
                if not ready:
                    continue
                data = os.read(self.master, 8192)
                if not data:
                    break
                with self.lock:
                    self.output.extend(data)
                    excess = max(0, len(self.output) - 262144)
                    if excess:
                        del self.output[:excess]
                        self.offset += excess
        except (OSError, ValueError):
            pass
        finally:
            self.eof = True

    def read(self, cursor):
        with self.lock:
            cursor = max(self.offset, min(cursor, self.offset + len(self.output)))
            return dict(data=base64.b64encode(self.output[cursor - self.offset:]).decode('ascii'),
                        cursor=self.offset + len(self.output), exited=self.eof,
                        session=self.id, exit_code=self.proc.poll())

    def write(self, data):
        with self.lock:
            if self.closed or self.proc.poll() is not None:
                raise ValueError('Die Shell wurde beendet. Terminal neu starten.')
            raw = data.encode('utf-8')
            while raw:
                _, ready, _ = select.select([], [self.master], [], 1)
                if not ready:
                    raise ValueError('Terminal nimmt gerade keine Eingaben an.')
                count = os.write(self.master, raw[:1024])
                raw = raw[count:]

    def resize(self, cols, rows):
        with self.lock:
            if not self.closed:
                fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            try:
                foreground = os.tcgetpgrp(self.master)
            except OSError:
                foreground = self.proc.pid
            for group in {self.proc.pid, foreground}:
                if group > 0:
                    try:
                        os.killpg(group, signal.SIGHUP)
                    except ProcessLookupError:
                        pass
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait()
        self.reader.join(timeout=1)
        os.close(self.master)


class TerminalManager:
    def __init__(self, project_directory):
        self.project_directory = project_directory
        self.sessions = {}
        self.lock = threading.Lock()
        self.blueprint = Blueprint('terminal', __name__)
        self.blueprint.add_url_rule('/terminal/open', view_func=self.open, methods=['POST'])
        self.blueprint.add_url_rule('/terminal/output', view_func=self.output, methods=['GET'])
        self.blueprint.add_url_rule('/terminal/input', view_func=self.input, methods=['POST'])
        self.blueprint.add_url_rule('/terminal/resize', view_func=self.resize, methods=['POST'])
        self.blueprint.register_error_handler(ValueError, lambda error: (jsonify(error=str(error)), 400))
        self.blueprint.register_error_handler(OSError, lambda error: (jsonify(error=str(error)), 500))

    def project(self, data):
        name = data.get('project', '')
        if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_-]+', name):
            raise ValueError('Ungueltiges Projekt.')
        directory = self.project_directory(name)
        if not os.path.isdir(directory):
            raise ValueError('Projektverzeichnis nicht gefunden.')
        return name, directory

    def size(self, data):
        try:
            cols, rows = int(data.get('cols', 80)), int(data.get('rows', 24))
        except (TypeError, ValueError):
            raise ValueError('Ungueltige Terminalgroesse.')
        return min(500, max(2, cols)), min(200, max(1, rows))

    def open(self):
        data = request.get_json(silent=True) or {}
        name, directory = self.project(data)
        cols, rows = self.size(data)
        with self.lock:
            session = self.sessions.get(name)
            if session and data.get('restart'):
                if data.get('session') != session.id:
                    raise ValueError('Die Terminalsitzung hat sich geaendert.')
                session.close()
                del self.sessions[name]
                session = None
            if session is None:
                session = TerminalSession(directory, cols, rows)
                self.sessions[name] = session
            return jsonify(session=session.id, project=name)

    def session(self, data):
        name, _ = self.project(data)
        with self.lock:
            session = self.sessions.get(name)
            if session is None or session.id != data.get('session'):
                raise ValueError('Terminalsitzung nicht gefunden. Terminal erneut oeffnen.')
            return session

    def output(self):
        try:
            cursor = max(0, int(request.args.get('cursor', 0)))
        except ValueError:
            raise ValueError('Ungueltige Ausgabeposition.')
        return jsonify(self.session(request.args).read(cursor))

    def input(self):
        data = request.get_json(silent=True) or {}
        value = data.get('data', '')
        if not isinstance(value, str) or len(value.encode('utf-8')) > 16384:
            raise ValueError('Eingabe zu gross.')
        self.session(data).write(value)
        return jsonify(ok=True)

    def resize(self):
        data = request.get_json(silent=True) or {}
        self.session(data).resize(*self.size(data))
        return jsonify(ok=True)

    def close(self):
        with self.lock:
            for session in self.sessions.values():
                session.close()
            self.sessions.clear()
