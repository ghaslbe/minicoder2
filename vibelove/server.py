import io
import json
import os
from collections import deque
import re
import subprocess
import sys
import time
import atexit
import signal
import socket
import select
import zipfile
from collections import deque
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename

# po.py liegt eine Ebene hoeher, direkt neben mc.py.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import po

app = Flask(__name__)
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
    'max_steps': 100
}

def save_settings():
    """Speichert alle Laufzeit-Einstellungen einschließlich des aktiven Projekts."""
    with open(SETTINGS_FILE_PATH, 'w', encoding='utf-8') as f:
        json.dump(MC_SETTINGS, f, indent=2, ensure_ascii=False)

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
            for key in ('model', 'base_url', 'api_key', 'projekt'):
                if key in saved and saved[key]:
                    MC_SETTINGS[key] = saved[key]
            try:
                saved_max_steps = int(saved.get('max_steps', MC_SETTINGS['max_steps']))
                if saved_max_steps >= 1:
                    MC_SETTINGS['max_steps'] = saved_max_steps
            except (TypeError, ValueError):
                pass
        print(f"[settings] mc_settings.json geladen: model={MC_SETTINGS['model']}, "
              f"base_url={MC_SETTINGS['base_url']}, "
              f"api_key={'gesetzt' if MC_SETTINGS['api_key'] else '(leer)'}")
    except FileNotFoundError:
        print("[settings] Keine mc_settings.json vorhanden – nutze Umgebungsvariablen/Defaults.")

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

def _kill_port(port):
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
        out = subprocess.run(['lsof', '-ti', f':{port}'], capture_output=True, text=True)
        for pid in out.stdout.split():
            try:
                os.kill(int(pid), signal.SIGTERM)
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
        backend_process = subprocess.Popen(
            ["env", f"{BACKEND_MARKER}=1", "bash", "-c", command],
            cwd=backend_dir,
            start_new_session=True
        )
    except Exception as e:
        print(f"Fehler beim Starten des Backend-Servers: {e}")

