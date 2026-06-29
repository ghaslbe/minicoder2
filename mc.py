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
import socket
import ssl
import urllib.request
import urllib.error
from urllib.parse import urlsplit

BASE_URL = os.environ.get("MC_BASE_URL", "http://localhost:11434/v1").rstrip("/")
DEFAULT_MODEL = os.environ.get("MC_MODEL", "qwen3-coder:30b")
API_KEY = os.environ.get("MC_API_KEY", "")

# Netzwerk: in Firmenumgebungen (z.B. Zscaler) muss der Traffic durch einen Proxy,
# und das TLS wird oft mit einem eigenen CA-Zertifikat aufgebrochen.
PROXY = os.environ.get("MC_PROXY", "")              # z.B. http://proxy:8080
CA_BUNDLE = os.environ.get("MC_CA_BUNDLE", "")       # Pfad zur Zscaler-CA (.pem)
INSECURE = False                                     # TLS-Pruefung abschalten (Notnagel)
VERBOSE = os.environ.get("MC_VERBOSE", "") not in ("", "0", "false")  # passive Logausgaben

MAX_STEPS = int(os.environ.get("MC_MAX_STEPS", "40"))  # Sicherheitslimit pro Aufgabe
MAX_OUTPUT_CHARS = 8000  # Trunkierung von Tool-Ausgaben an das Modell

# Token-/Kostenzaehler ueber die ganze Sitzung (Kosten nur, wenn der Endpoint sie
# liefert, z.B. OpenRouter via usage.cost).
USAGE = {"prompt": 0, "completion": 0, "cost": 0.0, "reqs": 0}


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

def _socks_handler(proxy_url):
    """SOCKS-Proxy-Handler (benoetigt das Paket PySocks: pip install PySocks).
    socks5h://… loest DNS am Proxy auf (wichtig hinter Zscaler, wenn der lokale
    Rechner externe Namen nicht aufloesen kann)."""
    try:
        import socks  # PySocks
        from sockshandler import SocksiPyHandler
    except ImportError:
        raise SystemExit(
            f"{C.RED}SOCKS-Proxy angegeben, aber PySocks fehlt.{C.RESET}\n"
            f"  Installieren:  python -m pip install PySocks\n"
            f"  Danach erneut:  ... --proxy {re.sub(r'//[^@/]*@', '//***@', proxy_url)} ...")
    s = urlsplit(proxy_url)
    rdns = s.scheme.lower() in ("socks5h", "socks4a")
    ptype = socks.SOCKS4 if s.scheme.lower().startswith("socks4") else socks.SOCKS5
    return SocksiPyHandler(ptype, s.hostname, s.port or 1080, rdns=rdns,
                           username=s.username, password=s.password)


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
        if PROXY.lower().startswith(("socks5", "socks4")):
            handlers.append(_socks_handler(PROXY))
        else:
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


def account_usage(u):
    """Summiert Tokens und (falls vorhanden) Kosten eines Requests auf."""
    USAGE["prompt"] += u.get("prompt_tokens", 0) or 0
    USAGE["completion"] += u.get("completion_tokens", 0) or 0
    USAGE["cost"] += u.get("cost", 0.0) or 0.0
    USAGE["reqs"] += 1
    if VERBOSE:
        msg = f"Tokens: +{u.get('prompt_tokens',0)}/{u.get('completion_tokens',0)}"
        if u.get("cost"):
            msg += f" · +${u['cost']:.5f}"
        log(msg)


def print_usage_summary():
    """Gibt Token-/Kostensumme der Sitzung aus (am Ende einer Aufgabe)."""
    if USAGE["reqs"] == 0:
        return
    total = USAGE["prompt"] + USAGE["completion"]
    line = (f"Σ {USAGE['reqs']} Requests · {total} Tokens "
            f"(prompt {USAGE['prompt']} + completion {USAGE['completion']})")
    if USAGE["cost"] > 0:
        line += f" · Kosten: ${USAGE['cost']:.4f}"
    print(f"{C.CYAN}{line}{C.RESET}")


