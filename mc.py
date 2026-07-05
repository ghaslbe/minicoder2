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
import threading
import time
import http.client
import socket
import ssl
import urllib.request
import urllib.error
from urllib.parse import urlsplit

BASE_URL = os.environ.get("MC_BASE_URL", "http://localhost:11434/v1").rstrip("/")
DEFAULT_MODEL = os.environ.get("MC_MODEL", "qwen3-coder:30b")
API_KEY = os.environ.get("MC_API_KEY", "")
# Zusaetzliche HTTP-Header pro Request, z.B. MC_HEADERS="X-Foo: bar; X-Baz: qux"
# (mehrere durch ';' oder Zeilenumbruch getrennt, je 'Name: Wert').
EXTRA_HEADERS_RAW = os.environ.get("MC_HEADERS", "")

# Netzwerk: in Firmenumgebungen (z.B. Zscaler) muss der Traffic durch einen Proxy,
# und das TLS wird oft mit einem eigenen CA-Zertifikat aufgebrochen.
PROXY = os.environ.get("MC_PROXY", "")              # z.B. http://proxy:8080
CA_BUNDLE = os.environ.get("MC_CA_BUNDLE", "")       # Pfad zur Zscaler-CA (.pem)
INSECURE = False                                     # TLS-Pruefung abschalten (Notnagel)
VERBOSE = os.environ.get("MC_VERBOSE", "") not in ("", "0", "false")  # passive Logausgaben

MAX_STEPS = int(os.environ.get("MC_MAX_STEPS", "40"))  # Sicherheitslimit pro Aufgabe
MAX_OUTPUT_CHARS = 8000  # Trunkierung von Tool-Ausgaben an das Modell

# Validierung geschriebener Dateien (bekannte Typen) + Git-Rollback.
VALIDATE = True            # nach dem Schreiben bekannte Dateitypen pruefen
GIT_ROLLBACK = False       # nur True, wenn git installiert + sauberes Repo (in main gesetzt)
TOUCHED = []               # von mc geschriebene/geaenderte Pfade (fuer Rollback)
CLEAN_FINISH = False       # True nur bei explizitem finish (nicht Schrittlimit/Prosa-Ende)
MAX_FIX_ATTEMPTS = 3       # so oft darf das Modell eine ungueltige Datei nachbessern

# Robustheit (aus dem GPU-Benchmark gelernt):
# - Grosse write_files-Bloecke sind das Haupt-Truncation-Risiko -> Limit wird
#   vom TOOL erzwungen, nicht nur im Prompt erbeten.
# - Modelle erklaeren sich nach einem verworfenen Schritt gern in Prosa fuer
#   "fertig" -> finish wird gegen die in der Aufgabe genannten Dateien geprueft.
MAX_WRITE_FILES_BATCH = 3  # max. Dateien pro write_files-Block
MAX_FINISH_REJECTS = 2     # so oft wird ein verfruehtes finish zurueckgewiesen
EXPECTED_FILES = []        # aus der Aufgabe extrahierte Dateipfade (Finish-Check)

# Check-Modus (--check): finish wird erst akzeptiert, wenn das Modell seine
# Arbeit nach der letzten Aenderung per run WIRKLICH ausgefuehrt hat (exit=0).
# Hintergrund: Syntax-Validierung findet keine falschen API-Annahmen,
# Feldnamen-Verwechslungen oder kaputte Dependencies — echte Ausfuehrung schon.
CHECK = os.environ.get("MC_CHECK", "") not in ("", "0", "false")
RAN_SINCE_WRITE = False    # seit letztem Schreiben ein run mit exit=0?
BG_PROCS = []              # Hintergrundprozesse (Dev-Server); Ende: aufgeraeumt
# Selbst genanntes Pruefprogramm aus der Plan-Phase (--plan --check): wird bei
# einem verfruehten finish woertlich zurueckgespielt, statt nur generisch an
# "irgendwas ausfuehren" zu erinnern — das Modell soll an seinem EIGENEN
# Versprechen gemessen werden, nicht an einer abstrakten Regel.
CHECK_PLAN = ""
# Notbremse fuer run mit --yes: offensichtlich destruktive Kommandos ablehnen.
DANGEROUS_RUN = re.compile(
    r"\b(sudo|shutdown|reboot|halt|mkfs\S*)\b"
    r"|rm\s+(-\w+\s+)*(/|~)(\s|$)"
    r"|dd\s+.*of=/dev/")

# Kontext-Beschneidung: die Message-Historie waechst pro Schritt, weil jede
# Tool-Ausgabe und jeder write-Block (mit komplettem Dateiinhalt!) dauerhaft
# mitgeschickt wird. Auf lokalen Maschinen ist Prompt-Processing der
# Flaschenhals -> aeltere Schritte werden auf Kurzfassungen reduziert; die
# Dateien liegen ja auf der Platte und sind per read_file/grep erreichbar.
KEEP_CONTEXT = int(os.environ.get("MC_KEEP_CONTEXT", "3"))  # letzte N Schritte bleiben voll
PRUNE = True               # Kontext-Beschneidung an (--no-prune schaltet ab)

# Fence-Modus: Dateiinhalte als rohe ```content Bloecke statt als escapte
# JSON-Strings (--fence / MC_FENCE=1). Betrifft nur, was der System-Prompt dem
# Modell beibringt — der Parser versteht IMMER beide Formate.
FENCE = os.environ.get("MC_FENCE", "") not in ("", "0", "false")

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


def extra_headers():
    """Parst MC_HEADERS ('Name: Wert' je Eintrag, getrennt durch ';' oder Zeilen-
    umbruch) in ein Dict, das jedem Request beigefuegt wird."""
    out = {}
    for part in re.split(r"[;\n]", EXTRA_HEADERS_RAW):
        part = part.strip()
        if not part or ":" not in part:
            continue
        name, val = part.split(":", 1)
        name = name.strip()
        if name:
            out[name] = val.strip()
    return out


MAX_CONTINUATIONS = 4  # max. automatische Fortsetzungen bei abgeschnittener Antwort


class Spinner:
    """Kleiner animierter Warte-Indikator in einem Hintergrund-Thread. Zeigt, dass
    das Modell arbeitet, waehrend der Hauptthread auf die Netzwerk-Antwort wartet.
    Nur aktiv im interaktiven Terminal (TTY); bei Pipe/Redirect passiv."""
    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, label="denke"):
        self.label = label
        self._stop = threading.Event()
        self._thread = None
        self.active = sys.stdout.isatty()

    def _run(self):
        i = 0
        start = time.time()
        while not self._stop.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            sys.stdout.write(f"\r{C.CYAN}{frame}{C.RESET} {C.DIM}{self.label} "
                             f"({time.time()-start:.0f}s)…{C.RESET}")
            sys.stdout.flush()
            i += 1
            self._stop.wait(0.1)

    def __enter__(self):
        if self.active:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc):
        if self._stop.is_set():
            return  # idempotent: zweiter Aufruf (finally) macht nichts
        self._stop.set()
        if self._thread:
            self._thread.join()
        if self.active:
            # Spinner-Zeile loeschen, damit die Antwort sauber beginnt.
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()


def _chat_once(messages, model):
    """Ein einzelner /chat/completions-Streaming-Aufruf. Gibt (text, finish_reason)
    zurueck und streamt live mit."""
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
    headers.update(extra_headers())

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    parts = []
    first = True
    usage = None
    finish_reason = None
    spin = Spinner("Modell denkt")
    spin.__enter__()  # Warte-Spinner bis zum ersten Token
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
                    if choices[0].get("finish_reason"):
                        finish_reason = choices[0]["finish_reason"]
                    token = choices[0].get("delta", {}).get("content")
                    if token:
                        if first:
                            spin.__exit__()  # Spinner weg, sobald die Antwort beginnt
                            log("Antwort beginnt …")
                            first = False
                        parts.append(token)
                        sys.stdout.write(f"{C.DIM}{token}{C.RESET}")
                        sys.stdout.flush()
                if obj.get("usage"):
                    usage = obj["usage"]
        if usage:
            account_usage(usage)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"\n{C.RED}HTTP {e.code} vom Endpoint:{C.RESET} {body}")
    except NET_ERRORS as e:
        raise SystemExit(net_error(getattr(e, "reason", e)))
    finally:
        spin.__exit__()  # Spinner-Thread immer beenden (auch bei Fehler)
    return "".join(parts), finish_reason


