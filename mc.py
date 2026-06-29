#!/usr/bin/env python3
"""
mc - Mini Coding Tool
=====================

Ein kleines agentisches Coding-Tool fuer OpenAI-kompatible Ollama-Schnittstellen.

Manche Endpoints unterstuetzen kein natives OpenAI-Tool-Calling (das `tools`-Feld
liefert dann HTTP 400). Deshalb nutzt dieses Tool ein text-basiertes Action-Protokoll
und funktioniert auch ohne Function-Calling: das Modell
gibt JSON-Action-Bloecke aus, die hier geparst, ausgefuehrt und zurueckgespeist werden.

Faehigkeiten des Agenten:
  - read_file   Datei lesen
  - write_file  Datei schreiben/anlegen   (Bestaetigung noetig)
  - list_dir    Verzeichnis auflisten
  - run         Shell-Kommando ausfuehren (Bestaetigung noetig)
  - finish      Aufgabe abschliessen

Benutzung:
  python3 mc.py                      # interaktiver Chat
  python3 mc.py "schreib fizzbuzz.py"  # einmalige Aufgabe
  python3 mc.py --model gpt-oss:20b
  python3 mc.py --yes                # alle Aktionen ohne Rueckfrage (Vorsicht!)

Env-Variablen:
  MC_BASE_URL  (default http://localhost:11434/v1 — lokales Ollama)
  MC_MODEL     (default qwen3-coder:30b)
  MC_API_KEY   (optional, falls der Endpoint einen Key verlangt)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import http.client
import ssl
import urllib.request
import urllib.error

BASE_URL = os.environ.get("MC_BASE_URL", "http://localhost:11434/v1").rstrip("/")
DEFAULT_MODEL = os.environ.get("MC_MODEL", "qwen3-coder:30b")
API_KEY = os.environ.get("MC_API_KEY", "")

# Netzwerk: in Firmenumgebungen (z.B. Zscaler) muss der Traffic durch einen Proxy,
# und das TLS wird oft mit einem eigenen CA-Zertifikat aufgebrochen.
PROXY = os.environ.get("MC_PROXY", "")              # z.B. http://proxy:8080
CA_BUNDLE = os.environ.get("MC_CA_BUNDLE", "")       # Pfad zur Zscaler-CA (.pem)
INSECURE = False                                     # TLS-Pruefung abschalten (Notnagel)
VERBOSE = os.environ.get("MC_VERBOSE", "") not in ("", "0", "false")  # passive Logausgaben

MAX_STEPS = 25          # Sicherheitslimit pro Aufgabe
MAX_OUTPUT_CHARS = 8000  # Trunkierung von Tool-Ausgaben an das Modell


# ----------------------------- Farben / UI ---------------------------------

class C:
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    BLUE = "\033[34m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    CYAN = "\033[36m"

    @classmethod
    def disable(cls):
        for k in dir(cls):
            if k.isupper():
                setattr(cls, k, "")


if not sys.stdout.isatty():
    C.disable()


def info(msg):
    print(f"{C.DIM}{msg}{C.RESET}")


def banner(msg):
    print(f"{C.CYAN}{C.BOLD}{msg}{C.RESET}")


def log(msg):
    """Passive Statuszeile, nur im Verbose-Modus (z.B. fuers Proxy-Debugging)."""
    if VERBOSE:
        print(f"{C.DIM}· {msg}{C.RESET}")


# --------------------------- HTTP / API-Aufruf -----------------------------

def build_opener():
    """Baut einen urllib-Opener mit Proxy- und TLS-Einstellungen.

    - MC_PROXY / --proxy : erzwingt einen HTTP(S)-Proxy (sonst HTTP(S)_PROXY aus env).
    - MC_CA_BUNDLE / --ca-bundle : eigenes CA-Zertifikat (z.B. Zscaler-Root).
    - --insecure : TLS-Pruefung komplett aus (nur als Notnagel).
    """
    handlers = []

    if PROXY:
        # Passwort im Log maskieren.
        shown = re.sub(r"//[^@/]*@", "//***@", PROXY)
        log(f"nutze Proxy {shown}")
        handlers.append(urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
    # ohne explizite Angabe nutzt urllib automatisch HTTP_PROXY/HTTPS_PROXY aus env.

    if INSECURE:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    elif CA_BUNDLE:
        ctx = ssl.create_default_context(cafile=CA_BUNDLE)
    else:
        ctx = ssl.create_default_context()
    handlers.append(urllib.request.HTTPSHandler(context=ctx))

    return urllib.request.build_opener(*handlers)


def net_error(reason):
    """Erzeugt eine verstaendliche Fehlermeldung inkl. Hinweisen fuer
    Firmenumgebungen wie Zscaler."""
    txt = str(reason) or reason.__class__.__name__
    low = txt.lower()
    msg = f"\n{C.RED}Verbindungsfehler:{C.RESET} {txt}"
    if "getaddrinfo" in txt or "Name or service" in txt or "nodename" in txt:
        msg += (f"\n{C.YELLOW}DNS-Aufloesung fehlgeschlagen — typisch hinter Zscaler/Firmenproxy."
                f"\nSetze einen Proxy, z.B.:{C.RESET}\n"
                f"  export HTTPS_PROXY=http://dein-proxy:8080   (oder --proxy ...)\n"
                f"  python3 mc.py --proxy http://dein-proxy:8080 --list-models")
    elif any(k in low for k in ("closed connection", "remotedisconnected", "reset",
                                "broken pipe", "refused", "bad gateway", "502")):
        msg += (f"\n{C.YELLOW}Der Proxy hat die Verbindung abgewiesen/geschlossen. Wahrscheinlich:{C.RESET}\n"
                f"  1. Proxy braucht Login -> Zugangsdaten in die URL:\n"
                f"     python3 mc.py --proxy http://USER:PASS@proxy:8080 ...\n"
                f"  2. Falscher Proxy-Host/-Port -> echten Proxy pruefen:\n"
                f"     echo $HTTPS_PROXY   bzw. System-/Browser-Proxyeinstellungen\n"
                f"  3. Direkt mit curl testen:\n"
                f"     curl -v -x http://proxy:8080 {BASE_URL}/models")
    elif "407" in txt or "authentication" in low:
        msg += (f"\n{C.YELLOW}Proxy verlangt Authentifizierung (407). Zugangsdaten mitgeben:{C.RESET}\n"
                f"  python3 mc.py --proxy http://USER:PASS@proxy:8080 ...")
    elif "certificate_verify_failed" in low or "certificate" in low:
        msg += (f"\n{C.YELLOW}TLS-Zertifikat nicht vertrauenswuerdig — Zscaler bricht HTTPS auf."
                f"\nGib die Firmen-CA an oder umgehe die Pruefung:{C.RESET}\n"
                f"  python3 mc.py --ca-bundle /pfad/zur/zscaler-root.pem ...\n"
                f"  python3 mc.py --insecure ...   (nur als Notnagel)")
    return msg


# Netzwerkfehler, die nicht alle URLError sind (RemoteDisconnected ist OSError).
NET_ERRORS = (urllib.error.URLError, http.client.HTTPException, OSError)


def chat_stream(messages, model):
    """Ruft /chat/completions im Streaming-Modus auf und gibt den vollen Text
    zurueck (waehrend des Empfangs wird live ausgegeben)."""
    url = f"{BASE_URL}/chat/completions"
    payload = {"model": model, "messages": messages, "stream": True}
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    parts = []
    first = True
    try:
        log(f"verbinde mit {url} …")
        with build_opener().open(req, timeout=300) as resp:
            log(f"verbunden (HTTP {resp.status}), frage Modell '{model}', warte auf Antwort …")
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[len("data:"):].strip()
                if chunk == "[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                delta = obj.get("choices", [{}])[0].get("delta", {})
                token = delta.get("content")
                if token:
                    if first:
                        log("Antwort beginnt …")
                        first = False
                    parts.append(token)
                    sys.stdout.write(f"{C.DIM}{token}{C.RESET}")
                    sys.stdout.flush()
        log(f"Antwort vollstaendig ({len(''.join(parts))} Zeichen).")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"\n{C.RED}HTTP {e.code} vom Endpoint:{C.RESET} {body}")
    except NET_ERRORS as e:
        raise SystemExit(net_error(getattr(e, "reason", e)))
    print()
    return "".join(parts)


def list_models():
    """Holt /models vom Endpoint und gibt die IDs als Liste zurueck."""
    url = f"{BASE_URL}/models"
    headers = {}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        log(f"verbinde mit {url} …")
        with build_opener().open(req, timeout=30) as resp:
            log(f"verbunden (HTTP {resp.status}), lese Modell-Liste …")
            obj = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{C.RED}HTTP {e.code} beim Abruf der Modelle.{C.RESET}")
    except NET_ERRORS as e:
        raise SystemExit(net_error(getattr(e, "reason", e)))
    return sorted(m.get("id", "?") for m in obj.get("data", []))


# --------------------------- Action-Parsing --------------------------------

# Das Modell soll Aktionen als JSON in einem ```action ... ``` Block ausgeben.
ACTION_RE = re.compile(r"```action\s*(.*?)```", re.DOTALL)


def extract_action(text):
    """Findet den ersten ```action```-Block und parst das JSON daraus."""
    m = ACTION_RE.search(text)
    if not m:
        return None, None
    raw = m.group(1).strip()
    try:
        return json.loads(raw), raw
    except json.JSONDecodeError as e:
        return {"_parse_error": str(e), "_raw": raw}, raw


# --------------------------- Tool-Ausfuehrung ------------------------------

def truncate(s):
    if len(s) > MAX_OUTPUT_CHARS:
        return s[:MAX_OUTPUT_CHARS] + f"\n...[gekuerzt, {len(s) - MAX_OUTPUT_CHARS} Zeichen ausgelassen]"
    return s


def confirm(prompt):
    if AUTO_YES:
        print(f"{C.DIM}(auto-yes){C.RESET}")
        return True
    try:
        ans = input(f"{C.YELLOW}{prompt} [y/N] {C.RESET}").strip().lower()
    except EOFError:
        return False
    return ans in ("y", "yes", "j", "ja")


def do_read_file(args):
    path = args.get("path", "")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return True, f"Inhalt von {path} ({len(content)} Zeichen):\n{truncate(content)}"
    except Exception as e:
        return False, f"FEHLER beim Lesen von {path}: {e}"


def do_write_file(args):
    path = args.get("path", "")
    content = args.get("content", "")
    print(f"{C.YELLOW}» write_file{C.RESET} {C.BOLD}{path}{C.RESET} ({len(content)} Zeichen)")
    preview = content if len(content) < 600 else content[:600] + "\n..."
    print(f"{C.DIM}{preview}{C.RESET}")
    if not confirm(f"Datei '{path}' schreiben?"):
        return False, "Abgelehnt durch den Benutzer."
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return True, f"OK, {len(content)} Zeichen nach {path} geschrieben."
    except Exception as e:
        return False, f"FEHLER beim Schreiben von {path}: {e}"


def do_list_dir(args):
    path = args.get("path", ".")
    try:
        entries = []
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            tag = "/" if os.path.isdir(full) else ""
            entries.append(name + tag)
        return True, f"Inhalt von {path}:\n" + "\n".join(entries)
    except Exception as e:
        return False, f"FEHLER beim Auflisten von {path}: {e}"


def do_run(args):
    cmd = args.get("command", "")
    print(f"{C.YELLOW}» run{C.RESET} {C.BOLD}{cmd}{C.RESET}")
    if not confirm("Kommando ausfuehren?"):
        return False, "Abgelehnt durch den Benutzer."
    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=120
        )
        out = proc.stdout + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        out = out.strip() or "(keine Ausgabe)"
        return True, f"exit={proc.returncode}\n{truncate(out)}"
    except subprocess.TimeoutExpired:
        return False, "FEHLER: Kommando-Timeout (120s)."
    except Exception as e:
        return False, f"FEHLER bei Ausfuehrung: {e}"


DISPATCH = {
    "read_file": do_read_file,
    "write_file": do_write_file,
    "list_dir": do_list_dir,
    "run": do_run,
}


# ------------------------------ System-Prompt ------------------------------

SYSTEM_PROMPT = """Du bist ein praeziser Coding-Agent, der in einer Shell-Umgebung arbeitet.
Du kannst NICHT direkt auf Dateien zugreifen. Stattdessen forderst du EINE Aktion pro
Antwort an, indem du genau EINEN ```action``` Block mit JSON ausgibst. Du erhaeltst dann
das Ergebnis und faehrst fort.

