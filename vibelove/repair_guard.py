"""Local QA checkpoints and reproducible, deliberately limited checks."""
import ast
import json
import os
import re
import signal
import subprocess


SKIP = {'.git', '.venv', 'venv', 'node_modules', 'dist', 'build', '__pycache__'}


def git(root, *args):
    result = subprocess.run(
        ['git', '-c', 'core.hooksPath=' + os.devnull, '-c', 'core.fsmonitor=false',
         '-c', 'commit.gpgsign=false', '-c', 'user.name=Vibelove QA',
         '-c', 'user.email=qa@localhost', *args],
        cwd=root, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[:400] or 'Git-Sicherung fehlgeschlagen')
    return result.stdout.strip()


def checkpoint(root):
    if not os.path.isdir(os.path.join(root, '.git')) or os.path.islink(os.path.join(root, '.git')):
        raise RuntimeError('Kein eigenes Projekt-Repository; keine automatische Reparatur')
    git(root, 'add', '-A')
    if git(root, 'status', '--porcelain'):
        git(root, 'commit', '-m', 'vibelove: QA-Zwischenstand')
    return git(root, 'rev-parse', 'HEAD')


def restore(root, commit):
    # Preserve the failed attempt in history, including newly created files.
    checkpoint(root)
    git(root, 'restore', '--source=' + commit, '--staged', '--worktree', '--', '.')
    if git(root, 'status', '--porcelain'):
        git(root, 'commit', '-m', 'vibelove: besseren QA-Zwischenstand wiederhergestellt')


def discover_checks(root):
    checks = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith('.')
                   and not os.path.islink(os.path.join(directory, d))]
        for name in files:
            path = os.path.join(directory, name)
            if os.path.islink(path):
                continue
            relative = os.path.relpath(path, root)
            if name.endswith(('.py', '.json')):
                checks.append(('syntax', relative))
            if name == 'package.json':
                try:
                    with open(path, encoding='utf-8') as f:
                        if (json.load(f).get('scripts') or {}).get('build'):
                            checks.append(('build', os.path.relpath(directory, root)))
                except (OSError, ValueError):
                    pass
    return checks


def run_checks(root, checks):
    results = {}
    for kind, relative in checks:
        key = kind + ':' + relative
        path = os.path.join(root, relative)
        try:
            if os.path.commonpath([os.path.realpath(root), os.path.realpath(path)]) != os.path.realpath(root):
                raise ValueError('Pruefpfad verlaesst Projekt')
            if kind == 'syntax':
                with open(path, encoding='utf-8') as f:
                    text = f.read()
                ast.parse(text) if path.endswith('.py') else json.loads(text)
                results[key] = {'ok': True, 'output': 'Syntax OK'}
            else:
                with subprocess.Popen(['npm', 'run', 'build'], cwd=path,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, start_new_session=True) as proc:
                    try:
                        output, _ = proc.communicate(timeout=120)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.communicate()
                        raise RuntimeError('Build-Timeout nach 120s')
                results[key] = {'ok': proc.returncode == 0, 'output': output[-2000:]}
        except (OSError, ValueError, SyntaxError, RuntimeError) as exc:
            results[key] = {'ok': False, 'output': str(exc)[:1000]}
    return results


def criteria(evaluation):
    rows = re.findall(r'^\s*(\d+)\.\s*\[(PASS|FAIL|UNVERIFIED)\]',
                      evaluation.get('criteria', ''), re.M)
    if len({key for key, _ in rows}) != len(rows):
        return {}
    return dict(rows)


def improvement(previous, current, before, after):
    """No traded-away passes; an unverified/new verdict alone is not progress."""
    if any(result['ok'] and not after.get(key, {}).get('ok') for key, result in before.items()):
        return False, 'Eine zuvor erfolgreiche lokale Pruefung schlaegt jetzt fehl.'
    if any(not result['ok'] and key not in before for key, result in after.items()):
        return False, 'Eine neu hinzugekommene lokale Pruefung schlaegt fehl.'
    old, new = criteria(previous), criteria(current)
    if not old or old.keys() != new.keys() or current.get('status') == 'error':
        return False, 'Bewertungen sind nicht verlaesslich vergleichbar.'
    if any(value == 'PASS' and new[key] != 'PASS' for key, value in old.items()):
        return False, 'Ein zuvor erfuelltes Kriterium ist nicht mehr belegt.'
    if any(value != 'FAIL' and new[key] == 'FAIL' for key, value in old.items()):
        return False, 'Ein neues Kriterium schlaegt fehl.'
    progressed = any(not result['ok'] and after.get(key, {}).get('ok') for key, result in before.items())
    progressed |= any(value != 'PASS' and new[key] == 'PASS' for key, value in old.items())
    return bool(progressed), 'Fortschritt belegt.' if progressed else 'Kein belegbarer Fortschritt.'
