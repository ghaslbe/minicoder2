import io
import json
import os
import re
import subprocess
import sys
import time
import atexit
import signal
import socket
import select
import zipfile
from flask import Flask, render_template, request, jsonify, send_file

app = Flask(__name__)

# Konfiguration
PORT_VIBELOVE = 5050
PORT_VITE = 5173
WORKSPACE_DIR = os.path.join(os.getcwd(), 'workspace')
PROJEKTE_ROOT = os.path.join(os.getcwd(), 'projekte')
CURRENT_PROJECT = 'workspace'
# mc.py liegt eine Ebene hoeher
MC_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'mc.py'))

# ── Laufzeit-Einstellungen (konfigurierbar über /settings) ────────────────
SETTINGS_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mc_settings.json')

DEFAULT_MODEL = 'gemma-4-26b-a4b-it@mxfp4'
DEFAULT_BASE_URL = 'http://localhost:1234/v1'

MC_SETTINGS = {
    'model': DEFAULT_MODEL,
    'base_url': DEFAULT_BASE_URL,
    'api_key': ''
}

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
            for key in ('model', 'base_url', 'api_key'):
                if key in saved and saved[key]:
                    MC_SETTINGS[key] = saved[key]
        print(f"[settings] mc_settings.json geladen: model={MC_SETTINGS['model']}, "
              f"base_url={MC_SETTINGS['base_url']}, "
              f"api_key={'gesetzt' if MC_SETTINGS['api_key'] else '(leer)'}")
    except FileNotFoundError:
        print("[settings] Keine mc_settings.json vorhanden – nutze Umgebungsvariablen/Defaults.")

# Globaler Prozess-Speicher für den Vite-Server
vite_process = None

# Chat-Verlauf
BUILD_HISTORY = []

def reset_history():
    global BUILD_HISTORY
    BUILD_HISTORY = []

def projekt_dir(name):
    """Bereinigt den Projektnamen und liefert den zugehörigen Verzeichnispfad."""
    name = re.sub(r'[^a-zA-Z0-9_-]', '', str(name))
    if name == 'workspace':
        return WORKSPACE_DIR
    return os.path.join(PROJEKTE_ROOT, name)

def stop_vite_processes():
    """Beendet alle laufenden Vite-Prozesse dieses Projekts (pkill + gemerktes Handle)."""
    global vite_process
    try:
        subprocess.run(['pkill', '-f', 'node_modules/.bin/vite'], capture_output=True)
    except Exception as e:
        print(f'pkill vite: {e}')
    if vite_process:
        try:
            os.killpg(os.getpgid(vite_process.pid), signal.SIGTERM)
        except Exception as e:
            print(f'Fehler beim Stoppen des gemerkten Vite-Prozesses: {e}')
        vite_process = None

def switch_project(name):
    """Wechselt das aktive Projekt, setzt die Historie zurück und startet Vite neu."""
    global CURRENT_PROJECT
    cleaned = re.sub(r'[^a-zA-Z0-9_-]', '', str(name))
    if not cleaned:
        raise ValueError('Ungültiger Projektname')
    CURRENT_PROJECT = cleaned
    reset_history()
    stop_vite_processes()
    # Kurz warten bis der Vite-Port freigegeben wurde (max ~5s)
    for _ in range(50):
        if not is_port_in_use(PORT_VITE):
            break
        time.sleep(0.1)
    start_vite_server()

def extract_urls(text, max_urls=3):
    """Finde http(s)-URLs in einem Text und gib die ersten max_urls zurück."""
    pattern = r'https?://\S+'
    matches = re.findall(pattern, text)
    return matches[:max_urls]

def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