Verfuegbare Aktionen (Feld "action"):
  read_file   -> {"action":"read_file","path":"<pfad>"}
  write_file  -> {"action":"write_file","path":"<pfad>","content":"<voller dateiinhalt>"}
  list_dir    -> {"action":"list_dir","path":"<pfad>"}
  run         -> {"action":"run","command":"<shell-kommando>"}
  finish      -> {"action":"finish","summary":"<kurze zusammenfassung>"}

Regeln:
- Pro Antwort GENAU EIN action-Block. Davor darfst du kurz dein Vorgehen erklaeren.
- JSON muss valide sein. Bei write_file ist "content" der KOMPLETTE neue Dateiinhalt.
- Arbeite in kleinen Schritten. Lies bestehende Dateien bevor du sie aenderst.
- Wenn die Aufgabe erledigt ist, gib eine finish-Aktion aus.
- Schreibe sauberen, lauffaehigen Code. Halte dich an vorhandene Konventionen.

Beispiel-Antwort:
Ich lege die Datei an.
```action
{"action":"write_file","path":"hello.py","content":"print('hello')\\n"}
```"""


# ------------------------------ Agenten-Loop -------------------------------

def run_task(messages, model):
    """Fuehrt die Agenten-Schleife aus, bis 'finish' oder das Schrittlimit erreicht ist."""
    for step in range(1, MAX_STEPS + 1):
        print(f"\n{C.BLUE}── Schritt {step} ─────────────────────────────{C.RESET}")
        reply = chat_stream(messages, model)
        messages.append({"role": "assistant", "content": reply})

        action, raw = extract_action(reply)
        if action is None:
            # Keine Aktion -> Modell ist mit einer Textantwort fertig.
            return reply

        if "_parse_error" in action:
            obs = (f"FEHLER: dein action-JSON war ungueltig ({action['_parse_error']}). "
                   f"Bitte gib einen einzelnen validen ```action``` Block aus.")
            print(f"{C.RED}{obs}{C.RESET}")
            messages.append({"role": "user", "content": obs})
            continue

        name = action.get("action")
        if name == "finish":
            summary = action.get("summary", "Fertig.")
            print(f"\n{C.GREEN}{C.BOLD}✓ {summary}{C.RESET}")
            return summary

        handler = DISPATCH.get(name)
        if not handler:
            obs = f"FEHLER: unbekannte Aktion '{name}'."
            print(f"{C.RED}{obs}{C.RESET}")
            messages.append({"role": "user", "content": obs})
            continue

        ok, result = handler(action)
        marker = C.GREEN + "✓" if ok else C.RED + "✗"
        print(f"{marker}{C.RESET} {C.DIM}{result.splitlines()[0][:100]}{C.RESET}")
        messages.append({"role": "user", "content": f"[Ergebnis von {name}]\n{result}"})

    print(f"{C.RED}Schrittlimit ({MAX_STEPS}) erreicht.{C.RESET}")
    return None


def main():
    global AUTO_YES, BASE_URL, PROXY, CA_BUNDLE, INSECURE, VERBOSE
    ap = argparse.ArgumentParser(description="Mini Coding Tool (Ollama / OpenAI-kompatibel)")
    ap.add_argument("task", nargs="*", help="Aufgabe / Prompt (optional; sonst interaktiv)")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"Modell (default {DEFAULT_MODEL})")
    ap.add_argument("--base-url", default=BASE_URL,
                    help=f"Server-Basis-URL (default {BASE_URL})")
    ap.add_argument("--list-models", action="store_true", help="Verfuegbare Modelle anzeigen und beenden")
    ap.add_argument("--proxy", default=PROXY,
                    help="HTTP(S)-Proxy, z.B. http://proxy:8080 (Zscaler/Firmennetz)")
    ap.add_argument("--ca-bundle", default=CA_BUNDLE,
                    help="Pfad zu eigenem CA-Zertifikat (z.B. Zscaler-Root .pem)")
    ap.add_argument("--insecure", action="store_true",
                    help="TLS-Pruefung abschalten (nur als Notnagel)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="Passive Statuszeilen ausgeben (Verbindung, Anfrage, Antwort)")
    ap.add_argument("--yes", action="store_true", help="Alle Aktionen ohne Rueckfrage ausfuehren")
    args = ap.parse_args()
    AUTO_YES = args.yes
    BASE_URL = args.base_url.rstrip("/")
    PROXY = args.proxy
    CA_BUNDLE = args.ca_bundle
    INSECURE = args.insecure
    VERBOSE = VERBOSE or args.verbose

    if args.list_models:
        print(f"{C.CYAN}Modelle @ {BASE_URL}:{C.RESET}")
        for mid in list_models():
            print(f"  {mid}")
        return

    banner(f"mc · Mini Coding Tool  ({args.model} @ {BASE_URL})")
    if AUTO_YES:
        print(f"{C.RED}Achtung: --yes aktiv, Aktionen werden ohne Rueckfrage ausgefuehrt.{C.RESET}")
    info(f"Arbeitsverzeichnis: {os.getcwd()}")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Einmal-Modus
    if args.task:
        messages.append({"role": "user", "content": " ".join(args.task)})
        run_task(messages, args.model)
        return

    # Interaktiver Modus
    info("Interaktiv. Gib eine Aufgabe ein (oder 'exit' / Ctrl-D zum Beenden).")
    while True:
        try:
            user = input(f"\n{C.GREEN}{C.BOLD}du> {C.RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user:
            continue
        if user.lower() in ("exit", "quit", "q"):
            break
        messages.append({"role": "user", "content": user})
        run_task(messages, args.model)


if __name__ == "__main__":
    AUTO_YES = False
    main()
