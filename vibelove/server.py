import io
import codecs
import http.client
import hashlib
import tempfile
import json
import os
from collections import deque
import re
import subprocess
import sys
import time
import atexit
import signal
import threading
import uuid
import socket
import select
import zipfile
import urllib.request
import urllib.error
from collections import deque
from flask import Flask, render_template, request, jsonify, send_file, g
from werkzeug.utils import secure_filename

# po.py liegt eine Ebene hoeher, direkt neben mc.py.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import po
import mc_terminal
from vibelove.terminal_backend import TerminalManager

app = Flask(__name__, root_path=os.path.dirname(os.path.abspath(__file__)))
PROJECT_OPERATION_LOCK = threading.Lock()


@app.before_request
def reserve_project_operation():
    if request.method == 'POST' and request.endpoint != 'stop_build' and request.blueprint != 'terminal':
        if not PROJECT_OPERATION_LOCK.acquire(blocking=False):
            return jsonify({'ok': False, 'error': 'Ein Vorgang laeuft noch. Bitte warten.'}), 409
        g.project_operation_reserved = True


@app.after_request
def release_project_operation(response):
    if getattr(g, 'project_operation_reserved', False):
        g.project_operation_reserved = False
        if response.is_streamed:
            response.call_on_close(PROJECT_OPERATION_LOCK.release)
        else:
            PROJECT_OPERATION_LOCK.release()
    return response


@app.teardown_request
def release_failed_project_operation(error):
    if getattr(g, 'project_operation_reserved', False):
        g.project_operation_reserved = False
        PROJECT_OPERATION_LOCK.release()


@app.after_request
def _keine_zwischenspeicherung(response):
    """Vibelove ist ein Ein-Nutzer-Entwerkzeug mit staendig wechselndem
    Zustand (Chat-Verlauf, Vorschau) -- ein vom Browser zwischengespeicherter
    alter Stand (Chat zeigt nur die Antwort ohne den urspruenglichen Prompt,
    Vorschau zeigt eine veraltete Version) sah wie ein Server-Bug aus, war
    aber Browser-Caching. Kein Cache fuer irgendeine Antwort dieser App."""
    response.headers['Cache-Control'] = 'no-store'
    return response
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB Obergrenze fuer Datei-Uploads

# Konfiguration
PORT_VIBELOVE = 5050
PORT_VITE = 5173
# Feste Portkonvention wie bei PORT_VITE: JEDES Projekt-Backend (falls
# vorhanden) lauscht immer auf demselben Port -- kein Aushandeln/Parsen
# eines variablen Ports noetig, genau wie Vite auch immer auf PORT_VITE laeuft.
BACKEND_PORT = 5001
API_PREFIX = '/api/'
WORKSPACE_DIR = os.path.join(os.getcwd(), 'workspace')
PROJEKTE_ROOT = os.path.join(os.getcwd(), 'projekte')
CURRENT_PROJECT = 'workspace'
# mc.py liegt eine Ebene hoeher
MC_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'mc.py'))
STATIC_PREVIEW_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static_preview_server.py')
# Manifest-Datei, ueber die ein Projekt sein Backend beschreibt -- mc.py wird
# in der Bauaufgabe angewiesen, sie zu erzeugen. Deterministisch zu parsen
# (im Gegensatz zu freiem Text in MC-NOTIZEN.md), das ist es, was
# ensure_backend_running() unten liest.
BACKEND_MANIFEST_NAME = 'vibelove-backend.json'

# ── Laufzeit-Einstellungen (konfigurierbar über /settings) ────────────────
SETTINGS_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mc_settings.json')

DEFAULT_MODEL = 'gemma-4-26b-a4b-it@mxfp4'
DEFAULT_BASE_URL = 'http://localhost:1234/v1'

MC_SETTINGS = {
    'model': DEFAULT_MODEL,
    'base_url': DEFAULT_BASE_URL,
    'api_key': '',
    'max_steps': 200,
    'max_tokens': 16000
}

def save_settings():
    """Speichert alle Laufzeit-Einstellungen einschließlich des aktiven Projekts."""
    with tempfile.NamedTemporaryFile(mode='w', dir=os.path.dirname(SETTINGS_FILE_PATH),
                                     encoding='utf-8', delete=False) as f:
        json.dump(MC_SETTINGS, f, indent=2, ensure_ascii=False)
    os.replace(f.name, SETTINGS_FILE_PATH)


PROFILE_FIELDS = ('model', 'base_url', 'api_key', 'max_steps', 'max_tokens')


def ensure_profiles():
    if not MC_SETTINGS.get('profiles'):
        MC_SETTINGS['profiles'] = {'default': {
            'name': MC_SETTINGS['model'], **{key: MC_SETTINGS[key] for key in PROFILE_FIELDS}}}
    MC_SETTINGS.setdefault('project_profiles', {})


def selected_profile():
    ensure_profiles()
    return MC_SETTINGS['project_profiles'].get(CURRENT_PROJECT, next(iter(MC_SETTINGS['profiles'])))


def apply_project_profile():
    ensure_profiles()
    profile_id = selected_profile()
    if profile_id not in MC_SETTINGS['profiles']:
        profile_id = next(iter(MC_SETTINGS['profiles']))
    MC_SETTINGS['project_profiles'][CURRENT_PROJECT] = profile_id
    MC_SETTINGS.update({key: MC_SETTINGS['profiles'][profile_id][key] for key in PROFILE_FIELDS})

def load_settings():
    """Lädt Laufzeit-Einstellungen: erst Env-Variablen, dann mc_settings.json (hat Vorrang)."""
    global MC_SETTINGS

    # 1) Env-Variablen als Basis
    for key, env_name in [('model', 'VIBELOVE_MODEL'), ('base_url', 'VIBELOVE_BASE_URL'), ('api_key', 'MC_API_KEY')]:
        val = os.environ.get(env_name)
        if val:
            MC_SETTINGS[key] = val

    # 2) Gespeicherte Datei hat Vorrang gegenüber Env-Variablen
    try:
        with open(SETTINGS_FILE_PATH, 'r', encoding='utf-8') as f:
            saved = json.load(f)
            for key in ('profiles', 'project_profiles'):
                if isinstance(saved.get(key), dict):
                    MC_SETTINGS[key] = saved[key]
            for key in ('model', 'base_url', 'api_key', 'projekt'):
                if key in saved and saved[key]:
                    MC_SETTINGS[key] = saved[key]
            try:
                saved_max_steps = int(saved.get('max_steps', MC_SETTINGS['max_steps']))
                if saved_max_steps >= 1:
                    MC_SETTINGS['max_steps'] = saved_max_steps
            except (TypeError, ValueError):
                pass
            try:
                saved_max_tokens = int(saved.get('max_tokens', MC_SETTINGS['max_tokens']))
                if saved_max_tokens >= 1:
                    MC_SETTINGS['max_tokens'] = saved_max_tokens
            except (TypeError, ValueError):
                pass
        print(f"[settings] mc_settings.json geladen: model={MC_SETTINGS['model']}, "
              f"base_url={MC_SETTINGS['base_url']}, "
              f"api_key={'gesetzt' if MC_SETTINGS['api_key'] else '(leer)'}")
    except FileNotFoundError:
        print("[settings] Keine mc_settings.json vorhanden – nutze Umgebungsvariablen/Defaults.")
    ensure_profiles()

# Globaler Prozess-Speicher für den Vite-Server
vite_process = None

# Globaler Prozess-Speicher fuer das Backend eines Projekts (siehe
# ensure_backend_running weiter unten) -- getrennt von vite_process, weil
# beide unabhaengig voneinander laufen/neu starten koennen.
backend_process = None

# Chat-Verlauf
BUILD_HISTORY = []

# Gespraechsverlauf mit dem Product-Owner-Miniagenten (po.py) fuer die
# GERADE laufende Klaerung einer Aufgabe -- wird geleert, sobald ein
# Bauauftrag tatsaechlich an mc.py geht oder das Projekt wechselt.
PO_HISTORY = []

# Status eines laufenden Bauauftrags für das Polling bei Stream-Abbrüchen.
BUILD_STATUS = {'laeuft': False, 'zeilen': deque(maxlen=200)}
BUILD_STOP = threading.Event()
BUILD_PROCESS = None


@app.route('/build/stop', methods=['POST'])
def stop_build():
    if BUILD_PROCESS is None or BUILD_PROCESS.poll() is not None:
        return jsonify({'ok': False, 'error': 'Kein Bauprozess aktiv.'}), 409
    BUILD_STOP.set()
    return jsonify({'ok': True})


def terminate_build_process(proc):
    # The build owns a process group, including shell commands started by mc.py.
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()

def add_build_lines(text):
    """Speichert gestreamte Ausgabe zeilenweise für den Status-Endpunkt."""
    for line in text.splitlines():
        BUILD_STATUS['zeilen'].append(line)

# mc.py haengt seine finish-Zusammenfassung als eine Zeile "✓ <text>" DIREKT
# vor der Token-/Kosten-Zeile "Σ ... Requests" an (print_usage_summary()
# laeuft unmittelbar nach run_task()s Rueckkehr, ohne dass etwas anderes
# dazwischen gedruckt wird) -- das ist die einzige Stelle, an der ein sauber
# abgeschlossener Lauf sein Ergebnis in EINEM kurzen Satz zusammenfasst.
_FINISH_SUMMARY_RE = re.compile(r"^✓ (.+)$", re.MULTILINE)
_USAGE_LINE_RE = re.compile(r"^Σ \d+ Requests", re.MULTILINE)
# Grobe Version von mc.pys eigener Endlos-/Wiederholungs-Erkennung (DEGEN_
# CHAR_RE/DEGEN_WORD_RE) -- Sicherheitsnetz, falls trotz allem doch mal
# degenerierter Text als Zusammenfassung durchrutschen sollte.
_DEGEN_CHAR_RE = re.compile(r"(.)\1{119,}", re.DOTALL)
_DEGEN_WORD_RE = re.compile(r"(\b\w{1,20})(?:[ \t]+\1\b){19,}")


def _looks_degenerate(text):
    return bool(_DEGEN_CHAR_RE.search(text) or _DEGEN_WORD_RE.search(text))