def chat_stream(messages, model):
    """Ruft /chat/completions im Streaming-Modus auf und gibt den vollen Text
    zurueck (waehrend des Empfangs wird live ausgegeben)."""
    url = f"{BASE_URL}/chat/completions"
    payload = {"model": model, "messages": messages, "stream": True,
               # Token-/Kostenabrechnung anfordern (OpenAI-Standard + OpenRouter).
               # Endpoints, die das nicht kennen (z.B. Ollama), ignorieren es.
               "stream_options": {"include_usage": True},
               "usage": {"include": True}}
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    parts = []
    first = True
    usage = None
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
                # Der Usage-Chunk hat oft leere/keine choices -> sicher zugreifen.
                choices = obj.get("choices") or []
                if choices:
                    token = choices[0].get("delta", {}).get("content")
                    if token:
                        if first:
                            log("Antwort beginnt …")
                            first = False
                        parts.append(token)
                        sys.stdout.write(f"{C.DIM}{token}{C.RESET}")
                        sys.stdout.flush()
                if obj.get("usage"):
                    usage = obj["usage"]
        if usage:
            account_usage(usage)
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


def debug_net():
    """Gibt aus, welche Proxy-/Netzwerk-Konfiguration das System meldet.
    Hilft, hinter Zscaler den ECHTEN Proxy zu finden statt zu raten."""
    print(f"{C.CYAN}{C.BOLD}Netzwerk-Diagnose{C.RESET}")
    print(f"  Plattform        : {sys.platform}")
    print(f"  Ziel (BASE_URL)  : {BASE_URL}")

    # DNS-Test des Zielhosts — das ist die Ursache von 'getaddrinfo failed'.
    split = urlsplit(BASE_URL)
    host = split.hostname or "?"
    port = split.port or (443 if split.scheme == "https" else 80)
    print(f"\n{C.BOLD}DNS-Aufloesung von '{host}'{C.RESET}:")
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        ips = sorted({i[4][0] for i in infos})
        print(f"  {C.GREEN}OK{C.RESET} -> {', '.join(ips)}")
        dns_ok = True
    except OSError as e:
        print(f"  {C.RED}FEHLGESCHLAGEN{C.RESET}: {e}")
        print(f"  {C.YELLOW}=> Dein Rechner kann den Host nicht aufloesen. Typisch, wenn der "
              f"Zugang nur ueber einen Proxy geht, der die DNS-Aufloesung uebernimmt.{C.RESET}")
        dns_ok = False

    # Direkter TCP-Connect-Test (nur wenn DNS klappt).
    if dns_ok:
        print(f"\n{C.BOLD}TCP-Verbindung zu {host}:{port}{C.RESET}:")
        try:
            with socket.create_connection((host, port), timeout=8):
                print(f"  {C.GREEN}OK{C.RESET} — Port erreichbar (direkter Zugang moeglich)")
        except OSError as e:
            print(f"  {C.RED}FEHLGESCHLAGEN{C.RESET}: {e}")
            print(f"  {C.YELLOW}=> DNS klappt, aber kein direkter Zugang — Traffic muss durch "
                  f"einen Proxy/Tunnel (Zscaler).{C.RESET}")

    print(f"\n{C.BOLD}Vom System gemeldete Proxies{C.RESET} (urllib.getproxies):")
    sysproxies = urllib.request.getproxies()
    if sysproxies:
        for k, v in sysproxies.items():
            print(f"  {k:6} -> {v}")
    else:
        print("  (keine) — evtl. PAC-Datei oder transparenter Proxy")

    print(f"\n{C.BOLD}Proxy-Umgebungsvariablen{C.RESET}:")
    found = False
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
                "http_proxy", "https_proxy", "no_proxy"):
        val = os.environ.get(var)
        if val:
            print(f"  {var} = {val}")
            found = True
    if not found:
        print("  (keine gesetzt)")

    # Windows: PAC-Datei (AutoConfigURL) auslesen — die haeufigste Zscaler-Variante.
    # Sowohl benutzer- (HKCU) als auch maschinenweit (HKLM) pruefen.
    if sys.platform == "win32":
        print(f"\n{C.BOLD}Windows Internet-Settings{C.RESET}:")
        try:
            import winreg
            roots = [("HKCU", winreg.HKEY_CURRENT_USER),
                     ("HKLM", winreg.HKEY_LOCAL_MACHINE)]
            any_val = False
            for label, root in roots:
                try:
                    key = winreg.OpenKey(
                        root, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
                except OSError:
                    continue
                for name in ("ProxyEnable", "ProxyServer", "AutoConfigURL"):
                    try:
                        val, _ = winreg.QueryValueEx(key, name)
                        print(f"  {label}\\{name} = {val}")
                        any_val = True
                    except FileNotFoundError:
                        pass
            if not any_val:
                print("  (kein ProxyServer / keine AutoConfigURL gesetzt)")
            print(f"  {C.YELLOW}Tipp: 'netsh winhttp show proxy' zeigt zusaetzlich den "
                  f"System-(WinHTTP-)Proxy.{C.RESET}")
        except Exception as e:
            print(f"  (Registry nicht lesbar: {e})")

    print(f"\n{C.BOLD}CA-Zertifikate{C.RESET}:")
    print(f"  Default-Pfade: {ssl.get_default_verify_paths().cafile}")
    print(f"\n{C.YELLOW}Tipp:{C.RESET} Gefundenen Proxy testen mit:")
    print(f"  curl.exe -v --proxy http://PROXY:PORT {BASE_URL}/models")


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


def do_write_files(args):
    """Schreibt mehrere Dateien in EINEM Schritt — fuer Projekt-Gerueste mit
    vielen Dateien in vielen Verzeichnissen."""
    files = args.get("files")
    if not isinstance(files, list) or not files:
        return False, "FEHLER: 'files' muss eine nicht-leere Liste von {path,content} sein."
    print(f"{C.YELLOW}» write_files{C.RESET} {C.BOLD}{len(files)}{C.RESET} Datei(en):")
    for f in files:
        print(f"   {f.get('path','?')} ({len(f.get('content',''))} Zeichen)")
    if not confirm(f"{len(files)} Datei(en) schreiben?"):
        return False, "Abgelehnt durch den Benutzer."
    written, errors = [], []
    for f in files:
        path, content = f.get("path", ""), f.get("content", "")
        if not path:
            errors.append("(Eintrag ohne 'path' uebersprungen)")
            continue
        try:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            written.append(path)
        except Exception as e:
            errors.append(f"{path}: {e}")
    msg = f"{len(written)} Datei(en) geschrieben:\n" + "\n".join(written)
    if errors:
        msg += "\nFEHLER:\n" + "\n".join(errors)
    return (not errors), msg


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


# Verzeichnisse, die beim Durchsuchen/Ueberblick ignoriert werden.
IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
               ".mypy_cache", ".pytest_cache", ".idea", ".vscode", "dist", "build"}