def _looks_truncated(text, finish_reason):
    """Heuristik: wurde die Antwort abgeschnitten? Zwei unabhaengige Signale —
    das offizielle finish_reason und ein Strukturcheck auf einen nicht
    geschlossenen ```action```-Block."""
    if finish_reason == "length":
        return True
    # Strukturcheck: LETZTER oeffnender ```action/```content-Fence ohne
    # schliessendes ``` danach — faengt auch Proxy-Abbrueche mitten im Block.
    last = None
    for m in re.finditer(r"`{3,}(action|content)\b", text):
        last = m
    if last and "```" not in text[last.end():]:
        return True
    return False


def chat_stream(messages, model):
    """Wie _chat_once, aber faengt abgeschnittene Antworten ab: bei Truncation
    wird das Modell automatisch um Fortsetzung gebeten und der Text zusammengefuegt
    — modell- und groessenunabhaengig, ohne kaputtes JSON zu flicken."""
    text, fr = _chat_once(messages, model)
    cont = 0
    while _looks_truncated(text, fr) and cont < MAX_CONTINUATIONS:
        cont += 1
        print()
        # Ursache klassifizieren und IMMER anzeigen (nicht nur verbose), damit man
        # erkennt, ob ein Token-Limit oder ein Verbindungs-/Proxy-Abbruch vorliegt.
        if fr == "length":
            grund = "Token-Limit (Ausgabe gekappt)"
        elif fr is None:
            grund = ("Verbindung/Proxy hat den Stream abgebrochen — ggf. "
                     "Proxy-/Netzwerk-Timeout erhoehen")
        else:
            grund = f"finish_reason={fr}"
        print(f"{C.YELLOW}⚠ Antwort abgeschnitten: {grund}. "
              f"Fordere Fortsetzung {cont}/{MAX_CONTINUATIONS} …{C.RESET}")
        cont_msgs = messages + [
            {"role": "assistant", "content": text},
            {"role": "user", "content":
                "Deine vorige Antwort wurde abgeschnitten. Fahre EXAKT an der "
                "abgebrochenen Stelle fort — gib NUR die Fortsetzung aus, ohne "
                "Wiederholung, ohne Einleitung, ohne den bereits gesendeten Teil "
                "zu erneut zu schreiben."}]
        more, fr = _chat_once(cont_msgs, model)
        text += more
    print()
    log(f"Antwort vollstaendig ({len(text)} Zeichen"
        + (f", {cont} Fortsetzung(en)" if cont else "") + ").")
    return text


def list_models():
    """Holt /models vom Endpoint und gibt je Modell (id, preis-info) zurueck.
    'preis-info' ist ein String wie 'gratis', '$0.95/$3.00 pro Mio Tok' oder ''
    (wenn der Endpoint keine Preise liefert, z.B. lokales Ollama)."""
    url = f"{BASE_URL}/models"
    headers = {}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    headers.update(extra_headers())
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

    out = []
    for m in obj.get("data", []):
        mid = m.get("id", "?")
        pr = m.get("pricing") or {}
        info = ""
        try:
            p = float(pr.get("prompt", "") or "nan")
            c = float(pr.get("completion", "") or "nan")
            if p == 0 and c == 0:
                info = "gratis"
            elif p == p and c == c:  # nicht NaN
                # OpenRouter-Preise sind pro Token -> auf pro Mio Token skalieren
                info = f"${p*1e6:.2f}/${c*1e6:.2f} pro Mio Tok"
        except (ValueError, TypeError):
            info = ""
        out.append((mid, info))
    return sorted(out, key=lambda x: x[0])


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


# Roher Dateiinhalt in einem ```content-Block nach dem action-Block. Laengere
# Zaeune (````content) sind erlaubt, falls der Inhalt selbst ```-Zeilen hat;
# der schliessende Zaun muss mindestens so lang sein wie der oeffnende
# (CommonMark-Regel) — kuerzere Backtick-Zeilen im Inhalt schliessen nicht.
CONTENT_FENCE_RE = re.compile(
    r"^(`{3,})content[ \t]*\n(.*?)\n\1`*[ \t]*$", re.DOTALL | re.MULTILINE)


def _attach_fence_contents(action, tail):
    """Ergaenzt write_file/write_files um Inhalte aus ```content Bloecken
    hinter dem action-Block (Fence-Modus). Gibt eine Fehlermeldung zurueck,
    wenn Bloecke fehlen oder die Anzahl nicht passt (sonst leerer String).
    Explizite "content"-Felder im JSON haben Vorrang (Abwaertskompatibilitaet)."""
    name = action.get("action")
    if name not in ("write_file", "write_files"):
        return ""
    fences = [mm.group(2) + "\n" for mm in CONTENT_FENCE_RE.finditer(tail)]
    if name == "write_file":
        if "content" in action:
            return ""
        if not fences:
            return ("write_file ohne Inhalt: es fehlt der ```content Block direkt "
                    "nach dem action-Block (roher Dateiinhalt, kein JSON-String).")
        action["content"] = fences[0]
        return ""
    files = action.get("files")
    if not isinstance(files, list):
        return ""  # wird im Handler gemeldet
    missing = [f for f in files if isinstance(f, dict) and "content" not in f]
    if not missing:
        return ""
    if len(fences) != len(missing):
        return (f"write_files: {len(missing)} Datei(en) ohne 'content' deklariert, "
                f"aber {len(fences)} ```content Block/Bloecke gefunden — je Datei "
                f"genau EIN Block, in derselben Reihenfolge wie die Pfade.")
    for f, c in zip(missing, fences):
        f["content"] = c
    return ""


def extract_action(text):
    """Findet den ersten ```action```-Block und parst das JSON daraus.
    Fehlende Dateiinhalte werden aus ```content Bloecken NACH dem
    action-Block ergaenzt (Fence-Modus) — beide Formate gehen immer."""
    m = ACTION_RE.search(text)
    if not m:
        return None, None
    raw = m.group(1).strip()
    try:
        action = json.loads(raw)
    except json.JSONDecodeError as e:
        return {"_parse_error": str(e), "_raw": raw}, raw
    if isinstance(action, dict):
        err = _attach_fence_contents(action, text[m.end():])
        if err:
            action["_fence_error"] = err
    return action, raw


# ------------------------- Kontext-Beschneidung -----------------------------

RESULT_RE = re.compile(r"^\[Ergebnis von (\w+)\]")


def _shrink_result(content):
    """Kuerzt eine aeltere Tool-Ausgabe auf die Kopfzeile(n) + Hinweis."""
    head = "\n".join(content.splitlines()[:2])[:300]
    return (head + "\n…[aeltere Tool-Ausgabe gekuerzt — die Dateien liegen auf "
            "der Platte, bei Bedarf read_file/grep nutzen]")


def _shrink_action(content):
    """Ersetzt in einer aelteren Assistant-Antwort die grossen Datei-Inhalte
    des action-Blocks durch eine kompakte Zusammenfassung (Pfad + Groesse)."""
    m = ACTION_RE.search(content)
    if not m:
        return content if len(content) <= 500 else content[:500] + "…[gekuerzt]"
    prose = content[:m.start()].strip()
    raw = m.group(1).strip()
    try:
        obj = json.loads(raw)
        name = obj.get("action", "?")
        if name == "write_files":
            parts = [str(f.get("path", "?")) +
                     (f" ({len(f['content'])} Z)" if isinstance(f, dict) and "content" in f else "")
                     for f in obj.get("files", [])]
            summary = f"(write_files ausgefuehrt: {', '.join(parts)} — Inhalte gekuerzt)"
        elif name in ("write_file", "edit_file"):
            n = len(obj.get("content", "") or obj.get("new", ""))
            summary = f"({name} ausgefuehrt: {obj.get('path','?')} ({n} Z) — Inhalt gekuerzt)"
        else:
            if len(raw) <= 300:
                return content  # kleine Aktionen (read/find/grep) unveraendert
            summary = f"({name}-Aktion, gekuerzt)"
    except (json.JSONDecodeError, AttributeError, TypeError):
        summary = "(ungueltiger action-Block, gekuerzt)"
    return (prose[:200] + "\n" if prose else "") + summary