def _extract_run_summary(full_output):
    """Zieht mc.pys eigene finish-Zusammenfassung aus der kompletten
    Prozessausgabe -- statt (wie frueher) blind die letzten 500 Zeichen zu
    nehmen. Real beobachtet: ein Lauf entgleiste (degenerierendes Modell,
    Endlos-Wiederholungen/Meta-Kommentar-Muell) und wurde abgebrochen, bevor
    ein finish erreicht wurde -- die alten letzten 500 Zeichen bestanden dann
    aus genau diesem Muell und wurden UNGEFILTERT als 'Ergebnis' des
    vorherigen Schritts in den naechsten Bauauftrag (BUILD_HISTORY)
    uebernommen, was den naechsten Lauf mit sinnlosem Kontext fuetterte.
    Ohne sauberes finish (oder bei degeneriert wirkendem Fund) gibt es
    einen neutralen Platzhalter statt Rohtext."""
    usage_match = _USAGE_LINE_RE.search(full_output)
    if usage_match:
        vor_usage = full_output[:usage_match.start()]
        finish_matches = list(_FINISH_SUMMARY_RE.finditer(vor_usage))
        if finish_matches:
            kandidat = finish_matches[-1].group(1).strip()
            if kandidat and not _looks_degenerate(kandidat):
                return kandidat
    return ("Lauf nicht sauber abgeschlossen (kein finish erreicht) -- "
            "Details im Build-Log, nicht als Kontext uebernommen.")


def stelle_sauberen_arbeitsbaum_sicher(project_dir):
    """Committet liegen gebliebene Aenderungen VOR einem neuen Bauauftrag --
    real beobachtet: ein per SIGTERM abgebrochener Lauf hinterliess neue,
    NIE committete Dateien (frontend/, MC-NOTIZEN.md). mc.pys eigene
    Git-Absicherung (git_usable()) verlangt fuer JEDEN Lauf einen sauberen
    Arbeitsbaum -- fand sie stattdessen 'offene Aenderungen' vor, blieb
    GIT_ROLLBACK fuer den GESAMTEN naechsten Lauf deaktiviert, obwohl dieser
    selbst sauber durchlief. Ergebnis: kein Commit trotz erfolgreichem Build,
    und damit kein Rollback-Ziel fuer die Chat-Oberflaeche. Ohne eigenes
    Git-Repo (z.B. 'workspace') passiert hier nichts."""
    if not os.path.isdir(os.path.join(project_dir, '.git')):
        return
    try:
        status = subprocess.run(['git', 'status', '--porcelain'], cwd=project_dir,
                                 capture_output=True, text=True, timeout=10)
        if not status.stdout.strip():
            return  # Arbeitsbaum bereits sauber -- nichts zu tun
        subprocess.run(['git', 'add', '-A'], cwd=project_dir, capture_output=True, timeout=15)
        subprocess.run(
            ['git', 'commit', '-m', 'vibelove: Zwischenstand (vor naechster Anweisung gesichert)'],
            cwd=project_dir, capture_output=True, text=True, timeout=15
        )
    except Exception:
        pass  # best effort -- ein Fehlschlag hier soll den Bauauftrag nicht blockieren


VERLAUF_DATEINAME = "bauverlauf.jsonl"

def project_head(project_dir):
    if not os.path.isdir(os.path.join(project_dir, '.git')):
        return None
    try:
        result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=project_dir,
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def schreibe_verlauf_eintrag(project_dir, instruction, summary, model, before_commit=None):
    """Haengt einen VOLLSTAENDIGEN Eintrag (keine Kuerzung) an
    <projekt>/bauverlauf.jsonl an -- unabhaengig von Git, damit die Historie
    auch dann erhalten bleibt, wenn ein Commit fehlschlaegt oder ein Lauf
    ohne eigene Dateiaenderungen durchlief. Grundlage fuer GET /bauverlauf
    und damit die Chat-Rekonstruktion beim Laden (reichhaltiger als das
    Git-Log allein, das nur eine knappe Commit-Message pro Lauf hat). Best
    effort, wie stelle_sauberen_arbeitsbaum_sicher()."""
    pfad = os.path.join(project_dir, VERLAUF_DATEINAME)
    commit = project_head(project_dir)
    zeile = json.dumps({
        "zeit": time.strftime("%Y-%m-%d %H:%M:%S"), "model": model,
        "instruction": instruction, "summary": summary,
        "commit": commit,
        "rollback_to": before_commit if commit and commit != before_commit else None,
    }, ensure_ascii=False)
    try:
        with open(pfad, "a", encoding="utf-8") as f:
            f.write(zeile + "\n")
    except OSError:
        pass

def lade_verlauf(project_dir):
    """Liest bauverlauf.jsonl (falls vorhanden) fuer GET /bauverlauf."""
    pfad = os.path.join(project_dir, VERLAUF_DATEINAME)
    eintraege = []
    if not os.path.isfile(pfad):
        return eintraege
    try:
        with open(pfad, "r", encoding="utf-8") as f:
            for zeile in f:
                zeile = zeile.strip()
                if not zeile:
                    continue
                try:
                    eintraege.append(json.loads(zeile))
                except ValueError:
                    continue
    except OSError:
        pass
    return eintraege

def reset_history():
    global BUILD_HISTORY, PO_HISTORY
    BUILD_HISTORY = []
    PO_HISTORY = []

def projekt_dir(name):
    """Bereinigt den Projektnamen und liefert den zugehörigen Verzeichnispfad."""
    name = re.sub(r'[^a-zA-Z0-9_-]', '', str(name))
    if name == 'workspace':
        return WORKSPACE_DIR
    return os.path.join(PROJEKTE_ROOT, name)


TERMINALS = TerminalManager(lambda name: projekt_dir(name))
app.register_blueprint(TERMINALS.blueprint)

STATIC_SERVER_MARKER = 'vibelove_static_preview_marker'

def stop_vite_processes():
    """Beendet alle laufenden Vite-/Statik-Vorschau-Prozesse dieses Projekts
    (pkill + gemerktes Handle)."""
    global vite_process
    try:
        subprocess.run(['pkill', '-f', 'node_modules/.bin/vite'], capture_output=True)
        subprocess.run(['pkill', '-f', STATIC_SERVER_MARKER], capture_output=True)
    except Exception as e:
        print(f'pkill vite: {e}')
    if vite_process:
        try:
            os.killpg(os.getpgid(vite_process.pid), signal.SIGTERM)
        except Exception as e:
            print(f'Fehler beim Stoppen des gemerkten Vite-Prozesses: {e}')
        vite_process = None

def switch_project(name, start_vite=True):
    """Wechselt das aktive Projekt und startet Vite/Backend bei Bedarf neu."""
    global CURRENT_PROJECT
    cleaned = re.sub(r'[^a-zA-Z0-9_-]', '', str(name))
    if not cleaned:
        raise ValueError('Ungültiger Projektname')
    CURRENT_PROJECT = cleaned
    MC_SETTINGS['projekt'] = CURRENT_PROJECT
    apply_project_profile()
    save_settings()
    reset_history()
    if not start_vite:
        return
    stop_vite_processes()
    stop_backend_server()
    # Kurz warten bis Vite-/Backend-Port freigegeben wurden (max ~5s)
    for _ in range(50):
        if not is_port_in_use(PORT_VITE) and not is_port_in_use(BACKEND_PORT):
            break
        time.sleep(0.1)
    start_backend_server()
    start_vite_server()

def extract_urls(text, max_urls=3):
    """Finde http(s)-URLs in einem Text und gib die ersten max_urls zurück."""
    pattern = r'https?://\S+'
    matches = re.findall(pattern, text)
    return matches[:max_urls]

def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

def _static_frontend_dir(proj):
    """Findet ein Verzeichnis mit einer index.html (frontend/ oder Wurzel) fuer
    Projekte OHNE package.json -- reine HTML/CSS/JS-Bauten (z.B. ein
    Canvas-Spiel ohne Build-Tool) haben sonst keine Vorschau, weil
    start_vite_server() ohne package.json bisher schlicht nichts startete."""
    for kandidat in (os.path.join(proj, 'frontend'), proj):
        if os.path.isfile(os.path.join(kandidat, 'index.html')):
            return kandidat
    return None

BACKEND_MARKER = 'vibelove_backend_marker'

def _backend_manifest(proj):
    """Liest backend/vibelove-backend.json, falls vorhanden: {"command":
    "python3 app.py"}. mc.py wird in der Bauaufgabe angewiesen, diese Datei
    anzulegen -- deterministisch parsebar, im Gegensatz zu freiem Text in
    MC-NOTIZEN.md. Der Port ist NICHT Teil der Datei: genau wie Vite immer
    auf PORT_VITE laeuft, laeuft jedes Projekt-Backend immer auf
    BACKEND_PORT. Gibt (backend_dir, command) oder None zurueck."""
    backend_dir = os.path.join(proj, 'backend')
    manifest_path = os.path.join(backend_dir, BACKEND_MANIFEST_NAME)
    if not os.path.isfile(manifest_path):
        return None
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        command = str(data.get('command', '')).strip()
        if not command:
            return None
        return backend_dir, command
    except (OSError, ValueError, TypeError):
        return None

def _frontend_shape(proj):
    """Erkennt die Art des Frontends nach DEMSELBEN Muster wie
    start_vite_server(): Vite (package.json MIT 'dev'-Skript, in frontend/
    oder Wurzel) vor Static (index.html ohne brauchbares package.json) --
    absichtlich dieselbe Erkennung wie die Live-Vorschau, damit 'was vibelove
    zum Testen startet' und 'was der generierte Container ausliefert' nicht
    auseinanderlaufen. Gibt ('vite', verzeichnis) / ('static', verzeichnis)
    / (None, None) zurueck."""
    front_dir = os.path.join(proj, 'frontend')
    front_pkg = os.path.join(front_dir, 'package.json')
    if os.path.isfile(front_pkg) and _hat_dev_skript(front_pkg):
        return 'vite', front_dir
    root_pkg = os.path.join(proj, 'package.json')
    if os.path.isfile(root_pkg) and _hat_dev_skript(root_pkg):
        return 'vite', proj
    static_dir = _static_frontend_dir(proj)
    if static_dir:
        return 'static', static_dir
    return None, None


DOCKERIGNORE = """.git
node_modules
__pycache__
*.pyc
*.db
*.log
dist
"""