def _norm(s):
    """Auf Kleinbuchstaben + nur alphanumerisch reduzieren — fuer unscharfen
    Vergleich, z.B. 'hello world' == 'helloworld'."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def do_find(args):
    """Sucht Dateien, deren Name das Muster enthaelt — auch unscharf
    (Leerzeichen/Sonderzeichen werden ignoriert)."""
    pattern = args.get("pattern") or args.get("name") or ""
    root = args.get("path", ".")
    if not pattern:
        return False, "FEHLER: 'pattern' fehlt."
    npat = _norm(pattern)
    matches = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            if pattern.lower() in fn.lower() or (npat and npat in _norm(fn)):
                matches.append(os.path.normpath(os.path.join(dirpath, fn)))
            if len(matches) >= 100:
                break
    if not matches:
        return True, (f"Keine Datei gefunden, deren Name '{pattern}' enthaelt. "
                      f"Pruefe mit list_dir, was vorhanden ist.")
    return True, "Gefundene Dateien:\n" + "\n".join(matches)


def project_overview(root=".", max_entries=200):
    """Kompakter rekursiver Dateiueberblick fuer den Startkontext des Agenten."""
    paths = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in IGNORE_DIRS and not d.startswith("."))
        rel = os.path.relpath(dirpath, root)
        for fn in sorted(filenames):
            paths.append(fn if rel == "." else os.path.join(rel, fn))
            if len(paths) >= max_entries:
                paths.append(f"... (>{max_entries} Dateien, gekuerzt)")
                return paths
    return paths


def do_ask(args):
    """Stellt dem Nutzer eine Frage (z.B. um einen Plan bestaetigen zu lassen)
    und gibt dessen Antwort an den Agenten zurueck."""
    question = (args.get("question") or "").strip() or "(keine Frage angegeben)"
    print(f"{C.CYAN}{C.BOLD}» Rueckfrage:{C.RESET} {question}")
    if AUTO_YES:
        print(f"{C.DIM}(--yes aktiv: ohne Rueckfrage fortfahren){C.RESET}")
        return True, "Auto-Modus (--yes): triff sinnvolle Annahmen und fahre ohne Rueckfrage fort."
    try:
        ans = input(f"{C.GREEN}{C.BOLD}deine Antwort> {C.RESET}").strip()
    except EOFError:
        return True, ("Keine Eingabe moeglich (nicht-interaktiv): triff sinnvolle "
                      "Annahmen und fahre fort.")
    if not ans:
        return True, "(keine Antwort) Triff eine sinnvolle Annahme und fahre fort."
    return True, f"Antwort des Nutzers: {ans}"


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
    "write_files": do_write_files,
    "list_dir": do_list_dir,
    "find": do_find,
    "ask": do_ask,
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
  write_files -> {"action":"write_files","files":[{"path":"a","content":"…"},{"path":"b/c","content":"…"}]}
  list_dir    -> {"action":"list_dir","path":"<pfad>"}
  find        -> {"action":"find","pattern":"<namensteil>"}
  ask         -> {"action":"ask","question":"<frage an den nutzer>"}
  run         -> {"action":"run","command":"<shell-kommando>"}
  finish      -> {"action":"finish","summary":"<kurze zusammenfassung>"}

Regeln:
- Wenn eine Anforderung WIRKLICH unklar ist, nutze die ask-Aktion zum Nachfragen,
  statt zu raten. Bei eindeutigen Aufgaben arbeite direkt los.
- Pro Antwort GENAU EIN action-Block. Davor darfst du kurz dein Vorgehen erklaeren.
- JSON muss valide sein. Bei write_file ist "content" der KOMPLETTE neue Dateiinhalt.
- Arbeite in kleinen Schritten. Lies bestehende Dateien bevor du sie aenderst.
- WICHTIG: Wenn der Nutzer eine bestehende Datei AENDERN will, lege NIEMALS einfach
  eine neue an. Suche sie zuerst mit find/list_dir. Nutzer benennen Dateien oft
  ungenau — "hello world" kann "helloworld.py", "HelloWorld.js" o.ae. heissen.
  find ignoriert Gross-/Kleinschreibung und Leer-/Sonderzeichen.
- Erst wenn find/list_dir nichts Passendes liefern, frage nach oder lege neu an.
- Fuer Projekte mit VIELEN Dateien: schreibe sie gebuendelt mit write_files
  (mehrere auf einmal) statt einzeln — das spart Schritte.
- Fuer ein NEUES Projektgeruest nutze, wenn moeglich, offizielle Generatoren via
  run (z.B. 'npm create vite@latest frontend -- --template react') und passe
  danach gezielt einzelne Dateien an, statt jede Datei von Hand zu erzeugen.
- Wenn die Aufgabe erledigt ist, gib eine finish-Aktion aus.
- Schreibe sauberen, lauffaehigen Code. Halte dich an vorhandene Konventionen.

Beispiel-Antwort:
Ich lege die Datei an.
```action
{"action":"write_file","path":"hello.py","content":"print('hello')\\n"}
```"""


