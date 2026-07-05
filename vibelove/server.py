import os
import subprocess
import sys
import time
import atexit
import signal
import socket
from flask import Flask, render_template, request

app = Flask(__name__)

# Konfiguration
PORT_VIBELOVE = 5050
PORT_VITE = 5173
WORKSPACE_DIR = os.path.join(os.getcwd(), 'workspace')
# mc.py liegt eine Ebene hoeher
MC_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'mc.py'))

# Globaler Prozess-Speicher für den Vite-Server
vite_process = None

# Chat-Verlauf
BUILD_HISTORY = []

def reset_history():
    global BUILD_HISTORY
    BUILD_HISTORY = []

def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

def start_vite_server():
    global vite_process
    if is_port_in_use(PORT_VITE):
        return
    
    print(f"Starte Vite-Server auf Port {PORT_VITE}...")
    try:
        # Wir starten den Vite-Server im Verzeichnis workspace/frontend
        vite_dir = os.path.join(WORKSPACE_DIR, 'frontend')
        # Nutze start_new_session=True, damit der Prozess unabhängig bleibt
        vite_process = subprocess.Popen(
            ["npm", "run", "dev", "--", "--port", str(PORT_VITE), "--", "--strict", "--", "--host"],
            cwd=vite_dir,
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
    if not is_port_in_use(PORT_VITE):
        start_vite_server()

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

    print(f"Starte Bauprozess für: {instruction[:50]}...")
    
    # Umgebungsvariablen auslesen
    base_url = os.environ.get('VIBELOVE_BASE_URL', 'http://localhost:1234/v1')
    model = os.environ.get('VIBELOVE_MODEL', 'gemma-4-26b-a4b-it@mxfp4')

    # Befehl zusammenbauen
    command = [
        "python3", 
        MC_PATH,
        "--dir", WORKSPACE_DIR,
        "--yes",
        "--check",
        "--max-steps", "60",
        "--base-url", base_url,
        "--model", model,
        full_instruction
    ]

    try:
        # Ausführung von mc.py mit Timeout von 900s
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=900
        )
        # Kombinierte stdout/stderr Ausgabe
        output = result.stdout + "\n" + result.stderr
        
        # Nach dem Bauen sicherstellen, dass der Vite-Server wieder läuft (falls er durch mc.py beendet wurde)
        ensure_vite_running()

        # Verlauf speichern
        summary = output[-500:] if len(output) > 500 else output
        BUILD_HISTORY.append({"instruction": instruction, "result_summary": summary})
        
        return output
    except subprocess.TimeoutExpired:
        error_msg = "Fehler: Bauprozess hat das Timeout von 900 Sekunden überschritten."
        BUILD_HISTORY.append({"instruction": instruction, "result_summary": error_msg})
        return error_msg
    except Exception as e:
        error_msg = f"Fehler beim Ausführen von mc.py: {str(e)}"
        BUILD_HISTORY.append({"instruction": instruction, "result_summary": error_msg})
        return error_msg

@app.route('/reset', methods=['POST'])
def reset():
    reset_history()
    return "OK"


def cleanup():
    stop_vite_server()

# Wir nutzen atexit für den sauberen Cleanup
atexit.register(cleanup)

if __name__ == '__main__':
    # Beim Start von server.py: Vite starten
    start_vite_server()
    # Falls der Server schon läuft, nichts tun (wird durch is_port_in_use geprüft)
    
    # Flask starten
    app.run(port=PORT_VIBELOVE, debug=False)