def generate_container_files(proj):
    """Baut Dockerfile(s)/.dockerignore (+ docker-compose.yml + nginx.conf bei
    Frontend UND Backend zusammen) fuer das aktive Projekt. Gibt
    (files, hinweis) zurueck: files ist {relativer_pfad: inhalt}, hinweis ein
    kurzer Start-Befehl. (None, fehlermeldung) wenn nichts erkannt wurde."""
    kind, front_dir = _frontend_shape(proj)
    backend = _backend_manifest(proj)  # (backend_dir, command) oder None

    if not kind and not backend:
        return None, ("Kein Frontend (index.html/package.json) und kein "
                       "Backend-Manifest (backend/vibelove-backend.json) "
                       "gefunden -- keine Grundlage fuer einen Container.")

    def rel(p):
        r = os.path.relpath(p, proj)
        return '.' if r == '.' else r.replace(os.sep, '/')

    backend_has_reqs = bool(backend) and os.path.isfile(
        os.path.join(backend[0], 'requirements.txt'))

    def backend_dockerfile():
        backend_dir, command = backend
        backend_rel = rel(backend_dir)
        pip_step = (
            f"COPY {backend_rel}/requirements.txt ./\n"
            "RUN pip install --no-cache-dir -r requirements.txt\n"
            if backend_has_reqs else
            "# Kein requirements.txt gefunden -- keine externen Pakete zu "
            "installieren.\n"
        )
        return (
            "FROM python:3.12-slim\n"
            "WORKDIR /app\n"
            f"{pip_step}"
            f"COPY {backend_rel}/ ./\n"
            f"EXPOSE {BACKEND_PORT}\n"
            f'CMD ["sh", "-c", {json.dumps(command)}]\n'
        )

    def frontend_dockerfile(with_nginx_conf):
        nginx_conf_copy = ("COPY nginx.conf /etc/nginx/conf.d/default.conf\n"
                            if with_nginx_conf else "")
        front_rel = rel(front_dir)
        if kind == 'vite':
            return (
                "FROM node:20-alpine AS build\n"
                "WORKDIR /app\n"
                f"COPY {front_rel}/package*.json ./\n"
                "RUN npm install\n"
                f"COPY {front_rel}/ ./\n"
                "RUN npm run build\n"
                "\n"
                "FROM nginx:alpine\n"
                "COPY --from=build /app/dist /usr/share/nginx/html\n"
                f"{nginx_conf_copy}"
                "EXPOSE 80\n"
            )
        copy_src = f"{front_rel}/" if front_rel != '.' else "."
        return (
            "FROM nginx:alpine\n"
            f"COPY {copy_src} /usr/share/nginx/html\n"
            f"{nginx_conf_copy}"
            "EXPOSE 80\n"
        )

    files = {'.dockerignore': DOCKERIGNORE}

    if backend and kind:
        # Getrennte Frontend-/Backend-Container statt einem gemeinsamen: ein
        # Container fuer beide Prozesse waere ohne eigenes init-System
        # fragil -- und static_preview_server.py macht die Trennung beim
        # lokalen Vorschau-Testen bereits genauso vor (Proxy statt
        # gemeinsamer Prozess).
        files['Dockerfile.backend'] = backend_dockerfile()
        files['Dockerfile.frontend'] = frontend_dockerfile(with_nginx_conf=True)
        files['nginx.conf'] = (
            "server {\n"
            "    listen 80;\n"
            "    root /usr/share/nginx/html;\n"
            "    index index.html;\n"
            "\n"
            f"    location {API_PREFIX} {{\n"
            f"        proxy_pass http://backend:{BACKEND_PORT}{API_PREFIX};\n"
            "        proxy_set_header Host $host;\n"
            "    }\n"
            "\n"
            "    location / {\n"
            "        try_files $uri $uri/ /index.html;\n"
            "    }\n"
            "}\n"
        )
        files['docker-compose.yml'] = (
            "services:\n"
            "  backend:\n"
            "    build:\n"
            "      context: .\n"
            "      dockerfile: Dockerfile.backend\n"
            "    expose:\n"
            f'      - "{BACKEND_PORT}"\n'
            "  frontend:\n"
            "    build:\n"
            "      context: .\n"
            "      dockerfile: Dockerfile.frontend\n"
            "    ports:\n"
            '      - "8080:80"\n'
            "    depends_on:\n"
            "      - backend\n"
        )
        hinweis = "docker compose up --build  (danach http://localhost:8080)"
    elif backend:
        files['Dockerfile'] = backend_dockerfile()
        hinweis = (f"docker build -t projekt . && docker run -p "
                    f"{BACKEND_PORT}:{BACKEND_PORT} projekt")
    else:
        files['Dockerfile'] = frontend_dockerfile(with_nginx_conf=False)
        hinweis = "docker build -t projekt . && docker run -p 8080:80 projekt"

    if backend and not backend_has_reqs:
        hinweis += ("\nHinweis: backend/requirements.txt fehlt -- falls das "
                     "Backend externe Pakete importiert, fehlen die im "
                     "Container.")

    return files, hinweis


def _kill_port(port, sig=signal.SIGTERM):
    """Beendet JEDEN Prozess, der auf 'port' lauscht -- unabhaengig davon, ob
    vibelove ihn selbst gestartet hat. Noetig, weil ein Backend nicht nur
    von start_backend_server() stammen kann, sondern auch von mc.py WAEHREND
    der --check-Verifikation gestartet worden sein kann (\"run\" mit
    background:true, um den eigenen Endpunkt per curl zu testen) -- ein
    solcher Prozess ist vibelove's gemerktem Handle/Marker UNBEKANNT (real
    beobachtet: PPID 1, also bereits verwaist), pkill -f auf einen Marker
    trifft ihn also nicht. Portbasiertes Beenden ist das einzig zuverlaessige
    Mittel, unabhaengig vom Ursprung des Prozesses."""
    try:
        out = subprocess.run(['lsof', '-nP', f'-tiTCP:{port}', '-sTCP:LISTEN'], capture_output=True, text=True, timeout=5)
        for pid in out.stdout.split():
            try:
                os.kill(int(pid), sig)
            except (ValueError, ProcessLookupError, PermissionError):
                pass
    except Exception as e:
        print(f'_kill_port({port}): {e}')

def stop_backend_server():
    """Beendet einen laufenden Backend-Prozess: portbasiert (siehe _kill_port,
    der zuverlaessige Weg) PLUS Marker-pkill/gemerktes Handle als Ergaenzung."""
    global backend_process
    try:
        subprocess.run(['pkill', '-f', BACKEND_MARKER], capture_output=True)
    except Exception as e:
        print(f'pkill backend: {e}')
    if backend_process:
        try:
            os.killpg(os.getpgid(backend_process.pid), signal.SIGTERM)
        except Exception as e:
            print(f'Fehler beim Stoppen des gemerkten Backend-Prozesses: {e}')
        backend_process = None
    _kill_port(BACKEND_PORT)

PREVIEW_LOCK = threading.Lock()
PREVIEW_STATE = {'running': False, 'phase': 'idle', 'message': '', 'project': None}
PREVIEW_LOG = deque(maxlen=100)
PREVIEW_START_TIMEOUT = 30
PREVIEW_STOP_TIMEOUT = 5


def preview_phase(phase, message, running=True):
    with PREVIEW_LOCK:
        PREVIEW_STATE.update(phase=phase, message=message, running=running, project=CURRENT_PROJECT)


def launch_preview(label, command, **kwargs):
    try:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
    except OSError as error:
        with PREVIEW_LOCK:
            PREVIEW_LOG.append(f'[{label}] {error}\n')
        raise

    def collect_output():
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        try:
            while True:
                chunk = os.read(proc.stdout.fileno(), 2048)
                if not chunk:
                    break
                with PREVIEW_LOCK:
                    PREVIEW_LOG.append(f'[{label}] ' + decoder.decode(chunk))
        finally:
            proc.stdout.close()

    threading.Thread(target=collect_output, daemon=True).start()
    return proc


def start_backend_server():
    """Startet das Backend des AKTIVEN Projekts auf BACKEND_PORT, falls eines
    per backend/vibelove-backend.json beschrieben ist. Analog zu
    start_vite_server(), aber fuer den API-Teil eines Projekts -- ohne das
    bliebe ein waehrend --check gestarteter Backend-Prozess nur fuer die
    Dauer des mc.py-Laufs am Leben (kill_bg_procs beendet ihn danach) und
    das fertige Frontend haette nach dem Bauauftrag nichts mehr zum Reden."""
    global backend_process
    if is_port_in_use(BACKEND_PORT):
        return
    manifest = _backend_manifest(projekt_dir(CURRENT_PROJECT))
    if not manifest:
        return
    backend_dir, command = manifest
    print(f"Starte Backend fuer '{CURRENT_PROJECT}' auf Port {BACKEND_PORT}: {command}")
    try:
        backend_process = launch_preview('Backend',
            ["env", f"{BACKEND_MARKER}=1", "bash", "-c", command],
            cwd=backend_dir,
            start_new_session=True
        )
    except Exception as e:
        print(f"Fehler beim Starten des Backend-Servers: {e}")

def ensure_backend_running():
    global backend_process
    if backend_process is not None and backend_process.poll() is None:
        return
    if is_port_in_use(BACKEND_PORT):
        _kill_port(BACKEND_PORT)
        time.sleep(0.3)
    start_backend_server()