# ------------------------------ Agenten-Loop -------------------------------

def plan_phase(messages, model):
    """Deterministische Plan-Phase: holt zuerst einen Plan vom Modell, zeigt ihn
    und laesst den Nutzer bestaetigen/anpassen — BEVOR Dateien geaendert werden.
    Gibt False zurueck, wenn der Nutzer abbricht."""
    messages.append({"role": "user", "content":
        "Bevor du handelst: Erstelle einen KNAPPEN, nummerierten Plan fuer diese "
        "Aufgabe — geplante Dateien/Verzeichnisse, Schritte und wichtige Annahmen. "
        "Gib NUR den Plan als Text aus, KEINEN action-Block."})
    print(f"\n{C.CYAN}{C.BOLD}── Plan ─────────────────────────────────{C.RESET}")
    plan = chat_stream(messages, model)
    messages.append({"role": "assistant", "content": plan})
    try:
        fb = input(f"\n{C.YELLOW}Plan ok? [Enter]=ja · Text=Aenderungswunsch · "
                   f"n=abbrechen> {C.RESET}").strip()
    except EOFError:
        fb = ""
    if fb.lower() in ("n", "nein", "no", "q", "abbrechen"):
        print(f"{C.DIM}Abgebrochen.{C.RESET}")
        messages.append({"role": "user", "content": "(Nutzer hat den Plan abgelehnt/abgebrochen.)"})
        return False
    if fb:
        messages.append({"role": "user", "content":
            f"Aenderungswunsch zum Plan: {fb}\nBeruecksichtige das und setze den "
            f"angepassten Plan jetzt mit Aktionen um."})
    else:
        messages.append({"role": "user", "content":
            "Plan ist bestaetigt. Setze ihn jetzt Schritt fuer Schritt mit Aktionen um."})
    return True


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
    global AUTO_YES, BASE_URL, PROXY, CA_BUNDLE, INSECURE, VERBOSE, MAX_STEPS
    ap = argparse.ArgumentParser(description="Mini Coding Tool (Ollama / OpenAI-kompatibel)")
    ap.add_argument("task", nargs="*", help="Aufgabe / Prompt (optional; sonst interaktiv)")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"Modell (default {DEFAULT_MODEL})")
    ap.add_argument("--base-url", default=BASE_URL,
                    help=f"Server-Basis-URL (default {BASE_URL})")
    ap.add_argument("--list-models", action="store_true", help="Verfuegbare Modelle anzeigen und beenden")
    ap.add_argument("--debug-net", action="store_true",
                    help="System-Proxy/Netzwerk-Konfiguration anzeigen und beenden")
    ap.add_argument("--proxy", default=PROXY,
                    help="HTTP(S)-Proxy, z.B. http://proxy:8080 (Zscaler/Firmennetz)")
    ap.add_argument("--ca-bundle", default=CA_BUNDLE,
                    help="Pfad zu eigenem CA-Zertifikat (z.B. Zscaler-Root .pem)")
    ap.add_argument("--insecure", action="store_true",
                    help="TLS-Pruefung abschalten (nur als Notnagel)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="Passive Statuszeilen ausgeben (Verbindung, Anfrage, Antwort)")
    ap.add_argument("--max-steps", type=int, default=MAX_STEPS,
                    help=f"Max. Agenten-Schritte pro Aufgabe (default {MAX_STEPS})")
    ap.add_argument("--plan", action="store_true",
                    help="Erst einen Plan zeigen und bestaetigen lassen, dann umsetzen")
    ap.add_argument("--yes", action="store_true", help="Alle Aktionen ohne Rueckfrage ausfuehren")
    args = ap.parse_args()
    AUTO_YES = args.yes
    MAX_STEPS = args.max_steps
    # Plan-Phase: opt-in per --plan (mit --yes nicht sinnvoll, daher aus).
    plan_mode = args.plan and not AUTO_YES
    BASE_URL = args.base_url.rstrip("/")
    PROXY = args.proxy
    CA_BUNDLE = args.ca_bundle
    INSECURE = args.insecure
    VERBOSE = VERBOSE or args.verbose

    if args.debug_net:
        debug_net()
        return

    if args.list_models:
        print(f"{C.CYAN}Modelle @ {BASE_URL}:{C.RESET}")
        for mid in list_models():
            print(f"  {mid}")
        return

    banner(f"mc · Mini Coding Tool  ({args.model} @ {BASE_URL})")
    if AUTO_YES:
        print(f"{C.RED}Achtung: --yes aktiv, Aktionen werden ohne Rueckfrage ausgefuehrt.{C.RESET}")
    info(f"Arbeitsverzeichnis: {os.getcwd()}")

    # Projektueberblick als Kontext: damit der Agent vorhandene Dateien kennt und
    # bei ungenauer Benennung die richtige trifft, statt eine neue anzulegen.
    overview = project_overview()
    listing = "\n".join(overview) if overview else "(keine Dateien)"
    context_msg = (
        f"Arbeitsverzeichnis: {os.getcwd()}\n"
        f"Vorhandene Dateien (rekursiv):\n{listing}\n\n"
        f"Wenn der Nutzer eine Datei ungenau benennt, ordne sie einer dieser Dateien "
        f"zu (find hilft beim unscharfen Suchen), statt blind eine neue anzulegen.")
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "system", "content": context_msg}]

    # Einmal-Modus
    if args.task:
        messages.append({"role": "user", "content": " ".join(args.task)})
        if plan_mode and not plan_phase(messages, args.model):
            return
        run_task(messages, args.model)
        print_usage_summary()
        return

    # Interaktiver Modus
    info("Interaktiv. Gib eine Aufgabe ein (oder 'exit' / Ctrl-D zum Beenden).")
    if plan_mode:
        info("Plan-Modus aktiv (--plan): erst Plan + Bestaetigung, dann Umsetzung.")
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
        if plan_mode and not plan_phase(messages, args.model):
            continue
        run_task(messages, args.model)
        print_usage_summary()


if __name__ == "__main__":
    AUTO_YES = False
    main()