def prune_messages(messages):
    """Reduziert AELTERE Schritte auf Kurzfassungen; die letzten KEEP_CONTEXT
    Schritte bleiben vollstaendig. System-Prompt und Aufgabentext werden nie
    angetastet (matchen die Muster nicht). Idempotent: bereits gekuerzte
    Nachrichten sind klein genug und werden uebersprungen."""
    if not PRUNE:
        return
    idx = [i for i, msg in enumerate(messages)
           if (msg["role"] == "assistant" and "```action" in msg.get("content", ""))
           or (msg["role"] == "user" and RESULT_RE.match(msg.get("content", "")))]
    cutoff = len(idx) - 2 * max(KEEP_CONTEXT, 0)  # 1 Schritt = assistant + ergebnis
    saved = 0
    for j, i in enumerate(idx):
        if j >= cutoff:
            break
        msg = messages[i]
        old_len = len(msg["content"])
        if old_len <= 400:
            continue  # klein genug, lohnt nicht
        if msg["role"] == "assistant":
            msg["content"] = _shrink_action(msg["content"])
        else:
            msg["content"] = _shrink_result(msg["content"])
        saved += old_len - len(msg["content"])
    if saved > 0:
        log(f"Kontext beschnitten: {saved} Zeichen aus aelteren Schritten entfernt.")


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


def _shrink_warning(path, new_len):
    """Erkennt den Fall 'write_file/write_files versehentlich statt read_file
    benutzt' (in der Praxis beobachtet: Modell will eine Datei nur ANSEHEN,
    greift aber zur Schreib-Aktion und ueberschreibt sie dabei mit fast
    nichts). Nur eine Warnung, kein Blocker — mit --yes gibt es ohnehin keine
    interaktive Rueckfrage, also muss die Rueckmeldung selbst reichen, damit
    das Modell den Verlust bemerkt und den Inhalt wiederherstellt."""
    try:
        old_len = os.path.getsize(path)
    except OSError:
        return ""
    if old_len > 40 and new_len < old_len * 0.4:
        return (f"\nACHTUNG: {path} hatte vorher {old_len} Zeichen, jetzt nur "
                f"{new_len} — falls das nicht beabsichtigt war (z.B. write_file "
                f"statt read_file verwendet, um nur reinzuschauen), stelle den "
                f"vorherigen Inhalt umgehend wieder her (git diff/read_file "
                f"pruefen, dann korrekt neu schreiben).")
    return ""


def do_write_file(args):
    path = args.get("path", "")
    content = args.get("content", "")
    print(f"{C.YELLOW}» write_file{C.RESET} {C.BOLD}{path}{C.RESET} ({len(content)} Zeichen)")
    preview = content if len(content) < 600 else content[:600] + "\n..."
    print(f"{C.DIM}{preview}{C.RESET}")
    if not confirm(f"Datei '{path}' schreiben?"):
        return False, "Abgelehnt durch den Benutzer."
    warn = _shrink_warning(path, len(content))
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        if warn:
            print(f"{C.RED}⚠{C.RESET} {warn.strip()}")
        return True, f"OK, {len(content)} Zeichen nach {path} geschrieben." + warn
    except Exception as e:
        return False, f"FEHLER beim Schreiben von {path}: {e}"


def do_write_files(args):
    """Schreibt mehrere Dateien in EINEM Schritt — fuer Projekt-Gerueste mit
    vielen Dateien in vielen Verzeichnissen."""
    files = args.get("files")
    if not isinstance(files, list) or not files:
        return False, "FEHLER: 'files' muss eine nicht-leere Liste von {path,content} sein."
    if len(files) > MAX_WRITE_FILES_BATCH:
        # Hartes Limit statt Prompt-Bitte: grosse Einzelbloecke sind das
        # Haupt-Risiko fuer abgeschnittene Antworten (kaputtes JSON).
        return False, (f"FEHLER: {len(files)} Dateien in EINEM write_files-Block — "
                       f"maximal {MAX_WRITE_FILES_BATCH} erlaubt (Schutz vor abgeschnittenen "
                       f"Antworten). Teile auf MEHRERE write_files-Schritte auf "
                       f"(z.B. erst backend/, dann frontend/) und fahre fort.")
    print(f"{C.YELLOW}» write_files{C.RESET} {C.BOLD}{len(files)}{C.RESET} Datei(en):")
    for f in files:
        print(f"   {f.get('path','?')} ({len(f.get('content',''))} Zeichen)")
    if not confirm(f"{len(files)} Datei(en) schreiben?"):
        return False, "Abgelehnt durch den Benutzer."
    written, errors, warns = [], [], []
    for f in files:
        path, content = f.get("path", ""), f.get("content", "")
        if not path:
            errors.append("(Eintrag ohne 'path' uebersprungen)")
            continue
        warn = _shrink_warning(path, len(content))
        try:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            written.append(path)
            if warn:
                warns.append(warn.strip())
        except Exception as e:
            errors.append(f"{path}: {e}")
    msg = f"{len(written)} Datei(en) geschrieben:\n" + "\n".join(written)
    if errors:
        msg += "\nFEHLER:\n" + "\n".join(errors)
    if warns:
        msg += "\n" + "\n".join(warns)
        print(f"{C.RED}⚠ {warns[0][:120]}{C.RESET}")
    return (not errors), msg