def _hat_dev_skript(package_json_pfad):
    """True nur wenn package.json einen 'dev'-Skript-Eintrag hat -- ein
    package.json allein bedeutet noch nicht Vite. mc.py legt auch fuer
    reine Static-Projekte (z.B. ein Canvas-Spiel ohne Build-Tool) ein
    package.json mit eigenen scripts wie 'start'/'build' an, aber ohne
    'dev'. Ohne diese Pruefung wuerde start_vite_server() faelschlich
    'npm run dev' versuchen (Skript fehlt -> Prozess bricht sofort ab,
    keine Vorschau), statt auf den Static-Server-Fallback auszuweichen."""
    try:
        with open(package_json_pfad, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return 'dev' in (data.get('scripts') or {})
    except (OSError, ValueError, TypeError):
        return False

def start_vite_server():
    global vite_process
    if is_port_in_use(PORT_VITE):
        return

    # Verzeichnis des AKTIVEN Projekts nutzen
    proj = projekt_dir(CURRENT_PROJECT)
    front_dir = os.path.join(proj, 'frontend')
    front_pkg = os.path.join(front_dir, 'package.json')
    if not (os.path.isfile(front_pkg) and _hat_dev_skript(front_pkg)):
        # Fallback: manche Projekte liegen direkt im Wurzelverzeichnis
        root_pkg = os.path.join(proj, 'package.json')
        if os.path.isfile(root_pkg) and _hat_dev_skript(root_pkg):
            front_dir = proj
        else:
            static_dir = _static_frontend_dir(proj)
            if static_dir:
                hat_backend = _backend_manifest(proj) is not None
                backend_port = BACKEND_PORT if hat_backend else 0
                print(f"Kein package.json in '{CURRENT_PROJECT}' -- starte "
                      f"stattdessen einen Static-Server (mit Backend-Proxy: "
                      f"{'ja, Port ' + str(backend_port) if backend_port else 'nein'}) "
                      f"fuer die Vorschau auf Port {PORT_VITE}...")
                try:
                    vite_process = launch_preview('Vorschau',
                        ["env", f"{STATIC_SERVER_MARKER}=1", "python3",
                         STATIC_PREVIEW_SCRIPT, static_dir, str(PORT_VITE),
                         str(backend_port), API_PREFIX],
                        start_new_session=True
                    )
                except Exception as e:
                    print(f"Fehler beim Starten des Static-Servers: {e}")
                return
            if _backend_manifest(proj) is not None:
                # Kein Frontend gefunden, aber ein Backend-Manifest -- ein
                # eigenstaendiger Server (z.B. Flask mit serverseitig
                # gerenderten Templates) IST hier die ganze Anwendung, kein
                # getrenntes Frontend zum Ausliefern. static_preview_server.py
                # bekommt einen LEEREN static_dir ('') -- in diesem Modus
                # leitet es JEDE Anfrage an das Backend weiter, nicht nur
                # welche unter API_PREFIX.
                print(f"Kein Frontend, aber Backend-Manifest in "
                      f"'{CURRENT_PROJECT}' -- leite die gesamte Vorschau auf "
                      f"Port {PORT_VITE} an das Backend (Port {BACKEND_PORT}) "
                      f"weiter...")
                try:
                    vite_process = launch_preview('Vorschau',
                        ["env", f"{STATIC_SERVER_MARKER}=1", "python3",
                         STATIC_PREVIEW_SCRIPT, "", str(PORT_VITE),
                         str(BACKEND_PORT), API_PREFIX],
                        start_new_session=True
                    )
                except Exception as e:
                    print(f"Fehler beim Starten des Backend-Proxys: {e}")
                return
            print(f"[vite] Kein package.json und keine index.html in '{CURRENT_PROJECT}' (frontend/ oder Wurzel) – keine Vorschau moeglich.")
            return

    print(f"Starte Vite-Server auf Port {PORT_VITE}...")
    try:
        vite_process = launch_preview('Vite',
            ["npm", "run", "dev", "--", "--port", str(PORT_VITE), "--host", "0.0.0.0", "--strictPort"],
            cwd=front_dir,
            start_new_session=True
        )
    except Exception as e:
        print(f"Fehler beim Starten des Vite-Servers: {e}")

def stop_vite_server():
    global vite_process
    if vite_process:
        print("Stoppe Vite-Server...")
        try:
            os.killpg(os.getpgid(vite_process.pid), signal.SIGTERM)
        except Exception as e:
            print(f"Fehler beim Stoppen des Vite-Servers: {e}")
        vite_process = None

def ensure_vite_running():
    global vite_process
    if vite_process is not None and vite_process.poll() is None:
        return
    if is_port_in_use(PORT_VITE):
        _kill_port(PORT_VITE)
        time.sleep(0.3)
    start_vite_server()

@app.route('/settings', methods=['GET'])
def get_settings():
    """Liefert Modell, Basis-URL, Schrittlimit und ob ein API-Key gesetzt ist – NIE den Key selbst."""
    return jsonify({
        'model': MC_SETTINGS.get('model', DEFAULT_MODEL),
        'base_url': MC_SETTINGS.get('base_url', DEFAULT_BASE_URL),
        'max_steps': MC_SETTINGS.get('max_steps', 200),
        'max_tokens': MC_SETTINGS.get('max_tokens', 16000),
        'api_key_gesetzt': bool(MC_SETTINGS.get('api_key'))
    })


def mask_api_key(key):
    if not key:
        return ''
    return (key[:4] if len(key) > 4 else '') + 'xxxxxxxxxxxx'


@app.route('/profiles', methods=['GET', 'POST'])
def profiles():
    ensure_profiles()
    if request.method == 'GET':
        return jsonify({'profiles': [dict(id=key, **{k: v for k, v in profile.items() if k != 'api_key'},
                                         api_key_gesetzt=bool(profile.get('api_key')),
                                         api_key_masked=mask_api_key(profile.get('api_key', '')))
                                     for key, profile in MC_SETTINGS['profiles'].items()],
                        'selected': selected_profile(), 'project': CURRENT_PROJECT})
    data = request.get_json(silent=True) or {}
    profile_id = data.get('id') or uuid.uuid4().hex
    if not isinstance(profile_id, str):
        return jsonify({'error': 'Ungueltiges Profil.'}), 400
    if data.get('delete'):
        if profile_id not in MC_SETTINGS['profiles']:
            return jsonify({'error': 'Profil nicht gefunden.'}), 404
        if len(MC_SETTINGS['profiles']) == 1 or profile_id in MC_SETTINGS['project_profiles'].values():
            return jsonify({'error': 'Dieses Profil wird noch verwendet oder ist das letzte Profil.'}), 409
        del MC_SETTINGS['profiles'][profile_id]
    else:
        try:
            profile = {key: str(data.get(key, '')).strip() for key in ('name', 'model', 'base_url')}
            if not all(profile.values()) or not profile['base_url'].startswith(('http://', 'https://')):
                raise ValueError()
            for key in ('max_steps', 'max_tokens'):
                profile[key] = int(data.get(key, 0))
                if profile[key] < 1:
                    raise ValueError()
        except (ValueError, TypeError):
            return jsonify({'error': 'Name, Modell, HTTP-Endpunkt und positive Limits erforderlich.'}), 400
        old = MC_SETTINGS['profiles'].get(profile_id, {})
        profile['api_key'] = str(data.get('api_key') or old.get('api_key', ''))
        if data.get('clear_api_key'):
            profile['api_key'] = ''
        # Weitere Modellkennungen, die sich dasselbe Endpunkt/Key-Paar teilen
        # (z.B. mehrere OpenRouter-Modelle) -- Liste zum schnellen Umschalten
        # im Chat, ohne fuer jedes Modell ein eigenes Profil anzulegen. Das
        # 'model'-Feld oben bleibt das beim Speichern aktive Modell; ist es
        # nicht Teil der Liste, wird es automatisch vorangestellt, damit die
        # Schnellauswahl immer mit dem aktiven Wert startet.
        models_raw = data.get('models')
        if models_raw is None:
            models = old.get('models', [])
        elif isinstance(models_raw, list):
            models = [str(m).strip() for m in models_raw if str(m).strip()]
        else:
            models = [line.strip() for line in str(models_raw).splitlines() if line.strip()]
        seen = set()
        deduped = []
        for m in [profile['model']] + models:
            if m not in seen:
                seen.add(m)
                deduped.append(m)
        profile['models'] = deduped
        MC_SETTINGS['profiles'][profile_id] = profile
    apply_project_profile()
    save_settings()
    return jsonify({'ok': True, 'id': profile_id})


@app.route('/projects/profile', methods=['POST'])
def select_project_profile():
    ensure_profiles()
    data = request.get_json(silent=True) or {}
    if data.get('project') != CURRENT_PROJECT:
        return jsonify({'error': 'Projekt hat sich geaendert.'}), 409
    if data.get('id') not in MC_SETTINGS['profiles']:
        return jsonify({'error': 'Profil nicht gefunden.'}), 404
    MC_SETTINGS['project_profiles'][CURRENT_PROJECT] = data['id']
    apply_project_profile()
    save_settings()
    return jsonify({'ok': True})


GLOBAL_SKILLS_DIR = os.path.expanduser('~/.mc/skills')
SHARED_SKILLS_DIR = os.path.join(os.path.dirname(MC_PATH), 'mc_skills')


def skill_directory(scope):
    if scope == 'shared':
        return SHARED_SKILLS_DIR
    return GLOBAL_SKILLS_DIR if scope == 'global' else os.path.join(projekt_dir(CURRENT_PROJECT), 'mc_skills')


def available_skills():
    result = []
    for scope in ('shared', 'global', 'project'):
        directory = skill_directory(scope)
        if not os.path.isdir(directory):
            continue
        for filename in sorted(os.listdir(directory)):
            if not filename.endswith(mc_terminal.SKILL_EXTS):
                continue
            path = os.path.join(directory, filename)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            try:
                with open(path, encoding='utf-8') as source:
                    content = source.read()
                meta, body = mc_terminal._split_frontmatter(content)
                result.append(dict(name=filename, scope=scope, content=content,
                                   description=meta.get('beschreibung', meta.get('description', '')),
                                   body=body, meta=meta))
            except (OSError, UnicodeError):
                continue
    return result


@app.route('/skills', methods=['GET', 'POST'])
def manage_skills():
    if request.method == 'GET':
        return jsonify({'skills': available_skills(), 'project': CURRENT_PROJECT})
    data = request.get_json(silent=True) or {}
    scope, name = data.get('scope'), data.get('name')
    if scope not in ('shared', 'global', 'project') or not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_-]+\.(md|txt)', name):
        return jsonify({'error': 'Ungueltiger Skillname oder Geltungsbereich.'}), 400
    if scope == 'project' and data.get('project') != CURRENT_PROJECT:
        return jsonify({'error': 'Projekt hat sich geaendert.'}), 409
    directory = skill_directory(scope)
    path = os.path.join(directory, name)
    if os.path.islink(directory) or os.path.islink(path):
        return jsonify({'error': 'Verknuepfungen sind nicht bearbeitbar.'}), 400
    try:
        if data.get('delete'):
            os.unlink(path)
        else:
            content = data.get('content')
            if not isinstance(content, str) or len(content.encode('utf-8')) > FILE_PREVIEW_LIMIT:
                return jsonify({'error': 'Ungueltiger oder zu grosser Skillinhalt.'}), 400
            if data.get('create') and os.path.exists(path):
                return jsonify({'error': 'Ein Skill mit diesem Namen existiert bereits.'}), 409
            os.makedirs(directory, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as target:
                target.write(content)
    except OSError as error:
        return jsonify({'error': str(error)}), 400
    return jsonify({'ok': True})

@app.route('/settings', methods=['POST'])
def post_settings():
    """Übernimmt neue MC-Einstellungen aus JSON und speichert sie persistent."""
    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({'ok': False, 'error': 'JSON-Body erforderlich'}), 400

    model = data.get('model')
    base_url = data.get('base_url')
    api_key = data.get('api_key')
    max_steps = data.get('max_steps')
    max_tokens = data.get('max_tokens')

    if model is not None:
        model = str(model).strip()
        if model:
            MC_SETTINGS['model'] = model
        else:
            MC_SETTINGS['model'] = DEFAULT_MODEL

    if base_url is not None:
        base_url = str(base_url).strip()
        if base_url:
            MC_SETTINGS['base_url'] = base_url
        else:
            MC_SETTINGS['base_url'] = DEFAULT_BASE_URL

    # Leerer API-Key lasst den bestehenden unverändert.
    if api_key is not None and api_key != '':
        MC_SETTINGS['api_key'] = str(api_key)

    if data.get('reset_api_key') is True:
        MC_SETTINGS['api_key'] = os.environ.get('MC_API_KEY', '')

    if max_steps is not None:
        try:
            parsed_max_steps = int(max_steps)
            if parsed_max_steps >= 1:
                MC_SETTINGS['max_steps'] = parsed_max_steps
        except (TypeError, ValueError):
            pass

    if max_tokens is not None:
        try:
            parsed_max_tokens = int(max_tokens)
            if parsed_max_tokens >= 1:
                MC_SETTINGS['max_tokens'] = parsed_max_tokens
        except (TypeError, ValueError):
            pass

    # Persistieren – der Key wird gespeichert (nur lokal, nicht über GET ausgeliefert)
    ensure_profiles()
    MC_SETTINGS['profiles'][selected_profile()].update({key: MC_SETTINGS[key] for key in PROFILE_FIELDS})
    try:
        save_settings()
    except Exception as e:
        return jsonify({'ok': False, 'error': f'Speichern fehlgeschlagen: {e}'}), 500

    print(f"[settings] Gespeichert: model={MC_SETTINGS['model']}, "
          f"base_url={MC_SETTINGS['base_url']}, "
          f"max_steps={MC_SETTINGS['max_steps']}, "
          f"max_tokens={MC_SETTINGS['max_tokens']}, "
          f"api_key={'gesetzt' if MC_SETTINGS['api_key'] else '(leer)'}")
    return jsonify({'ok': True})

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/build-status')
def build_status():
    return jsonify({
        'laeuft': BUILD_STATUS['laeuft'],
        'letzte_zeilen': list(BUILD_STATUS['zeilen'])[-15:],
    })


def _bauverlauf_kontext_text():
    """Baut den 'bisherige Bauschritte'-Kontext-Text aus BUILD_HISTORY (letzte
    5 Eintraege), gemeinsam genutzt von /build (immer, siehe unten) und
    /refine (nur wenn der Nutzer im Chat 'Verlauf einbeziehen' aktiviert hat).
    Leerer String, wenn es noch keine Bauschritte in dieser Sitzung gab."""
    if not BUILD_HISTORY:
        return ""
    teile = ["Bisherige Bauschritte in dieser Sitzung (chronologisch, ggf. darauf aufbauen):"]
    for i, entry in enumerate(BUILD_HISTORY[-5:]):
        instr = entry['instruction']
        if len(instr) > 300:
            instr = instr[:300] + f"…[gekuerzt, ursprünglich {len(instr)} Zeichen]"
        teile.append(f"{i+1}. Anweisung: {instr}")
        teile.append(f"   Ergebnis: {entry['result_summary']}")
    return "\n".join(teile)


def _po_project_context(mit_verlauf=False):
    """Kurzer Kontext-Text ueber das aktive Projekt fuer po.py -- die eigentliche
    Sammel-Logik lebt in po.gather_project_context() (auch von po.py's eigener
    Kommandozeile genutzt), hier nur mit dem aktiven vibelove-Projektnamen
    vorangestellt statt eines nackten Pfades. mit_verlauf=True haengt zusaetzlich
    die letzten Bauschritte dieser Sitzung an -- standardmaessig AUS, weil der
    Produktdialog sonst bei jeder Rueckfrage denselben (ggf. langen) Verlauf
    erneut mitschickt; der Nutzer aktiviert es gezielt per Checkbox im Chat."""
    text = (f"Aktives Projekt: {CURRENT_PROJECT}\n\n"
            + po.gather_project_context(projekt_dir(CURRENT_PROJECT)))
    if mit_verlauf:
        verlauf = _bauverlauf_kontext_text()
        if verlauf:
            text += "\n\n" + verlauf
    return text


@app.route('/refine', methods=['POST'])
def refine_instruction():
    """Ein Schritt des Produktdialogs mit po.py: nimmt entweder den
    urspruenglichen Nutzer-Wunsch oder die Antwort auf eine vorige
    Rueckfrage entgegen und gibt entweder eine weitere Rueckfrage oder eine
    fertig ausformulierte Aufgabe fuer mc.py zurueck."""
    data = request.get_json(silent=True) or {}
    message = str(data.get('message', '')).strip()
    if not message:
        return jsonify({'type': 'error', 'error': 'Keine Nachricht erhalten'}), 400
    first, _, arguments = message.partition(' ')
    skills = {os.path.splitext(skill['name'])[0].lower(): skill for skill in available_skills()}
    if first.startswith('/') and first[1:].lower() in skills:
        skill = skills[first[1:].lower()]
        return jsonify({'type': 'spec', 'summary': skill['description'] or first,
                        'instruction': mc_terminal.render_skill(skill, arguments),
                        'analyse': mc_terminal.skill_flags(skill)['analyse']})
    global PO_HISTORY
    context_text = _po_project_context(mit_verlauf=bool(data.get('mit_verlauf')))
    # refine_retrying(), NICHT refine(): dieselbe automatische Wiederholung
    # bei kaputtem Protokoll-Format, die die eigenstaendige Kommandozeile
    # (po.py _main()) schon nutzt -- ohne sie sah dieser Endpunkt hier
    # gelegentliche Format-Aussetzer eines kleinen/schnellen Modells als
    # harten Fehler statt sie (wie im CLI-Pfad laengst ueblich) einfach
    # automatisch neu zu versuchen.
    decision, PO_HISTORY = po.refine_retrying(
        message, context_text, PO_HISTORY,
        MC_SETTINGS['base_url'], MC_SETTINGS['model'], MC_SETTINGS['api_key'],
        max_tokens=MC_SETTINGS['max_tokens'])
    return jsonify(decision)


@app.route('/refine/reset', methods=['POST'])
def refine_reset():
    global PO_HISTORY
    PO_HISTORY = []
    return jsonify({'ok': True})


def _preflight_key_modell_fehler(base_url, model, api_key, timeout=10):
    """Billiger 1-Token-Testaufruf VOR dem eigentlichen (ggf. minutenlangen)
    Bauprozess -- faengt einen falschen/fehlenden/nicht freigeschalteten
    Schluessel fuer dieses Modell sofort ab, statt erst nach Schritt 1 eines
    vollen mc.py-Laufs. Real beobachtet: mehrere volle Bauversuche scheiterten
    ausschliesslich daran, jedes Mal erst nach dem Start bemerkt. Liefert nur
    bei einem EINDEUTIGEN Auth-Fehler (401/403) eine Meldung -- alle anderen
    Faelle (429, 5xx, Timeout, Netzwerkfehler beim Preflight selbst) geben
    None zurueck, damit ein voruebergehendes Preflight-Problem nicht einen
    an sich funktionierenden Bauversuch blockiert; der echte Lauf entscheidet."""
    url = base_url.rstrip('/') + '/chat/completions'
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": "hi"}],
                        "max_tokens": 1}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                  headers={"Content-Type": "application/json"})
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
        return None
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            body_txt = e.read().decode('utf-8', 'replace')[:300]
            return (f"Vorab-Pruefung fehlgeschlagen: HTTP {e.code} vom Endpoint fuer "
                    f"Modell '{model}' -- vermutlich Schluessel-/Zugriffsproblem "
                    f"(fehlend, falsch, abgelaufen oder Modell auf diesem Konto/"
                    f"Schluessel nicht freigeschaltet): {body_txt}")
        return None
    except Exception:
        return None


