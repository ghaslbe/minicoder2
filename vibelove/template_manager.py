"""Trusted, bundled project scaffolds. Never load template paths from requests."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

TEMPLATE_ROOT = Path(__file__).with_name('project_templates')


def templates():
    result = [{'id': 'empty', 'name': 'Leeres Projekt', 'stack': '', 'version': 1}]
    for manifest in sorted(TEMPLATE_ROOT.glob('*/template.json')):
        with manifest.open(encoding='utf-8') as f:
            data = json.load(f)
        if data['id'] != manifest.parent.name or not (manifest.parent / 'files').is_dir():
            continue
        result.append({key: data[key] for key in ('id', 'name', 'stack', 'version')})
    return result


def populate(destination, template_id):
    selected = next((t for t in templates() if t['id'] == template_id), None)
    if selected is None:
        raise ValueError('Unbekannte Projektvorlage')
    if template_id == 'empty':
        Path(destination, '.gitignore').write_text('node_modules/\ndist/\n.mc-runtime/\n*.log\n.DS_Store\n', encoding='utf-8')
    else:
        source = TEMPLATE_ROOT / template_id / 'files'
        shutil.copytree(source, destination, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('node_modules', 'dist', '__pycache__', '.DS_Store', '.git'))
        Path(destination, '.vibelove-template.json').write_text(json.dumps(selected, indent=2) + '\n', encoding='utf-8')
        Path(destination, 'frontend/src/project.json').write_text(json.dumps({
            'storageKey': 'vibelove.customers.' + uuid.uuid4().hex}, indent=2) + '\n', encoding='utf-8')
    return selected


def prepare(destination, template_id):
    if template_id != 'empty':
        env = os.environ.copy()
        env['CI'] = 'true'
        try:
            subprocess.run(['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund'],
                           cwd=Path(destination, 'frontend'), env=env, capture_output=True,
                           text=True, timeout=180, check=True)
            subprocess.run(['npm', 'run', 'build'], cwd=Path(destination, 'frontend'),
                           env=env, capture_output=True, text=True, timeout=120, check=True)
        except FileNotFoundError:
            raise ValueError('Node.js/npm ist nicht installiert.') from None
        except subprocess.TimeoutExpired:
            raise ValueError('Vorbereitung hat zu lange gedauert. Bitte erneut versuchen.') from None
        except subprocess.CalledProcessError as exc:
            output = (exc.stderr or '') + (exc.stdout or '')
            raise ValueError('Vite-Vorlage konnte nicht vorbereitet werden: ' + output[-1400:]) from None
    git_env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    git = ['git', '-c', 'core.hooksPath=' + os.devnull, '-c', 'commit.gpgsign=false',
           '-c', 'user.name=Vibelove', '-c', 'user.email=vibelove@localhost']
    for args in (['init'], ['add', '-A'], ['commit', '-m', 'vibelove: Projekt aus ' + template_id, '--allow-empty']):
        subprocess.run(git + args, cwd=destination, env=git_env, capture_output=True, text=True, timeout=30, check=True)


def project_hint(project):
    marker = Path(project, '.vibelove-template.json')
    if marker.is_symlink() or not marker.is_file():
        return ''
    return ('\nProjekt wurde aus einer Vorlage angelegt. Lies zuerst DESIGN.md und '
            'ARCHITECTURE.md. Verwende vorhandene Komponenten und entwickle das '
            'bestehende Projekt weiter; kein erneutes Scaffolding. '
            'Verifikation: cd frontend && npm run build.\n')