def do_edit_file(args):
    """Ersetzt in einer bestehenden Datei einen exakten Textausschnitt durch einen
    neuen — es wandert nur die Aenderung ueber die Leitung, nicht die ganze Datei.
    'old' muss EINDEUTIG vorkommen (sonst Fehler), ausser replace_all=true."""
    path = args.get("path", "")
    old = args.get("old", "")
    new = args.get("new", "")
    replace_all = bool(args.get("replace_all", False))
    if not path or old == "":
        return False, "FEHLER: 'path' und 'old' sind erforderlich."
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return False, f"FEHLER beim Lesen von {path}: {e}"

    count = content.count(old)
    if count == 0:
        return False, (f"FEHLER: der zu ersetzende Text wurde in {path} nicht gefunden. "
                       f"Gib 'old' exakt wie im Datei-Inhalt an (Whitespace zaehlt). "
                       f"Lies die Datei ggf. erneut mit read_file.")
    if count > 1 and not replace_all:
        return False, (f"FEHLER: 'old' kommt {count}x in {path} vor — nicht eindeutig. "
                       f"Mache den Ausschnitt groesser/eindeutiger oder setze "
                       f"replace_all=true.")

    print(f"{C.YELLOW}» edit_file{C.RESET} {C.BOLD}{path}{C.RESET} "
          f"({count}x ersetzen)" if replace_all else
          f"{C.YELLOW}» edit_file{C.RESET} {C.BOLD}{path}{C.RESET}")
    print(f"{C.RED}- {old[:200]}{C.RESET}")
    print(f"{C.GREEN}+ {new[:200]}{C.RESET}")
    if not confirm(f"Aenderung in '{path}' anwenden?"):
        return False, "Abgelehnt durch den Benutzer."
    try:
        updated = content.replace(old, new) if replace_all else content.replace(old, new, 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(updated)
        return True, (f"OK, {count if replace_all else 1} Stelle(n) in {path} ersetzt "
                      f"(Datei jetzt {len(updated)} Zeichen).")
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


GREP_SKIP_EXTS = {".db", ".sqlite", ".sqlite3", ".png", ".jpg", ".jpeg", ".gif",
                  ".ico", ".pdf", ".zip", ".gz", ".tar", ".pyc", ".woff", ".woff2"}


def do_grep(args):
    """Sucht Text/Regex IN Dateiinhalten (nicht nur im Namen) und liefert
    Datei:Zeile:Treffer — damit der Agent Stellen in bestehendem Code findet,
    statt viele Dateien komplett zu lesen (spart Tokens und Schritte)."""
    pattern = args.get("pattern", "")
    root = args.get("path", ".")
    if not pattern:
        return False, "FEHLER: 'pattern' fehlt."
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error:
        rx = None  # ungueltige Regex -> einfache Textsuche
    matches, limit = [], 50
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            if os.path.splitext(fn)[1].lower() in GREP_SKIP_EXTS:
                continue
            try:
                if os.path.getsize(full) > 2_000_000:
                    continue
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    for no, line in enumerate(f, 1):
                        hit = rx.search(line) if rx else (pattern.lower() in line.lower())
                        if hit:
                            matches.append(f"{os.path.normpath(full)}:{no}: {line.strip()[:160]}")
                            if len(matches) >= limit:
                                break
            except OSError:
                continue
            if len(matches) >= limit:
                break
        if len(matches) >= limit:
            break
    if not matches:
        return True, (f"Keine Treffer fuer '{pattern}' in Dateiinhalten. "
                      f"Pruefe die Schreibweise oder nutze find fuer Dateinamen.")
    out = "\n".join(matches)
    if len(matches) >= limit:
        out += f"\n...[auf {limit} Treffer gekuerzt]"
    return True, f"Treffer (Datei:Zeile):\n{out}"


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
    bg = bool(args.get("background"))
    try:
        timeout = min(max(int(args.get("timeout", 120)), 5), 300)
    except (TypeError, ValueError):
        timeout = 120
    tag = " (hintergrund)" if bg else ""
    print(f"{C.YELLOW}» run{tag}{C.RESET} {C.BOLD}{cmd}{C.RESET}")
    if DANGEROUS_RUN.search(cmd):
        return False, ("ABGELEHNT: das Kommando sieht destruktiv aus (sudo/rm auf "
                       "Wurzelpfade/etc.). Waehle ein harmloses, projektlokales Kommando.")
    if not confirm("Kommando ausfuehren?"):
        return False, "Abgelehnt durch den Benutzer."
    if bg:
        # Dauerlaeufer (Dev-Server): starten, kurz warten, erste Ausgabe zeigen.
        # Der Prozess laeuft weiter; alle BG-Prozesse werden am Ende beendet.
        import tempfile
        logf = tempfile.NamedTemporaryFile(prefix="mc_bg_", suffix=".log",
                                           delete=False, mode="w")
        try:
            proc = subprocess.Popen(cmd, shell=True, stdout=logf,
                                    stderr=subprocess.STDOUT,
                                    start_new_session=True)
        except Exception as e:
            return False, f"FEHLER beim Start: {e}"
        BG_PROCS.append(proc)
        time.sleep(3)
        try:
            with open(logf.name, "r", errors="replace") as f:
                head = f.read().strip()
        except Exception:
            head = ""
        if proc.poll() is not None:
            return False, (f"Prozess hat sich sofort beendet (exit={proc.returncode}). "
                           f"Ausgabe:\n{truncate(head or '(keine)')}")
        return True, (f"laeuft im Hintergrund (pid={proc.pid}). Erste Ausgabe:\n"
                      f"{truncate(head or '(noch keine)')}\n"
                      "Pruefe den Dienst jetzt mit einem normalen run (z.B. curl). "
                      "Hintergrundprozesse werden am Ende automatisch beendet.")
    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        out = proc.stdout + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        out = out.strip() or "(keine Ausgabe)"
        return True, f"exit={proc.returncode}\n{truncate(out)}"
    except subprocess.TimeoutExpired:
        return False, (f"FEHLER: Kommando-Timeout ({timeout}s). Dauerlaeufer wie "
                       "Dev-Server bitte mit \"background\":true starten.")
    except Exception as e:
        return False, f"FEHLER bei Ausfuehrung: {e}"


def kill_bg_procs():
    """Beendet alle vom Modell gestarteten Hintergrundprozesse (samt Kindern,
    dank start_new_session=True ueber die Prozessgruppe)."""
    import signal
    for p in BG_PROCS:
        if p.poll() is None:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except Exception:
                pass
    if BG_PROCS:
        time.sleep(0.5)
        n = sum(1 for p in BG_PROCS if p.poll() is not None)
        info(f"{n}/{len(BG_PROCS)} Hintergrundprozess(e) beendet.")


DISPATCH = {
    "read_file": do_read_file,
    "write_file": do_write_file,
    "write_files": do_write_files,
    "edit_file": do_edit_file,
    "list_dir": do_list_dir,
    "find": do_find,
    "grep": do_grep,
    "ask": do_ask,
    "run": do_run,
}


# ------------------------------ System-Prompt ------------------------------

SYSTEM_PROMPT_TEMPLATE = """Du bist ein praeziser Coding-Agent, der in einer Shell-Umgebung arbeitet.
Du kannst NICHT direkt auf Dateien zugreifen. Stattdessen forderst du EINE Aktion pro
Antwort an, indem du genau EINEN ```action``` Block mit JSON ausgibst. Du erhaeltst dann
das Ergebnis und faehrst fort.

Verfuegbare Aktionen (Feld "action"):
  read_file   -> {"action":"read_file","path":"<pfad>"}
@@WRITE_SPEC@@
  edit_file   -> {"action":"edit_file","path":"<pfad>","old":"<exakter ausschnitt>","new":"<ersatz>"}
  list_dir    -> {"action":"list_dir","path":"<pfad>"}
  find        -> {"action":"find","pattern":"<namensteil>"}
  grep        -> {"action":"grep","pattern":"<text oder regex>"}  (sucht IN Dateiinhalten, liefert Datei:Zeile)
  ask         -> {"action":"ask","question":"<frage an den nutzer>"}
  run         -> {"action":"run","command":"<shell-kommando>"}  (optional: "background":true fuer Dauerlaeufer wie Dev-Server, "timeout":<sek, max 300>)
  finish      -> {"action":"finish","summary":"<kurze zusammenfassung>"}

Regeln:
- Wenn eine Anforderung WIRKLICH unklar ist, nutze die ask-Aktion zum Nachfragen,
  statt zu raten. Bei eindeutigen Aufgaben arbeite direkt los.
- Pro Antwort GENAU EIN action-Block. Davor darfst du kurz dein Vorgehen erklaeren.
- JSON muss valide sein. @@CONTENT_RULE@@
- Arbeite in kleinen Schritten. Lies bestehende Dateien bevor du sie aenderst.
- KLEINE Aenderungen an bestehenden Dateien IMMER mit edit_file (gezieltes
  Ersetzen) statt die ganze Datei mit write_file neu zu schreiben — das spart
  Tokens und vermeidet abgeschnittene Antworten. "old" muss EXAKT und EINDEUTIG
  dem aktuellen Dateiinhalt entsprechen (inkl. Whitespace/Einrueckung); waehle
  genug Kontext, damit der Ausschnitt nur einmal vorkommt. write_file nur fuer
  NEUE Dateien oder komplette Neufassungen.
- WICHTIG: Wenn der Nutzer eine bestehende Datei AENDERN will, lege NIEMALS einfach
  eine neue an. Suche sie zuerst mit find/list_dir. Nutzer benennen Dateien oft
  ungenau — "hello world" kann "helloworld.py", "HelloWorld.js" o.ae. heissen.
  find ignoriert Gross-/Kleinschreibung und Leer-/Sonderzeichen.
- Erst wenn find/list_dir nichts Passendes liefern, frage nach oder lege neu an.
- Fuer Projekte mit VIELEN Dateien: schreibe sie gebuendelt mit write_files
  (mehrere auf einmal) statt einzeln — das spart Schritte.
- ABER: packe nicht ein ganzes Projekt in EINEN riesigen write_files-Block.
  Maximal 3 Dateien pro Block — MEHR WIRD VOM TOOL ABGELEHNT. Verteile
  groessere Projekte auf MEHRERE write_files-Schritte (z.B. erst Backend,
  dann Frontend). Sehr lange Antworten koennen abgeschnitten werden, wodurch
  das JSON unvollstaendig bleibt.
- Fuer Aenderungen an BESTEHENDEM Code: finde die Stelle zuerst mit grep
  (Inhaltssuche, liefert Datei:Zeile), dann gezielt read_file + edit_file —
  statt viele Dateien komplett zu lesen.
- finish wird vom Tool GEPRUEFT: alle in der Aufgabe woertlich genannten
  Dateien muessen existieren und valide sein, sonst wird finish abgelehnt.
  Gib finish erst aus, wenn wirklich alles geschrieben ist.
- Fuer ein NEUES Projektgeruest nutze, wenn moeglich, offizielle Generatoren via
  run (z.B. 'npm create vite@latest frontend -- --template react') und passe
  danach gezielt einzelne Dateien an, statt jede Datei von Hand zu erzeugen.
- Nutze run auch zum NACHSCHAUEN statt zu raten: bist du bei einer Bibliotheks-
  API unsicher, pruefe sie real (ls node_modules/<paket>/, pip show <paket>,
  python -c "import x; print(dir(x))"). Ein API-Endpunkt laesst sich mit
  run + curl direkt testen. Was du nachgeschlagen hast, kann nicht halluziniert
  sein.
- Wenn die Aufgabe erledigt ist, gib eine finish-Aktion aus.
- Schreibe sauberen, lauffaehigen Code. Halte dich an vorhandene Konventionen.

@@EXAMPLE@@"""


# Die @@…@@-Platzhalter werden je nach Modus (JSON-Strings vs. Fence-Bloecke
# fuer Dateiinhalte) gefuellt. Fence-Modus (--fence / MC_FENCE=1) vermeidet die
# haeufigste Fehlerklasse ueberhaupt: kaputtes Escaping grosser Dateiinhalte in
# JSON-Strings (fehlende '}', ueberzaehlige ']', \\n-/Quote-Fehler). Der PARSER
# versteht unabhaengig vom Modus immer beide Formate.

WRITE_SPEC_JSON = """  write_file  -> {"action":"write_file","path":"<pfad>","content":"<voller dateiinhalt>"}
  write_files -> {"action":"write_files","files":[{"path":"a","content":"…"},{"path":"b/c","content":"…"}]}"""

WRITE_SPEC_FENCE = """  write_file  -> {"action":"write_file","path":"<pfad>"}  + danach EIN ```content Block mit dem ROHEN Dateiinhalt
  write_files -> {"action":"write_files","files":[{"path":"a"},{"path":"b/c"}]}  + danach JE Datei ein ```content Block (gleiche Reihenfolge)"""

CONTENT_RULE_JSON = 'Bei write_file ist "content" der KOMPLETTE neue Dateiinhalt.'

CONTENT_RULE_FENCE = ("Dateiinhalte gehoeren NICHT als String ins JSON, sondern ROH "
                      "(ohne jedes Escaping — echte Zeilenumbrueche, echte Quotes) in "
                      "```content Bloecke DIREKT nach dem action-Block. Enthaelt ein "
                      "Inhalt selbst ```-Zeilen (z.B. Markdown), nimm einen laengeren "
                      "Zaun: ````content … ````.")

EXAMPLE_JSON = """Beispiel-Antwort:
Ich lege die Datei an.
```action
{"action":"write_file","path":"hello.py","content":"print('hello')\\n"}
```"""

EXAMPLE_FENCE = """Beispiel-Antwort:
Ich lege die Datei an.
```action
{"action":"write_file","path":"hello.py"}
```
```content
print('hello')
```"""


CHECK_PROMPT = """
CHECK-MODUS AKTIV — dein finish wird erst akzeptiert, wenn du deine Arbeit
nach der letzten Aenderung real ueberprueft hast (mind. ein run mit exit=0):
  1. Abhaengigkeiten installieren (pip install -r …, npm install).
  2. Syntax/Build pruefen (z.B. python -c "import app", npm run build,
     node --check datei.js).
  3. Dienste mit {"action":"run","command":"…","background":true} starten
     und dann mit run + curl testen: Endpunkte aufrufen, Antworten pruefen —
     auch Fehlerfaelle (unbekannte ID sollte 404 liefern, nicht Erfolg).
  4. Fehlermeldungen ERNST NEHMEN und beheben, dann erneut pruefen.
Hintergrundprozesse werden am Ende automatisch beendet. Verlasse dich nicht
auf dein Gedaechtnis, was eine Bibliothek 'haben muesste' — pruefe es
(z.B. ls node_modules/@material/web/) statt zu raten."""


def system_prompt(fence):
    """Baut den System-Prompt fuer den gewaehlten Modus zusammen."""
    sp = SYSTEM_PROMPT_TEMPLATE
    sp = sp.replace("@@WRITE_SPEC@@", WRITE_SPEC_FENCE if fence else WRITE_SPEC_JSON)
    sp = sp.replace("@@CONTENT_RULE@@", CONTENT_RULE_FENCE if fence else CONTENT_RULE_JSON)
    sp = sp.replace("@@EXAMPLE@@", EXAMPLE_FENCE if fence else EXAMPLE_JSON)
    if CHECK:
        sp += "\n" + CHECK_PROMPT
    return sp


# ------------------------------ Agenten-Loop -------------------------------

def plan_phase(messages, model):
    """Deterministische Plan-Phase: holt zuerst einen Plan vom Modell, zeigt ihn
    und laesst den Nutzer bestaetigen/anpassen — BEVOR Dateien geaendert werden.
    Gibt False zurueck, wenn der Nutzer abbricht."""
    ask = ("Bevor du handelst: Erstelle einen KNAPPEN, nummerierten Plan fuer diese "
           "Aufgabe — geplante Dateien/Verzeichnisse, Schritte und wichtige Annahmen. "
           "Gib NUR den Plan als Text aus, KEINEN action-Block.")
    if CHECK:
        ask += ("\nErstelle ZUSAETZLICH einen eigenen Abschnitt \"Pruefschritte:\" mit "
                "den KONKRETEN Kommandos, mit denen du JEDEN Teil der Aufgabe (Backend "
                "UND Frontend/Build getrennt, inkl. Fehlerfaellen wie unbekannte IDs) "
                "wirklich verifizieren wirst — nicht nur 'ich teste es', sondern die "
                "Kommandos selbst (z.B. 'npm run build', 'curl -X DELETE .../999').")
    messages.append({"role": "user", "content": ask})
    print(f"\n{C.CYAN}{C.BOLD}── Plan ─────────────────────────────────{C.RESET}")
    plan = chat_stream(messages, model)
    messages.append({"role": "assistant", "content": plan})

    if CHECK:
        global CHECK_PLAN
        m = re.search(r"pr(?:[üu]f|uef)schritte:?\s*(.+)", plan, re.IGNORECASE | re.DOTALL)
        CHECK_PLAN = (m.group(1) if m else plan).strip()[:1500]
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


# ------------------------- Finish-Verifikation -----------------------------

# Endungen, die als "vom Agenten zu erstellende" Quelltext-/Konfig-Dateien
# gelten. Laufzeit-Artefakte (.db, .log) bleiben bewusst aussen vor — die legt
# die App selbst an, nicht der Agent.
SRC_EXTS = {".py", ".txt", ".json", ".html", ".htm", ".js", ".jsx", ".ts",
            ".tsx", ".css", ".md", ".yaml", ".yml", ".php", ".sh", ".sql",
            ".xml", ".toml", ".ini", ".cfg", ".vue", ".svelte"}


def expected_files_from_task(task):
    """Extrahiert woertlich in der Aufgabe genannte Dateipfade (mit '/',
    bekannte Quelltext-Endung). Grundlage fuer den deterministischen
    Finish-Check: ein Modell kann sich dann nicht mehr in Prosa fuer 'fertig'
    erklaeren, waehrend geforderte Dateien fehlen."""
    task = task or ""
    out = []
    for m in re.finditer(r"[A-Za-z0-9_](?:[A-Za-z0-9_./-]*[A-Za-z0-9_])?\.[A-Za-z0-9]{1,6}",
                         task):
        p = m.group(0)
        if "/" not in p or "//" in p:  # nur explizite Pfade
            continue
        # URLs ausschliessen: Match beginnt hinter '://' bzw. 'www.'
        pre = task[max(0, m.start() - 4):m.start()]
        if "//" in pre or pre.endswith(":") or p.lower().startswith("www."):
            continue
        if os.path.splitext(p)[1].lower() in SRC_EXTS and p not in out:
            out.append(p)
    return out


# --------------------- Validierung & Git-Rollback --------------------------

def _git(*args, timeout=15):
    """Fuehrt ein git-Kommando aus, gibt (returncode, stdout) zurueck."""
    try:
        p = subprocess.run(["git", *args], capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 127, ""


def git_usable():
    """True nur wenn: git installiert UND im Arbeitsbaum UND Baum SAUBER (keine
    offenen Aenderungen). Nur dann ist ein exakter Rollback gefahrlos moeglich."""
    rc, _ = _git("rev-parse", "--is-inside-work-tree")
    if rc != 0:
        return False, "kein Git-Repository"
    rc, out = _git("status", "--porcelain")
    if rc != 0:
        return False, "git status fehlgeschlagen"
    if out.strip():
        return False, "Arbeitsbaum nicht sauber (offene Aenderungen)"
    return True, "ok"


DEFAULT_GITIGNORE = """node_modules/
venv/
.venv/
__pycache__/
*.pyc
*.db
dist/
build/
.DS_Store
"""


def git_auto_init():
    """Legt in einem frischen Arbeitsverzeichnis (noch KEIN Git-Repo) automatisch
    eines an, mit einem Ausgangs-Commit des bereits Vorhandenen — sonst waere
    die ganze Git-Absicherung (Commit nach sauberem finish, s.o.) in genau dem
    Fall wirkungslos, fuer den sie am meisten gedacht ist: mc.py in einem neuen,
    separaten Projektverzeichnis. Risikoarm und jederzeit rueckgaengig zu machen
    (nur ein lokales .git-Verzeichnis, kein Remote, kein Push)."""
    rc, out = _git("init")
    if rc != 0:
        return False, f"git init fehlgeschlagen: {out.strip()[:150]}"
    if not os.path.exists(".gitignore"):
        with open(".gitignore", "w", encoding="utf-8") as f:
            f.write(DEFAULT_GITIGNORE)
    _git("add", "-A")
    rc, out = _git("commit", "-m", "mc: Ausgangszustand vor erstem Lauf")
    if rc != 0 and "nothing to commit" not in out:
        return False, f"Ausgangs-Commit fehlgeschlagen: {out.strip()[:150]}"
    return True, "ok"


def validate_path(path):
    """Validiert eine Datei nach Typ. Gibt (status, meldung) zurueck, wobei status
    'ok' | 'bad' | 'skip' ist. Unbekannte/nachsichtige Typen -> 'skip'."""
    ext = os.path.splitext(path)[1].lower()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception as e:
        return "bad", f"nicht lesbar: {e}"
    if ext == ".py":
        import ast
        try:
            ast.parse(text); return "ok", ""
        except SyntaxError as e:
            return "bad", f"Python-SyntaxError: Zeile {e.lineno}: {e.msg}"
    if ext == ".json":
        try:
            json.loads(text); return "ok", ""
        except json.JSONDecodeError as e:
            return "bad", f"JSON ungueltig: {e}"
    if ext in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            return "skip", ""
        try:
            yaml.safe_load(text); return "ok", ""
        except Exception as e:
            return "bad", f"YAML ungueltig: {e}"
    if ext == ".php":
        try:
            p = subprocess.run(["php", "-l", path], capture_output=True, text=True, timeout=15)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return "skip", ""   # php nicht installiert -> nicht validierbar
        if p.returncode == 0:
            return "ok", ""
        return "bad", f"PHP-Lint: {((p.stdout or '')+(p.stderr or '')).strip()[:200]}"
    return "skip", ""


def written_paths(name, action):
    """Liefert die Pfade, die eine Schreib-/Edit-Aktion betrifft."""
    if name in ("write_file", "edit_file"):
        p = action.get("path")
        return [p] if p else []
    if name == "write_files":
        return [f.get("path") for f in (action.get("files") or []) if f.get("path")]
    return []


def validate_written(paths):
    """Validiert die geschriebenen Pfade. Gibt eine Fehlermeldung zurueck, wenn
    welche ungueltig sind (sonst leerer String)."""
    if not VALIDATE:
        return ""
    bad = []
    for p in paths:
        if not p or not os.path.isfile(p):
            continue
        status, msg = validate_path(p)
        if status == "bad":
            bad.append(f"  {p}: {msg}")
    if bad:
        return ("VALIDIERUNG FEHLGESCHLAGEN — folgende Dateien sind ungueltig und "
                "muessen korrigiert werden:\n" + "\n".join(bad) +
                "\nKorrigiere NUR diese Datei(en) (am besten mit edit_file oder einer "
                "neuen, validen write_file).")
    return ""


def git_rollback():
    """Setzt die von mc geaenderten/angelegten Dateien auf den Stand vor dem Lauf
    zurueck: getrackte -> auf HEAD, neu angelegte -> loeschen. Nur sicher, weil der
    Baum beim Start sauber war (in main geprueft)."""
    restored, removed = [], []
    for p in sorted(set(TOUCHED)):
        rc, _ = _git("cat-file", "-e", f"HEAD:{p}")
        if rc == 0:
            _git("restore", "--source=HEAD", "--staged", "--worktree", "--", p)
            restored.append(p)
        else:
            try:
                if os.path.isfile(p):
                    os.remove(p)
                removed.append(p)
            except Exception:
                pass
    print(f"{C.GREEN}Rollback: {len(restored)} Datei(en) auf HEAD zurueckgesetzt, "
          f"{len(removed)} neu angelegte geloescht.{C.RESET}")


def git_commit_run(summary):
    """Committet die von mc beruehrten Dateien als EINEN Sicherungspunkt — nur
    nach einem SAUBEREN finish (nicht bei Schrittlimit/Prosa-Ende), damit die
    Historie nicht mit Zwischenstaenden eines gescheiterten Laufs vollmuellt.
    Das ist der Fall, der bei --yes bisher komplett ungesichert war: kein
    Rollback-Angebot (interaktiv), aber auch kein Commit — Aenderungen waren
    schlicht weder rueckholbar noch nachvollziehbar."""
    paths = sorted(p for p in set(TOUCHED) if os.path.isfile(p))
    if not paths:
        return
    _git("add", "--", *paths)
    rc, out = _git("commit", "-m", f"mc: {summary[:72]}")
    if rc == 0:
        print(f"{C.GREEN}Git-Commit erstellt ({len(paths)} Datei(en)) — "
              f"Sicherungspunkt fuer diesen Lauf.{C.RESET}")
    else:
        print(f"{C.DIM}Kein Git-Commit (evtl. keine Aenderungen): {out.strip()[:100]}{C.RESET}")


def run_task(messages, model):
    """Fuehrt die Agenten-Schleife aus, bis 'finish' oder das Schrittlimit erreicht ist."""
    global RAN_SINCE_WRITE, CLEAN_FINISH
    CLEAN_FINISH = False
    finish_rejects = 0
    for step in range(1, MAX_STEPS + 1):
        prune_messages(messages)  # aeltere Schritte kuerzen (Tokens/Tempo)
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

        if "_fence_error" in action:
            obs = f"FEHLER: {action.pop('_fence_error')} Sende die Aktion bitte erneut."
            print(f"{C.RED}{obs}{C.RESET}")
            messages.append({"role": "user", "content": obs})
            continue

        name = action.get("action")
        if name == "finish":
            # Deterministischer Finish-Check: in der Aufgabe genannte Dateien
            # muessen existieren, geschriebene muessen valide sein. Sonst wird
            # das finish zurueckgewiesen (max. MAX_FINISH_REJECTS mal), damit
            # ein "Prosa-fertig" ohne geschriebene Dateien nicht durchrutscht.
            missing = [p for p in EXPECTED_FILES if not os.path.isfile(p)]
            still_bad = [p for p in sorted(set(TOUCHED))
                         if os.path.isfile(p) and validate_path(p)[0] == "bad"]
            if (missing or still_bad) and finish_rejects < MAX_FINISH_REJECTS:
                finish_rejects += 1
                parts = []
                if missing:
                    parts.append("diese in der Aufgabe genannten Dateien fehlen: "
                                 + ", ".join(missing))
                if still_bad:
                    parts.append("diese geschriebenen Dateien sind ungueltig: "
                                 + ", ".join(still_bad))
                obs = ("FINISH ABGELEHNT — " + "; ".join(parts) +
                       ". Erstelle/korrigiere NUR diese Datei(en) (write_files mit "
                       f"max. {MAX_WRITE_FILES_BATCH} Dateien pro Block bzw. edit_file) "
                       "und gib erst dann wieder finish aus.")
                print(f"{C.RED}⚠ {obs.splitlines()[0][:120]}{C.RESET}")
                messages.append({"role": "user", "content": obs})
                continue
            # Check-Modus: finish erst nach echter Ausfuehrung. Ein Modell, das
            # nie gestartet/getestet hat, kann API-Halluzinationen und
            # Feldnamen-Fehler nicht bemerkt haben.
            if CHECK and not RAN_SINCE_WRITE and finish_rejects < MAX_FINISH_REJECTS:
                finish_rejects += 1
                if CHECK_PLAN:
                    obs = ("FINISH ABGELEHNT (Check-Modus) — du hast deine Arbeit seit "
                           "der letzten Aenderung nicht ausgefuehrt. Das sind DEINE "
                           "EIGENEN Pruefschritte aus deinem Plan:\n" + CHECK_PLAN +
                           "\nHast du WIRKLICH JEDEN davon ausgefuehrt (nicht nur einen "
                           "Teil, z.B. nur das Backend)? Fuehre alle fehlenden jetzt "
                           "nach, behebe was dabei auffaellt, und gib erst dann wieder "
                           "finish aus.")
                else:
                    obs = ("FINISH ABGELEHNT (Check-Modus) — du hast deine Arbeit seit "
                           "der letzten Aenderung nicht ausgefuehrt. Pruefe sie jetzt "
                           "real mit run: 1) Abhaengigkeiten installieren, 2) Syntax/"
                           "Build pruefen, 3) Dienste mit \"background\":true starten "
                           "und per curl testen (auch Fehlerfaelle wie unbekannte IDs), "
                           "4) Fehler beheben. Gib erst dann wieder finish aus.")
                print(f"{C.RED}⚠ {obs.splitlines()[0][:120]}{C.RESET}")
                messages.append({"role": "user", "content": obs})
                continue
            if missing or still_bad:
                print(f"{C.RED}Achtung: finish trotz offener Probleme akzeptiert "
                      f"(fehlend: {len(missing)}, ungueltig: {len(still_bad)}).{C.RESET}")
            else:
                CLEAN_FINISH = True  # nur OHNE offene Probleme gilt der Lauf als "sauber"
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

        # Check-Modus-Buchhaltung: nur ein VORDERGRUND-run mit exit=0 zaehlt als
        # Pruefung (ein gestarteter Server allein beweist nichts — der folgende
        # curl-Test ist dann der Vordergrund-run).
        if name == "run" and ok and result.startswith("exit=0"):
            RAN_SINCE_WRITE = True

        # Geschriebene Dateien fuer Rollback merken und (bekannte Typen) validieren.
        valed = ""
        if ok and name in ("write_file", "write_files", "edit_file"):
            RAN_SINCE_WRITE = False
            paths = written_paths(name, action)
            for p in paths:
                if p not in TOUCHED:
                    TOUCHED.append(p)
            valed = validate_written(paths)
            if valed:
                print(f"{C.RED}⚠ {valed.splitlines()[0]}{C.RESET}")

        obs = f"[Ergebnis von {name}]\n{result}"
        if valed:
            obs += "\n" + valed
        messages.append({"role": "user", "content": obs})

    print(f"{C.RED}Schrittlimit ({MAX_STEPS}) erreicht.{C.RESET}")
    return None


def main():
    global AUTO_YES, BASE_URL, PROXY, CA_BUNDLE, INSECURE, VERBOSE, MAX_STEPS, VALIDATE, GIT_ROLLBACK, KEEP_CONTEXT, PRUNE, FENCE, CHECK
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
                    help="Erst einen Plan zeigen und bestaetigen lassen, dann umsetzen. "
                         "Zusammen mit --yes: Plan wird automatisch akzeptiert (keine "
                         "Rueckfrage moeglich), dann laeuft alles Weitere unbeaufsichtigt")
    ap.add_argument("--dir", "-C", metavar="PFAD",
                    help="Zielverzeichnis, in dem gearbeitet wird (statt des aktuellen). "
                         "So kann mc.py getrennt vom bearbeiteten Projekt liegen.")
    ap.add_argument("--file", action="append", default=[], metavar="PFAD",
                    help="Datei(en) gleich in den Kontext laden (mehrfach angebbar), "
                         "z.B. --file=index.html — der Agent 'sieht' sie dann sofort")
    ap.add_argument("--no-validate", action="store_true",
                    help="Validierung geschriebener Dateien (py/json/yaml/php) abschalten")
    ap.add_argument("--keep-context", type=int, default=KEEP_CONTEXT, metavar="N",
                    help=f"So viele letzte Schritte bleiben vollstaendig im Kontext "
                         f"(default {KEEP_CONTEXT}); aeltere Tool-Ausgaben und "
                         f"Schreib-Bloecke werden gekuerzt — spart Tokens und Zeit")
    ap.add_argument("--no-prune", action="store_true",
                    help="Kontext-Beschneidung abschalten (volle Historie senden)")
    ap.add_argument("--fence", action="store_true",
                    help="Fence-Modus: Dateiinhalte als rohe ```content Bloecke statt "
                         "als JSON-Strings (vermeidet Escaping-Fehler); der Parser "
                         "versteht unabhaengig davon immer beide Formate")
    ap.add_argument("--check", action="store_true",
                    help="Selbsttest-Modus: finish wird erst akzeptiert, wenn das "
                         "Modell seine Arbeit per run real ausgefuehrt/geprueft hat "
                         "(Dependencies, Build, Dienst starten + curl-Tests). "
                         "Tipp: --max-steps erhoehen, jede Fix-Runde kostet Schritte")
    ap.add_argument("--yes", action="store_true", help="Alle Aktionen ohne Rueckfrage ausfuehren")
    args = ap.parse_args()
    AUTO_YES = args.yes
    MAX_STEPS = args.max_steps
    VALIDATE = not args.no_validate
    CHECK = CHECK or args.check
    KEEP_CONTEXT = args.keep_context
    PRUNE = not args.no_prune
    FENCE = FENCE or args.fence
    # Plan-Phase: opt-in per --plan (mit --yes nicht sinnvoll, daher aus).
    # --plan funktioniert jetzt auch zusammen mit --yes: plan_phase() nutzt
    # input() direkt (nicht confirm()) und behandelt EOF bereits als "Plan
    # akzeptiert, weiter" — im nicht-interaktiven Batch-Betrieb (nohup, kein
    # stdin) laeuft der Plan also automatisch durch, statt komplett zu entfallen.
    plan_mode = args.plan
    BASE_URL = args.base_url.rstrip("/")
    PROXY = args.proxy
    CA_BUNDLE = args.ca_bundle
    INSECURE = args.insecure
    VERBOSE = VERBOSE or args.verbose

    # Ins Zielverzeichnis wechseln, damit mc.py raeumlich getrennt vom Projekt
    # liegen kann. Alles Weitere (Projektueberblick, find, Schreiben, Git) bezieht
    # sich dann auf dieses Verzeichnis. --file-Pfade werden VOR dem Wechsel relativ
    # zum Aufrufort aufgeloest, damit auch eine Datei von ausserhalb mitgegeben
    # werden kann.
    args.file = [os.path.abspath(f) for f in args.file]
    if args.dir:
        try:
            os.chdir(args.dir)
        except OSError as e:
            raise SystemExit(f"{C.RED}--dir: {args.dir} nicht nutzbar: {e}{C.RESET}")

    if args.debug_net:
        debug_net()
        return

    if args.list_models:
        models = list_models()
        print(f"{C.CYAN}Modelle @ {BASE_URL}:{C.RESET}")
        width = min(max((len(mid) for mid, _ in models), default=0), 60)
        for mid, price in models:
            if price == "gratis":
                tag = f"  {C.GREEN}gratis{C.RESET}"
            elif price:
                tag = f"  {C.DIM}{price}{C.RESET}"
            else:
                tag = ""
            print(f"  {mid:<{width}}{tag}")
        free = sum(1 for _, i in models if i == "gratis")
        if free:
            print(f"{C.DIM}({free} davon gratis){C.RESET}")
        return

    banner(f"mc · Mini Coding Tool  ({args.model} @ {BASE_URL})")
    if AUTO_YES:
        print(f"{C.RED}Achtung: --yes aktiv, Aktionen werden ohne Rueckfrage ausgefuehrt.{C.RESET}")
    import atexit
    atexit.register(kill_bg_procs)
    if CHECK:
        info("Check-Modus aktiv: finish erst nach echter Ausfuehrung (run mit exit=0).")
    info(f"Arbeitsverzeichnis: {os.getcwd()}")

    # Git-Sicherung: unabhaengig von --yes pruefen (frueher nur interaktiv, damit
    # war bei --yes-Laeufen JEDE Git-Absicherung aus — genau die Laeufe, die sie
    # am noetigsten haben). Nur moeglich, wenn git installiert + sauberer Baum.
    # Gibt es noch KEIN Repo (z.B. ein frisches, separates Projektverzeichnis),
    # wird eins mit einem Ausgangs-Commit angelegt — sonst waere die Absicherung
    # ausgerechnet in diesem, dem naheliegendsten Fall, nutzlos.
    ok, why = git_usable()
    if not ok and why == "kein Git-Repository":
        init_ok, init_why = git_auto_init()
        if init_ok:
            info("Kein Git-Repository vorgefunden — eines angelegt (Ausgangszustand "
                 "committet, .gitignore ergaenzt falls noetig).")
            ok, why = git_usable()
        else:
            info(f"Automatisches 'git init' fehlgeschlagen ({init_why}).")
    GIT_ROLLBACK = ok
    if ok:
        info("Git verfuegbar: sauberer finish wird committet, unfertiger Stand "
             "kann verworfen werden.")
    else:
        info(f"Git-Absicherung nicht verfuegbar ({why}) — Aenderungen sind endgueltig.")
    if VALIDATE:
        info("Validierung aktiv: py/json/yaml/php werden nach dem Schreiben geprueft.")
    if PRUNE:
        info(f"Kontext-Beschneidung aktiv: letzte {KEEP_CONTEXT} Schritte bleiben "
             f"vollstaendig, aeltere werden gekuerzt (--no-prune schaltet ab).")
    if FENCE:
        info("Fence-Modus aktiv: Dateiinhalte als rohe ```content Bloecke "
             "(kein JSON-Escaping).")

    # Projektueberblick als Kontext: damit der Agent vorhandene Dateien kennt und
    # bei ungenauer Benennung die richtige trifft, statt eine neue anzulegen.
    overview = project_overview()
    listing = "\n".join(overview) if overview else "(keine Dateien)"
    context_msg = (
        f"Arbeitsverzeichnis: {os.getcwd()}\n"
        f"Vorhandene Dateien (rekursiv):\n{listing}\n\n"
        f"Wenn der Nutzer eine Datei ungenau benennt, ordne sie einer dieser Dateien "
        f"zu (find hilft beim unscharfen Suchen), statt blind eine neue anzulegen.")

    # Mit --file uebergebene Dateien direkt in den Kontext legen (der Agent 'sieht'
    # sie sofort, ohne erst read_file aufrufen zu muessen).
    for fp in args.file:
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                fcontent = fh.read()
            info(f"Datei in Kontext geladen: {fp} ({len(fcontent)} Zeichen)")
            context_msg += (f"\n\n--- Mitgelieferte Datei: {fp} "
                            f"({len(fcontent)} Zeichen) ---\n{truncate(fcontent)}")
        except Exception as e:
            print(f"{C.RED}--file: {fp} konnte nicht gelesen werden: {e}{C.RESET}")
    # System-Prompt und Projektueberblick in EINER system-Message buendeln.
    # Manche Chat-Templates (z.B. Ornith-GGUF) brechen bei zwei aufeinander-
    # folgenden system-Rollen sofort leer ab — eine kombinierte ist universell
    # vertraeglicher.
    messages = [{"role": "system", "content": system_prompt(FENCE) + "\n\n" + context_msg}]

    def after_run(summary=""):
        """Am Ende einer Aufgabe: noch ungueltige Dateien melden, dann je nach
        Ausgang sichern. Sauberer finish -> committen (Sicherungspunkt, auch
        unbeaufsichtigt bei --yes). Schrittlimit/offene Probleme -> wie bisher
        Rollback anbieten (interaktiv) bzw. bei --yes unangetastet lassen —
        automatisches VERWERFEN ohne Rueckfrage waere riskanter als das
        automatische SICHERN eines sauberen Ergebnisses."""
        print_usage_summary()
        if not TOUCHED:
            return
        still_bad = [p for p in set(TOUCHED) if os.path.isfile(p)
                     and validate_path(p)[0] == "bad"]
        if still_bad:
            print(f"{C.RED}Achtung: {len(still_bad)} Datei(en) sind weiterhin "
                  f"ungueltig:{C.RESET} " + ", ".join(still_bad))
        if GIT_ROLLBACK and CLEAN_FINISH and not still_bad:
            if AUTO_YES:
                git_commit_run(summary or "Fertig.")
            elif confirm("Sauberer Abschluss — Aenderungen per Git committen?"):
                git_commit_run(summary or "Fertig.")
        elif GIT_ROLLBACK and not AUTO_YES:
            frage = ("Es sind ungueltige Dateien uebrig. Alle Aenderungen dieses Laufs "
                     "per Git verwerfen?" if still_bad
                     else "Lauf nicht sauber abgeschlossen. Alle Aenderungen per Git "
                          "verwerfen (Rollback)?")
            if confirm(frage):
                git_rollback()
        TOUCHED.clear()

    # Einmal-Modus
    if args.task:
        task_text = " ".join(args.task)
        EXPECTED_FILES[:] = expected_files_from_task(task_text)
        if EXPECTED_FILES:
            info(f"Finish-Check aktiv: {len(EXPECTED_FILES)} in der Aufgabe "
                 f"genannte Datei(en) werden am Ende geprueft.")
        messages.append({"role": "user", "content": task_text})
        if plan_mode and not plan_phase(messages, args.model):
            return
        result = run_task(messages, args.model)
        after_run(result if isinstance(result, str) else "")
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
        EXPECTED_FILES[:] = expected_files_from_task(user)
        messages.append({"role": "user", "content": user})
        if plan_mode and not plan_phase(messages, args.model):
            continue
        result = run_task(messages, args.model)
        after_run(result if isinstance(result, str) else "")


if __name__ == "__main__":
    AUTO_YES = False
    main()