@app.route('/build', methods=['POST'])
def build():
    instruction = request.form.get('instruction', '')
    if not instruction:
        return "Keine Anweisung erhalten."
    global PO_HISTORY
    PO_HISTORY = []  # der Produktdialog fuer DIESE Aufgabe ist mit dem Bauauftrag abgeschlossen

    # Kontext-Text zusammenbauen aus BUILD_HISTORY (letzte 5 Einträge). Die
    # Anweisung wird gekuerzt (nicht die volle, ggf. sehr lange po.py-
    # generierte Spezifikation wiederholt) -- real beobachtet: nach mehreren
    # gescheiterten Versuchen wuchs allein diese Wiederholung auf ueber
    # 200000 Prompt-Token an, ohne dass der eigentliche NEUE Auftrag laenger
    # geworden waere. Fuer Kontinuitaet reicht ein kurzer Hinweis, WAS
    # verlangt war -- das Ergebnis (Erfolg/Fehler) bleibt vollstaendig.
    verlauf = _bauverlauf_kontext_text()
    if verlauf:
        full_instruction = verlauf + f"\n\nNEUE Anweisung: {instruction}"
    else:
        full_instruction = instruction

    # Der geforderte Zusatztext
    suffix = ("\n\nLege ein NEUES Projektgeruest (npm create ...) IMMER in einen Unterordner wie frontend/ an, nie direkt ins Wurzelverzeichnis (dort liegt Git-Zubehoer, der Generator wuerde interaktiv haengen). Starte KEINEN dauerhaften Dev-Server im Hintergrund. Pruefe Frontend-Aenderungen ausschliesslich per 'npm run build' (muss exit 0 liefern). Falls du einen Server kurz zum Testen per curl brauchst, starte ihn, teste, und beende ihn danach wieder (kill), bevor du finish aufrufst."
              f"\n\nFalls diese Aufgabe einen EIGENEN Backend-/Serverprozess braucht (z.B. eine "
              f"Flask/Express-API -- auch wenn es KEIN separates Frontend gibt, etwa bei einer "
              f"serverseitig gerenderten App mit Templates): lege den Backend-Code in einen "
              f"Unterordner backend/, lass ihn IMMER auf dem FESTEN Port {BACKEND_PORT} lauschen "
              f"(nicht konfigurierbar, nicht selbst waehlen), und lege "
              f"backend/{BACKEND_MANIFEST_NAME} mit dem Startbefehl an, z.B. "
              f'{{"command": "python3 app.py"}} -- nur so erkennt und startet die Live-Vorschau '
              f"das Backend automatisch.")
    full_instruction += suffix

    found_urls = extract_urls(instruction)
    if found_urls:
        url_list = "\n".join(f"- {url}" for url in found_urls)
        url_hint = f"\n\nHinweis: Die Anweisung enthält {len(found_urls)} URL(s):\n{url_list}\nBitte diese URLs ZUERST mit 'curl -sL' abrufen und die abgerufenen Inhalte als Vorlage für die Umsetzung nutzen."
        full_instruction += url_hint

    print(f"Starte Bauprozess für: {instruction[:50]}...")
    
    # Laufzeit-Einstellungen verwenden (aus MC-Settings-Dict)
    base_url = MC_SETTINGS['base_url']
    model = MC_SETTINGS['model']

    preflight_fehler = _preflight_key_modell_fehler(base_url, model, MC_SETTINGS.get('api_key', ''))
    if preflight_fehler:
        return preflight_fehler

    # Bauziel: das aktive Projekt (Verzeichnis sicherstellen)
    aktives_projekt_dir = projekt_dir(CURRENT_PROJECT)
    os.makedirs(aktives_projekt_dir, exist_ok=True)
    # Liegen gebliebene Aenderungen (z.B. von einem abgebrochenen Lauf) VOR
    # dem neuen Bauauftrag sichern -- sonst faellt mc.pys eigene Git-
    # Absicherung fuer den GESAMTEN naechsten Lauf aus (siehe Docstring).
    stelle_sauberen_arbeitsbaum_sicher(aktives_projekt_dir)
    before_commit = project_head(aktives_projekt_dir)

    # Befehl zusammenbauen
    command = [
        "python3", "-u", 
        MC_PATH,
        "--dir", aktives_projekt_dir,
        "--yes",
        "--check",
        "--max-steps", str(MC_SETTINGS['max_steps']),
        "--base-url", base_url,
        "--model", model,
        full_instruction
    ]
    if request.form.get('analyse') == 'true':
        command.insert(3, '--analyse')

    try:
        from flask import stream_with_context, Response

        def generate():
            nonlocal output
            global BUILD_PROCESS
            output_lines = []
            BUILD_STOP.clear()
            BUILD_STATUS['laeuft'] = True
            BUILD_STATUS['zeilen'].clear()

            def emit(text):
                output_lines.append(text)
                add_build_lines(text)
                return text

            env = os.environ.copy()
            env['MC_API_KEY'] = MC_SETTINGS.get('api_key', '')
            if MC_SETTINGS.get('max_tokens'):
                env['MC_MAX_TOKENS'] = str(MC_SETTINGS['max_tokens'])
            proc = None
            start_time = time.time()
            timeout_duration = 900
            try:
                proc = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    bufsize=0, env=env, start_new_session=True
                )
                BUILD_PROCESS = proc
                decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
                yield emit('\nBauprozess gestartet.\n')
                while True:
                    if BUILD_STOP.is_set():
                        yield emit('\nBauauftrag gestoppt.\n')
                        break
                    if time.time() - start_time > timeout_duration:
                        yield emit("\nFehler: Bauprozess hat das Timeout von 900 Sekunden überschritten.\n")
                        break
                    ready, _, _ = select.select([proc.stdout], [], [], 1.0)
                    if ready:
                        chunk = os.read(proc.stdout.fileno(), 65536)
                        if chunk:
                            yield emit(decoder.decode(chunk))
                        else:
                            yield emit(decoder.decode(b'', final=True))
                            break
                    elif proc.poll() is not None:
                        break
                    else:
                        time.sleep(0.1)
            except Exception as e:
                yield emit(f"\nFehler während des Prozesses: {str(e)}")
            finally:
                BUILD_PROCESS = None
                if proc is not None:
                    terminate_build_process(proc)
                if proc is not None and proc.stdout:
                    proc.stdout.close()
                BUILD_STATUS['laeuft'] = False
                # Dieselbe Absicherung wie vor dem NAECHSTEN Bauauftrag, aber
                # sofort statt erst verzoegert -- ein liegen gebliebener,
                # nie committeter Stand (abgebrochener/gescheiterter Lauf)
                # sass sonst unbegrenzt lange als offene Aenderung im
                # Arbeitsbaum, falls kein weiterer Bauauftrag folgte.
                stelle_sauberen_arbeitsbaum_sicher(aktives_projekt_dir)
                ensure_backend_running()
                ensure_vite_running()
                full_output = "".join(output_lines)
                output = full_output
                summary = ('Bauauftrag gestoppt. Zwischenstand gesichert.'
                           if BUILD_STOP.is_set() else _extract_run_summary(full_output))
                schreibe_verlauf_eintrag(aktives_projekt_dir, instruction, summary, model, before_commit)
                BUILD_HISTORY.append({"instruction": instruction, "result_summary": summary})
            yield ""

        output = ""
        return Response(stream_with_context(generate()), mimetype='text/plain')

    except Exception as e:
        output = str(e)
        schreibe_verlauf_eintrag(aktives_projekt_dir, instruction, output, model)
        BUILD_HISTORY.append({"instruction": instruction, "result_summary": output})
        return output