def ensure_backend_running():
    if not is_port_in_use(BACKEND_PORT):
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
                    vite_process = subprocess.Popen(
                        ["env", f"{STATIC_SERVER_MARKER}=1", "python3",
                         STATIC_PREVIEW_SCRIPT, static_dir, str(PORT_VITE),
                         str(backend_port), API_PREFIX],
                        start_new_session=True
                    )
                except Exception as e:
                    print(f"Fehler beim Starten des Static-Servers: {e}")
                return
            print(f"[vite] Kein package.json und keine index.html in '{CURRENT_PROJECT}' (frontend/ oder Wurzel) – keine Vorschau moeglich.")
            return

    print(f"Starte Vite-Server auf Port {PORT_VITE}...")
    try:
        vite_process = subprocess.Popen(
            ["npm", "run", "dev", "--", "--port", str(PORT_VITE), "--", "--strict", "--", "--host"],
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
    if not is_port_in_use(5173):
        start_vite_server()

@app.route('/settings', methods=['GET'])
def get_settings():
    """Liefert Modell, Basis-URL, Schrittlimit und ob ein API-Key gesetzt ist – NIE den Key selbst."""
    return jsonify({
        'model': MC_SETTINGS.get('model', DEFAULT_MODEL),
        'base_url': MC_SETTINGS.get('base_url', DEFAULT_BASE_URL),
        'max_steps': MC_SETTINGS.get('max_steps', 100),
        'api_key_gesetzt': bool(MC_SETTINGS.get('api_key'))
    })

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

    # Persistieren – der Key wird gespeichert (nur lokal, nicht über GET ausgeliefert)
    try:
        save_settings()
    except Exception as e:
        return jsonify({'ok': False, 'error': f'Speichern fehlgeschlagen: {e}'}), 500

    print(f"[settings] Gespeichert: model={MC_SETTINGS['model']}, "
          f"base_url={MC_SETTINGS['base_url']}, "
          f"max_steps={MC_SETTINGS['max_steps']}, "
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


def _po_project_context():
    """Kurzer Kontext-Text ueber das aktive Projekt fuer po.py -- die eigentliche
    Sammel-Logik lebt in po.gather_project_context() (auch von po.py's eigener
    Kommandozeile genutzt), hier nur mit dem aktiven vibelove-Projektnamen
    vorangestellt statt eines nackten Pfades."""
    return (f"Aktives Projekt: {CURRENT_PROJECT}\n\n"
            + po.gather_project_context(projekt_dir(CURRENT_PROJECT)))


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
    global PO_HISTORY
    context_text = _po_project_context()
    decision, PO_HISTORY = po.refine(
        message, context_text, PO_HISTORY,
        MC_SETTINGS['base_url'], MC_SETTINGS['model'], MC_SETTINGS['api_key'])
    return jsonify(decision)


@app.route('/refine/reset', methods=['POST'])
def refine_reset():
    global PO_HISTORY
    PO_HISTORY = []
    return jsonify({'ok': True})


@app.route('/build', methods=['POST'])
def build():
    instruction = request.form.get('instruction', '')
    if not instruction:
        return "Keine Anweisung erhalten."
    global PO_HISTORY
    PO_HISTORY = []  # der Produktdialog fuer DIESE Aufgabe ist mit dem Bauauftrag abgeschlossen

    # Kontext-Text zusammenbauen aus BUILD_HISTORY (letzte 5 Einträge)
    context_parts = []
    if BUILD_HISTORY:
        context_parts.append("Bisherige Bauschritte in dieser Sitzung (chronologisch, ggf. darauf aufbauen):")
        for i, entry in enumerate(BUILD_HISTORY[-5:]):
            context_parts.append(f"{i+1}. Anweisung: {entry['instruction']}")
            context_parts.append(f"   Ergebnis: {entry['result_summary']}")
    
    if context_parts:
        full_instruction = "\n".join(context_parts) + f"\n\nNEUE Anweisung: {instruction}"
    else:
        full_instruction = instruction

    # Der geforderte Zusatztext
    suffix = "\n\nLege ein NEUES Projektgeruest (npm create ...) IMMER in einen Unterordner wie frontend/ an, nie direkt ins Wurzelverzeichnis (dort liegt Git-Zubehoer, der Generator wuerde interaktiv haengen). Starte KEINEN dauerhaften Dev-Server im Hintergrund. Pruefe Frontend-Aenderungen ausschliesslich per 'npm run build' (muss exit 0 liefern). Falls du einen Server kurz zum Testen per curl brauchst, starte ihn, teste, und beende ihn danach wieder (kill), bevor du finish aufrufst."
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

    # Bauziel: das aktive Projekt (Verzeichnis sicherstellen)
    aktives_projekt_dir = projekt_dir(CURRENT_PROJECT)
    os.makedirs(aktives_projekt_dir, exist_ok=True)
    # Liegen gebliebene Aenderungen (z.B. von einem abgebrochenen Lauf) VOR
    # dem neuen Bauauftrag sichern -- sonst faellt mc.pys eigene Git-
    # Absicherung fuer den GESAMTEN naechsten Lauf aus (siehe Docstring).
    stelle_sauberen_arbeitsbaum_sicher(aktives_projekt_dir)

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

    try:
        from flask import stream_with_context, Response

        def generate():
            nonlocal output
            output_lines = []
            BUILD_STATUS['laeuft'] = True
            BUILD_STATUS['zeilen'].clear()

            def emit(text):
                output_lines.append(text)
                add_build_lines(text)
                return text

            env = os.environ.copy()
            if MC_SETTINGS.get('api_key'):
                env['MC_API_KEY'] = MC_SETTINGS['api_key']
            proc = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=env
            )
            start_time = time.time()
            timeout_duration = 900
            try:
                while True:
                    if time.time() - start_time > timeout_duration:
                        proc.terminate()
                        yield emit("\nFehler: Bauprozess hat das Timeout von 900 Sekunden überschritten.\n")
                        break
                    ready, _, _ = select.select([proc.stdout], [], [], 1.0)
                    if ready:
                        line = proc.stdout.readline()
                        if line:
                            yield emit(line)
                        elif proc.poll() is not None:
                            remaining = proc.stdout.read()
                            if remaining:
                                yield emit(remaining)
                            break
                    elif proc.poll() is not None:
                        remaining = proc.stdout.read()
                        if remaining:
                            yield emit(remaining)
                        break
                    else:
                        time.sleep(0.1)
            except Exception as e:
                yield emit(f"\nFehler während des Prozesses: {str(e)}")
            finally:
                if proc.poll() is None:
                    proc.terminate()
                BUILD_STATUS['laeuft'] = False
                ensure_backend_running()
                ensure_vite_running()
                full_output = "".join(output_lines)
                output = full_output
                summary = _extract_run_summary(full_output)
                BUILD_HISTORY.append({"instruction": instruction, "result_summary": summary})
            yield ""

        output = ""
        return Response(stream_with_context(generate()), mimetype='text/plain')

    except Exception as e:
        output = str(e)
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
        result = subprocess.run(
            ['git', 'log', '--reverse', '--pretty=format:%H%x1f%P%x1f%ci%x1f%s',
             '-n', '100'],
            cwd=project_path, capture_output=True, text=True, timeout=15
        )
    except Exception as e:
        return jsonify({'commits': [], 'error': str(e)})
    if result.returncode != 0:
        return jsonify({'commits': []})
    commits = []
    for line in result.stdout.splitlines():
        teile = line.split('\x1f')
        if len(teile) != 4:
            continue
        commit_hash, eltern, datum, nachricht = teile
        commits.append({
            'hash': commit_hash,
            'parent': eltern.split(' ')[0] if eltern else None,
            'date': datum,
            'message': nachricht,
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
        return jsonify({'path': relpath, 'size': size, 'binary': False,
                         'truncated': truncated, 'content': content})
    except UnicodeDecodeError:
        return jsonify({'path': relpath, 'size': size, 'binary': True,
                         'truncated': truncated})


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
    stop_vite_processes()
    stop_backend_server()
    # Kurz warten, bis Vite-/Backend-Port frei sind (max ~5s)
    for _ in range(50):
        if not is_port_in_use(PORT_VITE) and not is_port_in_use(BACKEND_PORT):
            break
        time.sleep(0.1)
    # Backend + Vite neu starten
    start_backend_server()
    start_vite_server()
    return 'Vorschau (Vite/Backend) wurde neu gestartet.'

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


def cleanup():
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
    start_backend_server()
    start_vite_server()
    # Falls der Server schon läuft, nichts tun (wird durch is_port_in_use geprüft)
    
    # Flask starten
    app.run(port=PORT_VIBELOVE, debug=False)