def start_vite_server():
    global vite_process
    if is_port_in_use(PORT_VITE):
        return
    
    # Verzeichnis des AKTIVEN Projekts nutzen
    proj = projekt_dir(CURRENT_PROJECT)
    front_dir = os.path.join(proj, 'frontend')
    if not os.path.isfile(os.path.join(front_dir, 'package.json')):
        print(f"[vite] Kein frontend/package.json in '{CURRENT_PROJECT}' – Vite wird nicht gestartet.")
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
    """Liefert Modell, Basis-URL und ob ein API-Key gesetzt ist – NIE den Key selbst."""
    return jsonify({
        'model': MC_SETTINGS.get('model', DEFAULT_MODEL),
        'base_url': MC_SETTINGS.get('base_url', DEFAULT_BASE_URL),
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

    # Leerer API-Key lasst den bestehenden unverändert
    if api_key is not None and api_key != '':
        MC_SETTINGS['api_key'] = str(api_key)

    # Persistieren – der Key wird gespeichert (nur lokal, nicht über GET ausgeliefert)
    try:
        with open(SETTINGS_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump({
                'model': MC_SETTINGS['model'],
                'base_url': MC_SETTINGS['base_url'],
                'api_key': MC_SETTINGS['api_key']
            }, f, indent=2, ensure_ascii=False)
    except Exception as e:
        return jsonify({'ok': False, 'error': f'Speichern fehlgeschlagen: {e}'}), 500

    print(f"[settings] Gespeichert: model={MC_SETTINGS['model']}, "
          f"base_url={MC_SETTINGS['base_url']}, "
          f"api_key={'gesetzt' if MC_SETTINGS['api_key'] else '(leer)'}")
    return jsonify({'ok': True})

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/build', methods=['POST'])
def build():
    instruction = request.form.get('instruction', '')
    if not instruction:
        return "Keine Anweisung erhalten."

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
    suffix = "\n\nStarte KEINEN dauerhaften Dev-Server im Hintergrund. Pruefe Frontend-Aenderungen ausschliesslich per 'npm run build' (muss exit 0 liefern). Falls du einen Server kurz zum Testen per curl brauchst, starte ihn, teste, und beende ihn danach wieder (kill), bevor du finish aufrufst."
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

    # Befehl zusammenbauen
    command = [
        "python3", "-u", 
        MC_PATH,
        "--dir", aktives_projekt_dir,
        "--yes",
        "--check",
        "--max-steps", "100",
        "--base-url", base_url,
        "--model", model,
        full_instruction
    ]

    try:
        from flask import stream_with_context, Response

        def generate():
            nonlocal output
            output_lines = []
            env = os.environ.copy()
            if MC_SETTINGS.get('api_key'):
                env['MC_API_KEY'] = MC_SETTINGS['api_key']
            proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env
            )

            start_time = time.time()
            timeout_duration = 900

            try:
                while True:
                    if time.time() - start_time > timeout_duration:
                        proc.terminate()
                        yield "\nFehler: Bauprozess hat das Timeout von 900 Sekunden überschritten.\n"
                        break
                    
                    # Prüfe mit select, ob Daten verfügbar sind, um Blockieren zu vermeiden
                    ready, _, _ = select.select([proc.stdout], [], [], 1.0)
                    if ready:
                        line = proc.stdout.readline()
                        if line:
                            output_lines.append(line)
                            yield line
                        elif proc.poll() is not None:
                            remaining = proc.stdout.read()
                            if remaining:
                                output_lines.append(remaining)
                            break
                    elif proc.poll() is not None:
                        # Falls kein Input bereit ist, aber der Prozess beendet wurde
                        remaining = proc.stdout.read()
                        if remaining:
                            output_lines.append(remaining)
                        break
                    else:
                        # Falls kein Input bereit ist und Prozess noch läuft, kurz warten
                        time.sleep(0.1)
            except Exception as e:
                yield f"\nFehler während des Prozesses: {str(e)}"
            finally:
                if proc.poll() is None:
                    proc.terminate()
                
                ensure_vite_running()
                full_output = "".join(output_lines)
                output = full_output
                summary = full_output[-500:] if len(full_output) > 500 else full_output
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


@app.route('/restart-vite', methods=['POST'])
def restart_vite():
    global vite_process
    # Alle laufenden Vite-Prozesse dieses Projekts beenden
    try:
        subprocess.run(['pkill', '-f', 'node_modules/.bin/vite'], capture_output=True)
    except Exception as e:
        print(f'pkill vite: {e}')
    # Auch das gemerkte Handle terminieren, falls vorhanden
    if vite_process:
        try:
            os.killpg(os.getpgid(vite_process.pid), signal.SIGTERM)
        except Exception as e:
            print(f'Fehler beim Stoppen des gemerkten Vite-Prozesses: {e}')
        vite_process = None
    # Kurz warten, bis Port 5173 frei ist (max ~5s)
    for _ in range(50):
        if not is_port_in_use(PORT_VITE):
            break
        time.sleep(0.1)
    # Vite neu starten
    start_vite_server()
    return 'Vite-Server wurde neu gestartet.'

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

# Wir nutzen atexit für den sauberen Cleanup
atexit.register(cleanup)

if __name__ == '__main__':
    # Beim Start von server.py: Einstellungen laden, dann Vite starten
    load_settings()
    start_vite_server()
    # Falls der Server schon läuft, nichts tun (wird durch is_port_in_use geprüft)
    
    # Flask starten
    app.run(port=PORT_VIBELOVE, debug=False)