@app.route('/projects', methods=['GET'])
def list_projects():
    """Alle Projekte (workspace + Unterverzeichnisse von projekte/) + aktives."""
    projekte = ['workspace']
    try:
        if os.path.isdir(PROJEKTE_ROOT):
            projekte += sorted(d for d in os.listdir(PROJEKTE_ROOT)
                               if os.path.isdir(os.path.join(PROJEKTE_ROOT, d)))
    except OSError:
        pass
    return jsonify({'projekte': projekte, 'aktiv': CURRENT_PROJECT})


@app.route('/projects', methods=['POST'])
def create_project():
    """Legt ein neues Projekt an und macht es aktiv."""
    data = request.get_json(silent=True) or {}
    name = re.sub(r'[^a-zA-Z0-9_-]', '', str(data.get('name', '')))
    if not name or name == 'workspace':
        return jsonify({'ok': False, 'error': 'Ungueltiger Projektname'}), 400
    project_path = os.path.join(PROJEKTE_ROOT, name)
    os.makedirs(project_path, exist_ok=True)
    with open(os.path.join(project_path, '.gitignore'), 'w', encoding='utf-8') as f:
        f.write('node_modules/\ndist/\n*.log\n.DS_Store\n')
    try:
        subprocess.run(['git', 'init'], cwd=project_path, capture_output=True)
        subprocess.run(['git', 'add', '-A'], cwd=project_path, capture_output=True)
        subprocess.run(
            ['git', 'commit', '-m', f'Erst-Commit: {name} aus vibelove', '--allow-empty'],
            cwd=project_path,
            capture_output=True
        )
    except Exception:
        pass
    switch_project(name)
    return jsonify({'ok': True, 'aktiv': CURRENT_PROJECT})


@app.route('/projects/aktiv', methods=['POST'])
def activate_project():
    """Wechselt das aktive Projekt."""
    data = request.get_json(silent=True) or {}
    name = re.sub(r'[^a-zA-Z0-9_-]', '', str(data.get('name', '')))
    if not name or (name != 'workspace'
                    and not os.path.isdir(os.path.join(PROJEKTE_ROOT, name))):
        return jsonify({'ok': False, 'error': 'Projekt nicht gefunden'}), 404
    switch_project(name)
    return jsonify({'ok': True, 'aktiv': CURRENT_PROJECT})


def aktives_projekt_hat_eigenes_git_repo():
    """Prüft, ob das aktive Projekt ein eigenes Git-Repository besitzt."""
    return os.path.isdir(os.path.join(projekt_dir(CURRENT_PROJECT), '.git'))


def git_repo_fehler():
    """Verhindert Git-Operationen auf workspace bzw. Projekten ohne eigenes Repo."""
    if not aktives_projekt_hat_eigenes_git_repo():
        return jsonify({'ok': False, 'error': 'Projekt hat kein eigenes Git-Repo'}), 400
    return None


@app.route('/projects/remote', methods=['GET'])
def get_project_remote():
    """Liefert die Origin-URL des aktiven Projekt-Repositories."""
    fehler = git_repo_fehler()
    if fehler:
        return fehler

    try:
        result = subprocess.run(
            ['git', 'remote', 'get-url', 'origin'],
            cwd=projekt_dir(CURRENT_PROJECT),
            capture_output=True,
            text=True
        )
        url = result.stdout.strip() if result.returncode == 0 else ''
    except Exception:
        url = ''
    return jsonify({'url': url})


@app.route('/projects/remote', methods=['POST'])
def post_project_remote():
    """Setzt oder entfernt die Origin-URL des aktiven Projekt-Repositories."""
    fehler = git_repo_fehler()
    if fehler:
        return fehler

    data = request.get_json(silent=True) or {}
    url = str(data.get('url', '')).strip()
    project_path = projekt_dir(CURRENT_PROJECT)

    try:
        if url:
            exists = subprocess.run(
                ['git', 'remote', 'get-url', 'origin'],
                cwd=project_path,
                capture_output=True,
                text=True
            ).returncode == 0
            command = ['git', 'remote', 'set-url' if exists else 'add', 'origin', url]
            subprocess.run(command, cwd=project_path, capture_output=True, text=True)
        else:
            subprocess.run(
                ['git', 'remote', 'remove', 'origin'],
                cwd=project_path,
                capture_output=True,
                text=True
            )
    except Exception:
        pass
    return jsonify({'ok': True})


@app.route('/projects/push', methods=['POST'])
def push_project():
    """Pusht den aktuellen HEAD des aktiven Projekt-Repositories zu Origin."""
    fehler = git_repo_fehler()
    if fehler:
        return fehler

    try:
        result = subprocess.run(
            ['git', 'push', '-u', 'origin', 'HEAD'],
            cwd=projekt_dir(CURRENT_PROJECT),
            capture_output=True,
            text=True,
            timeout=60
        )
        ausgabe = (result.stdout + result.stderr).strip()
        return jsonify({
            'ok': result.returncode == 0,
            'ausgabe': '\n'.join(ausgabe.splitlines()[-20:])
        })
    except subprocess.TimeoutExpired as e:
        ausgabe = ((e.stdout or '') + (e.stderr or '')).strip()
        return jsonify({
            'ok': False,
            'ausgabe': '\n'.join((ausgabe + '\nPush hat das Timeout von 60 Sekunden überschritten.').splitlines()[-20:])
        })
    except Exception as e:
        return jsonify({'ok': False, 'ausgabe': str(e)})


@app.route('/bauverlauf', methods=['GET'])
def bauverlauf():
    """Liefert die BAUVERLAUF.md-Eintraege (Anweisung+Ergebnis pro Build)
    des AKTIVEN Projekts als JSON -- Grundlage fuer die Chat-Rekonstruktion
    beim Laden, reichhaltiger als das Git-Log allein (das nur eine knappe
    Commit-Message hat, nicht die urspruengliche Anweisung)."""
    return jsonify({'eintraege': lade_verlauf(projekt_dir(CURRENT_PROJECT))})

@app.route('/projects/git-log', methods=['GET'])
def project_git_log():
    """Liefert die Commit-Historie des AKTIVEN Projekts als Grundlage fuer die
    Chat-Oberflaeche: jeder saubere mc-Lauf erzeugt bereits einen eigenen
    Commit (mc.pys eigene git_commit_run()) -- die Git-Historie ist damit die
    natuerliche, ueber Seiten-Reloads hinweg persistente Quelle fuer 'was
    wurde wann gebaut', OHNE eine zweite, parallele Chat-Historie im Server
    pflegen zu muessen. Kein eigenes Git-Repo -> leere Liste (kein Fehler),
    damit die Oberflaeche einfach einen leeren Chat zeigt."""
    if not aktives_projekt_hat_eigenes_git_repo():
        return jsonify({'commits': []})
    project_path = projekt_dir(CURRENT_PROJECT)
    try:
        # %x1e (Record Separator) trennt Commits, %x1f (Unit Separator) die
        # Felder INNERHALB eines Commits -- der Body (%b) kann selbst
        # Zeilenumbrueche enthalten, ein simples splitlines() auf %s allein
        # wuerde also reichen, aber %b braucht einen eindeutigen Endpunkt.
        result = subprocess.run(
            ['git', 'log', '--reverse', '--pretty=format:%H%x1f%P%x1f%ci%x1f%s%x1f%b%x1e',
             '-n', '100'],
            cwd=project_path, capture_output=True, text=True, timeout=15
        )
    except Exception as e:
        return jsonify({'commits': [], 'error': str(e)})
    if result.returncode != 0:
        return jsonify({'commits': []})
    commits = []
    for block in result.stdout.split('\x1e'):
        block = block.strip('\n')
        if not block:
            continue
        teile = block.split('\x1f')
        if len(teile) != 5:
            continue
        commit_hash, eltern, datum, nachricht, body = teile
        # Modell-Trailer, den mc.pys git_commit_run() bei Bedarf an den
        # Commit-Body anhaengt ("Modell: <name>") -- Chat-Nachrichten koennen
        # so nachtraeglich zeigen, mit welchem Modell dieser Lauf entstand,
        # ohne eine zweite, parallele Chat-Historie im Server zu pflegen.
        modell_match = re.search(r'^Modell:\s*(.+)$', body, re.MULTILINE)
        commits.append({
            'hash': commit_hash,
            'parent': eltern.split(' ')[0] if eltern else None,
            'date': datum,
            'message': nachricht,
            'model': modell_match.group(1).strip() if modell_match else None,
        })
    return jsonify({'commits': commits})


@app.route('/projects/rollback', methods=['POST'])
def rollback_project():
    """Setzt das AKTIVE Projekt per 'git reset --hard' + 'git clean -fd' auf
    einen zuvor per /projects/git-log erfassten Commit zurueck -- die
    Chat-Oberflaeche bietet das pro Nachricht als 'Rueckgaengig' an, um exakt
    den Stand VOR jener Anweisung wiederherzustellen (Lovable-/Undo-
    Semantik). 'git clean -fd' entfernt dabei nur echte Neuzugaenge, die nie
    committet wurden (z.B. Reste eines fehlgeschlagenen Laufs) -- .gitignore-
    Eintraege wie node_modules/ bleiben unangetastet (kein -x)."""
    fehler = git_repo_fehler()
    if fehler:
        return fehler
    data = request.get_json(silent=True) or {}
    commit = str(data.get('commit', '')).strip()
    if not re.fullmatch(r'[0-9a-fA-F]{7,40}', commit or ''):
        return jsonify({'ok': False, 'error': 'Ungueltiger oder fehlender Commit-Hash'}), 400
    project_path = projekt_dir(CURRENT_PROJECT)
    # Erst pruefen, dass der Commit WIRKLICH in DIESEM Repo existiert -- sonst
    # koennte ein veralteter Hash (z.B. nach Projektwechsel im Browser-Tab)
    # versehentlich im falschen Projekt landen.
    check = subprocess.run(['git', 'cat-file', '-e', commit + '^{commit}'],
                            cwd=project_path, capture_output=True, text=True)
    if check.returncode != 0:
        return jsonify({'ok': False, 'error': 'Commit nicht in diesem Projekt gefunden'}), 400
    reset = subprocess.run(['git', 'reset', '--hard', commit],
                            cwd=project_path, capture_output=True, text=True)
    if reset.returncode != 0:
        return jsonify({'ok': False, 'error': reset.stderr.strip()[:300]}), 500
    subprocess.run(['git', 'clean', '-fd'], cwd=project_path, capture_output=True, text=True)
    reset_history()
    ensure_vite_running()
    return jsonify({'ok': True})


# ── Datei-Explorer: alle Dateien eines Projekts ansehen und hochladen ──────

BROWSE_IGNORE_DIRS = {'.git', 'node_modules', '__pycache__', 'dist', '.venv', 'venv'}
FILE_PREVIEW_LIMIT = 2 * 1024 * 1024  # 2 MB -- reicht fuer Quelltext, keine Vorschau fuer riesige Binaries


def _resolve_project_path(relpath):
    """Loest einen vom Client kommenden relativen Pfad GEGEN das AKTIVE
    Projektverzeichnis auf und stellt sicher, dass das Ergebnis dort auch
    WIRKLICH drinbleibt (kein '../../etc/passwd') -- Datei-Explorer und
    Upload sind sonst ein klassischer Path-Traversal-Weg nach draussen.
    Gibt den absoluten Pfad zurueck oder None, wenn er ausserhalb liegt."""
    base = os.path.realpath(projekt_dir(CURRENT_PROJECT))
    target = os.path.realpath(os.path.join(base, (relpath or '').lstrip('/')))
    if target != base and not target.startswith(base + os.sep):
        return None
    return target


@app.route('/projects/files', methods=['GET'])
def list_project_files():
    """Listet alle Dateien des aktiven Projekts (rekursiv, ohne .git/
    node_modules/__pycache__/dist/venv) fuer den Datei-Explorer-Tab."""
    base = os.path.realpath(projekt_dir(CURRENT_PROJECT))
    files = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames
                              if d not in BROWSE_IGNORE_DIRS and not d.startswith('.'))
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, base).replace(os.sep, '/')
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            files.append({'path': rel, 'size': size})
    files.sort(key=lambda f: f['path'])
    return jsonify({'files': files})


@app.route('/projects/file', methods=['GET'])
def get_project_file():
    """Liefert den Inhalt EINER Datei des aktiven Projekts fuer die
    Datei-Explorer-Vorschau. Text bis FILE_PREVIEW_LIMIT wird als String
    geliefert, groessere/binaere Dateien nur mit binary:true markiert
    (kein Download-Zwang -- dafuer gibt es bereits /download-zip)."""
    relpath = request.args.get('path', '')
    full = _resolve_project_path(relpath)
    if not full or not os.path.isfile(full):
        return jsonify({'error': 'Datei nicht gefunden'}), 404
    size = os.path.getsize(full)
    try:
        with open(full, 'rb') as f:
            raw = f.read(FILE_PREVIEW_LIMIT + 1)
    except OSError as e:
        return jsonify({'error': str(e)}), 500
    truncated = len(raw) > FILE_PREVIEW_LIMIT
    raw = raw[:FILE_PREVIEW_LIMIT]
    try:
        content = raw.decode('utf-8')
        if '\x00' in content:
            raise UnicodeDecodeError('utf-8', raw, 0, 1, 'binary content')
        return jsonify({'path': relpath, 'size': size, 'binary': False,
                         'truncated': truncated, 'content': content,
                         'project': CURRENT_PROJECT,
                         'revision': hashlib.sha256(raw).hexdigest() if not truncated else None})
    except UnicodeDecodeError:
        return jsonify({'path': relpath, 'size': size, 'binary': True,
                         'truncated': truncated})


@app.route('/projects/file', methods=['POST'])
def save_project_file():
    data = request.get_json(silent=True) or {}
    if data.get('project') != CURRENT_PROJECT:
        return jsonify({'error': 'Das aktive Projekt hat sich geaendert. Datei neu laden.'}), 409
    relpath = data.get('path')
    content = data.get('content')
    if not isinstance(relpath, str) or not isinstance(content, str) or '\x00' in content:
        return jsonify({'error': 'Ungueltiger Dateipfad oder Textinhalt.'}), 400
    full = _resolve_project_path(relpath)
    base = os.path.realpath(projekt_dir(CURRENT_PROJECT))
    if not full or not os.path.isfile(full) or '.git' in os.path.relpath(full, base).split(os.sep):
        return jsonify({'error': 'Datei nicht bearbeitbar.'}), 400
    encoded = content.encode('utf-8')
    if len(encoded) > FILE_PREVIEW_LIMIT:
        return jsonify({'error': 'Der Editor unterstuetzt Dateien bis 2 MB.'}), 413
    temporary = None
    try:
        with open(full, 'rb') as source:
            original = source.read(FILE_PREVIEW_LIMIT + 1)
        if len(original) > FILE_PREVIEW_LIMIT or b'\x00' in original:
            return jsonify({'error': 'Datei nicht bearbeitbar.'}), 400
        original.decode('utf-8')
        if data.get('revision') != hashlib.sha256(original).hexdigest():
            return jsonify({'error': 'Die Datei wurde inzwischen geaendert. Bitte neu laden und Aenderungen abgleichen.'}), 409
        with tempfile.NamedTemporaryFile(dir=os.path.dirname(full), delete=False) as target:
            temporary = target.name
            target.write(encoded)
        os.chmod(temporary, os.stat(full).st_mode & 0o777)
        os.replace(temporary, full)
        temporary = None
    except UnicodeDecodeError:
        return jsonify({'error': 'Nur UTF-8-Textdateien koennen bearbeitet werden.'}), 400
    except OSError as error:
        return jsonify({'error': str(error)}), 500
    finally:
        if temporary:
            os.unlink(temporary)
    return jsonify({'ok': True, 'revision': hashlib.sha256(encoded).hexdigest()})


@app.route('/projects/upload', methods=['POST'])
def upload_project_file():
    """Laedt eine Datei in ein (optional angegebenes) Unterverzeichnis des
    aktiven Projekts hoch -- Pfad wird ueber _resolve_project_path
    abgesichert, Dateiname ueber secure_filename bereinigt."""
    target_dir = _resolve_project_path(request.form.get('dir', ''))
    if not target_dir or not os.path.isdir(target_dir):
        return jsonify({'ok': False, 'error': 'Ungueltiges Zielverzeichnis'}), 400
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'Keine Datei erhalten'}), 400
    upload = request.files['file']
    filename = secure_filename(upload.filename or '')
    if not filename:
        return jsonify({'ok': False, 'error': 'Ungueltiger Dateiname'}), 400
    dest = os.path.join(target_dir, filename)
    upload.save(dest)
    base = os.path.realpath(projekt_dir(CURRENT_PROJECT))
    rel = os.path.relpath(dest, base).replace(os.sep, '/')
    return jsonify({'ok': True, 'path': rel})


@app.route('/restart-vite', methods=['POST'])
def restart_vite():
    preview_phase('stopping', 'Wird beendet …')
    with PREVIEW_LOCK:
        PREVIEW_LOG.clear()
    old_processes = [proc for proc in (vite_process, backend_process) if proc is not None]
    try:
        stop_vite_processes()
        stop_backend_server()
        _kill_port(PORT_VITE)
        if not wait_preview_stopped(old_processes, PREVIEW_STOP_TIMEOUT):
            preview_phase('stopping', 'Prozesse reagieren nicht. Beenden wird erzwungen …')
            for proc in old_processes:
                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            for port in (PORT_VITE, BACKEND_PORT):
                _kill_port(port, signal.SIGKILL)
            if not wait_preview_stopped(old_processes, PREVIEW_STOP_TIMEOUT):
                raise RuntimeError('Alte Prozesse oder Ports sind weiterhin belegt. Neustart abgebrochen.')
        if _backend_manifest(projekt_dir(CURRENT_PROJECT)):
            preview_phase('backend', 'Backend startet …')
            start_backend_server()
            wait_preview_ready(backend_process, BACKEND_PORT, 'Backend')
        preview_phase('preview', 'Vorschau startet …')
        start_vite_server()
        wait_preview_ready(vite_process, PORT_VITE, 'Vorschau')
        if backend_process is not None and backend_process.poll() is not None:
            raise RuntimeError('Backend ist beim Start der Vorschau abgestuerzt.')
        preview_phase('ready', 'Bereit', running=False)
        return jsonify({'ok': True})
    except Exception as error:
        preview_phase('error', str(error), running=False)
        return jsonify({'ok': False, 'error': str(error)}), 500


def wait_preview_stopped(processes, timeout):
    deadline = time.monotonic() + timeout
    while True:
        processes_stopped = all(proc.poll() is not None for proc in processes)
        ports_free = not any(is_port_in_use(port) for port in (PORT_VITE, BACKEND_PORT))
        if processes_stopped and ports_free:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def preview_http_status(port):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
    try:
        connection.request('GET', '/')
        return connection.getresponse().status
    finally:
        connection.close()


def wait_preview_ready(proc, port, label):
    if proc is None:
        raise RuntimeError(f'{label} konnte nicht gestartet werden. Projektdateien und Startbefehl pruefen.')
    deadline = time.monotonic() + PREVIEW_START_TIMEOUT
    last = 'noch keine HTTP-Antwort'
    while True:
        if proc.poll() is not None:
            raise RuntimeError(f'{label} wurde beendet (Exit {proc.returncode}). Siehe Prozessausgabe.')
        try:
            status = preview_http_status(port)
            last = f'HTTP {status}'
            # API-only backends may legitimately have no route at /.
            if 200 <= status < 400 or status in (401, 403) or (label == 'Backend' and status == 404):
                if proc.poll() is None:
                    return
        except (OSError, http.client.HTTPException):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError(f'{label} auf Port {port} nach {PREVIEW_START_TIMEOUT} Sekunden nicht bereit ({last}).')
        time.sleep(0.2)


@app.route('/preview-status')
def preview_status():
    with PREVIEW_LOCK:
        return jsonify({**PREVIEW_STATE, 'log': ''.join(PREVIEW_LOG)})

@app.route('/reset', methods=['POST'])
def reset():
    reset_history()
    return "OK"


@app.route('/download-zip', methods=['GET'])
def download_zip():
    """Packt das aktive Projektverzeichnis in ein ZIP im Speicher und liefert es aus."""
    projekt = projekt_dir(CURRENT_PROJECT)
    if not os.path.isdir(projekt):
        return "Projektverzeichnis nicht gefunden", 404
    zip_buffer = io.BytesIO()
    excluded_dirs = {'node_modules', 'dist', '.git', '__pycache__'}

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for root, dirs, files in os.walk(projekt):
            # Ausgeschlossene Verzeichnisse entfernen (os.walk: Einträge in dirs überspringen)
            dirs[:] = [d for d in dirs if d not in excluded_dirs]
            for filename in files:
                if filename.endswith('.log'):
                    continue
                file_path = os.path.join(root, filename)
                # Relativen Pfad als Archivnamen verwenden
                arcname = os.path.relpath(file_path, projekt)
                zip_file.write(file_path, arcname)

    zip_buffer.seek(0)
    return send_file(
        zip_buffer,
        mimetype='application/zip',
        as_attachment=True,
        download_name='vibelove-projekt.zip'
    )


@app.route('/generate-container', methods=['POST'])
def generate_container():
    """Erzeugt Dockerfile(s)/.dockerignore (+ docker-compose.yml + nginx.conf
    bei Frontend UND Backend) fuer das AKTIVE Projekt und schreibt sie
    hinein -- dieselbe Erkennung wie die Live-Vorschau (_frontend_shape/
    _backend_manifest), damit Container und lokale Vorschau nicht
    auseinanderlaufen. Committet das Ergebnis als eigenen, benannten
    Sicherungspunkt (nicht den generischen 'Zwischenstand', damit die
    Historie erkennen laesst, WAS committet wurde)."""
    projekt = projekt_dir(CURRENT_PROJECT)
    if not os.path.isdir(projekt):
        return jsonify({'ok': False, 'error': 'Projektverzeichnis nicht gefunden'}), 404
    files, hinweis_oder_fehler = generate_container_files(projekt)
    if files is None:
        return jsonify({'ok': False, 'error': hinweis_oder_fehler}), 400
    for relpfad, inhalt in files.items():
        with open(os.path.join(projekt, relpfad), 'w', encoding='utf-8') as f:
            f.write(inhalt)
    try:
        if os.path.isdir(os.path.join(projekt, '.git')):
            subprocess.run(['git', 'add', '--'] + sorted(files), cwd=projekt,
                            capture_output=True, timeout=15)
            subprocess.run(
                ['git', 'commit', '-m', f'vibelove: Container-Setup generiert ({", ".join(sorted(files))})'],
                cwd=projekt, capture_output=True, timeout=15
            )
    except Exception as e:
        print(f'generate_container: Commit fehlgeschlagen: {e}')
    return jsonify({'ok': True, 'dateien': sorted(files), 'hinweis': hinweis_oder_fehler})


def cleanup():
    TERMINALS.close()
    stop_vite_server()
    stop_backend_server()

# Wir nutzen atexit für den sauberen Cleanup -- greift aber NUR bei einem
# normalen Prozessende (sys.exit(), Rueckkehr aus main, unbehandelte
# Exception). Ein externes 'kill <pid>' (SIGTERM) beendet den Prozess
# OHNE atexit-Handler auszufuehren -- der per start_new_session=True bewusst
# vom Server-Prozess entkoppelte Vite-Kindprozess blieb dadurch bei jedem
# per SIGTERM beendeten Server-Neustart als Zombie zurueck (real beobachtet:
# 10 verwaiste Vite-Prozesse nach mehreren Testneustarts waehrend der
# Entwicklung). Deshalb zusaetzlich ein expliziter Signal-Handler.
atexit.register(cleanup)


def _beende_sauber(signum, frame):
    cleanup()
    sys.exit(0)


signal.signal(signal.SIGTERM, _beende_sauber)
signal.signal(signal.SIGINT, _beende_sauber)

if __name__ == '__main__':
    # Beim Start von server.py: Einstellungen laden, dann Vite starten
    load_settings()
    # Gespeichertes aktives Projekt anwenden (fehlte: laden ohne anwenden)
    _gespeichert = re.sub(r'[^a-zA-Z0-9_-]', '', str(MC_SETTINGS.get('projekt', '')))
    if _gespeichert and (_gespeichert == 'workspace'
                         or os.path.isdir(os.path.join(PROJEKTE_ROOT, _gespeichert))):
        CURRENT_PROJECT = _gespeichert
        print(f"[projekt] Aktives Projekt wiederhergestellt: {CURRENT_PROJECT}")
    apply_project_profile()
    save_settings()
    start_backend_server()
    start_vite_server()
    # Falls der Server schon läuft, nichts tun (wird durch is_port_in_use geprüft)
    
    # Flask starten
    app.run(host="0.0.0.0", port=PORT_VIBELOVE, debug=False)
