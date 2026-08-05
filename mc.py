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
  python3 mc.py --model qwen3-coder:30b
  python3 mc.py --yes                # alle Aktionen ohne Rueckfrage (Vorsicht!)

Env-Variablen:
  MC_BASE_URL  (default http://localhost:1234/v1 — lokales LM Studio)
  MC_MODEL     (default gemma-4-26b-a4b-it@mxfp4)
  MC_API_KEY   (optional, falls der Endpoint einen Key verlangt)

Konfig-Datei (fuer den Alltag): ~/.mc.json bzw. MC_CONFIG=<pfad> — Schluessel
base_url, model, api_key, headers, proxy, ca_bundle, check, analyse, fence,
verbose, max_steps, keep_context. Rangfolge: CLI-Flag > Env > Konfig > Default.
"""

import argparse
import ast
import difflib
import unicodedata
import tempfile
import json
import os
import re
import shutil
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

try:
    import mc_terminal  # optionaler Terminal-Komfort (Slash-Kommandos, /skills)
except ImportError:
    mc_terminal = None  # Ein-Datei-Betrieb: mc.py laeuft auch ohne das Modul

def _load_config():
    """Laedt eine optionale Konfig-Datei: ~/.mc.json (oder MC_CONFIG=<pfad>).
    Fuer Menschen gedacht, die das Tool taeglich benutzen: statt vor jedem
    Aufruf Env-Variablen zu setzen (unter Windows besonders laestig), stehen
    base_url, model, headers & Co. einmal in der Datei — der Aufruf ist dann
    nur noch 'python mc.py "aufgabe"'. Rangfolge ueberall:
    CLI-Flag > Env-Variable > Konfig-Datei > eingebauter Default."""
    path = os.environ.get("MC_CONFIG") or os.path.join(
        os.path.expanduser("~"), ".mc.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            raise ValueError("Inhalt ist kein JSON-Objekt")
        cfg["_path"] = path
        return cfg
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"Warnung: Konfig-Datei {path} unlesbar ({e}) — wird ignoriert.",
              file=sys.stderr)
        return {}


CONFIG = _load_config()


def _truthy(val):
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() not in ("", "0", "false", "no", "nein")


def _setting(env, key, default):
    """Ein Einstellwert: Env-Variable schlaegt Konfig-Datei schlaegt Default."""
    v = os.environ.get(env)
    if v:
        return v
    if key in CONFIG:
        return CONFIG[key]
    return default


BASE_URL = str(_setting("MC_BASE_URL", "base_url", "http://localhost:1234/v1")).rstrip("/")
DEFAULT_MODEL = str(_setting("MC_MODEL", "model", "gemma-4-26b-a4b-it@mxfp4"))
API_KEY = str(_setting("MC_API_KEY", "api_key", ""))
# Zusaetzliche HTTP-Header pro Request, z.B. MC_HEADERS="X-Foo: bar; X-Baz: qux"
# (mehrere durch ';' oder Zeilenumbruch getrennt, je 'Name: Wert'). In der
# Konfig-Datei alternativ als Objekt: "headers": {"X-Foo": "bar"}.
EXTRA_HEADERS_RAW = os.environ.get("MC_HEADERS", "")

# Netzwerk: in Firmenumgebungen (z.B. Zscaler) muss der Traffic durch einen Proxy,
# und das TLS wird oft mit einem eigenen CA-Zertifikat aufgebrochen.
PROXY = str(_setting("MC_PROXY", "proxy", ""))       # z.B. http://proxy:8080
CA_BUNDLE = str(_setting("MC_CA_BUNDLE", "ca_bundle", ""))  # Pfad zur Zscaler-CA (.pem)
INSECURE = False                                     # TLS-Pruefung abschalten (Notnagel)
VERBOSE = _truthy(_setting("MC_VERBOSE", "verbose", False))  # passive Logausgaben

MAX_STEPS = int(_setting("MC_MAX_STEPS", "max_steps", 40))  # Sicherheitslimit pro Aufgabe
MAX_OUTPUT_CHARS = 8000  # Trunkierung von Tool-Ausgaben an das Modell
_SPILL_N = 0             # Zaehler fuer Spill-Dateien gekuerzter Ausgaben

# Validierung geschriebener Dateien (bekannte Typen) + Git-Rollback.
VALIDATE = True            # nach dem Schreiben bekannte Dateitypen pruefen
GIT_ROLLBACK = False       # nur True, wenn git installiert + sauberes Repo (in main gesetzt)
TOUCHED = []               # von mc geschriebene/geaenderte Pfade (fuer Rollback)
READ_FILES = set()         # in diesem Lauf per read_file gelesene Pfade (normpath)
EXPLORED = False           # wurde in diesem Lauf schon in den Bestand geschaut?
HAS_CODE = None            # Cache: hat das Projekt Bestandscode? (pro Lauf)
PLAN_POINTS = []           # Aenderungsplan aus der Analyse-Phase (--analyse)
SYSTEM_CONTEXT = ""        # Projektueberblick-Teil der System-Message (fuer Phasenwechsel)
CLEAN_FINISH = False       # True nur bei explizitem finish (nicht Schrittlimit/Prosa-Ende)
WRITE_HISTORY = {}         # Pfad -> (letzter Inhalt, Anzahl fast identischer Wiederholungen)
LOSS_WARNED_NAMES = set()  # (Pfad, Name) -- vom Verlust-Waechter in DIESEM Lauf schon gemeldet
MAX_FIX_ATTEMPTS = 3       # so oft darf das Modell eine ungueltige Datei nachbessern

# Robustheit (aus dem GPU-Benchmark gelernt):
# - Grosse write_files-Bloecke sind das Haupt-Truncation-Risiko -> Limit wird
#   vom TOOL erzwungen, nicht nur im Prompt erbeten.
# - Modelle erklaeren sich nach einem verworfenen Schritt gern in Prosa fuer
#   "fertig" -> finish wird gegen die in der Aufgabe genannten Dateien geprueft.
MAX_WRITE_FILES_BATCH = 3  # max. Dateien pro write_files-Block
MAX_READ_FILES_BATCH = 5   # max. Dateien pro read_files-Schritt (Lesen ist
                           # gefahrlos — der Batch spart Umlaeufe, und die
                           # Prompt-Tokens dominieren die Kosten mit >90%)
EXPLORE_STEPS = 10         # Schrittlimit fuer isolierte Erkundungs-Laeufe
MC_PLAN = "mc_plan.md"     # Aenderungsplan der Analyse-Phase (ueberlebt Abbrueche)
MC_VERLAUF = "mc_verlauf.json"  # Sitzungs-Verlauf fuer --resume
RESUME = False             # --resume: Verlauf sichern und fortsetzen
MAX_FINISH_REJECTS = 2     # so oft wird ein verfruehtes finish zurueckgewiesen
EXPECTED_FILES = []        # aus der Aufgabe extrahierte Dateipfade (Finish-Check)

# Check-Modus (--check): finish wird erst akzeptiert, wenn das Modell seine
# Arbeit nach der letzten Aenderung per run WIRKLICH ausgefuehrt hat (exit=0).
# Hintergrund: Syntax-Validierung findet keine falschen API-Annahmen,
# Feldnamen-Verwechslungen oder kaputte Dependencies — echte Ausfuehrung schon.
CHECK = _truthy(_setting("MC_CHECK", "check", False))
# Analyse-Phase (--analyse): bei Aufgaben an BESTEHENDEM Code arbeitet der
# Agent zweistufig — erst nur lesen/suchen und einen nummerierten
# Aenderungsplan ausgeben (plan-Aktion), erst DANACH werden Schreibaktionen
# freigeschaltet. Gegen den Neubau-Reflex kleiner Modelle: Verstehen wird
# nicht erbeten, sondern erzwungen — Schreibaktionen stehen in Phase 1 gar
# nicht erst im Protokoll.
ANALYSE = _truthy(_setting("MC_ANALYSE", "analyse", False))
RAN_SINCE_WRITE = False    # seit letztem Schreiben ein run mit exit=0?
BG_PROCS = []              # Hintergrundprozesse (Dev-Server); Ende: aufgeraeumt
# Selbst genanntes Pruefprogramm aus der Plan-Phase (--plan --check): wird bei
# einem verfruehten finish woertlich zurueckgespielt, statt nur generisch an
# "irgendwas ausfuehren" zu erinnern — das Modell soll an seinem EIGENEN
# Versprechen gemessen werden, nicht an einer abstrakten Regel.
CHECK_PLAN = ""
# Notbremse fuer run mit --yes: offensichtlich destruktive Kommandos ablehnen —
# inkl. der Windows-Pendants (del /s, rmdir /s, format, reg delete, diskpart),
# die vorher komplett durchgerutscht waeren.
DANGEROUS_RUN = re.compile(
    r"\b(sudo|shutdown|reboot|halt|mkfs\S*|diskpart)\b"
    r"|rm\s+(-\w+\s+)*(/|~)(\s|$)"
    r"|dd\s+.*of=/dev/"
    r"|\b(del|erase)\s+(/\w\s+)*/[sq]\b"
    r"|\b(rmdir|rd)\s+(/\w\s+)*/s\b"
    r"|\bformat\s+[a-z]:"
    r"|\breg\s+delete\b", re.IGNORECASE)
SHELL_BG = re.compile(r"(?<!&)&\s*$")  # trailiges einzelnes '&' (nicht '&&')
# Port-belegt-Fehler aller gaengigen Plattformen/Runtimes: der haeufigste Grund
# ist der EIGENE, frueher gestartete Hintergrundprozess. Ohne Hinweis wechseln
# Modelle dann den Port (real beobachtet: 5010 -> 5050 -> 8888 -> 8000) und
# hinterlassen eine App, deren Frontend ins Leere zeigt.
ADDR_IN_USE = re.compile(
    r"address already in use|EADDRINUSE|WinError\s+10048|Errno\s+(48|98)",
    re.IGNORECASE)
# Projekt-Generatoren (Scaffolder): fragen interaktiv nach 'Overwrite?', wenn das
# Zielverzeichnis schon existiert — und haengen dann bis zum Timeout.
GENERATOR_RE = re.compile(
    r"\b(npm\s+create|npx\s+create-|yarn\s+create|pnpm\s+create|npm\s+init\s+\S)",
    re.IGNORECASE)
FETCH_URL_RE = re.compile(r"\b(curl|wget)\b[^\n]*https?://", re.IGNORECASE)
FETCH_ANALYSIS_MAX_CHARS = 20000  # Fallback-Wert, falls das GELADENE
# Kontextfenster nicht abfragbar ist (siehe loaded_context_chars): viele
# lokale Server laden Modelle mit kleinerem Kontextfenster als deren
# theoretisches Maximum (z.B. 8192 statt 262144 Token) - bei Ueberschreitung
# kommt keine Fehlermeldung, sondern eine LEERE Antwort.
# summarize_large_fetch() faengt das zusaetzlich mit einem automatischen
# Rueckfall auf die Haelfte ab.
CURRENT_MODEL = ""  # von run_task() gesetzt, fuer isolierte Sub-Calls in do_run()
_LOADED_CTX_CACHE = {}  # model -> ermitteltes Zeichen-Limit (einmal pro Lauf abgefragt)
_LOADED_CTX_TOKENS = {}  # model -> geladene Kontext-Tokens (0 = nicht abfragbar)

# Kontext-Beschneidung: die Message-Historie waechst pro Schritt, weil jede
# Tool-Ausgabe und jeder write-Block (mit komplettem Dateiinhalt!) dauerhaft
# mitgeschickt wird. Auf lokalen Maschinen ist Prompt-Processing der
# Flaschenhals -> aeltere Schritte werden auf Kurzfassungen reduziert; die
# Dateien liegen ja auf der Platte und sind per read_file/grep erreichbar.
# ABER cache-freundlich (siehe maybe_prune): LM Studio & Co. verarbeiten einen
# Request, der den vorigen als Praefix enthaelt, fast ohne Prompt-Processing
# (KV-Cache) — deshalb wird NICHT mehr vor jedem Schritt gekuerzt, sondern
# erst, wenn die Historie die Schwelle des GELADENEN Kontextfensters reisst.
KEEP_CONTEXT = int(_setting("MC_KEEP_CONTEXT", "keep_context", 3))  # letzte N Schritte bleiben voll
# Kontextfenster fuer /model-reset (explizites Neuladen bei LM Studio/Ollama).
# 0 = kein Wert wird mitgeschickt (Engine-eigener Default bleibt bestehen).
# Hintergrund: JIT-Reload nach Entladen greift oft zu einem KLEINEN Default
# (real beobachtet: 8192) statt der zuvor manuell gesetzten Fenstergroesse.
CONTEXT_LENGTH = int(_setting("MC_CONTEXT_LENGTH", "context_length", 32768))
PRUNE = True               # Kontext-Beschneidung an (--no-prune schaltet ab)
CHARS_PER_TOKEN = 1.8      # konservative Umrechnung Zeichen/Token (Kalibrierung
                           # siehe loaded_context_chars: deutsche Prosa + Code-Mix)
PRUNE_CTX_FRACTION = 0.7   # Kuerzung erst, wenn die Historie diesen Anteil des
                           # geladenen Kontextfensters ueberschreitet (Reserve
                           # fuer Antwort + Ungenauigkeit der Umrechnung)

# Fence-Modus: Dateiinhalte (und edit_file-old/new) als rohe ```-Bloecke statt
# als escapte JSON-Strings. Seit den Weiterentwicklungs-Tests DEFAULT AN —
# die JSON-Fehlerrate der betroffenen Laeufe fiel damit auf 0 (--no-fence /
# MC_FENCE=0 schaltet zurueck). Betrifft nur, was der System-Prompt dem
# Modell beibringt — der Parser versteht IMMER beide Formate.
FENCE = _truthy(_setting("MC_FENCE", "fence", True))

# Manche Modelle (z.B. "Thinking"-Varianten wie gemma4 ueber vMLX) senden vor
# der eigentlichen Antwort einen oft langen reasoning_content-Trace. mc.py
# nutzt/zeigt den nicht, und ein knapp bemessenes Antwort-Token-Budget kann
# dabei komplett beim Nachdenken aufgebraucht werden, BEVOR ueberhaupt
# sichtbarer content entsteht (real beobachtet: 700 Reasoning-Chunks, 0
# Content-Chunks). THINK=False haengt dem Request zusaetzliche, breit
# vertraegliche Hinweise an, das Nachdenken abzuschalten (enable_thinking,
# chat_template_kwargs.enable_thinking, reasoning_effort=none) -- Endpunkte,
# die keinen dieser Namen kennen, ignorieren sie folgenlos (getestet u.a.
# gegen ein Nicht-Reasoning-Modell via OpenRouter: keine Fehler).
THINK = _truthy(_setting("MC_THINK", "think", True))

# Modus im interaktiven Terminal: 'dev' (Standard) haengt den vollen
# Werkzeug-/Aktions-Prompt samt Projekt-Steckbrief an, 'chat' schaltet auf
# reine Unterhaltung OHNE Dev-Prompt um (/mode dev|chat, ohne Argument zeigt
# den aktuellen Modus). Sinnvoll fuer Smalltalk/Rueckfragen, die keine
# Aktionen brauchen -- kein Grund, dafuer immer den vollen ~4000-Token-
# System-Prompt mitzuschicken.
MODE = "dev"
CHAT_SYSTEM_PROMPT = (
    "Du bist ein hilfreicher Gespraechspartner. Antworte direkt in normaler "
    "Sprache -- kein JSON, keine Aktionen, keine Werkzeuge. Falls eine "
    "Programmier-/Bearbeitungsaufgabe gewuenscht wird, weise darauf hin, "
    "dass dafuer im Terminal '/mode dev' aktiviert werden sollte.")

# Token-/Kostenzaehler ueber die ganze Sitzung (Kosten nur, wenn der Endpoint sie
# liefert, z.B. OpenRouter via usage.cost).
USAGE = {"prompt": 0, "completion": 0, "cost": 0.0, "reqs": 0}

# Zeichenlaenge des reasoning_content-Traces der LETZTEN Antwort (0, wenn
# keiner kam oder das Modell kein Reasoning sendet). Dient der Diagnose bei
# leerer Antwort: reines Kontext-Problem vs. Budget beim Nachdenken
# aufgebraucht, BEVOR content entstand -- zwei verschiedene Ursachen mit
# unterschiedlichem Gegenmittel.
LAST_REASONING_CHARS = 0


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


_ANSI_RE = re.compile(r"(\033\[[0-9;]*m)")


def rl_prompt(s):
    """Markiert ANSI-Farbcodes in einem input()-Prompt fuer readline als
    breitenlos (\\001..\\002) -- sonst verzaehlt sich readline bei der
    Cursorposition (readline ist aktiv, sobald mc_terminal das Modul
    importiert hat) und Redraws bei Zeilenumbruch, Paste oder History-
    Navigation (Pfeil hoch/runter) zeigen Reste der vorherigen Zeile."""
    return _ANSI_RE.sub(lambda m: "\001" + m.group(1) + "\002", s)


if sys.platform == "win32" and sys.stdout.isatty():
    os.system("")  # aktiviert die VT-Escape-Verarbeitung in cmd.exe/PowerShell

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
    """Header aus Konfig-Datei ('headers' als Objekt ODER String) und MC_HEADERS
    ('Name: Wert' je Eintrag, getrennt durch ';' oder Zeilenumbruch) in ein Dict,
    das jedem Request beigefuegt wird. Env-Eintraege ueberschreiben die Konfig."""
    out = {}
    cfg = CONFIG.get("headers")
    if isinstance(cfg, dict):
        out.update({str(k): str(v) for k, v in cfg.items()})
    raw = (cfg if isinstance(cfg, str) else "")
    raw = (raw + "\n" + EXTRA_HEADERS_RAW) if raw else EXTRA_HEADERS_RAW
    for part in re.split(r"[;\n]", raw):
        part = part.strip()
        if not part or ":" not in part:
            continue
        name, val = part.split(":", 1)
        name = name.strip()
        if name:
            out[name] = val.strip()
    return out


MAX_CONTINUATIONS = 4  # max. automatische Fortsetzungen bei abgeschnittener Antwort

# Runaway-Schutz: Modelle koennen mitten in EINER Antwort kollabieren (real
# beobachtet bei einem Cloud-Reasoning-Modell: erst halluzinierte Fehlermeldungs-
# Prosa samt erfundener Ticket-Nummer, dann Zeichenmuell — 166k Completion-
# Tokens, $0.83, und die Auto-Continuation bat den Muell auch noch hoeflich um
# Fortsetzung). Erkennung ueber drei Signale + harte Laengenbremse.
MAX_REPLY_CHARS = int(_setting("MC_MAX_REPLY_CHARS", "max_reply_chars", 40000))
DEGEN_CHAR_RE = re.compile(r"(.)\1{119,}", re.DOTALL)     # 120x dasselbe Zeichen
DEGEN_WORD_RE = re.compile(r"(\b\w{1,20})(?:[ \t]+\1\b){19,}")  # 20x dasselbe Wort
DEGEN_MARKER = "\n[mc: Antwort abgebrochen — Endlos-Ausgabe erkannt]"
TRUNC_MARKER = "\n[mc: Antwort blieb trotz Fortsetzungen unvollstaendig]"


def _looks_runaway(text):
    """True, wenn die Antwort erkennbar ausser Kontrolle ist. Drittes Signal:
    eine sehr lange Strecke ohne Zeilenumbruch AUSSERHALB eines offenen
    Code-Fences — gesunder Code/Prosa bricht um, einzeiliger JSON-Dateiinhalt
    steckt in einem (dann offenen) Fence und ist ausgenommen."""
    tail = text[-6000:]
    if DEGEN_CHAR_RE.search(tail) or DEGEN_WORD_RE.search(tail):
        return True
    tail = text[-2500:]
    if len(tail) == 2500 and "\n" not in tail and text.count("```") % 2 == 0:
        return True
    return False


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
    payload = {"model": model, "messages": _payload_messages(messages), "stream": True,
               # Token-/Kostenabrechnung anfordern (OpenAI-Standard + OpenRouter).
               # Endpoints, die das nicht kennen (z.B. Ollama), ignorieren es.
               "stream_options": {"include_usage": True},
               "usage": {"include": True},
               # Milde Anti-Wiederholungs-Bremse: beobachtet wurde, dass lokale
               # Modelle mitten in EINER Antwort in eine Token-Wiederholung
               # geraten koennen (z.B. ein JSON-Feld dutzendfach identisch
               # wiederholt), bevor ueberhaupt ein parsebarer Action-Block
               # entsteht — das faengt _check_repetition() nicht ab, die greift
               # erst NACH einem erfolgreich geparsten write. frequency_penalty
               # ist Standard-OpenAI-Feld, wird von inkompatiblen Endpoints
               # (z.B. reines Ollama) einfach ignoriert.
               "frequency_penalty": 0.3}
    if not THINK:
        payload["reasoning_effort"] = "none"
        payload["enable_thinking"] = False
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    headers.update(extra_headers())

    global LAST_REASONING_CHARS
    LAST_REASONING_CHARS = 0
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
                    delta = choices[0].get("delta", {})
                    reasoning_token = delta.get("reasoning_content")
                    if reasoning_token and first:
                        # Noch kein sichtbarer content -- Modell "denkt" (z.B.
                        # gemma4 ueber vMLX). Nicht Teil der Antwort, aber als
                        # Fortschritt anzeigen und fuer die Leere-Antwort-
                        # Diagnose zaehlen (siehe LAST_REASONING_CHARS oben).
                        LAST_REASONING_CHARS += len(reasoning_token)
                        if spin.active:
                            spin.label = f"Modell denkt (Reasoning: {LAST_REASONING_CHARS} Zeichen)"
                    token = delta.get("content")
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
        body = e.read().decode("utf-8", "replace")[:500]
        ctx = _parse_ctx_overflow(body)
        if ctx is not None:
            raise CtxOverflowError(ctx, body[:200])
        raise SystemExit(f"\n{C.RED}HTTP {e.code} vom Endpoint:{C.RESET} {body[:300]}")
    except NET_ERRORS as e:
        if parts:
            # Mitten im Stream abgerissen: das Vorhandene zurueckgeben —
            # die Truncation-Logik in chat_stream fordert die Fortsetzung an
            # (das eigene finish_reason stellt sicher, dass auch abgerissene
            # Prosa OHNE offenen Fence als unvollstaendig gilt).
            return "".join(parts), "net_abort"
        raise NetRetryError(net_error(getattr(e, "reason", e)))
    finally:
        spin.__exit__()  # Spinner-Thread immer beenden (auch bei Fehler)
    return "".join(parts), finish_reason


class CtxOverflowError(Exception):
    """Der Endpoint meldet einen Kontext-Ueberlauf per HTTP-Fehler. Traegt,
    wenn aus dem Fehlertext parsbar, die TATSAECHLICHE Fenstergroesse in
    Tokens (sonst 0) — damit kalibriert sich die Kuerzungs-Schwelle selbst,
    auch bei Endpoints ohne abfragbares Kontextfenster."""

    def __init__(self, tokens, detail):
        super().__init__(detail)
        self.tokens = tokens


def _parse_ctx_overflow(body):
    """Erkennt Kontext-Ueberlauf-Fehlertexte (llama.cpp, OpenAI-kompatible
    Server) und zieht, wenn moeglich, die Fenstergroesse heraus.
    None = kein Ueberlauf; sonst Tokens (0 = erkannt, Groesse unbekannt).
    Bei mehreren Zahlen (angefragt vs. Limit) ist das LIMIT die kleinste."""
    low = body.lower()
    if "context" not in low and "kontext" not in low:
        return None
    if not any(w in low for w in ("exceed", "too long", "too large", "maximum",
                                  "limit", "length", "ueberschritt", "size")):
        return None
    kand = [int(z) for z in re.findall(r"\d{4,7}", body)
            if 2048 <= int(z) <= 2_000_000]
    return min(kand) if kand else 0


class NetRetryError(Exception):
    """Netzwerkfehler VOR den ersten Antwort-Bytes — transient und gefahrlos
    wiederholbar (es wurde noch nichts verarbeitet). Real beobachtet: ein
    einzelner Read-Timeout beim allerersten Request hat sonst den kompletten
    Lauf beendet, obwohl der Endpoint Sekunden spaeter wieder da war
    (LM Studio laedt z.B. gerade ein Modell)."""


def _chat_once_retry(messages, model, attempts=3):
    for attempt in range(1, attempts + 1):
        try:
            return _chat_once(messages, model)
        except NetRetryError as e:
            if attempt == attempts:
                raise SystemExit(str(e))
            wait = 10 * attempt
            print(f"\n{C.YELLOW}⚠ Netzwerkfehler vor Antwortbeginn (Versuch "
                  f"{attempt}/{attempts}) — neuer Versuch in {wait}s … "
                  f"(Endpoint evtl. kurz ueberlastet/Modell laedt){C.RESET}")
            time.sleep(wait)


def _fix_fence_seams(text):
    """Repariert Zeilenumbruch-Nahtstellen zwischen automatischen Fortsetzungen
    (chat_stream haengt Fortsetzungen per simplem text += more an, OHNE auf
    einen Zeilenumbruch an der Naht zu achten). CONTENT_FENCE_RE verlangt
    aber zwingend einen Zeilenumbruch VOR einer schliessenden ``` -Fence
    (CommonMark-Regel) -- landet die Naht so, dass die schliessende Fence
    direkt an vorherigen Text anschliesst, wird der Block sonst gar nicht
    mehr erkannt (real beobachtet: 'write_file ohne Inhalt' nach mehreren
    Fortsetzungen, obwohl der Inhalt vollstaendig da war). Fuegt fehlende
    Zeilenumbrueche VOR jeder ``` -Sequenz ein, die nicht schon am
    Zeilenanfang steht -- macht nur die Fence-Marker zeilenrein, aendert den
    eigentlichen Dateiinhalt nicht."""
    out, last = [], 0
    for m in re.finditer(r"`{3,}", text):
        start = m.start()
        if start > 0 and text[start - 1] != "\n":
            out.append(text[last:start])
            out.append("\n")
        else:
            out.append(text[last:start])
        last = start
    out.append(text[last:])
    return "".join(out)


def _looks_truncated(text, finish_reason):
    """Heuristik: wurde die Antwort abgeschnitten? Zwei unabhaengige Signale —
    das offizielle finish_reason und ein Strukturcheck auf einen nicht
    geschlossenen ```action```-Block."""
    if finish_reason in ("length", "net_abort"):
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
    global LAST_REASONING_CHARS
    text, fr = _chat_once_retry(messages, model)
    # _chat_once() setzt LAST_REASONING_CHARS bei JEDEM Aufruf zurueck --
    # bei Fortsetzungen (unten) also ueber alle internen Aufrufe AUFSUMMIEREN,
    # sonst geht das Reasoning frueherer Versuche in der Diagnose verloren
    # (genau der Fall, der eine Reasoning-Truncation erst ausloest: Budget
    # beim Nachdenken aufgebraucht -> finish_reason=length -> Fortsetzung).
    reasoning_total = LAST_REASONING_CHARS
    cont = 0
    while _looks_truncated(text, fr) and cont < MAX_CONTINUATIONS:
        if len(text) > MAX_REPLY_CHARS or _looks_runaway(text):
            print(f"\n{C.RED}⚠ Antwort ausser Kontrolle (Endlos-Ausgabe oder "
                  f"> {MAX_REPLY_CHARS} Zeichen) — abgebrochen statt "
                  f"fortgesetzt.{C.RESET}")
            LAST_REASONING_CHARS = reasoning_total
            return text[:MAX_REPLY_CHARS] + DEGEN_MARKER
        cont += 1
        print()
        # Ursache klassifizieren und IMMER anzeigen (nicht nur verbose), damit man
        # erkennt, ob ein Token-Limit oder ein Verbindungs-/Proxy-Abbruch vorliegt.
        if fr == "length":
            grund = "Token-Limit (Ausgabe gekappt)"
        elif fr == "net_abort":
            grund = "Netzwerk/Proxy hat den Stream mittendrin abgerissen"
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
        more, fr = _chat_once_retry(cont_msgs, model)
        reasoning_total += LAST_REASONING_CHARS
        # Nahtstelle reparieren, BEVOR die naechste _looks_truncated()-Pruefung
        # (Schleifenbedingung oben) oder der Rueckgabewert sie sieht -- der
        # Konversationsverlauf (cont_msgs, bereits gesendet) bleibt UNVERAENDERT,
        # das Modell sieht also nach wie vor exakt seine eigene rohe Ausgabe.
        text = _fix_fence_seams(text + more)
    LAST_REASONING_CHARS = reasoning_total
    print()
    if _looks_truncated(text, fr):
        # Fortsetzungen ausgeschoepft, Antwort weiterhin unvollstaendig:
        # markieren, damit der Loop schreibende Aktionen daraus verweigert
        # (halbe Datei sieht als JSON oft komplett aus — Datenverlust-Falle).
        text += TRUNC_MARKER
    if len(text) > MAX_REPLY_CHARS or _looks_runaway(text):
        print(f"{C.RED}⚠ Antwort ausser Kontrolle (Endlos-Ausgabe oder zu lang) "
              f"— gekappt.{C.RESET}")
        return text[:MAX_REPLY_CHARS] + DEGEN_MARKER
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


def _endpoint_root(base_url=None):
    """BASE_URL (z.B. '.../v1') auf den Endpunkt-Root gekuerzt -- LM Studios
    und Ollamas eigene (nicht OpenAI-kompatible) Endpunkte liegen direkt am
    Root, nicht unter /v1."""
    b = (base_url or BASE_URL).rstrip("/")
    if b.endswith("/v1"):
        b = b[:-3]
    return b.rstrip("/")


def _local_engine_headers():
    headers = {}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    headers.update(extra_headers())
    return headers


def _detect_local_engine():
    """Erkennt am Endpunkt-Root, ob LM Studio, Ollama, vMLX oder oMLX
    dahinterstecken -- alle bieten eigene Lade-/Entlade- bzw. Kapazitaets-
    Endpunkte, die ein generischer OpenAI-Client nicht kennt. Gibt
    'lmstudio', 'ollama', 'vmlx', 'omlx' oder None (z.B. OpenRouter/Cloud-
    Endpunkt) zurueck.
    vMLX/oMLX zuerst pruefen: vMLX bildet u.a. AUCH /api/tags nach (Ollama-
    Kompatibilitaet) und wuerde sonst faelschlich als 'ollama' erkannt --
    'owned_by' in /v1/models ('vmlx-engine' bzw. 'omlx') ist das eindeutige
    Merkmal. oMLX verlangt dafuer einen gueltigen API-Key (sonst 401) --
    ohne API_KEY wird es hier schlicht nicht erkannt, faellt also auf
    None/Cloud-Behandlung zurueck (sicherer Default)."""
    root = _endpoint_root()
    headers = _local_engine_headers()

    def _get(path):
        req = urllib.request.Request(root + path, headers=headers, method="GET")
        with build_opener().open(req, timeout=4) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    try:
        obj = _get("/v1/models")
        eigner = {m.get("owned_by") for m in obj.get("data", [])}
        if "vmlx-engine" in eigner:
            return "vmlx"
        if "omlx" in eigner:
            return "omlx"
    except Exception:
        pass
    for path, key, kind in (("/api/v0/models", "data", "lmstudio"),
                            ("/api/tags", "models", "ollama")):
        try:
            obj = _get(path)
            if key in obj:
                return kind
        except Exception:
            continue
    return None


_LOCAL_ENGINE_CACHE = {}


def _is_local_engine():
    """Wie _detect_local_engine(), aber je BASE_URL gecacht -- sonst wuerde
    JEDER Chat-Request eine zusaetzliche Sonden-Anfrage ausloesen."""
    if BASE_URL not in _LOCAL_ENGINE_CACHE:
        _LOCAL_ENGINE_CACHE[BASE_URL] = _detect_local_engine() is not None
    return _LOCAL_ENGINE_CACHE[BASE_URL]


def _payload_messages(messages):
    """Nachrichtenliste fuer den Request-Body. Bei Cloud-Endpunkten (nicht
    LM Studio/Ollama) wird der stabile System-Prompt als Anthropic-
    kompatibler cache_control-Breakpoint markiert -- getestet via OpenRouter:
    ~90% Kostenersparnis auf Wiederholungen bei Claude, von anderen
    Anbietern (OpenAI, Gemini, DeepSeek, Kimi) folgenlos ignoriert oder
    sogar selbst honoriert. Lokale Engines haben ihren eigenen
    Prompt-/KV-Cache und werden bewusst NICHT angefasst (ungetestet, ob sie
    das Array-Format vertragen, und unnoetig). Gibt bei lokalen Endpunkten
    ODER fehlendem System-Prompt die Liste UNVERAENDERT zurueck -- der
    interne String-Zustand von messages[0] (Pruning, Kontobuch, --resume)
    bleibt ueberall sonst im Code unberuehrt, die Umformung passiert nur
    hier am Request-Rand."""
    if not messages or messages[0].get("role") != "system" or _is_local_engine():
        return messages
    kopie = list(messages)
    kopie[0] = {"role": "system", "content": [
        {"type": "text", "text": messages[0]["content"],
         "cache_control": {"type": "ephemeral"}}]}
    return kopie


def reset_model(model, context_length=None):
    """Laedt 'model' am lokal erkannten Endpunkt (LM Studio/Ollama/vMLX/oMLX)
    EXPLIZIT neu, mit fest gesetztem Kontextfenster wo moeglich -- Gegenmittel
    zum JIT-Reload-Default, der beim automatischen Neuladen greift und
    deutlich kleiner sein kann als eine zuvor manuell in der Oberflaeche
    gesetzte Fenstergroesse (real beobachtet: 8192 statt 125873, siehe Blog
    Kapitel 33). Bei vMLX gibt es keinen Lade-Endpunkt (Kontextfenster nur
    per Server-Start-Flag aenderbar), bei oMLX gibt es einen Lade-Endpunkt,
    aber das Setzen des Kontextfensters braucht eine separate Admin-
    Anmeldung, die mc.py nicht hat -- in beiden Faellen meldet die Funktion
    das ehrlich statt es vorzutaeuschen. Gibt (ok, meldung) zurueck."""
    kind = _detect_local_engine()
    if kind is None:
        return False, ("Endpunkt nicht als LM Studio/Ollama/vMLX/oMLX "
                        "erkannt — /model-reset ist nur fuer lokale Engines "
                        "mit eigenem Lade-Endpunkt anwendbar (z.B. nicht bei "
                        "OpenRouter).")
    root = _endpoint_root()
    headers = {"Content-Type": "application/json"}
    headers.update(_local_engine_headers())

    def _post(path, body, timeout=120):
        req = urllib.request.Request(root + path, data=json.dumps(body).encode("utf-8"),
                                      headers=headers, method="POST")
        with build_opener().open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    try:
        if kind == "omlx":
            # oMLX hat (anders als vMLX) einen echten Lade-/Entlade-Endpunkt,
            # aber das Kontextfenster (max_context_window) wird nur ueber
            # eine SEPARATE Admin-Anmeldung gesetzt (PUT /admin/api/models/
            # .../settings, eigener Login -- nicht derselbe API-Key wie
            # /v1/*). mc.py kennt kein Admin-Login, kann also nicht SETZEN --
            # aber entladen/neu laden (uebernimmt z.B. eine im Dashboard
            # bereits gesetzte Aenderung) und den Stand ehrlich melden.
            enc = urllib.parse.quote(model, safe="")
            try:
                _post(f"/v1/models/{enc}/unload", {}, timeout=30)
            except Exception:
                pass  # evtl. schon entladen -- load unten laedt/ersetzt ohnehin
            _post(f"/v1/models/{enc}/load", {}, timeout=180)
            req = urllib.request.Request(root + "/v1/models/status",
                                          headers=headers, method="GET")
            with build_opener().open(req, timeout=10) as resp:
                status_obj = json.loads(resp.read().decode("utf-8", "replace"))
            geladen = None
            for m in status_obj.get("models", []):
                if m.get("id") == model:
                    geladen = m.get("max_context_window")
                    break
            if geladen is None:
                return True, f"{model} neu geladen (oMLX) — Kontextfenster nicht abfragbar."
            if context_length and geladen != int(context_length):
                return True, (f"{model} neu geladen (oMLX) — Kontextfenster: "
                              f"{geladen} (angefordertes {context_length} kann "
                              f"mc.py nicht setzen: dafuer ist eine separate "
                              f"Admin-Anmeldung im oMLX-Dashboard noetig).")
            return True, f"{model} neu geladen (oMLX) — Kontextfenster: {geladen}"
        if kind == "vmlx":
            # vMLX setzt das Kontextfenster (max_prompt_tokens) beim
            # Server-START fest (--max-prompt-tokens) -- anders als LM Studio
            # gibt es KEINEN Lade-Endpunkt, um es zur Laufzeit zu aendern.
            # Ehrlich melden statt so zu tun, als waere neu geladen worden.
            enc = urllib.parse.quote(model, safe="")
            req = urllib.request.Request(root + f"/v1/models/{enc}/capabilities",
                                          headers=headers, method="GET")
            with build_opener().open(req, timeout=10) as resp:
                cap = json.loads(resp.read().decode("utf-8", "replace"))
            aktuell = cap.get("max_prompt_tokens")
            hinweis = f" (aktuell: {aktuell} Token)" if aktuell else ""
            return False, ("vMLX setzt das Kontextfenster (max_prompt_tokens) "
                           "beim Server-Start fest — kein Neuladen mit anderem "
                           f"Wert zur Laufzeit moeglich{hinweis}. Server mit "
                           "--max-prompt-tokens <n> neu starten, um es zu "
                           "aendern.")
        if kind == "lmstudio":
            req = urllib.request.Request(root + "/api/v0/models", headers=headers, method="GET")
            with build_opener().open(req, timeout=10) as resp:
                obj = json.loads(resp.read().decode("utf-8", "replace"))
            for m in obj.get("data", []):
                if m.get("state") == "loaded":
                    try:
                        _post("/api/v1/models/unload", {"instance_id": m.get("id")}, timeout=30)
                    except Exception:
                        pass  # schon entladen oder andere Instance-ID -- load unten ersetzt ohnehin
            body = {"model": model}
            if context_length:
                body["context_length"] = int(context_length)
            _post("/api/v1/models/load", body, timeout=180)
            # WICHTIG: load_config.context_length in der Load-Antwort ist nur
            # ein Echo der ANFRAGE, keine Bestaetigung -- real beobachtet:
            # angefordert 8192/16384/65536, tatsaechlich geladen jedesmal
            # 4352 (Modell-/Hardware-Grenze). Also nach dem Laden per
            # /api/v0/models nachfragen, was WIRKLICH geladen wurde.
            req2 = urllib.request.Request(root + "/api/v0/models", headers=headers, method="GET")
            with build_opener().open(req2, timeout=10) as resp:
                obj2 = json.loads(resp.read().decode("utf-8", "replace"))
            geladen = None
            for m in obj2.get("data", []):
                if m.get("id") == model and m.get("state") == "loaded":
                    geladen = m.get("loaded_context_length")
                    break
            if geladen is None:
                return True, f"{model} neu geladen (LM Studio) — Kontextfenster nicht abfragbar."
            if context_length and geladen < int(context_length):
                return True, (f"{model} neu geladen (LM Studio) — ACHTUNG: "
                              f"{context_length} angefordert, aber nur {geladen} "
                              f"tatsaechlich geladen (Modell-/Hardware-Grenze).")
            return True, f"{model} neu geladen (LM Studio) — Kontextfenster: {geladen}"

        # ollama
        _post("/api/generate", {"model": model, "prompt": "", "keep_alive": 0}, timeout=30)
        body = {"model": model, "prompt": "", "keep_alive": -1, "stream": False}
        if context_length:
            body["options"] = {"num_ctx": int(context_length)}
        _post("/api/generate", body, timeout=180)
        return True, f"{model} neu geladen (Ollama) — Kontextfenster: {context_length or 'Engine-Default'}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code} beim Neuladen: {e.read().decode('utf-8', 'replace')[:300]}"
    except NET_ERRORS as e:
        return False, net_error(getattr(e, "reason", e))


def _suspicious_cwd_warning():
    """Erkennt, ob das Arbeitsverzeichnis ein System-/Temp- oder Home-
    Verzeichnis ist statt ein eigenes Projektverzeichnis. Real beobachtet:
    mc.py in /private/tmp gestartet -- der automatische Projekt-Steckbrief/
    Code-Outline versuchte, daraus einen Kontext zu bauen, und las fremde,
    teils riesige Dateien anderer Prozesse/Nutzer ein (siehe Blog Kapitel
    39/41). Gibt eine Warnmeldung zurueck oder None, wenn unauffaellig."""
    cwd = os.path.realpath(os.getcwd())
    tempdirs = {os.path.realpath(tempfile.gettempdir()),
                "/tmp", "/private/tmp", "/var/tmp"}
    for t in tempdirs:
        if t and (cwd == t or cwd.startswith(t.rstrip("/") + "/")):
            return (f"Achtung: Arbeitsverzeichnis '{cwd}' sieht nach einem "
                    f"System-/Temp-Verzeichnis aus, nicht nach einem eigenen "
                    f"Projekt -- dort liegen oft fremde/riesige Dateien "
                    f"anderer Prozesse, die der automatische Projekt-"
                    f"Steckbrief faelschlich als Bestand einliest. Besser in "
                    f"ein eigenes Projektverzeichnis wechseln (mkdir/cd) und "
                    f"mc.py von dort starten.")
    home = os.path.realpath(os.path.expanduser("~"))
    if cwd == home:
        return (f"Achtung: Arbeitsverzeichnis ist direkt dein Home-"
                f"Verzeichnis ('{cwd}') — meintest du ein Unterverzeichnis "
                f"davon?")
    return None


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


# Rohe Textbloecke nach dem action-Block: ```content (Dateiinhalt fuer
# write_file/write_files) sowie ```old / ```new (fuer edit_file — JSON-Escaping
# mehrzeiliger old/new-Strings ist die mit Abstand haeufigste Fehlerquelle
# kleiner Modelle bei Aenderungen an BESTEHENDEN Dateien). Laengere Zaeune
# (````content) sind erlaubt, falls der Inhalt selbst ```-Zeilen hat;
# der schliessende Zaun muss mindestens so lang sein wie der oeffnende
# (CommonMark-Regel) — kuerzere Backtick-Zeilen im Inhalt schliessen nicht.
CONTENT_FENCE_RE = re.compile(
    r"^(`{3,})(content|old|new)[ \t]*\n(.*?)\n\1`*[ \t]*$", re.DOTALL | re.MULTILINE)


def _attach_fence_contents(action, tail):
    """Ergaenzt write_file/write_files um Inhalte aus ```content Bloecken und
    edit_file um old/new aus ```old / ```new Bloecken hinter dem action-Block
    (Fence-Modus). Gibt eine Fehlermeldung zurueck, wenn Bloecke fehlen oder
    die Anzahl nicht passt (sonst leerer String). Explizite Felder im JSON
    haben Vorrang (Abwaertskompatibilitaet)."""
    name = action.get("action")
    if name not in ("write_file", "write_files", "edit_file"):
        return ""
    blocks = [(mm.group(2), mm.group(3)) for mm in CONTENT_FENCE_RE.finditer(tail)]
    if name == "edit_file":
        # old/new OHNE angehaengten Zeilenumbruch uebernehmen: die Bloecke sind
        # zeilenbasiert, der Ausschnitt endet in der Datei praktisch immer vor
        # einem '\n' — ein erzwungenes Traileding-\n wuerde das Matching aber
        # brechen, wenn der Treffer am Dateiende ohne Newline liegt.
        for key in ("old", "new"):
            if key in action:
                continue
            vals = [body for lab, body in blocks if lab == key]
            if vals:
                action[key] = vals[0]
        return ""  # fehlende Pflichtfelder meldet der edit_file-Handler selbst
    fences = [body + "\n" for lab, body in blocks if lab == "content"]
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
    # Blanke String-Eintraege (["app.py", ...]) hier schon zu Objekten
    # normalisieren, damit ihre ```content Bloecke zugeordnet werden koennen
    # (real beobachtet; ohne das blieben die Inhalte unzugeordnet liegen).
    files = [{"path": f} if isinstance(f, str) else f for f in files]
    action["files"] = files
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


FENCED_JSON_RE = re.compile(r"```(?:json)?[ \t]*\n\s*(\{.*?\})\s*```", re.DOTALL)

# Aktions-Toleranz: kleine Modelle liefern staendig richtige Absichten in
# leicht falscher Form — Zahlen als Strings, Einzelwerte statt Listen,
# doppelt JSON-kodierte Felder, alternative Aktionsnamen. Statt harter
# Fehler: erst REPARIEREN (Form), dann KOERZIEREN (Typ), und nur wenn das
# scheitert ein Fehler, der dem Modell seine eigenen Argumente woertlich
# zurueckzeigt.
ACTION_ALIASE = {"write": "write_file", "create_file": "write_file",
                 "read": "read_file", "edit": "edit_file",
                 "bash": "run", "shell": "run", "execute": "run",
                 "search": "grep", "list": "list_dir"}
ACTION_FELDTYPEN = {
    "read_file": {"from": int, "to": int},
    "read_files": {"paths": list},
    "write_file": {"overwrite": bool},
    "write_files": {"overwrite": bool, "files": list},
    "edit_file": {"replace_all": bool},
    "run": {"background": bool, "timeout": int},
    "plan": {"punkte": list},
}


def _koerziere_wert(wert, ziel):
    if ziel is int:
        if isinstance(wert, bool) or isinstance(wert, (int, float)):
            return int(wert)
        if isinstance(wert, str) and wert.strip().lstrip("-").isdigit():
            return int(wert.strip())
    elif ziel is bool:
        if isinstance(wert, bool):
            return wert
        if isinstance(wert, (int, float)):
            return bool(wert)
        if isinstance(wert, str):
            low = wert.strip().lower()
            if low in ("true", "1", "yes", "ja", "wahr"):
                return True
            if low in ("false", "0", "no", "nein", "falsch", ""):
                return False
    elif ziel is list:
        if isinstance(wert, list):
            return wert
        if isinstance(wert, str) and wert.strip().startswith("["):
            try:
                geparst = json.loads(wert, strict=False)
                if isinstance(geparst, list):
                    return geparst
            except json.JSONDecodeError:
                pass
        return [wert]
    raise ValueError(f"nicht als {ziel.__name__} interpretierbar")


def repair_and_coerce_action(action):
    """Form-Reparatur + Typ-Koerzierung einer Aktion (mutiert das Dict).
    Rueckgabe: leerer String bei Erfolg, sonst eine Fehlermeldung, die dem
    Modell die empfangenen Argumente woertlich spiegelt."""
    name = action.get("action")
    if isinstance(name, str) and name.lower() in ACTION_ALIASE:
        action["action"] = ACTION_ALIASE[name.lower()]
        name = action["action"]
    # write_files: {"a.py": "inhalt", ...} als Dict statt Liste
    if name == "write_files" and isinstance(action.get("files"), dict):
        action["files"] = [{"path": p, "content": c}
                          for p, c in action["files"].items()]
    # doppelt JSON-kodierte Struktur-Felder ("files": "[{...}]")
    for feld in ("files", "paths", "punkte"):
        w = action.get(feld)
        if isinstance(w, str) and w.strip()[:1] in "[{":
            try:
                action[feld] = json.loads(w, strict=False)
            except json.JSONDecodeError:
                pass
    typen = ACTION_FELDTYPEN.get(name, {})
    for feld, ziel in typen.items():
        if feld not in action or action[feld] is None:
            continue
        try:
            action[feld] = _koerziere_wert(action[feld], ziel)
        except (ValueError, TypeError):
            versuch = {k: v for k, v in action.items()
                       if not str(k).startswith("_")}
            return (f"FEHLER: Argument '{feld}' ist unbrauchbar "
                    f"(erwartet {ziel.__name__}). Deine Argumente waren: "
                    + json.dumps(versuch, ensure_ascii=False)[:400]
                    + " — sende die Aktion korrigiert erneut.")
    return ""


def extract_action(text):
    """Findet den ersten ```action```-Block und parst das JSON daraus.
    Fehlende Dateiinhalte werden aus ```content Bloecken NACH dem
    action-Block ergaenzt (Fence-Modus) — beide Formate gehen immer.
    Toleranz (real beobachtet): manche Modelle labeln den Block ```json
    oder gar nicht — ein gefenctes JSON-Objekt MIT "action"-Feld zaehlt
    deshalb ebenfalls, sonst endete der Lauf als vermeintliche Prosa."""
    m = ACTION_RE.search(text)
    if not m:
        for fm in FENCED_JSON_RE.finditer(text):
            raw = fm.group(1).strip()
            try:
                obj = json.loads(raw, strict=False)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and "action" in obj:
                err = _attach_fence_contents(obj, text[fm.end():])
                if err:
                    obj["_fence_error"] = err
                return obj, raw
        return None, None
    raw = m.group(1).strip()
    try:
        action = json.loads(raw, strict=False)
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
        obj = json.loads(raw, strict=False)
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


def prune_messages(messages, keep=None):
    """Reduziert AELTERE Schritte auf Kurzfassungen; die letzten KEEP_CONTEXT
    Schritte bleiben vollstaendig. System-Prompt und Aufgabentext werden nie
    angetastet (matchen die Muster nicht). Idempotent: bereits gekuerzte
    Nachrichten sind klein genug und werden uebersprungen. Mit keep=N laesst
    sich haerter beschneiden als KEEP_CONTEXT (Notfall bei Kontext-Overflow —
    dann auch bei --no-prune)."""
    if not PRUNE and keep is None:
        return
    idx = [i for i, msg in enumerate(messages)
           if (msg["role"] == "assistant" and "```action" in msg.get("content", ""))
           or (msg["role"] == "user" and RESULT_RE.match(msg.get("content", "")))]
    k = KEEP_CONTEXT if keep is None else keep
    cutoff = len(idx) - 2 * max(k, 0)  # 1 Schritt = assistant + ergebnis
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
    return saved > 0


def maybe_prune(messages, model):
    """Cache-freundliche Kontext-Beschneidung. LM Studio & Co. (llama.cpp)
    verarbeiten einen Request fast ohne Prompt-Processing, wenn er den
    vorigen als PRAEFIX enthaelt (KV-/Prompt-Cache) — ein Agenten-Schritt
    ist genau das: alte Historie + neue Antwort + neues Ergebnis.
    prune_messages() vor JEDEM Schritt zerstoerte diesen Cache: es kuerzte
    pro Schritt genau die Nachricht, die gerade aus dem KEEP_CONTEXT-Fenster
    fiel — also MITTEN in der Historie. Ab dort musste der Server die
    kompletten letzten KEEP_CONTEXT Schritte (die groessten Brocken: volle
    Tool-Ausgaben und write-Bloecke) bei jedem Schritt neu vorverarbeiten.
    Deshalb: Historie unangetastet wachsen lassen, solange sie sicher ins
    GELADENE Kontextfenster passt, und erst beim Reissen der Schwelle EINMAL
    im Batch kuerzen — danach ist das Praefix wieder stabil und der Cache
    baut sich einmalig neu auf. Ist das Fenster nicht abfragbar (kein
    LM Studio), bleibt das bisherige Verhalten (jeden Schritt kuerzen):
    dort ist Ueberlauf-Schutz wichtiger als Cache-Optimierung, und bei
    Cloud-Endpoints sparen gekuerzte Prompts direkt Tokens/Kosten."""
    if not PRUNE:
        return
    ctx = _loaded_ctx_tokens(model)
    if not ctx:
        return bool(prune_messages(messages))
    budget = int(ctx * CHARS_PER_TOKEN * PRUNE_CTX_FRACTION)
    total = sum(len(m.get("content", "")) for m in messages)
    if total <= budget:
        return False
    log(f"Historie {total} Zeichen > Schwelle {budget} "
        f"({int(PRUNE_CTX_FRACTION * 100)}% von {ctx} Token geladen) — kuerze im Batch.")
    prune_messages(messages)
    rest = sum(len(m.get("content", "")) for m in messages)
    if rest > budget:
        # Auch nach normaler Kuerzung zu gross (z.B. wenige, riesige juengste
        # Schritte) -> Notfall-Stufe wie beim Leere-Antwort-Fall.
        prune_messages(messages, keep=1)
    return True


# --------------------------- Tool-Ausfuehrung ------------------------------

def truncate(s):
    """Kuerzt lange Tool-Ausgaben — zeigt KOPF UND ENDE statt nur den Kopf.
    Grund (real beobachtet): bei Build-Fehlern (npm run build, Compiler)
    steht die eigentliche Fehlermeldung fast immer am ENDE der Ausgabe;
    eine reine Kopf-Kuerzung liefert dem Modell dann 8000 Zeichen
    erfolgreicher Zwischenmeldungen, aber nie den Fehler selbst."""
    if len(s) <= MAX_OUTPUT_CHARS:
        return s
    head = int(MAX_OUTPUT_CHARS * 0.6)
    tail = MAX_OUTPUT_CHARS - head
    cut = len(s) - head - tail
    # Spill-Datei: die VOLLE Ausgabe bleibt nachschlagbar, statt verloren zu
    # gehen — aus Datenverlust wird ein Nachschlagewerk (read_file/grep auf
    # den absoluten Pfad). Liegt bewusst im Temp-Verzeichnis, nicht im
    # Projekt (sonst taucht sie im Ueberblick/Git auf).
    hint = ""
    try:
        global _SPILL_N
        _SPILL_N += 1
        spill = os.path.join(tempfile.gettempdir(),
                             f"mc_spill_{os.getpid()}_{_SPILL_N}.txt")
        with open(spill, "w", encoding="utf-8") as f:
            f.write(s)
        hint = (f"\n[Vollstaendige Ausgabe ({len(s)} Zeichen) gespeichert unter "
                f"{spill} — bei Bedarf dort mit read_file (from/to) oder grep "
                f"nachsehen]")
    except OSError:
        pass
    return (s[:head] + f"\n...[{cut} Zeichen in der MITTE ausgelassen — Anfang und Ende bleiben]...\n"
            + s[-tail:] + hint)


def _loaded_ctx_tokens(model):
    """Fragt das TATSAECHLICH GELADENE Kontextfenster des Modells in Token ab
    (LM Studios /api/v0/models liefert loaded_context_length getrennt vom
    theoretischen max_context_length). Real beobachtet: das Modell hatte
    262144 Token Maximum, war aber nur mit 8192 geladen — ein zu grosser
    Prompt lieferte dann eine stillschweigend LEERE Antwort statt eines
    Fehlers. Gibt 0 zurueck, wenn der Endpunkt fehlt (z.B. Ollama) oder
    nicht erreichbar ist. Cache-Verhalten: ein ERFOLGREICHER Wert und ein
    FEHLGESCHLAGENER Abruf (kein LM Studio) werden gecacht — aber NICHT der
    transiente Fall 'Endpoint da, Modell (noch) nicht geladen': LM Studio
    laedt JIT erst beim ersten Chat-Request, davor ist loaded_context_length
    schlicht leer. Ein Dauer-Cache von 0 haette das Lazy Pruning dann fuer
    den ganzen Lauf deaktiviert; so zieht der Wert ab Schritt 2 nach."""
    if model in _LOADED_CTX_TOKENS:
        return _LOADED_CTX_TOKENS[model]
    base = BASE_URL[:-3] if BASE_URL.endswith("/v1") else BASE_URL
    ctx = 0
    reached = False  # mind. ein Endpunkt hat geantwortet (auch ohne brauchbaren Wert)
    try:
        req = urllib.request.Request(base + "/api/v0/models")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        reached = True
        for m in data.get("data", []):
            if m.get("id") == model:
                ctx = int(m.get("loaded_context_length") or 0)
                break
    except Exception:
        pass
    if not ctx:
        # vMLX: kein loaded_context_length wie LM Studio, aber /v1/capabilities
        # meldet max_prompt_tokens -- das (nicht das theoretische Maximum) ist
        # das tatsaechlich nutzbare Fenster (siehe Blog: 10326 statt 262144).
        try:
            enc = urllib.parse.quote(model, safe="")
            req = urllib.request.Request(base + f"/v1/models/{enc}/capabilities")
            with urllib.request.urlopen(req, timeout=5) as resp:
                cap = json.loads(resp.read().decode("utf-8", errors="replace"))
            reached = True
            ctx = int(cap.get("max_prompt_tokens") or 0)
        except Exception:
            pass
    # oMLX bietet zwar /v1/models/status mit max_context_window an, aber das
    # ist -- real erprobt -- das THEORETISCHE Konfigurationsmaximum, nicht
    # das tatsaechlich nutzbare Fenster: gemeldet 262144, ein echter
    # Kontext-Ueberlauf traf aber schon bei ca. 22000 gesendeten Token ein
    # (Endpoint kalibrierte danach auf 13081 Token, siehe Blog Kapitel 45).
    # Wuerde man 262144 hier zurueckgeben, bliebe das Lazy-Pruning (unten in
    # maybe_prune) die GANZE Zeit inaktiv, bis der Lauf real ueberlief --
    # zu spaet. Deshalb bewusst KEIN oMLX-Sonderfall hier: ctx bleibt 0,
    # maybe_prune() faellt dann auf sein bereits sicheres Verhalten fuer
    # 'Fenster unbekannt' zurueck (jeden Schritt kuerzen) -- weniger
    # Cache-Vorteil, aber kein falsches Vertrauen in eine irrefuehrende Zahl.
    # Die reaktive Selbstkalibrierung ueber CtxOverflowError bleibt davon
    # unberuehrt und greift weiterhin, sobald der Endpoint wirklich ueberlaeuft.
    if ctx:
        _LOADED_CTX_TOKENS[model] = ctx
    elif not reached:
        # kein Endpunkt erreichbar (weder LM Studio noch vMLX/oMLX) ->
        # definitiv cachen. War ein Endpunkt erreichbar, aber (noch) kein
        # Wert (Modell laedt JIT), bleibt es TRANSIENT ungecacht -- siehe
        # Docstring oben.
        _LOADED_CTX_TOKENS[model] = 0
    return ctx


def loaded_context_chars(model):
    """Ermittelt ein sicheres Zeichen-Limit fuer den isolierten Analyse-Aufruf
    aus dem geladenen Kontextfenster (_loaded_ctx_tokens). Umrechnung bewusst
    konservativ (CHARS_PER_TOKEN nach Abzug einer Reserve fuer Prompt-Text und
    Antwort), kalibriert am beobachteten Fall: bei 8192 Token geladen
    scheiterten 20000 Zeichen, 10000 gingen. Ist das Fenster nicht abfragbar,
    greift der Fallback FETCH_ANALYSIS_MAX_CHARS samt Halbierungs-Retry."""
    if model in _LOADED_CTX_CACHE:
        return _LOADED_CTX_CACHE[model]
    limit = FETCH_ANALYSIS_MAX_CHARS
    ctx = _loaded_ctx_tokens(model)
    if ctx > 2000:
        limit = max(4000, int((ctx - 1700) * CHARS_PER_TOKEN))
        info(f"Geladenes Kontextfenster: {ctx} Token -> "
             f"Analyse-Limit {limit} Zeichen.")
    if model in _LOADED_CTX_TOKENS:  # nur definitive Werte cachen (s. dort)
        _LOADED_CTX_CACHE[model] = limit
    return limit


def summarize_large_fetch(raw_output, model):
    """Fuer grosse curl/wget-Ergebnisse (z.B. eine ganze Webseite): statt die
    Rohausgabe blind auf MAX_OUTPUT_CHARS zu kuerzen (bei einer WordPress-Seite
    steckt oft schon der halbe <head> mit Meta-Tags in den ersten 8000 Zeichen,
    der eigentliche <body> kommt nie an), wird ein ISOLIERTER Chat-Aufruf
    ausserhalb der Haupt-Konversation gemacht: die Rohausgabe (deutlich
    grosszuegiger als das normale Limit, weil sie NICHT dauerhaft im Verlauf
    verbleibt) wird analysiert, und nur die kompakte Struktur-Zusammenfassung
    fliesst zurueck in den eigentlichen Agenten-Loop.

    WICHTIG: "grosszuegig" heisst hier NICHT das theoretische Maximum des
    Modells (max_context_length kann z.B. 262144 sein), sondern das aktuell
    in LM Studio/Ollama GELADENE Kontextfenster (loaded_context_length) -
    das ist oft viel kleiner (z.B. 8192), um RAM zu sparen. Ein zu grosser
    Prompt liefert dann keinen Fehler, sondern eine LEERE Antwort. Deshalb:
    erst mit FETCH_ANALYSIS_MAX_CHARS versuchen, bei leerer Antwort mit der
    HAELFTE erneut (einmal), sonst eine klare Fehlermeldung statt stillem
    Nichts."""
    def ask_for(chars):
        content = raw_output[:chars]
        ask = (
            "Die folgende Rohausgabe stammt von einem curl/wget-Abruf einer Webseite "
            "und ist zu gross fuer den normalen Arbeitskontext. Analysiere sie und "
            "liefere eine KOMPAKTE, aber vollstaendige STRUKTUR-Beschreibung: "
            "Reihenfolge und Art der Abschnitte/Sections, Layout-Hinweise (Farben, "
            "auffaellige CSS-Klassen falls erkennbar), verwendete Komponenten (Hero, "
            "Formulare, Bildbereiche, Navigation, Footer etc.), Ueberschriften "
            "sinngemaess zusammengefasst. KEINE wortwoertliche Wiedergabe von "
            "Fliesstext oder ganzen Saetzen aus der Seite — nur Struktur und "
            "Zusammenfassung in eigenen Worten, das reicht fuer einen Nachbau.\n\n"
            f"--- ROHAUSGABE (ggf. gekuerzt) ---\n{content}"
        )
        return chat_stream([{"role": "user", "content": ask}], model)

    print(f"{C.DIM}(Große Abrufausgabe erkannt — analysiere in einem separaten, "
          f"isolierten Aufruf statt sie in den Verlauf zu uebernehmen …){C.RESET}")
    limit = loaded_context_chars(model)
    try:
        summary = ask_for(limit)
        if not summary.strip():
            print(f"{C.DIM}(Leere Antwort — vermutlich reicht das GELADENE "
                  f"Kontextfenster des Modells nicht, versuche mit der Haelfte "
                  f"erneut …){C.RESET}")
            summary = ask_for(limit // 2)
    except Exception as e:
        return f"FEHLER bei der Analyse der grossen Abrufausgabe: {e}"
    if not summary.strip():
        return (f"FEHLER: Die Analyse der {len(raw_output)} Zeichen grossen Abrufausgabe "
                f"lieferte zweimal eine leere Antwort — das geladene Kontextfenster "
                f"des Modells reicht vermutlich nicht aus. Nutze stattdessen gezielte "
                f"Werkzeuge wie 'curl ... | grep' oder 'curl ... | sed -n ...', um nur "
                f"einen kleineren, relevanten Ausschnitt zu holen.")
    return (f"[Hinweis: Die Rohausgabe war {len(raw_output)} Zeichen gross und "
            f"wurde deshalb NICHT direkt uebernommen, sondern in einem "
            f"separaten Aufruf analysiert. Das ist das Ergebnis:]\n\n{summary}")


REJECT_REASON = ""  # optionaler Freitext des Nutzers bei einer Ablehnung


def confirm(prompt):
    """Bestaetigungs-Prompt mit Steuerkanal: Antwortet der Nutzer weder mit
    ja noch nein, gilt die Eingabe als Ablehnung MIT BEGRUENDUNG — der Text
    geht als Aktions-Ergebnis ans Modell zurueck ('nimm Port 5030 statt
    5020'), das daraufhin den Kurs korrigieren kann, statt dieselbe Aktion
    erneut zu versuchen. Aus einem binaeren Nein wird eine Anweisung."""
    global REJECT_REASON
    REJECT_REASON = ""
    if AUTO_YES:
        print(f"{C.DIM}(auto-yes){C.RESET}")
        return True
    try:
        ans = input(rl_prompt(f"{C.YELLOW}{prompt} [j/N/Text=Grund] {C.RESET}")).strip()
    except EOFError:
        return False
    low = ans.lower()
    if low in ("y", "yes", "j", "ja"):
        return True
    if low not in ("", "n", "no", "nein", "q"):
        REJECT_REASON = ans
    return False


def user_reject_msg():
    """Ergebnis-Text fuer eine vom Nutzer abgelehnte Aktion (inkl. Grund)."""
    if REJECT_REASON:
        return ("Abgelehnt durch den Benutzer. Anweisung des Benutzers: "
                + REJECT_REASON + " — beruecksichtige das im naechsten Schritt.")
    return "Abgelehnt durch den Benutzer."


# read_file darf deutlich mehr liefern als Tool-Ausgaben (MAX_OUTPUT_CHARS):
# Real beobachtet, dass Modelle bei einer mittig gekappten Datei anfangen,
# sie in sed/cat-Haeppchen zu blaettern — zyklisch bis ins Schrittlimit.
# Der Verlauf waechst dadurch MEHR als durch einmal Ganz-Lesen (und die
# Kontext-Beschneidung kuerzt alte Reads ohnehin wieder weg).
READFILE_MAX_CHARS = 24000


def do_read_files(args):
    """Mehrere Dateien in EINEM Schritt lesen — das Lese-Gegenstueck zu
    write_files. Lesen kann nichts kaputtmachen, und jeder gesparte Umlauf
    spart das erneute Verarbeiten des kompletten Verlaufs (Prompt-Tokens
    dominieren die Kosten mit ueber 90 Prozent)."""
    paths = args.get("paths") or args.get("files") or []
    if isinstance(paths, str):
        paths = [paths]
    paths = [p.get("path") if isinstance(p, dict) else p for p in paths]
    paths = [p for p in paths if p]
    if not paths:
        return False, "FEHLER: 'paths' muss eine nicht-leere Liste von Pfaden sein."
    if len(paths) > MAX_READ_FILES_BATCH:
        return False, (f"FEHLER: {len(paths)} Dateien in einem read_files — "
                       f"maximal {MAX_READ_FILES_BATCH}. Teile auf oder nutze "
                       f"explore fuer breite Erkundungen.")
    teile, fehler = [], 0
    for p in paths:
        ok, res = do_read_file({"path": p})
        if not ok:
            fehler += 1
        teile.append(res)
    return fehler == 0, "\n\n".join(teile)


EXPLORE_PROMPT = """Du bist ein Erkundungs-Agent mit FRISCHEM Kontext. Beantworte den Erkundungs-Auftrag,
indem du NUR liest und suchst — EINE Aktion pro Antwort als ```action Block mit JSON:
  read_file  -> {"action":"read_file","path":"<pfad>"}
  read_files -> {"action":"read_files","paths":["a","b"]}  (max 5)
  list_dir   -> {"action":"list_dir","path":"<pfad>"}
  find       -> {"action":"find","pattern":"<namensteil>"}
  grep       -> {"action":"grep","pattern":"<text oder regex>"}
  finish     -> {"action":"finish","summary":"<GRUENDLICHES ergebnis>"}

Regeln:
- Ein leeres Suchergebnis heisst: Muster verbreitern und erneut suchen.
- Schliesse mit finish ab. Die summary ist ALLES, was der Hauptlauf von dir
  erfaehrt — nenne konkrete Fundstellen (Pfad, Zeile, Funktionsname), wie die
  Teile zusammenhaengen und was fuer den Auftrag relevant ist.

Beispiel-Antwort:
Ich suche zuerst nach dem Begriff.
```action
{"action":"grep","pattern":"persons"}
```"""


def do_explore(args):
    """Unterauftrag mit frischem Kontext: eine isolierte Mini-Schleife
    erkundet das Projekt (nur Lese-Aktionen), und NUR die Zusammenfassung
    kommt in den Haupt-Verlauf zurueck. Verallgemeinerung der isolierten
    Analyse grosser Abrufausgaben — schuetzt kleine Kontextfenster davor,
    an breiten Erkundungen zu ersticken."""
    auftrag = (args.get("task") or args.get("auftrag")
               or args.get("question") or "").strip()
    if not auftrag:
        return False, "FEHLER: 'task' fehlt (der Erkundungs-Auftrag)."
    print(f"{C.CYAN}» explore{C.RESET} (isolierter Kontext): {auftrag[:100]}")
    ueberblick = "\n".join(project_overview()) or "(keine Dateien)"
    msgs = [{"role": "system", "content": EXPLORE_PROMPT},
            {"role": "user", "content":
             f"Erkundungs-Auftrag: {auftrag}\n\nVorhandene Dateien:\n{ueberblick}"}]
    lese = {"read_file": do_read_file, "read_files": do_read_files,
            "list_dir": do_list_dir, "find": do_find, "grep": do_grep}
    for _ in range(EXPLORE_STEPS):
        try:
            reply = chat_stream(msgs, CURRENT_MODEL)
        except (CtxOverflowError, NetRetryError, SystemExit) as e:
            return True, f"ERKUNDUNG abgebrochen ({e}). Erkunde selbst gezielt weiter."
        msgs.append({"role": "assistant", "content": reply})
        action, _raw = extract_action(reply)
        if action is None:
            return True, "ERKUNDUNGS-ERGEBNIS:\n" + reply.strip()[:4000]
        if "_parse_error" in action:
            msgs.append({"role": "user", "content":
                         "FEHLER: ungueltiges action-JSON. Bitte erneut."})
            continue
        name = action.get("action")
        if name == "finish":
            return True, ("ERKUNDUNGS-ERGEBNIS:\n"
                          + str(action.get("summary", "")).strip()[:4000])
        handler = lese.get(name)
        if not handler:
            msgs.append({"role": "user", "content":
                         f"FEHLER: '{name}' gibt es in der Erkundung nicht — "
                         f"nur Lese-Aktionen und finish."})
            continue
        ok, result = handler(action)
        msgs.append({"role": "user", "content": truncate(str(result))})
    return True, ("ERKUNDUNG am Schrittlimit beendet — letzter Stand:\n"
                  + msgs[-1]["content"][:2000])


def _closest_paths_hint(path, limit=3):
    """'Meintest du …?' fuer vertippte/geratene Pfade — kleine Modelle
    vertippen Dateinamen staendig, und die blosse Fehlermeldung fuehrt dann
    gern zum Neuanlegen statt zum zweiten Versuch."""
    try:
        alle = [p for p in project_overview(max_entries=400)
                if not p.startswith("...")]
    except Exception:
        return ""
    treffer = difflib.get_close_matches(path, alle, n=limit, cutoff=0.5)
    base = os.path.basename(path)
    if not treffer and base and base != path:
        nach_name = {os.path.basename(p): p for p in alle}
        treffer = [nach_name[b] for b in
                   difflib.get_close_matches(base, list(nach_name),
                                             n=limit, cutoff=0.6)]
    if not treffer:
        return ""
    return " Meintest du: " + ", ".join(treffer) + "?"


def do_read_file(args):
    global EXPLORED
    EXPLORED = True
    path = args.get("path", "")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return False, f"FEHLER beim Lesen von {path}: {e}" + _closest_paths_hint(path)
    READ_FILES.add(os.path.normpath(path))
    lines = content.split("\n")
    total = len(lines)
    frm, to = args.get("from"), args.get("to")
    if frm or to:
        try:
            frm = max(int(frm or 1), 1)
            to = min(int(to or frm + 199), total)
        except (TypeError, ValueError):
            return False, ("FEHLER: 'from'/'to' muessen Zeilennummern sein, z.B. "
                           "{\"action\":\"read_file\",\"path\":\"...\",\"from\":120,\"to\":260}")
        seg = "\n".join(lines[frm - 1:to])
        return True, (f"Zeilen {frm}-{to} von {path} (gesamt {total} Zeilen):\n"
                      f"{truncate(seg)}")
    if len(content) > READFILE_MAX_CHARS:
        head = int(READFILE_MAX_CHARS * 0.6)
        tail = READFILE_MAX_CHARS - head
        return True, (
            f"Inhalt von {path} ({len(content)} Zeichen, {total} Zeilen) — zu "
            f"gross fuer eine Ausgabe, Anfang und Ende folgen. Den FEHLENDEN "
            f"MITTELTEIL holst du gezielt mit "
            f"{{\"action\":\"read_file\",\"path\":\"{path}\",\"from\":<zeile>,\"to\":<zeile>}} "
            f"— NICHT mit sed/cat blaettern.\n"
            + content[:head]
            + f"\n...[Mitte ausgelassen — per from/to nachladen]...\n"
            + content[-tail:])
    return True, f"Inhalt von {path} ({len(content)} Zeichen, {total} Zeilen):\n{content}"


OVERWRITE_REJECTS = {}      # Pfad -> Anzahl abgelehnter blinder Ueberschreib-Versuche
MAX_OVERWRITE_REJECTS = 2   # danach Notausgang (Warnungen greifen weiter), sonst Endlosschleife


def _project_has_code(root="."):
    """Hat das Projekt Bestandscode? (Quelldateien ausserhalb der Ignore-
    Verzeichnisse, Projekt-Notizen zaehlen nicht.) Pro Lauf gecacht."""
    global HAS_CODE
    if HAS_CODE is not None:
        return HAS_CODE
    HAS_CODE = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in IGNORE_DIRS and not d.startswith(".") and not _is_venv_dir(os.path.join(dirpath, d))]
        for fn in filenames:
            # Eigene Zustandsdateien zaehlen nicht als Projekt-Bestand.
            if fn in (os.path.basename(MC_NOTES), MC_PLAN, MC_VERLAUF):
                continue
            if os.path.splitext(fn)[1].lower() in SRC_EXTS:
                HAS_CODE = True
                return True
    return False


def _new_file_gate(paths):
    """Neubau-Bremse: In einem Projekt MIT Bestandscode wird das Anlegen
    NEUER Dateien abgelehnt, solange in diesem Lauf noch gar nicht in den
    Bestand geschaut wurde (kein read_file/grep/find/list_dir). Real
    beobachtet: ein Modell findet auf Anhieb nichts (oder sucht gar nicht
    erst) und legt die Datei einfach neu an — der Neubau-Reflex. Die Bremse
    erzwingt nur den ERSTEN Blick; jedes Suchergebnis (auch ein leeres)
    schaltet sie frei."""
    if EXPLORED:
        return ""
    neu = [p for p in paths if p and not os.path.exists(p)]
    vorhanden = [p for p in paths if p and os.path.exists(p)]
    if not neu or vorhanden or not _project_has_code():
        # Beruehrt der Block auch BESTEHENDE Dateien, greift bereits das
        # Overwrite-Gate (lesen oder explizites overwrite) — nicht doppelt
        # bremsen. Die Neubau-Bremse gilt nur fuer REIN neue Dateien.
        return ""
    return ("NEUBAU-BREMSE: du willst neue Datei(en) anlegen (" + ", ".join(neu)
            + "), hast dir aber den BESTAND dieses Projekts noch gar nicht "
            "angesehen. Pruefe erst mit find/grep/read_file, ob die "
            "Funktionalitaet (ggf. unter anderem Namen) schon existiert — "
            "danach ist das Anlegen freigeschaltet. Ein leeres Suchergebnis "
            "heisst dabei: Muster verbreitern und erneut suchen, nicht sofort "
            "neu anlegen.")


def _overwrite_gate(path, force=False):
    """Lehnt das komplette Ueberschreiben einer BEREITS EXISTIERENDEN Datei ab,
    die in diesem Lauf weder gelesen noch selbst geschrieben wurde — BEVOR etwas
    kaputt geht (die _blind_overwrite_warning kam bisher erst NACH dem Schaden).
    Hintergrund: bei einem erneuten Lauf im selben Projektverzeichnis startet
    das Modell mit leerem Wissen (READ_FILES ist pro Lauf leer) und haelt alles
    fuer 'neu'. Die Ablehnung zwingt es, erst read_file zu nutzen — dasselbe
    Zwangs-Muster wie bei finish-Rejects, auf das auch kleine Modelle
    zuverlaessig reagieren. Bewusstes Neuschreiben bleibt per "overwrite":true
    moeglich; nach MAX_OVERWRITE_REJECTS Ablehnungen je Pfad greift ein
    Notausgang gegen Endlosschleifen (dann warnen die bestehenden Checks)."""
    if force:
        return ""
    norm = os.path.normpath(path)
    if not os.path.isfile(norm):
        return ""  # neue Datei — unkritisch
    if norm in READ_FILES or norm in {os.path.normpath(p) for p in TOUCHED}:
        return ""  # Inhalt bekannt (gelesen) oder in diesem Lauf selbst geschrieben
    n = OVERWRITE_REJECTS.get(norm, 0)
    if n >= MAX_OVERWRITE_REJECTS:
        return ""
    OVERWRITE_REJECTS[norm] = n + 1
    return (f"ABGELEHNT: {path} existiert bereits, wurde in diesem Lauf aber noch "
            f"NICHT mit read_file gelesen — blindes Ueberschreiben wuerde den "
            f"bestehenden Inhalt vernichten. Lies die Datei zuerst mit read_file "
            f"und aendere sie dann GEZIELT mit edit_file. Nur wenn ein kompletter "
            f"Neuschrieb wirklich beabsichtigt ist, wiederhole die Schreib-Aktion "
            f"mit dem zusaetzlichen Feld \"overwrite\":true.")


def _blind_overwrite_warning(path):
    """Warnt, wenn eine BEREITS EXISTIERENDE Datei komplett ueberschrieben
    wird, die in diesem Lauf weder gelesen noch selbst angelegt wurde. Zwei
    real beobachtete Fehlerklassen haben genau dieses Muster: (1) Datenverlust,
    weil write_file versehentlich statt read_file benutzt wurde, und (2)
    Scope-Creep, bei dem eine nicht zur Aufgabe gehoerende Datei (index.html)
    ungefragt komplett neu geschrieben und dabei Bestandsfunktionalitaet
    zerstoert wurde. Kein Blocker — nur eine Rueckmeldung, auf die das Modell
    im naechsten Schritt reagieren kann."""
    norm = os.path.normpath(path)
    if not os.path.isfile(norm):
        return ""  # neue Datei — unkritisch
    if norm in READ_FILES or norm in {os.path.normpath(p) for p in TOUCHED}:
        return ""  # Inhalt bekannt (gelesen) oder in diesem Lauf selbst geschrieben
    return (f"\nACHTUNG: {path} existierte bereits, wurde in diesem Lauf aber NIE "
            f"mit read_file gelesen — du hast den alten Inhalt ueberschrieben, ohne "
            f"ihn zu kennen. Falls die Datei nicht Teil deiner Aufgabe war oder "
            f"Funktionalitaet enthielt: pruefe mit git diff, was verloren ging, und "
            f"stelle Noetiges wieder her.")


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


def _check_repetition(path, new_content):
    """Erkennt eine Wiederholungsschleife: dieselbe Datei wird wiederholt fast
    unveraendert neu geschrieben, ohne dass sich etwas am eigentlichen Problem
    aendert (in der Praxis beobachtet: ein Tippfehler wird 'korrigiert', aber
    der naechste komplette Neuschrieb bringt ihn wieder mit). Ein generischer
    Validierungsfehler allein loest das nicht, weil das Modell dieselbe
    (falsche) Strategie — Datei komplett neu schreiben — einfach wiederholt,
    statt die Strategie zu wechseln. Nach der 3. fast identischen Version in
    Folge wird das Modell explizit zu 'edit_file statt komplettem Neuschreiben'
    gedraengt. Zaehler wird zurueckgesetzt, sobald sich der Inhalt spuerbar
    aendert."""
    global WRITE_HISTORY
    prev_content, count = WRITE_HISTORY.get(path, (None, 0))
    if prev_content is not None:
        ratio = difflib.SequenceMatcher(None, prev_content, new_content).quick_ratio()
        count = count + 1 if ratio > 0.9 else 0
    WRITE_HISTORY[path] = (new_content, count)
    if count >= 2:
        return (f"\nACHTUNG: {path} wurde jetzt {count + 1}x in Folge fast "
                f"identisch komplett neu geschrieben, ohne den Fehler zu "
                f"beheben. Wechsle die Strategie: Nutze 'edit_file', um NUR "
                f"die konkrete fehlerhafte Stelle gezielt zu ersetzen, statt "
                f"die ganze Datei erneut zu schreiben. Ist unklar, was genau "
                f"falsch ist, erklaere das zuerst in einem Satz, bevor du "
                f"erneut schreibst.")
    return ""


def do_write_file(args):
    path = args.get("path", "")
    content = args.get("content", "")
    nb = _new_file_gate([path])
    if nb:
        print(f"{C.RED}✗ Neubau-Bremse: {path} (Bestand nie angesehen){C.RESET}")
        return False, nb
    gate = _overwrite_gate(path, force=bool(args.get("overwrite")))
    if gate:
        print(f"{C.RED}✗ Overwrite-Gate: {path} (existiert, nie gelesen){C.RESET}")
        return False, gate
    alt_inhalt = ""
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                alt_inhalt = f.read()
        except OSError:
            pass
    print(f"{C.YELLOW}» write_file{C.RESET} {C.BOLD}{path}{C.RESET} ({len(content)} Zeichen)")
    preview = content if len(content) < 600 else content[:600] + "\n..."
    print(f"{C.DIM}{preview}{C.RESET}")
    if not confirm(f"Datei '{path}' schreiben?"):
        return False, user_reject_msg()
    warn = (_shrink_warning(path, len(content)) + _check_repetition(path, content)
            + _blind_overwrite_warning(path))
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        if alt_inhalt:
            warn += _loss_warning(path, alt_inhalt, content)
            warn += _duplicate_warning(path, alt_inhalt, content)
        warn += _reference_warning(path)
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
    # Robustheit (real beobachtet, Harness-Crash): ein Modell schickte die
    # Eintraege als BLANKE STRINGS statt Objekte (["app.py", ...]) — das ist
    # als Absicht eindeutig (Pfad, Inhalt folgt als Fence-Block) und wird
    # normalisiert statt mit AttributeError abzustuerzen.
    files = [{"path": f} if isinstance(f, str) else f for f in files]
    bad = [repr(f)[:60] for f in files
           if not isinstance(f, dict) or not f.get("path")]
    if bad:
        return False, ("FEHLER: jeder files-Eintrag braucht ein 'path'-Feld, "
                       "ungueltig: " + ", ".join(bad))
    ohne_inhalt = [f["path"] for f in files if "content" not in f]
    if ohne_inhalt:
        return False, ("FEHLER: fuer diese Datei(en) fehlt der Inhalt "
                       "('content'-Feld bzw. je ein ```content Block nach dem "
                       "action-Block): " + ", ".join(ohne_inhalt))
    nb = _new_file_gate([f.get("path", "") for f in files])
    if nb:
        print(f"{C.RED}✗ Neubau-Bremse: Bestand nie angesehen{C.RESET}")
        return False, nb
    if len(files) > MAX_WRITE_FILES_BATCH:
        # Hartes Limit statt Prompt-Bitte: grosse Einzelbloecke sind das
        # Haupt-Risiko fuer abgeschnittene Antworten (kaputtes JSON).
        return False, (f"FEHLER: {len(files)} Dateien in EINEM write_files-Block — "
                       f"maximal {MAX_WRITE_FILES_BATCH} erlaubt (Schutz vor abgeschnittenen "
                       f"Antworten). Teile auf MEHRERE write_files-Schritte auf "
                       f"(z.B. erst backend/, dann frontend/) und fahre fort.")
    force = bool(args.get("overwrite"))  # gilt fuer den ganzen Block …
    gated = [g for g in (_overwrite_gate(f.get("path", ""),
                                         force=force or bool(f.get("overwrite")))
                         for f in files if isinstance(f, dict) and f.get("path"))
             if g]  # … oder pro Datei via "overwrite":true am Datei-Eintrag
    if gated:
        print(f"{C.RED}✗ Overwrite-Gate: {len(gated)} existierende, nie gelesene "
              f"Datei(en){C.RESET}")
        return False, "\n".join(gated)
    print(f"{C.YELLOW}» write_files{C.RESET} {C.BOLD}{len(files)}{C.RESET} Datei(en):")
    for f in files:
        print(f"   {f.get('path','?')} ({len(f.get('content',''))} Zeichen)")
    if not confirm(f"{len(files)} Datei(en) schreiben?"):
        return False, user_reject_msg()
    written, errors, warns = [], [], []
    for f in files:
        path, content = f.get("path", ""), f.get("content", "")
        if not path:
            errors.append("(Eintrag ohne 'path' uebersprungen)")
            continue
        warn = (_shrink_warning(path, len(content)) + _check_repetition(path, content)
                + _blind_overwrite_warning(path))
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


def _closest_snippet(content, old, min_ratio=0.5):
    """Sucht die dem verfehlten 'old' AEHNLICHSTE Stelle in der Datei und gibt
    sie woertlich zurueck — damit das Modell den exakten Text KOPIEREN kann,
    statt beim naechsten Versuch erneut zu raten (real beobachtet: drei
    identische 'nicht gefunden'-Fehlschlaege in Folge, weil die Rueckmeldung
    keinerlei Anhaltspunkt bot, WAS am geratenen Ausschnitt falsch war)."""
    lines = content.split("\n")[:4000]
    o_lines = old.split("\n")
    n = max(len(o_lines), 1)
    best, best_i = 0.0, -1
    for i in range(max(len(lines) - n + 1, 1)):
        cand = "\n".join(lines[i:i + n])
        sm = difflib.SequenceMatcher(None, cand, old)
        if sm.quick_ratio() <= best:
            continue
        r = sm.ratio()
        if r > best:
            best, best_i = r, i
    if best_i < 0 or best < min_ratio:
        return ""
    snippet = "\n".join(lines[best_i:best_i + n])[:700]
    return (f"\nAEHNLICHSTE Stelle in der Datei (ab Zeile {best_i + 1}, "
            f"Aehnlichkeit {best:.0%}) — verwende fuer 'old' EXAKT diesen Text:\n"
            f"{snippet}")


KENNUNG_RE = re.compile(
    r"id=[\"']([\w-]+)[\"']"                       # HTML-IDs
    r"|(?:function|def)\s+(\w+)"                    # Funktionsnamen
    r"|(?:const|let|var)\s+(\w+)\s*="               # JS-Bindungen
    r"|class\s+(\w+)[\s(:{]"                        # Klassennamen
    r"|<(iframe|form|nav|main|footer|table|video|canvas)\b")  # Struktur-Tags
REMOVAL_WORDS_RE = re.compile(
    r"\b(loesch|lösch|entfern|ersetz|umbenenn|refactor|aufraeum|aufräum|"
    r"weg damit|raus damit)", re.IGNORECASE)
CURRENT_TASK = ""  # Aufgabentext des laufenden Auftrags (fuer den Verlust-Waechter)


def _kennungen(text):
    out = set()
    for m in KENNUNG_RE.finditer(text or ""):
        out.add(next(g for g in m.groups() if g))
    return out


def _loss_warning(path, alt, neu):
    """Verlust-Waechter: Schreibvorgaenge an BESTEHENDEN Dateien, die
    benannte Elemente (HTML-IDs, Funktions-/Klassennamen, Struktur-Tags)
    verschwinden lassen, bekommen eine Warnung ins Aktions-Ergebnis — ausser
    die Aufgabe verlangt erkennbar ein Entfernen. Hintergrund (real passiert):
    ein Modell ersetzte beim 'Ergaenzen' einer Werkzeugleiste das komplette
    Preview-iframe und nickte die Loeschung im Diff-Review selbst ab. Auf
    sorgfaeltige Prompts ('nur hinzufuegen, nichts entfernen') kann man sich
    bei tippfaulen Menschen nicht verlassen — der Schutz gehoert ins Tool.

    Jeder (Pfad, Name) wird PRO AUFGABE nur EINMAL gemeldet (LOSS_WARNED_
    NAMES) -- sonst feuert die Meldung bei jedem weiteren Komplett-Neuschrieb
    erneut fuer denselben, laengst kommentierten Verlust und verleitet ein
    schwaches Modell dazu, den entfernten Namen kuenstlich (z.B. per totem
    console.log) wieder einzubauen, nur um die Warnung loszuwerden -- real
    beobachtet. Eine einmalige Meldung reicht: sie steht im Diff-Review
    weiterhin sichtbar, muss aber nicht jeden Schritt neu verhandelt werden."""
    if REMOVAL_WORDS_RE.search(CURRENT_TASK or ""):
        return ""  # Entfernen ist Teil des Auftrags
    verloren = sorted(_kennungen(alt) - _kennungen(neu))
    neu_verloren = [v for v in verloren if (path, v) not in LOSS_WARNED_NAMES]
    if not neu_verloren:
        return ""
    for v in neu_verloren:
        LOSS_WARNED_NAMES.add((path, v))
    return ("\nVERLUST-WAECHTER: dieser Schreibvorgang hat bestehende benannte "
            "Elemente aus " + path + " ENTFERNT: " + ", ".join(neu_verloren[:8])
            + (" …" if len(neu_verloren) > 8 else "") + ". Die Aufgabe verlangt "
            "kein Entfernen. Das ist NUR ein Hinweis, kein Fehler: kein "
            "Code-Fix noetig, kein kuenstliches Wiedereinfuegen (z.B. per "
            "totem console.log) nur um die Namen 'benutzt' aussehen zu "
            "lassen. Entweder im naechsten Schritt echt wiederherstellen "
            "(falls unbeabsichtigt) ODER in EINEM Satz in deiner naechsten "
            "Antwort begruenden, warum es weg sollte — dann normal "
            "weiterarbeiten. Diese Meldung erscheint pro Name nur einmal.")


def _duplicate_warning(path, alt, neu):
    """Duplikat-Waechter — das Gegenstueck zum Verlust-Waechter. Real passiert:
    ein Modell fuegte einen Button ein, verlor die eigene Einfuegung nach
    vielen Schritten aus dem (gekuerzten) Kontext und fuegte ihn beim Umbau
    derselben Region ERNEUT ein. Edits sind lokal, der Build akzeptiert
    Duplikate, der Verlust-Waechter prueft nur Verschwundenes. Deshalb:
    Zeilen, die durch einen Schreibvorgang MEHRFACH vorhanden werden, obwohl
    sie vorher genau einmal existierten, werden gemeldet (nur substanzielle
    Zeilen ab 30 Zeichen — '</div>' & Co. duerfen sich wiederholen)."""
    def zaehlung(text):
        z = {}
        for zeile in (text or "").split("\n"):
            t = zeile.strip()
            if len(t) >= 30:
                z[t] = z.get(t, 0) + 1
        return z
    vorher, nachher = zaehlung(alt), zaehlung(neu)
    verdoppelt = [z for z, n in nachher.items()
                  if n >= 2 and vorher.get(z, 0) == 1]
    if not verdoppelt:
        return ""
    beispiele = "; ".join(z[:80] for z in verdoppelt[:3])
    return ("\nDUPLIKAT-WAECHTER: durch diesen Schreibvorgang existieren "
            "Zeilen jetzt MEHRFACH, die vorher genau einmal da waren: "
            + beispiele + ". Pruefe, ob du ein Element doppelt eingefuegt "
            "hast (z.B. Button/Link erneut eingebaut, den es schon gab) — "
            "falls ja, entferne das Duplikat im naechsten Schritt.")


def _projekt_frontend_texte(root=".", max_dateien=200):
    """Sammelt Frontend-Quelltexte des Projekts: (jsx/html/js-Texte,
    css-Texte inkl. <style>-Bloecke) — gedeckelt gegen Riesenprojekte."""
    jsx, css, n = [], [], 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in IGNORE_DIRS and not d.startswith(".") and not _is_venv_dir(os.path.join(dirpath, d))]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in (".jsx", ".tsx", ".html", ".css", ".js"):
                continue
            n += 1
            if n > max_dateien:
                return jsx, css
            try:
                with open(os.path.join(dirpath, fn), encoding="utf-8",
                          errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            if ext == ".css":
                css.append(text)
            else:
                jsx.append(text)
                for m in re.finditer(r"<style[^>]*>(.*?)</style>", text, re.S):
                    css.append(m.group(1))
    return jsx, css


def _reference_warning(path):
    """Referenz-Waechter — gegen die 'Build ist gruen, Seite ist kaputt'-
    Familie (alle drei real passiert): (A) Komponenten-Klassen benutzt, aber
    nie im CSS definiert -> Seite rendert nackt. (B) Komponente definiert,
    aber nie eingebunden -> unsichtbar. npm run build ist fuer beides blind;
    dieser Abgleich ist reine Regex-Arithmetik ueber das Projekt."""
    if os.path.splitext(path)[1].lower() not in (".jsx", ".tsx", ".html", ".css"):
        return ""
    jsx, css = _projekt_frontend_texte()
    alles_jsx, alles_css = "\n".join(jsx), "\n".join(css)
    if "tailwind" in (alles_jsx + alles_css).lower():
        return ""  # Utility-Framework: Klassen sind absichtlich nirgends definiert
    befunde = []
    if css:
        benutzt = set()
        for m in re.finditer(r"class(?:Name)?=[\"']([^\"'{]+)[\"']", alles_jsx):
            for k in m.group(1).split():
                if re.fullmatch(r"[A-Za-z][\w-]*", k):
                    benutzt.add(k)
        definiert = set(re.findall(r"\.([A-Za-z][\w-]*)", alles_css))
        fehlt = sorted(benutzt - definiert)
        if fehlt:
            befunde.append(f"{len(fehlt)} benutzte CSS-Klasse(n) OHNE Regel: "
                           + ", ".join(fehlt[:6])
                           + (" …" if len(fehlt) > 6 else ""))
    definierte = set(re.findall(r"(?:const|function)\s+([A-Z]\w*)\s*[=(]",
                                alles_jsx))
    gemountet = set(re.findall(r"<([A-Z]\w*)", alles_jsx))
    ungenutzt = sorted(definierte - gemountet)
    if ungenutzt:
        befunde.append("definierte, aber NIE eingebundene Komponente(n): "
                       + ", ".join(ungenutzt[:6]))
    if not befunde:
        return ""
    return ("\nREFERENZ-WAECHTER: " + " | ".join(befunde)
            + ". Build-Checks sehen so etwas NICHT — ergaenze fehlende "
            "CSS-Regeln bzw. binde die Komponenten ein (oder entferne sie "
            "bewusst).")


def _shift_indent(text, delta):
    """Verschiebt jede nicht-leere Zeile um delta Spalten (negativ: entfernen,
    soweit fuehrender Whitespace vorhanden ist)."""
    out = []
    for l in text.split("\n"):
        if not l.strip():
            out.append(l)
        elif delta >= 0:
            out.append(" " * delta + l)
        else:
            cut = min(-delta, len(l) - len(l.lstrip()))
            out.append(l[cut:])
    return "\n".join(out)


def _unescape_once(s):
    """Macht EINE Ebene JSON-artiges Escaping rueckgaengig (\\n -> Newline usw.)
    — fuer Modelle, die old/new versehentlich doppelt escapen."""
    return (s.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
            .replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\"))


_UNI_MAP = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"',
    "\u2013": "-", "\u2014": "-", "\u2212": "-",
    "\u00a0": " ", "\u2009": " ", "\u202f": " "})


def _uni(s):
    """Unicode-tolerant vergleichen: Smart Quotes, Gedankenstriche und
    gesch. Leerzeichen auf ASCII-Gegenstuecke falten (Modelle tippen die
    staendig 'schoener' als die Datei sie hat)."""
    return unicodedata.normalize("NFKC", s).translate(_UNI_MAP)


def _fuzzy_edit_match(content, old, new):
    """Edit-Toleranz-Kaskade: findet 'old' mit steigender Nachsicht, wenn der
    woertliche Text nicht in der Datei steht. Hintergrund (real beobachtet und
    auch in anderen Agent-Harnesses DIE dominante Fehlklasse): kleine Modelle
    liefern ein 'old', das zu 99%% stimmt — falsche Einrueckung, doppeltes
    Escaping, eine halluzinierte Zeile in der Blockmitte. Ein harter Fehler
    fuehrt dann oft dazu, dass das Modell aufgibt und die GANZE Datei neu
    schreibt (genau der Neubau-Reflex, den wir bekaempfen). Jede Stufe
    verlangt EINDEUTIGKEIT — lieber ein erklaerter Fehler als ein stiller
    Treffer an der falschen Stelle. Rueckgabe:
      (datei_text, angepasstes_new, meldung)  bei eindeutigem Treffer
      (None, None, fehlermeldung)             bei Mehrdeutigkeit/Gefahr
      (None, None, "")                        wenn keine Stufe greift."""
    lines = content.split("\n")
    o_lines = old.split("\n")
    n = len(o_lines)

    # Stufe 1: zeilenweise getrimmt — deckt Einrueckungs-Drift (Modell hat den
    # Block aus anderer Verschachtelungstiefe kopiert) UND Whitespace-Reste ab.
    o_trim = [_uni(l.strip()) for l in o_lines]
    hits = [i for i in range(len(lines) - n + 1)
            if [_uni(l.strip()) for l in lines[i:i + n]] == o_trim]
    if len(hits) > 1:
        return None, None, (f"FEHLER: 'old' passt (zeilenweise getrimmt) auf "
                            f"{len(hits)} Stellen — nicht eindeutig. Nimm mehr "
                            f"umgebenden Kontext in den Ausschnitt.")
    if len(hits) == 1:
        i = hits[0]
        span = "\n".join(lines[i:i + n])
        delta = ((len(lines[i]) - len(lines[i].lstrip()))
                 - (len(o_lines[0]) - len(o_lines[0].lstrip())))
        return (span, _shift_indent(new, delta) if delta else new,
                f"old nicht woertlich gefunden — zeilenweise getrimmt eindeutig "
                f"ab Zeile {i + 1} identifiziert"
                + (f", Einrueckung von new um {delta:+d} Spalten angepasst"
                   if delta else ""))

    # Stufe 2: Escape-Ebene entfernen (old/new kamen doppelt escaped an).
    if "\\n" in old or "\\t" in old:
        old2 = _unescape_once(old)
        if old2 != old and old2 in content:
            if content.count(old2) > 1:
                return None, None, ("FEHLER: 'old' kommt (nach Entfernen der "
                                    "Escape-Ebene) mehrfach vor — nicht "
                                    "eindeutig. Mehr Kontext angeben.")
            return (old2, _unescape_once(new),
                    "old/new waren doppelt escaped — Escape-Ebene entfernt")

    # Stufe 3: Block-Anker fuer Bloecke ab 3 Zeilen — erste und letzte Zeile
    # muessen (getrimmt) exakt stimmen, die Mitte nur zu >= 75%% aehneln.
    # Rettet Edits, bei denen das Modell das Innere eines Blocks aus dem
    # Gedaechtnis statt aus der Datei zitiert hat. Groessen-Wächter: der
    # gefundene Block darf nicht deutlich groesser sein als 'old', sonst
    # wuerde die Ersetzung still zu viel Code fressen.
    if n >= 3 and o_trim[0] and o_trim[-1]:
        cands = []
        for i in range(len(lines)):
            if _uni(lines[i].strip()) != o_trim[0]:
                continue
            for j in range(i + 2, min(i + n + 3, len(lines))):
                if _uni(lines[j].strip()) == o_trim[-1]:
                    mid_file = "\n".join(x.strip() for x in lines[i + 1:j])
                    mid_old = "\n".join(o_trim[1:-1])
                    r = difflib.SequenceMatcher(None, mid_file, mid_old).ratio()
                    if r >= 0.75:
                        cands.append((i, j, r))
                    break  # je Anfangs-Anker nur das naechste passende Ende
        if len(cands) > 1:
            return None, None, ("FEHLER: 'old' passt per Block-Anker auf "
                                f"{len(cands)} Stellen — nicht eindeutig. "
                                "Mehr Kontext angeben.")
        if len(cands) == 1:
            i, j, r = cands[0]
            if j - i + 1 > n + 3:
                return None, None, (
                    "FEHLER: die per Anker gefundene Stelle ist deutlich "
                    "GROESSER als dein 'old' — Ersetzung abgelehnt, um nicht "
                    "zu viel Code zu ersetzen. Lies die Datei neu (read_file) "
                    "und gib 'old' vollstaendig und exakt an.")
            span = "\n".join(lines[i:j + 1])
            return (span, new,
                    f"old per Block-Anker (Mitte {r:.0%} aehnlich) eindeutig "
                    f"ab Zeile {i + 1} identifiziert")
    return None, None, ""


def do_edit_file(args):
    """Ersetzt in einer bestehenden Datei einen exakten Textausschnitt durch einen
    neuen — es wandert nur die Aenderung ueber die Leitung, nicht die ganze Datei.
    'old' muss EINDEUTIG vorkommen (sonst Fehler), ausser replace_all=true."""
    path = args.get("path", "")
    old = args.get("old", "")
    new = args.get("new", "")
    replace_all = bool(args.get("replace_all", False))
    fuzzy_note = ""  # gesetzt, wenn die Toleranz-Kaskade den Treffer fand
    if not path or old == "":
        return False, ("FEHLER: 'path' und 'old' sind erforderlich. Tipp: gib "
                       "old/new nicht als JSON-Strings an, sondern als rohe "
                       "```old und ```new Bloecke direkt nach dem action-Block.")
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return False, f"FEHLER beim Lesen von {path}: {e}"

    count = content.count(old)
    if count == 0:
        # Whitespace-Toleranz: NUR \r und Leerraum am ZEILENENDE duerfen
        # abweichen (Einrueckung am Zeilenanfang bleibt signifikant) — das
        # deckt die haeufigsten Fehltreffer kleiner Modelle ab, ohne falsche
        # Stellen zu treffen.
        pat = r"\r?\n".join(re.escape(l.rstrip()) + r"[ \t]*"
                            for l in old.replace("\r\n", "\n").split("\n"))
        try:
            hits = list(re.finditer(pat, content))
        except re.error:
            hits = []
        if len(hits) == 1:
            old = hits[0].group(0)  # exakten Datei-Text uebernehmen
            count = 1
            print(f"{C.DIM}(old nur mit Zeilenende-Whitespace-Toleranz gefunden "
                  f"— uebernehme den exakten Datei-Text){C.RESET}")
        elif len(hits) > 1:
            return False, (f"FEHLER: 'old' kommt (mit Whitespace-Toleranz) {len(hits)}x "
                           f"in {path} vor — nicht eindeutig. Mache den Ausschnitt "
                           f"groesser/eindeutiger.")
        elif not replace_all:
            # Toleranz-Kaskade (nur ohne replace_all — bei Umbenennungen ist
            # ein kurzes, mehrfach vorkommendes 'old' ja Absicht).
            f_old, f_new, note = _fuzzy_edit_match(content, old, new)
            if f_old is not None:
                old, new = f_old, f_new
                count = 1
                fuzzy_note = note
                print(f"{C.DIM}({note}){C.RESET}")
            elif note:
                return False, note  # eindeutige Diagnose (mehrdeutig/zu gross)
            else:
                return False, (f"FEHLER: der zu ersetzende Text wurde in {path} nicht "
                               f"gefunden. Gib 'old' exakt wie im Datei-Inhalt an "
                               f"(Whitespace zaehlt)." + _closest_snippet(content, old))
        else:
            return False, (f"FEHLER: der zu ersetzende Text wurde in {path} nicht "
                           f"gefunden. Gib 'old' exakt wie im Datei-Inhalt an "
                           f"(Whitespace zaehlt)." + _closest_snippet(content, old))
    if count > 1 and not replace_all:
        return False, (f"FEHLER: 'old' kommt {count}x in {path} vor — nicht eindeutig. "
                       f"Entweder den Ausschnitt groesser/eindeutiger machen, ODER — "
                       f"wenn du ALLE Vorkommen ersetzen willst (z.B. bei einer "
                       f"Umbenennung) — dieselbe Aktion mit \"replace_all\":true und "
                       f"NUR dem kurzen Namen als 'old' wiederholen (ein Schritt pro "
                       f"Datei statt vieler Einzel-Edits).")

    print(f"{C.YELLOW}» edit_file{C.RESET} {C.BOLD}{path}{C.RESET} "
          f"({count}x ersetzen)" if replace_all else
          f"{C.YELLOW}» edit_file{C.RESET} {C.BOLD}{path}{C.RESET}")
    print(f"{C.RED}- {old[:200]}{C.RESET}")
    print(f"{C.GREEN}+ {new[:200]}{C.RESET}")
    if not confirm(f"Aenderung in '{path}' anwenden?"):
        return False, user_reject_msg()
    try:
        updated = content.replace(old, new) if replace_all else content.replace(old, new, 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(updated)
        return True, (f"OK, {count if replace_all else 1} Stelle(n) in {path} ersetzt "
                      f"(Datei jetzt {len(updated)} Zeichen)."
                      + (f" Hinweis: {fuzzy_note} — gib 'old' kuenftig exakt "
                         f"aus der Datei an." if fuzzy_note else "")
                      + _loss_warning(path, content, updated)
                      + _duplicate_warning(path, content, updated)
                      + _reference_warning(path))
    except Exception as e:
        return False, f"FEHLER beim Schreiben von {path}: {e}"


def do_list_dir(args):
    global EXPLORED
    EXPLORED = True
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
# mc_skills gehoert dazu: Skill-Vorlagen sind Tool-Zubehoer, kein
# Projekt-Bestand (sonst wuerde ein leeres Projekt mit Skills faelschlich
# als 'hat Bestandscode' gelten und z.B. die Neubau-Bremse ausloesen).
IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
               ".mypy_cache", ".pytest_cache", ".idea", ".vscode", "dist",
               "build", "mc_skills"}


def _is_venv_dir(path):
    """Erkennt eine Python-Virtualenv UNABHAENGIG vom Namen -- ueber die von
    'python -m venv'/virtualenv erzeugte Markerdatei pyvenv.cfg, statt sich
    auf uebliche Namen wie 'venv'/'.venv' (siehe IGNORE_DIRS) zu verlassen.
    Real beobachtet: eine 'whisper-env' genannte venv in einem fremden
    Scratchpad-Unterordner wurde sonst wie ein normaler Projektordner
    durchsucht -- Hunderte fremder site-packages-Dateien landeten im
    Code-Outline und blaehten den System-Prompt sinnlos auf."""
    try:
        return os.path.isfile(os.path.join(path, "pyvenv.cfg"))
    except OSError:
        return False


def _norm(s):
    """Auf Kleinbuchstaben + nur alphanumerisch reduzieren — fuer unscharfen
    Vergleich, z.B. 'hello world' == 'helloworld'."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def do_find(args):
    """Sucht Dateien, deren Name das Muster enthaelt — auch unscharf
    (Leerzeichen/Sonderzeichen werden ignoriert)."""
    global EXPLORED
    EXPLORED = True
    pattern = args.get("pattern") or args.get("name") or ""
    root = args.get("path", ".")
    if not pattern:
        return False, "FEHLER: 'pattern' fehlt."
    npat = _norm(pattern)
    matches = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".") and not _is_venv_dir(os.path.join(dirpath, d))]
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
    global EXPLORED
    EXPLORED = True
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
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".") and not _is_venv_dir(os.path.join(dirpath, d))]
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
                             if d not in IGNORE_DIRS and not d.startswith(".") and not _is_venv_dir(os.path.join(dirpath, d)))
        rel = os.path.relpath(dirpath, root)
        for fn in sorted(filenames):
            paths.append(fn if rel == "." else os.path.join(rel, fn))
            if len(paths) >= max_entries:
                paths.append(f"... (>{max_entries} Dateien, gekuerzt)")
                return paths
    return paths


def repo_brief(root="."):
    """Deterministischer Projekt-Steckbrief OHNE Modell-Aufruf: erkannter
    Stack, real vorhandene Kommandos, juengste Git-Historie. Ein kleines
    Modell, das schwarz auf weiss liest 'hier existiert ein Python-Projekt,
    Tests laufen per pytest, letzter Commit war X', startet mit der
    Grundannahme BESTAND statt Neubau — der billigste Hebel gegen den
    Neubau-Reflex, weil er vor dem ersten Modell-Token wirkt."""
    def hat(p):
        return os.path.exists(os.path.join(root, p))
    zeilen, stacks, cmds = [], [], []
    if hat("pyproject.toml") or hat("requirements.txt") or hat("setup.py"):
        stacks.append("Python")
        if hat("requirements.txt"):
            cmds.append("pip install -r requirements.txt")
        if hat("pytest.ini") or hat("tests") or hat("test"):
            cmds.append("python3 -m pytest")
    if hat("package.json"):
        try:
            with open(os.path.join(root, "package.json"), encoding="utf-8") as f:
                pkg = json.load(f)
        except Exception:
            pkg = {}
        deps = {}
        if isinstance(pkg, dict):
            deps = {**(pkg.get("dependencies") or {}),
                    **(pkg.get("devDependencies") or {})}
        art = next((k for k in ("next", "react", "vue", "svelte", "vite",
                                "express", "flask") if k in deps), "")
        stacks.append("Node/JS" + (f" ({art})" if art else ""))
        for s in ("dev", "build", "test", "lint"):
            if isinstance(pkg, dict) and s in (pkg.get("scripts") or {}):
                cmds.append(f"npm run {s}")
    if hat("Cargo.toml"):
        stacks.append("Rust")
        cmds.append("cargo test")
    if hat("go.mod"):
        stacks.append("Go")
        cmds.append("go test ./...")
    if hat("composer.json"):
        stacks.append("PHP")
    if stacks:
        zeilen.append("Erkannter Stack: " + ", ".join(stacks))
    if cmds:
        zeilen.append("Real vorhandene Kommandos: " + " | ".join(cmds))
    try:
        r = subprocess.run(["git", "log", "--oneline", "-5"], cwd=root,
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            zeilen.append("Letzte Commits:\n  "
                          + "\n  ".join(r.stdout.strip().splitlines()))
    except Exception:
        pass
    return zeilen


ROUTE_RE = re.compile(r"\.(route|get|post|put|delete|patch)\(\s*['\"]([^'\"]+)")
JS_DEF_RE = re.compile(
    r"^[ \t]*(?:export\s+(?:default\s+)?)?(?:async\s+)?"
    r"(?:function\s+(\w+)|class\s+(\w+)|const\s+(\w+)\s*=\s*(?:async\s*)?[(f])",
    re.MULTILINE)


def _outline_py(path):
    """Top-Level-Klassen/Funktionen (+Routen-Dekoratoren) einer Python-Datei."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            src = f.read()
        tree = ast.parse(src)
    except Exception:
        return []
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            route = ""
            for d in node.decorator_list:
                seg = ast.get_source_segment(src, d) or ""
                m = ROUTE_RE.search(seg)
                if m:
                    route = f" [{m.group(1)} {m.group(2)}]"
                    break
            out.append(f"def {node.name}() Z{node.lineno}{route}")
        elif isinstance(node, ast.ClassDef):
            meth = [m.name for m in node.body
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
            out.append(f"class {node.name} Z{node.lineno}"
                       + (f" ({', '.join(meth[:6])})" if meth else ""))
    return out


def _outline_js(path):
    """Funktionen/Klassen/Routen einer JS/TS-Datei (Regex-Naeherung)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            src = f.read()
    except Exception:
        return []
    out = []
    for m in JS_DEF_RE.finditer(src):
        name = m.group(1) or m.group(2) or m.group(3)
        line = src.count("\n", 0, m.start()) + 1
        out.append(("class " if m.group(2) else "fn ") + f"{name} Z{line}")
    for m in ROUTE_RE.finditer(src):
        out.append(f"route {m.group(1).upper()} {m.group(2)}")
    return out[:15]


def code_outline(root=".", max_files=30):
    """Kompakte Struktur-Uebersicht des Bestandscodes ohne Modell-Aufruf:
    je Quelldatei die Klassen/Funktionen/Routen mit Zeilennummern. Ein Modell,
    das nur DATEINAMEN sieht, kennt den Bestand nicht — erst die Struktur
    macht 'verstehen vor aendern' billig genug, dass kleine Modelle es tun."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in IGNORE_DIRS and not d.startswith(".") and not _is_venv_dir(os.path.join(dirpath, d)))
        for fn in sorted(filenames):
            ext = os.path.splitext(fn)[1].lower()
            if ext not in (".py", ".js", ".jsx", ".ts", ".tsx"):
                continue
            full = os.path.join(dirpath, fn)
            items = (_outline_py(full) if ext == ".py" else _outline_js(full))
            if not items:
                continue
            rel = os.path.normpath(os.path.relpath(full, root))
            out.append(f"{rel}: " + " · ".join(items[:12]))
            if len(out) >= max_files:
                out.append("... (Struktur-Uebersicht gekappt)")
                return out
    return out


def do_ask(args):
    """Stellt dem Nutzer eine Frage (z.B. um einen Plan bestaetigen zu lassen)
    und gibt dessen Antwort an den Agenten zurueck."""
    question = (args.get("question") or "").strip() or "(keine Frage angegeben)"
    print(f"{C.CYAN}{C.BOLD}» Rueckfrage:{C.RESET} {question}")
    if AUTO_YES:
        print(f"{C.DIM}(--yes aktiv: ohne Rueckfrage fortfahren){C.RESET}")
        return True, "Auto-Modus (--yes): triff sinnvolle Annahmen und fahre ohne Rueckfrage fort."
    try:
        ans = input(rl_prompt(f"{C.GREEN}{C.BOLD}deine Antwort> {C.RESET}")).strip()
    except EOFError:
        return True, ("Keine Eingabe moeglich (nicht-interaktiv): triff sinnvolle "
                      "Annahmen und fahre fort.")
    if not ans:
        return True, "(keine Antwort) Triff eine sinnvolle Annahme und fahre fort."
    return True, f"Antwort des Nutzers: {ans}"


def _generator_conflict(cmd):
    """Faengt Scaffolder-Aufrufe (npm create …) ab, deren Zielverzeichnis bereits
    existiert und Inhalt hat: die fragen dann interaktiv 'Overwrite?' und haengen
    bis zum Timeout (real beobachtet beim zweiten Lauf im selben Projektordner).
    Heuristik: jedes flaglose Kommando-Token, das ein nicht-leeres Verzeichnis
    benennt, gilt als Konflikt."""
    if not GENERATOR_RE.search(cmd):
        return ""
    # Werkzeug-Zubehoer zaehlt nicht als Projektinhalt: seit dem Auto-Git der
    # Projektverwaltung kommen neue Projektordner mit .git/.gitignore zur
    # Welt — fuer Generatoren sind solche Ordner trotzdem 'praktisch leer'
    # (real beobachtet: die Bremse bzw. npm selbst blockierte den ERSTEN
    # Bauauftrag eines frischen Projekts).
    ZUBEHOER = {".git", ".gitignore", "MC-NOTIZEN.md", "mc_plan.md",
                "mc_verlauf.json", ".DS_Store"}

    def echter_inhalt(d):
        try:
            return [f for f in os.listdir(d) if f not in ZUBEHOER]
        except OSError:
            return []

    # Ziel '.': npm/vite fragen bei JEDEM vorhandenen Inhalt interaktiv nach
    # (auch bei blossem .git) und haengen dann — deshalb hier aktiv auf einen
    # Unterordner umlenken, statt es haengen zu lassen.
    if re.search(r"\s\.\s*($|&&|;)", cmd) or cmd.rstrip().endswith(" ."):
        if os.listdir("."):
            return ("ABGELEHNT: der Generator soll ins AKTUELLE Verzeichnis "
                    "schreiben, das ist aber nicht leer (u.a. Git-Zubehoer) — "
                    "er wuerde interaktiv nach 'Overwrite?' fragen und haengen. "
                    "Nutze stattdessen einen UNTERORDNER, z.B. "
                    "'npm create vite@latest frontend -- --template vanilla', "
                    "und arbeite dann in frontend/ weiter.")
    skip = {"npm", "npx", "yarn", "pnpm", "create", "init", "--", "&&", ";", "."}
    for t in re.split(r"\s+", cmd):
        if not t or t.startswith("-") or "@" in t or "/" in t or t in skip:
            continue
        try:
            if os.path.isdir(t) and echter_inhalt(t):
                return (f"ABGELEHNT: das Zielverzeichnis '{t}' existiert bereits und "
                        f"ist nicht leer — der Generator wuerde interaktiv nach "
                        f"'Overwrite?' fragen und haengen. Das Projekt ist also schon "
                        f"angelegt: arbeite direkt an den bestehenden Dateien weiter "
                        f"(list_dir/read_file/edit_file) statt neu zu generieren.")
        except OSError:
            continue
    return ""


SHELL_READS = {}  # Pfad -> Anzahl Shell-Lesezugriffe in diesem Lauf
READ_CMD_RE = re.compile(r"^\s*(cat|head|tail|awk|sed|more|type)\b")


def _shell_read_hint(cmd):
    """Shell-Lesekommandos (cat/sed -n/head/...) auf Projektdateien: die Datei
    als 'gelesen' registrieren (sonst ist das Overwrite-Gate blind fuer per
    Shell gelesene Inhalte) und Blaetter-Schleifen erkennen. Real beobachtet:
    ein starkes Modell las dieselbe Datei 24x in variierenden sed-Haeppchen —
    zyklisch bis ins Schrittlimit; die Konsekutiv-Erkennung im Loop griff
    nicht, weil kein Aufruf dem vorigen exakt glich."""
    if not READ_CMD_RE.match(cmd):
        return ""
    hint = ""
    for tok in cmd.split():
        tok = tok.strip("'\";|&()")
        if ("/" in tok or "." in tok) and os.path.isfile(tok):
            norm = os.path.normpath(tok)
            READ_FILES.add(norm)
            n = SHELL_READS.get(norm, 0) + 1
            SHELL_READS[norm] = n
            if n >= 3 and n % 3 == 0:
                hint += (f"\nHINWEIS: du liest {tok} jetzt zum {n}. Mal ueber die "
                         f"Shell. Hoer auf, in der Datei zu blaettern: nutze EINMAL "
                         f"read_file fuer den kompletten Inhalt und fuehre dann "
                         f"SOFORT die geplante Aenderung mit edit_file aus.")
                print(f"{C.YELLOW}⚠ Shell-Lese-Schleife: {tok} zum {n}. Mal "
                      f"— Hinweis angehaengt.{C.RESET}")
    return hint


def bg_status():
    """Laufende, von mc gestartete Hintergrundprozesse als (pid, kommando)."""
    return [(p.pid, p.args if isinstance(p.args, str) else " ".join(p.args))
            for p in BG_PROCS if p.poll() is None]


def _kill_hint(pid):
    """Plattformrichtiges Kommando, um einen Prozess(baum) zu beenden."""
    if sys.platform == "win32":
        return f"taskkill /F /T /PID {pid}"
    return f"kill {pid}"


def _addr_in_use_hint(output):
    """Erkennt 'Port belegt'-Fehler und benennt die wahrscheinliche Ursache:
    den eigenen, frueher gestarteten Hintergrundprozess — mit konkretem
    Kill-Kommando. WICHTIG dabei: den Port NICHT wechseln (das Frontend/
    andere Teile referenzieren ihn bereits)."""
    if not ADDR_IN_USE.search(output):
        return ""
    running = bg_status()
    hint = ("\nHINWEIS: Der Port ist bereits belegt — sehr wahrscheinlich durch "
            "deinen EIGENEN, frueher gestarteten Hintergrundprozess. Beende den "
            "alten Prozess und starte dann ERNEUT AUF DEMSELBEN PORT. Wechsle "
            "NICHT den Port — andere Teile des Projekts (z.B. das Frontend) "
            "referenzieren ihn bereits.")
    if running:
        hint += "\nLaufende Hintergrundprozesse:"
        for pid, cmd in running:
            hint += f"\n  pid={pid}: {cmd}\n    beenden mit: {_kill_hint(pid)}"
    print(f"{C.YELLOW}⚠ Port belegt erkannt — Hinweis (eigenen Prozess beenden, "
          f"Port behalten) angehaengt.{C.RESET}")
    return hint


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
    conflict = _generator_conflict(cmd)
    if conflict:
        print(f"{C.RED}✗ Generator-Konflikt erkannt{C.RESET}")
        return False, conflict
    if not confirm("Kommando ausfuehren?"):
        return False, user_reject_msg()
    if bg:
        # Dauerlaeufer (Dev-Server): starten, kurz warten, erste Ausgabe zeigen.
        # Der Prozess laeuft weiter; alle BG-Prozesse werden am Ende beendet.
        import tempfile
        logf = tempfile.NamedTemporaryFile(prefix="mc_bg_", suffix=".log",
                                           delete=False, mode="w")
        kwargs = dict(shell=True, stdout=logf, stderr=subprocess.STDOUT,
                      stdin=subprocess.DEVNULL)
        if sys.platform == "win32":
            # start_new_session ist POSIX-only; unter Windows braucht der
            # spaetere Kill des ganzen Prozessbaums eine eigene Prozessgruppe.
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(cmd, **kwargs)
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
                           f"Ausgabe:\n{truncate(head or '(keine)')}"
                           + _addr_in_use_hint(head))
        msg = (f"laeuft im Hintergrund (pid={proc.pid}). Erste Ausgabe:\n"
               f"{truncate(head or '(noch keine)')}\n"
               "Pruefe den Dienst jetzt mit einem normalen run (z.B. curl). "
               "Hintergrundprozesse werden am Ende automatisch beendet.")
        # Doppelstart-Schutzhinweis: laufende Geschwister-Prozesse benennen —
        # sonst startet das Modell denselben Dienst mehrfach, der Port ist
        # belegt, und es "loest" das per Port-Wechsel (siehe ADDR_IN_USE).
        others = [(pid, c) for pid, c in bg_status() if pid != proc.pid]
        if others:
            msg += "\nACHTUNG: es laufen bereits weitere Hintergrundprozesse von dir:"
            for pid, c in others:
                msg += f"\n  pid={pid}: {c}  (beenden: {_kill_hint(pid)})"
            msg += ("\nStarte denselben Dienst NICHT doppelt — beende zuerst den "
                    "alten Prozess, falls das ein Neustart sein sollte.")
        return True, msg
    try:
        # stdin geschlossen: ein Kommando, das interaktiv fragt (z.B. npm-Scaffolder
        # bei 'Overwrite?'), bekommt sofort EOF und scheitert mit lesbarer Meldung,
        # statt still bis zum Timeout auf eine Eingabe zu warten.
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL
        )
        out = proc.stdout + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        out = out.strip() or "(keine Ausgabe)"
        warn = ""
        if SHELL_BG.search(cmd):
            warn = ("\nACHTUNG: Dieses Kommando endet auf '&' (Shell-Hintergrundstart) — "
                    "ein so gestarteter Prozess wird von mc NICHT verfolgt und beim "
                    "Programmende NICHT automatisch beendet (verwaist danach). Nutze "
                    "fuer Dauerlaeufer stattdessen \"background\":true.")
        warn += _addr_in_use_hint(out)
        warn += _shell_read_hint(cmd)
        if len(out) > MAX_OUTPUT_CHARS and FETCH_URL_RE.search(cmd):
            # Grosser curl/wget-Abruf (z.B. eine ganze Webseite): statt blind
            # auf MAX_OUTPUT_CHARS zu kuerzen (haengt bei HTML oft nur im
            # <head> fest), isoliert analysieren statt in den Verlauf zu
            # uebernehmen (siehe summarize_large_fetch).
            body = summarize_large_fetch(out, CURRENT_MODEL)
        else:
            body = truncate(out)
        return True, f"exit={proc.returncode}\n{body}" + warn
    except subprocess.TimeoutExpired:
        return False, (f"FEHLER: Kommando-Timeout ({timeout}s). Moegliche Ursachen: "
                       "(1) es ist ein Dauerlaeufer (Dev-Server) — dann mit "
                       "\"background\":true starten; (2) es hat auf eine INTERAKTIVE "
                       "Eingabe gewartet (z.B. eine Ja/Nein- oder Overwrite-Frage) — "
                       "dann mit non-interaktiven Flags erneut ausfuehren "
                       "(-y/--yes bzw. CI=true als Umgebungsvariable).")
    except Exception as e:
        return False, f"FEHLER bei Ausfuehrung: {e}"


def kill_bg_procs():
    """Beendet alle vom Modell gestarteten Hintergrundprozesse samt Kindern —
    POSIX ueber die Prozessgruppe (start_new_session), Windows ueber
    'taskkill /T' (os.killpg existiert dort nicht; vorher wurde die Exception
    still geschluckt und jeder Dev-Server blieb als Zombie zurueck)."""
    import signal
    for p in BG_PROCS:
        if p.poll() is None:
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                                   capture_output=True, timeout=10)
                else:
                    os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except Exception:
                pass
    if BG_PROCS:
        time.sleep(0.5)
        n = sum(1 for p in BG_PROCS if p.poll() is not None)
        info(f"{n}/{len(BG_PROCS)} Hintergrundprozess(e) beendet.")


DISPATCH = {
    "read_file": do_read_file,
    "read_files": do_read_files,
    "write_file": do_write_file,
    "write_files": do_write_files,
    "edit_file": do_edit_file,
    "list_dir": do_list_dir,
    "find": do_find,
    "grep": do_grep,
    "ask": do_ask,
    "run": do_run,
    "explore": do_explore,
}


# ------------------------------ System-Prompt ------------------------------

SYSTEM_PROMPT_TEMPLATE = """Du bist ein praeziser Coding-Agent, der in einer Shell-Umgebung arbeitet.
Du kannst NICHT direkt auf Dateien zugreifen. Stattdessen forderst du EINE Aktion pro
Antwort an, indem du genau EINEN ```action``` Block mit JSON ausgibst. Du erhaeltst dann
das Ergebnis und faehrst fort.

Verfuegbare Aktionen (Feld "action"):
  read_file   -> {"action":"read_file","path":"<pfad>"}  (optional "from"/"to": Zeilenbereich, fuer den Mittelteil grosser Dateien — NICHT per sed/cat blaettern)
  read_files  -> {"action":"read_files","paths":["a","b/c"]}  (mehrere Dateien in EINEM Schritt, max 5 — spart Umlaeufe)
  explore     -> {"action":"explore","task":"<erkundungs-auftrag>"}  (fuer BREITE Erkundungen: laeuft isoliert mit frischem Kontext, NUR die Zusammenfassung kommt in deinen Verlauf)
@@WRITE_SPEC@@
@@EDIT_SPEC@@
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
- Das Ueberschreiben einer BEREITS EXISTIERENDEN Datei per write_file/write_files
  wird vom Tool ABGELEHNT, solange du sie in diesem Lauf nicht mit read_file
  gelesen hast. Also: erst lesen, dann gezielt mit edit_file aendern. Nur wenn
  ein kompletter Neuschrieb wirklich gewollt ist: "overwrite":true mitgeben.
- WICHTIG: Wenn der Nutzer eine bestehende Datei AENDERN will, lege NIEMALS einfach
  eine neue an. Suche sie zuerst mit find/list_dir. Nutzer benennen Dateien oft
  ungenau — "hello world" kann "helloworld.py", "HelloWorld.js" o.ae. heissen.
  find ignoriert Gross-/Kleinschreibung und Leer-/Sonderzeichen.
- Ein LEERES find/grep-Ergebnis heisst NICHT 'gibt es nicht': verbreitere das
  Suchmuster (kuerzerer Begriff, andere Schreibweise, ab Wurzel suchen) und
  suche ERNEUT, bevor du etwas als fehlend einstufst oder neu anlegst.
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
- UMBENENNUNGEN (derselbe Name kommt an VIELEN Stellen vor, z.B. ein Feld- oder
  Funktionsname): NICHT viele einzelne edit_file-Schritte mit grossen Bloecken!
  Stattdessen pro betroffener Datei genau EIN edit_file mit dem kurzen Namen
  als "old", dem neuen Namen als "new" und "replace_all":true. Die betroffenen
  Dateien findest du vorher mit grep.
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
- PORTWAHL fuer Server/Dienste: meide Port 5000 (auf macOS oft durch AirPlay
  belegt) sowie Ports, die Browser als "unsafe" blockieren und NIE ansprechen,
  egal ob dort ein Server lauscht (u.a. 5060/5061 SIP, 6000 X11, 6665-6669 IRC
  -> im Browser ERR_UNSAFE_PORT, obwohl curl funktioniert). Sichere Wahl:
  5010-5059, 5065-5099, 8000-8999.
- PROJEKT-NOTIZEN: Triffst du eine FESTLEGUNG, die spaetere Laeufe kennen
  muessen (fester Port, Feld-/Spaltennamen, gewaehlte Bibliothek, Start-
  Kommandos), halte sie STICHPUNKTARTIG in der Datei MC-NOTIZEN.md fest
  (anlegen bzw. per edit_file ergaenzen — kurz halten, keine Prosa). Steht
  in den Projekt-Notizen bereits eine Festlegung (z.B. ein fester Port),
  aendere sie NICHT, sondern passe abweichenden Code an die Festlegung an.
- DATEN-/DB-PFADE im Code absolut zur Skript-Datei aufloesen (BASE_DIR-Muster:
  os.path.join(os.path.dirname(os.path.abspath(__file__)), "daten.db")) statt
  relativ zum Arbeitsverzeichnis — sonst haengt es davon ab, von WO gestartet
  wird, und die App findet ihre eigene Datenbank nicht mehr.
- Fertiger Code laeuft OHNE Debug-Modus (z.B. Flask: app.run ohne debug=True —
  der Debugger erlaubt Code-Ausfuehrung im Browser und gehoert nicht in eine
  fertige App).
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

EDIT_SPEC_JSON = """  edit_file   -> {"action":"edit_file","path":"<pfad>","old":"<exakter ausschnitt>","new":"<ersatz>"}"""

EDIT_SPEC_FENCE = """  edit_file   -> {"action":"edit_file","path":"<pfad>"}  + danach EIN ```old Block (exakter bestehender Ausschnitt, ROH) und EIN ```new Block (Ersatz, ROH) — old/new NIE als JSON-Strings"""

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
     auch Fehlerfaelle (unbekannte ID sollte 404 liefern, nicht Erfolg) und
     UNGUELTIGE EINGABEN: ein FEHLENDES und ein LEERES Pflichtfeld muessen
     beide abgelehnt werden (400), nicht als Erfolg durchrutschen.
  4. Fehlermeldungen ERNST NEHMEN und beheben, dann erneut pruefen.
Hintergrundprozesse werden am Ende automatisch beendet. Verlasse dich nicht
auf dein Gedaechtnis, was eine Bibliothek 'haben muesste' — pruefe es
(z.B. ls node_modules/@material/web/) statt zu raten."""


ANALYSE_PROMPT = """Du bist ein praeziser Coding-Agent in der ANALYSE-PHASE fuer eine Aenderung an BESTEHENDEM Code.
In dieser Phase darfst du NICHTS schreiben oder ausfuehren — erst verstehen, dann planen.
Du forderst EINE Aktion pro Antwort an, indem du genau EINEN ```action``` Block mit JSON ausgibst.

Verfuegbare Aktionen (Feld "action"):
  read_file  -> {"action":"read_file","path":"<pfad>"}  (optional "from"/"to": Zeilenbereich)
  read_files -> {"action":"read_files","paths":["a","b/c"]}  (mehrere Dateien in EINEM Schritt, max 5)
  list_dir   -> {"action":"list_dir","path":"<pfad>"}
  find       -> {"action":"find","pattern":"<namensteil>"}
  grep       -> {"action":"grep","pattern":"<text oder regex>"}  (sucht IN Dateiinhalten, liefert Datei:Zeile)
  explore    -> {"action":"explore","task":"<erkundungs-auftrag>"}  (breite Erkundung in frischem Kontext, nur die Zusammenfassung kommt zurueck)
  ask        -> {"action":"ask","question":"<frage an den nutzer>"}
  plan       -> {"action":"plan","punkte":["<datei>: <konkrete aenderung>", "..."]}

Regeln:
- Finde ZUERST die betroffenen Stellen: grep nach Feld-/Funktions-/Routen-Namen,
  dann gezielt read_file. Die Struktur-Uebersicht unten zeigt dir, was existiert.
- Ein LEERES Suchergebnis heisst NICHT, dass der Code fehlt — verbreitere das
  Muster und suche erneut, bevor du irgendetwas als 'nicht vorhanden' einstufst.
- Lies JEDE Datei, die du aendern willst, BEVOR du planst.
- Schliesse die Phase mit der plan-Aktion ab: jeder Punkt genau EINE konkrete,
  kleine Aenderung mit Dateipfad (z.B. "backend/app.py: Feld 'gewicht' in
  POST /api/persons ergaenzen"). Keine vagen Punkte ('Code verbessern').
- Der Plan wird erst akzeptiert, wenn du mindestens eine Datei gelesen hast.
- Danach werden die Schreibaktionen freigeschaltet und du setzt die Punkte
  NACHEINANDER um.

Beispiel-Antwort:
Ich schaue mir zuerst das Backend an.
```action
{"action":"read_file","path":"app.py"}
```"""


def system_prompt(fence, analyse=False):
    """Baut den System-Prompt fuer den gewaehlten Modus zusammen. In der
    Analyse-Phase (analyse=True) enthaelt das Protokoll BEWUSST keine
    Schreibaktionen: was nicht im Protokoll steht, kann ein kleines Modell
    auch nicht benutzen — Weglassen ist zuverlaessiger als Verbieten."""
    if analyse:
        return ANALYSE_PROMPT
    sp = SYSTEM_PROMPT_TEMPLATE
    sp = sp.replace("@@WRITE_SPEC@@", WRITE_SPEC_FENCE if fence else WRITE_SPEC_JSON)
    sp = sp.replace("@@EDIT_SPEC@@", EDIT_SPEC_FENCE if fence else EDIT_SPEC_JSON)
    sp = sp.replace("@@CONTENT_RULE@@", CONTENT_RULE_FENCE if fence else CONTENT_RULE_JSON)
    sp = sp.replace("@@EXAMPLE@@", EXAMPLE_FENCE if fence else EXAMPLE_JSON)
    if CHECK:
        sp += "\n" + CHECK_PROMPT
    return sp


def _system_message_for_mode():
    """Der Inhalt fuer messages[0] je nach /mode: 'chat' nutzt einen kurzen,
    werkzeugfreien Prompt (kein Dev-/Aktions-Kontext), 'dev' den vollen
    System-Prompt samt Projekt-Steckbrief/Code-Outline (SYSTEM_CONTEXT)."""
    if MODE == "chat":
        return CHAT_SYSTEM_PROMPT
    return system_prompt(FENCE) + "\n\n" + SYSTEM_CONTEXT


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
    try:
        plan = chat_stream(messages, model)
    except CtxOverflowError:
        info("Kontext-Ueberlauf schon in der Plan-Phase — fahre ohne Plan fort.")
        messages.pop()  # Plan-Aufforderung wieder entfernen
        return True
    messages.append({"role": "assistant", "content": plan})

    if CHECK:
        global CHECK_PLAN
        m = re.search(r"pr(?:[üu]f|uef)schritte:?\s*(.+)", plan, re.IGNORECASE | re.DOTALL)
        CHECK_PLAN = (m.group(1) if m else plan).strip()[:1500]
    try:
        print()
        fb = input(rl_prompt(f"{C.YELLOW}Plan ok? [Enter]=ja · Text=Aenderungswunsch · "
                             f"n=abbrechen> {C.RESET}")).strip()
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
    # Backslashes normalisieren: Windows-Nutzer schreiben Pfade wie
    # backend\app.py — os-Funktionen akzeptieren auf Windows auch '/'.
    task = (task or "").replace("\\", "/")
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


def _resolve_project_file(p):
    """Loest einen in der Aufgabe genannten Pfad gegen den Projektbaum auf.
    Existiert er nicht woertlich, wird per SUFFIX gesucht (real beobachtet:
    der Prompt nannte 'src/App.jsx' relativ zum Frontend-Ordner, die Datei
    liegt unter 'frontend/src/App.jsx' — der Finish-Check meldete faelschlich
    'fehlt'). Eindeutiger Treffer -> aufgeloester Pfad, sonst None."""
    if os.path.isfile(p):
        return p
    target = p.replace("\\", "/").lstrip("./")
    hits = []
    for dirpath, dirnames, filenames in os.walk("."):
        dirnames[:] = [d for d in dirnames
                       if d not in IGNORE_DIRS and not d.startswith(".") and not _is_venv_dir(os.path.join(dirpath, d))]
        for fn in filenames:
            full = os.path.join(dirpath, fn).replace("\\", "/").lstrip("./")
            if full == target or full.endswith("/" + target):
                hits.append(os.path.normpath(os.path.join(dirpath, fn)))
                if len(hits) > 1:
                    return None  # mehrdeutig -> lieber nicht raten
    return hits[0] if len(hits) == 1 else None


# Marker-Dateien, an denen ein BESTEHENDES Projekt erkannt wird (fuer die
# deterministische Task-Anreicherung beim Start).
PROJECT_MARKERS = ("package.json", "vite.config.js", "vite.config.ts",
                   "requirements.txt", "pyproject.toml", "composer.json")
# Projekt-Gedaechtnis: Invarianten (feste Ports, Feldnamen, Konventionen), die
# Laeufe ueberdauern muessen. Wird beim Start in die Task-Hinweise eingespeist.
MC_NOTES = "MC-NOTIZEN.md"


def existing_project_dirs(max_depth=2):
    """Findet Verzeichnisse (inkl. '.'), die einen Projekt-Marker enthalten —
    flach gehalten (max. 2 Ebenen), es geht nur um den Startueberblick."""
    found = []
    for dirpath, dirnames, filenames in os.walk("."):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in IGNORE_DIRS and not d.startswith(".") and not _is_venv_dir(os.path.join(dirpath, d)))
        if dirpath.count(os.sep) >= max_depth:
            dirnames[:] = []
        for mk in PROJECT_MARKERS:
            if mk in filenames:
                found.append((os.path.normpath(dirpath), mk))
                break
    return found


def _write_plan_file(punkte):
    """Sichert den Aenderungsplan als Datei mit Abhak-Kaestchen — der Plan
    ueberlebt so Kontext-Kuerzungen und sogar Abbrueche: ein Folgelauf liest
    ihn ueber task_hints wieder ein und macht beim offenen Punkt weiter."""
    try:
        with open(MC_PLAN, "w", encoding="utf-8") as f:
            f.write("# Aenderungsplan (mc)\n\n" + "\n".join(
                f"- [ ] {i}. {p}" for i, p in enumerate(punkte, 1)) + "\n")
        return True
    except OSError:
        return False


def _git_diff_summary(max_chars=3500):
    """Gesamt-Diff des Laufs fuer den Selbstreview vor dem finish:
    git status (inkl. neuer Dateien) + gekapptes Diff der Aenderungen."""
    rc1, status = _git("status", "--short")
    rc2, diff = _git("diff")
    if rc1 != 0 or not status.strip():
        return ""
    out = "Geaenderte/neue Dateien:\n" + status.strip()
    if rc2 == 0 and diff.strip():
        d = diff.strip()
        if len(d) > max_chars:
            d = d[:max_chars] + f"\n...[Diff gekuerzt, {len(diff)} Zeichen gesamt]"
        out += "\n\nDiff:\n" + d
    return out


def task_hints(task):
    """Deterministische Ist-Zustand-Hinweise, die VOR dem ersten Modell-Call an
    die Aufgabe angehaengt werden (kein LLM-Aufruf, reiner Dateisystem-Check).
    Hintergrund: der Projektueberblick im System-Prompt ist eine passive Liste,
    die kleine Modelle zuverlaessig ignorieren — beim ZWEITEN Lauf im selben
    Verzeichnis behandeln sie alles als 'neu', ueberschreiben Bestehendes oder
    starten Generatoren, die interaktiv nach 'Overwrite?' fragen und haengen.
    Konkrete, aufgabenbezogene Anweisungen direkt in der User-Message wirken
    bei kleinen Modellen deutlich besser als eine Regel im System-Prompt."""
    hints = []
    # Projekt-Notizen: Invarianten und Festlegungen frueherer Laeufe (feste
    # Ports, Feldnamen, Konventionen). Jeder Lauf startet ohne Gedaechtnis —
    # real beobachtet: ein Reparatur-Lauf bog das Frontend auf den falschen
    # Backend-Port um, weil er die Festlegung "Port 5010" nicht kennen KONNTE.
    # Die Datei pflegt das Modell selbst (System-Prompt-Regel); hier wird sie
    # nur deterministisch eingelesen.
    if os.path.isfile(MC_NOTES):
        try:
            with open(MC_NOTES, "r", encoding="utf-8", errors="replace") as f:
                notes = f.read().strip()
            if notes:
                hints.append(f"Projekt-Notizen aus {MC_NOTES} (Festlegungen "
                             f"frueherer Laeufe — HALTE DICH DARAN):\n"
                             + notes[:2000])
        except OSError:
            pass
    # Offener Aenderungsplan aus einem frueheren (abgebrochenen) Lauf.
    if os.path.isfile(MC_PLAN):
        try:
            with open(MC_PLAN, "r", encoding="utf-8", errors="replace") as f:
                plan_inhalt = f.read().strip()
            if "- [ ]" in plan_inhalt:
                hints.append(
                    f"Es existiert ein OFFENER Aenderungsplan aus einem "
                    f"frueheren Lauf ({MC_PLAN}):\n" + plan_inhalt[:1500] +
                    f"\nSetze die offenen Punkte ('- [ ]') fort, hake erledigte "
                    f"per edit_file ab ('- [ ]' -> '- [x]') — oder loesche die "
                    f"Datei, wenn der Plan erledigt/obsolet ist.")
        except OSError:
            pass
    existing = [p for p in expected_files_from_task(task) if os.path.isfile(p)]
    projs = existing_project_dirs()
    if projs:
        desc = ", ".join(f"{d}/ ({mk} vorhanden)" for d, mk in projs[:8])
        hints.append(
            f"In diesem Arbeitsverzeichnis existiert BEREITS ein Projekt: {desc}. "
            f"Die Aufgabe ist daher eine WEITERENTWICKLUNG des Bestehenden, kein "
            f"Neubau. Verschaffe dir zuerst mit list_dir/read_file einen Ueberblick "
            f"und aendere bestehende Dateien gezielt mit edit_file.")
        hints.append(
            "Fuehre KEINEN Projekt-Generator erneut aus (npm create …, npx "
            "create-… o.ae.) — der wuerde interaktiv nach Ueberschreiben fragen "
            "und haengen. Abhaengigkeiten sind ggf. schon installiert.")
    if existing:
        hints.append(
            "Diese in der Aufgabe genannten Dateien existieren BEREITS: "
            + ", ".join(existing[:12]) +
            ". Lies sie mit read_file, bevor du sie aenderst — blindes "
            "Ueberschreiben wird vom Tool abgelehnt.")
    if not hints:
        return ""
    return ("\n\n[HINWEISE VOM TOOL — automatisch ermittelter Ist-Zustand]\n- "
            + "\n- ".join(hints))


# --------------------- Validierung & Git-Rollback --------------------------

TERSE_TASK_CHARS = 50
QA_START_RE = re.compile(
    r"^\s*(warum|wieso|weshalb|was (ist|sind|macht|passiert|bedeutet|fehlt)"
    r"|wie (funktioniert|ist|wird|laeuft|läuft|viele?)"
    r"|wo (ist|liegt|wird|steht)|welche[rs]?\b|gibt es)", re.IGNORECASE)
IMPERATIV_RE = re.compile(
    r"\b(bau|erstell|schreib|aender|änder|fix|beheb|ergaenz|ergänz|"
    r"implementier|loesch|lösch|entfern|refactor|migrier|fueg|füg|mach)\w*\b",
    re.IGNORECASE)


def terse_task_hint(task):
    """Knappheits-Stupser: Menschen tippen faul — voellig ok, wenn das
    Werkzeug die Luecke deterministisch fuellt. Sehr kurze Auftraege an
    Projekten MIT Bestand bekommen die Anweisung, die wahrscheinlichste
    Absicht aus Bestand/Notizen abzuleiten, bei ECHTER Mehrdeutigkeit genau
    EINE ask-Frage zu stellen und sonst mit benannten Annahmen loszulegen —
    statt zehn Schritte in die falsche Richtung zu laufen."""
    global HAS_CODE
    if len((task or "").strip()) >= TERSE_TASK_CHARS:
        return ""
    HAS_CODE = None  # frisch pruefen (interaktiv kann sich der Bestand aendern)
    if not _project_has_code():
        return ""
    return ("\n\n[HINWEIS VOM TOOL] Der Auftrag ist knapp gehalten. Leite die "
            "wahrscheinlichste Absicht aus dem Bestand ab (Struktur-Uebersicht, "
            "MC-NOTIZEN.md, aehnliche vorhandene Features als Vorbild). Nur bei "
            "ECHTER Mehrdeutigkeit: stelle genau EINE ask-Frage. Sonst: nenne "
            "in einem Satz deine Annahme(n) und beginne direkt.")


def qa_task_hint(task):
    """Frage-Weiche: liest sich der Auftrag wie eine FRAGE (Fragewort am
    Anfang oder '?' am Ende, ohne Umsetzungs-Verben), soll der Lauf sie mit
    Lese-Aktionen beantworten — nicht ungefragt Code aendern."""
    t = (task or "").strip()
    if not t or IMPERATIV_RE.search(t):
        return ""
    if not (QA_START_RE.search(t) or t.endswith("?")):
        return ""
    return ("\n\n[HINWEIS VOM TOOL] Das ist eine FRAGE. Beantworte sie mit "
            "Lese-Aktionen (grep/read_file/list_dir) und schliesse mit einer "
            "TEXT-Antwort ab — KEINE Schreibaktionen, nichts aendern.")


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


def _find_js_checker(path):
    """Sucht projektlokal (node_modules/.bin, vom Dateiverzeichnis aufwaerts)
    einen Syntax-Pruefer fuer JSX/TSX: esbuild bevorzugt (reiner Parser),
    sonst oxlint (bringt Vite 7+ mit; Warnungen lassen den Exit-Code bei 0,
    Parse-Fehler nicht). Nichts gefunden -> Validierung wird uebersprungen."""
    d = os.path.dirname(os.path.abspath(path))
    for _ in range(6):
        for name in ("esbuild", "oxlint"):
            for suffix in ("", ".cmd"):
                cand = os.path.join(d, "node_modules", ".bin", name + suffix)
                if os.path.isfile(cand):
                    return cand
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return ""


SCRIPT_TAG_RE = re.compile(
    r"<script\b(?![^>]*\bsrc\s*=)([^>]*)>(.*?)</script>", re.IGNORECASE | re.DOTALL)
_SCRIPT_TYPE_SKIP = {"importmap", "application/json", "application/ld+json",
                     "speculationrules"}


def _extract_inline_scripts(html):
    """Extrahiert eingebettete <script>-Bloecke OHNE src-Attribut (die mit
    src verweisen auf externe Dateien, kein Inline-Code zu pruefen) und ohne
    JSON-artige type-Attribute (importmap/application-json/...) -- nur
    echte JS/Modul-Bloecke."""
    out = []
    for m in SCRIPT_TAG_RE.finditer(html):
        typ_m = re.search(r'type\s*=\s*["\']([^"\']+)["\']', m.group(1), re.IGNORECASE)
        typ = (typ_m.group(1).lower() if typ_m else "")
        if typ in _SCRIPT_TYPE_SKIP:
            continue
        out.append(m.group(2))
    return out


def _check_js_syntax(js_text, near_path):
    """Prueft JS-Syntax: zuerst projektlokaler esbuild/oxlint (wie bei JSX/
    TSX), sonst system-weites 'node --check' als Fallback -- oft vorhanden
    AUCH OHNE npm-Projekt/node_modules, genau der Fall bei einer einzelnen
    HTML-Datei mit CDN-Importen (real beobachtet: ein fehlendes Argument in
    'new THREE.BoxGeometry(50, , 50)' blieb sonst unentdeckt, weil .html nie
    geprueft wurde). .mjs-Endung fuer node, damit import/export (ESM)
    korrekt geparst wird -- als .js faellt node sonst auf CommonJS zurueck
    und meldet bei jedem import-Statement einen falschen Fehler."""
    checker = _find_js_checker(near_path)
    if checker:
        suffix, cmd = ".js", '"{checker}" "{temp}"'
    else:
        node = shutil.which("node")
        if not node:
            return "skip", ""
        checker, suffix, cmd = node, ".mjs", '"{checker}" --check "{temp}"'
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(
            suffix=suffix, dir=os.path.dirname(os.path.abspath(near_path)) or ".")
        with os.fdopen(fd, "w", encoding="utf-8") as tf:
            tf.write(js_text)
        p = subprocess.run(cmd.format(checker=checker, temp=temp_path),
                           shell=True, capture_output=True, text=True, timeout=30)
    except Exception:
        return "skip", ""
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    if p.returncode == 0:
        warns = [l for l in out.splitlines() if "warning" in l.lower()]
        return "ok", " | ".join(warns[:3])[:300]
    lines = [l for l in out.splitlines()
            if "error" in l.lower() or "syntaxerror" in l.lower()]
    return "bad", " | ".join((lines or out.splitlines())[:3])[:300]


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
    if ext in (".jsx", ".tsx"):
        # Real beobachtet: ein edit_file setzte ein ueberzaehliges </div> in
        # eine React-Komponente — Vite lieferte nur noch die Fehler-Overlay-
        # Seite, aber das finish ging durch, weil .jsx nie geprueft wurde.
        checker = _find_js_checker(path)
        if not checker:
            return "skip", ""
        try:
            p = subprocess.run(f'"{checker}" "{os.path.abspath(path)}"',
                               shell=True, capture_output=True, text=True,
                               timeout=30)
        except Exception:
            return "skip", ""
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        if p.returncode == 0:
            # Warnungen blockieren nicht, werden aber als Hinweis durchgereicht
            # (real beobachtet: 'setSortOrder is declared but never used' —
            # sprich: das Sortier-Feature wurde nie fertig verdrahtet).
            warns = [l for l in out.splitlines() if "warning" in l.lower()]
            return "ok", " | ".join(warns[:3])[:300]
        lines = [l for l in out.splitlines() if "error" in l.lower()]
        return "bad", ("JSX/TSX-Fehler: "
                       + " | ".join((lines or out.splitlines())[:3])[:300])
    if ext in (".html", ".htm"):
        # Real beobachtet: eine einzelne index.html mit CDN-Three.js hatte
        # einen echten JS-SyntaxError im eingebetteten <script type="module">
        # ('new THREE.BoxGeometry(50, , 50)') -- unentdeckt, weil .html bisher
        # nie geprueft wurde (nur py/json/yaml/php/jsx/tsx).
        scripts = _extract_inline_scripts(text)
        if not scripts:
            return "skip", ""
        status, meldung = _check_js_syntax("\n;\n".join(scripts), path)
        if status == "bad":
            return "bad", f"Eingebettetes <script>-JS fehlerhaft: {meldung}"
        return status, meldung
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
    welche ungueltig sind; sonst ggf. nicht-blockierende Hinweise (Warnungen
    des Checkers), sonst leerer String."""
    if not VALIDATE:
        return ""
    bad, notes = [], []
    for p in paths:
        if not p or not os.path.isfile(p):
            continue
        status, msg = validate_path(p)
        if status == "bad":
            bad.append(f"  {p}: {msg}")
        elif status == "ok" and msg:
            notes.append(f"  {p}: {msg}")
    if bad:
        return ("VALIDIERUNG FEHLGESCHLAGEN — folgende Dateien sind ungueltig und "
                "muessen korrigiert werden:\n" + "\n".join(bad) +
                "\nKorrigiere NUR diese Datei(en) (am besten mit edit_file oder einer "
                "neuen, validen write_file).")
    if notes:
        return ("HINWEIS aus der Validierung (nicht blockierend, aber pruefen — "
                "z.B. deutet eine nie benutzte Variable auf ein halb verdrahtetes "
                "Feature hin):\n" + "\n".join(notes))
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


def _send_size_info(messages, model):
    """Kurzer Zeichen-/Token-Hinweis fuer Diagnosemeldungen -- 'wieviel haben
    wir eigentlich hingeschickt', damit sich 'vermutlich Kontextfenster
    ueberschritten' direkt nachpruefen laesst statt geraten werden zu
    muessen. Kein Netzwerk-Aufruf (nur der bereits bekannte Cache-Wert),
    damit die Diagnose selbst keine zusaetzliche Verzoegerung verursacht."""
    chars = sum(len(m.get("content", "") or "") for m in messages)
    token_schaetzung = int(chars / CHARS_PER_TOKEN)
    info_txt = f"gesendet: ~{chars} Zeichen (~{token_schaetzung} Token geschaetzt)"
    bekannt = _LOADED_CTX_TOKENS.get(model)
    if bekannt:
        info_txt += f", bekanntes Fenster: {bekannt} Token"
    return info_txt


def _ledger_block():
    """Datei-Kontobuch fuer die Zeit NACH einer Kontext-Kuerzung: welche
    Dateien dieser Lauf gelesen bzw. geschrieben hat. Die Kuerzung entfernt
    genau diese Erinnerung — und ein Modell, das die eigenen Edits vergisst,
    legt Dateien neu an oder fuegt Elemente doppelt ein (real passiert).
    Deterministisch aus READ_FILES/TOUCHED, null Halluzinationsrisiko."""
    if not (READ_FILES or TOUCHED):
        return ""
    teile = ["\n\n[KONTOBUCH VOM TOOL — Stand nach Kontext-Kuerzung]"]
    if TOUCHED:
        teile.append("Von dir bereits GESCHRIEBEN/GEAENDERT: "
                     + ", ".join(sorted(set(TOUCHED))[:20]))
    gelesen = sorted(READ_FILES
                     - {os.path.normpath(p) for p in TOUCHED})[:20]
    if gelesen:
        teile.append("Von dir bereits GELESEN: " + ", ".join(gelesen))
    teile.append("Diese Dateien existieren — NICHT neu anlegen; bei "
                 "Unsicherheit ueber den aktuellen Inhalt erneut lesen.")
    return "\n".join(teile)


def _save_transcript(messages):
    """Sichert den Verlauf (--resume) nach jedem Schritt — so bleibt auch ein
    hart abgebrochener Lauf fortsetzbar."""
    if not RESUME:
        return
    try:
        with open(MC_VERLAUF, "w", encoding="utf-8") as f:
            json.dump(messages, f, ensure_ascii=False)
    except OSError:
        pass


def _load_transcript():
    """Laedt einen gesicherten Verlauf — OHNE System-Message: die wird immer
    frisch gebaut, damit Prompt-Aenderungen und der aktuelle Projektstand
    gelten."""
    try:
        with open(MC_VERLAUF, "r", encoding="utf-8") as f:
            alte = json.load(f)
        return [m for m in alte if isinstance(m, dict)
                and m.get("role") in ("user", "assistant") and m.get("content")]
    except (OSError, ValueError):
        return []


def run_task(messages, model):
    """Fuehrt die Agenten-Schleife aus, bis 'finish' oder das Schrittlimit erreicht ist."""
    global RAN_SINCE_WRITE, CLEAN_FINISH, CURRENT_MODEL, EXPLORED, HAS_CODE
    CLEAN_FINISH = False
    CURRENT_MODEL = model
    EXPLORED = False
    HAS_CODE = None
    PLAN_POINTS.clear()
    # Aufgaben-lokalen Zustand zuruecksetzen: im interaktiven Modus galten
    # READ_FILES & Co. bisher fuer die GANZE Sitzung — eine in Aufgabe 1
    # gelesene Datei durfte in Aufgabe 5 noch blind ueberschrieben werden,
    # obwohl ihr Inhalt laengst veraltet sein konnte.
    READ_FILES.clear()
    OVERWRITE_REJECTS.clear()
    WRITE_HISTORY.clear()
    SHELL_READS.clear()
    LOSS_WARNED_NAMES.clear()
    RAN_SINCE_WRITE = False
    finish_rejects = 0
    parse_error_streak = 0
    check_probe_done = False
    prose_end_nudged = False
    prose_nudges = 0
    empty_replies = 0
    last_ro_raw = None  # raw-JSON der letzten NUR-LESE-Aktion (Schleifen-Erkennung)
    ctx_overflows = 0   # vom Endpoint gemeldete Kontext-Ueberlaeufe
    budget_warned = False
    notes_probe_done = False
    check_finish_pending = False  # finish wurde nur mangels Pruefung abgelehnt
    plan_probe_done = False
    diff_probe_done = False
    # Analyse-Phase: nur sinnvoll, wenn es ueberhaupt Bestand zu verstehen gibt.
    analyse_active = ANALYSE and _project_has_code()
    analyse_steps = 0
    analyse_nudged = False
    if analyse_active:
        messages[0]["content"] = (system_prompt(FENCE, analyse=True)
                                  + ("\n\n" + SYSTEM_CONTEXT if SYSTEM_CONTEXT else ""))
        info("Analyse-Phase aktiv: erst verstehen (nur Lese-Aktionen), dann "
             "Aenderungsplan, erst danach werden Schreibaktionen freigeschaltet.")
    for step in range(1, MAX_STEPS + 1):
        # Schrittbudget-Hinweis: das Modell weiss sonst nicht, dass ihm die
        # Schritte ausgehen (real beobachtet: die eigentliche Arbeit war nach
        # 15 Schritten fertig, dann 35 Schritte Verifikations-Perfektionismus
        # bis zum harten Abbruch OHNE finish — ein sauberes finish nach dem
        # Wichtigsten waere besser gewesen). Der Hinweis wird an die letzte
        # user-Nachricht angehaengt statt als eigene Message (zwei user-Rollen
        # hintereinander vertragen manche Chat-Templates nicht).
        remaining = MAX_STEPS - step + 1
        if (not budget_warned and remaining <= 5
                and messages and messages[-1]["role"] == "user"):
            budget_warned = True
            messages[-1]["content"] += (
                f"\n\n[BUDGET-HINWEIS VOM TOOL] Dir bleiben nur noch {remaining} "
                f"Schritte, danach wird der Lauf HART abgebrochen (ohne finish, "
                f"unfertig). Bringe die Aufgabe JETZT zum Abschluss: erledige nur "
                f"noch das wichtigste Fehlende, fang nichts Neues mehr an, und "
                f"gib dann finish mit einer ehrlichen Zusammenfassung aus (offen "
                f"Gebliebenes darin benennen).")
            print(f"{C.YELLOW}⚠ Budget-Hinweis: noch {remaining} Schritte.{C.RESET}")
        # Analyse-Stupser: gegen endloses Herumlesen ohne Plan (einmalig, an
        # die letzte user-Nachricht angehaengt — keine doppelte user-Rolle).
        if (analyse_active and analyse_steps >= 10 and not analyse_nudged
                and messages and messages[-1]["role"] == "user"):
            analyse_nudged = True
            messages[-1]["content"] += (
                "\n\n[HINWEIS VOM TOOL] Du bist seit 10 Schritten in der "
                "Analyse-Phase. Wenn du genug verstanden hast, gib JETZT den "
                "Aenderungsplan aus (plan-Aktion).")
        if (maybe_prune(messages, model)  # kuerzt nur bei Kontextdruck
                and messages and messages[-1]["role"] == "user"):
            # Kontobuch: die Kuerzung nimmt dem Modell die Erinnerung an die
            # eigenen Dateizugriffe — deterministisch wieder einspielen
            # (kostet fast nichts, kann nicht halluzinieren).
            messages[-1]["content"] += _ledger_block()
        _save_transcript(messages)    # --resume: Stand nach jedem Schritt sichern
        print(f"\n{C.BLUE}── Schritt {step} ─────────────────────────────{C.RESET}")
        try:
            reply = chat_stream(messages, model)
        except CtxOverflowError as e:
            # Selbstkalibrierung: der Endpoint hat den Ueberlauf gemeldet —
            # gemeldete Fenstergroesse uebernehmen, hart kuerzen, weiter.
            ctx_overflows += 1
            if e.tokens:
                _LOADED_CTX_TOKENS[model] = e.tokens
                _LOADED_CTX_CACHE.pop(model, None)
                info(f"Endpoint meldet Kontextfenster: {e.tokens} Token — "
                     f"Kuerzungs-Schwelle neu kalibriert.")
            if ctx_overflows > 2:
                print(f"{C.RED}Abbruch: {ctx_overflows}x Kontext-Ueberlauf trotz "
                      f"harter Kuerzung — Modell mit groesserem Fenster laden "
                      f"oder --keep-context senken. "
                      f"({_send_size_info(messages, model)}){C.RESET}")
                if messages and messages[-1]["role"] == "user":
                    messages.pop()
                return None
            print(f"{C.YELLOW}⚠ Kontext-Ueberlauf vom Endpoint gemeldet — "
                  f"beschneide aeltere Schritte hart und versuche es erneut … "
                  f"({_send_size_info(messages, model)}){C.RESET}")
            prune_messages(messages, keep=1)
            continue

        if not reply.strip():
            # LEERE Antwort hat ZWEI moegliche Ursachen, die sich nicht
            # verwechseln lassen sollten: (a) das GELADENE Kontextfenster ist
            # ueberschritten (klassischer Fall, Beschneiden hilft) -- oder
            # (b) ein "Thinking"-Modell hat das Antwort-Token-Budget komplett
            # beim Nachdenken (reasoning_content) aufgebraucht, BEVOR
            # sichtbarer Text entstand (real beobachtet: 700 Reasoning-Chunks,
            # 0 Content-Chunks, bei einem Prompt weit unter dem Kontext-Limit).
            # LAST_REASONING_CHARS (von _chat_once gesetzt) unterscheidet
            # beides -- Beschneiden wuerde bei (b) nichts bringen, das
            # Output-Budget ist ein getrennter Topf vom Prompt.
            reasoniert = LAST_REASONING_CHARS > 0
            empty_replies += 1
            if empty_replies > 2:
                groesse = _send_size_info(messages, model)
                if reasoniert:
                    print(f"{C.RED}Abbruch: {empty_replies}x leere Antwort in Folge — "
                          f"das Modell hat das Antwort-Token-Budget offenbar "
                          f"jedesmal beim Nachdenken (reasoning) aufgebraucht, "
                          f"bevor sichtbarer Text entstand. Kein Kontext-Problem: "
                          f"/settings think false schaltet das Nachdenken ab. "
                          f"({groesse}){C.RESET}")
                else:
                    print(f"{C.RED}Abbruch: {empty_replies}x leere Antwort in Folge — "
                          f"das geladene Kontextfenster des Modells reicht fuer diese "
                          f"Historie nicht. Modell mit groesserem Kontext laden oder "
                          f"--keep-context verkleinern. ({groesse}){C.RESET}")
                # Die letzte (unbeantwortete) user-Nachricht NICHT im Verlauf
                # haengen lassen -- sonst sieht ein spaeterer Zug (auch nach
                # /mode chat!) noch die alten Hinweise dieser gescheiterten
                # Aufgabe und bezieht sich verwirrend darauf.
                if messages and messages[-1]["role"] == "user":
                    messages.pop()
                return None
            if reasoniert:
                print(f"{C.YELLOW}⚠ Leere Antwort, aber {LAST_REASONING_CHARS} "
                      f"Zeichen Reasoning gesehen — Budget wurde offenbar beim "
                      f"Nachdenken aufgebraucht (kein Kontext-Problem). Versuche "
                      f"es erneut …{C.RESET}")
            else:
                print(f"{C.YELLOW}⚠ Leere Antwort (vermutlich Kontextfenster des "
                      f"geladenen Modells ueberschritten) — beschneide aeltere "
                      f"Schritte hart und versuche es erneut … "
                      f"({_send_size_info(messages, model)}){C.RESET}")
                prune_messages(messages, keep=1)
                if messages and messages[-1]["role"] == "user":
                    messages[-1]["content"] += _ledger_block()
            continue
        empty_replies = 0
        war_unvollstaendig = reply.endswith(TRUNC_MARKER)
        if war_unvollstaendig:
            reply = reply[: -len(TRUNC_MARKER)]
        messages.append({"role": "assistant", "content": reply})

        action, raw = extract_action(reply)
        if action is None:
            if reply.endswith(DEGEN_MARKER):
                # Kollabierte Antwort ohne brauchbare Aktion: NICHT als
                # "Textantwort = fertig" werten, sondern neu anfordern.
                obs = ("Deine letzte Antwort ist in eine Endlos-Ausgabe "
                       "degeneriert und wurde vom Tool abgebrochen. Sammle "
                       "dich: gib jetzt GENAU EINEN kleinen, validen "
                       "```action Block aus — ein kleiner Schritt, kurzer "
                       "Inhalt, keine langen Erklaerungen.")
                print(f"{C.RED}⚠ Degenerierte Antwort — fordere einen neuen, "
                      f"kleinen Schritt an.{C.RESET}")
                # Kollabierten Text nicht komplett im Verlauf lassen.
                messages[-1]["content"] = (reply[:800]
                                           + "\n…[Rest degeneriert, entfernt]")
                messages.append({"role": "user", "content": obs})
                continue
            # Keine Aktion im Antworttext. Frueher galt das sofort als
            # "Textantwort = fertig" — ein UNBEWACHTER Ausgang, der das
            # komplette Check-/Finish-Gate umgeht. Zwei real beobachtete
            # Varianten: (1) deepseek-v4-flash schrieb "(edit_file
            # ausgefuehrt: ...)" als PROSA — es imitierte das Format der
            # gekuerzten Kontext-Historie —, der Edit fand nie statt, der
            # Lauf endete mitten in der Arbeit. (2) mimo-v2.5 KUENDIGTE in
            # Schritt 1 nur an ("Ich lese zuerst die relevanten Dateien")
            # — ohne Action-Block, Lauf nach 5s beendet, bevor irgendetwas
            # geschah. Deshalb EINE Rueckfrage, wenn der Lauf erkennbar
            # ein Arbeits-Lauf ist: bereits geschrieben (TOUCHED), ODER
            # Check-Modus aktiv, ODER die Aufgabe nennt Dateien
            # (EXPECTED_FILES). Fertig heisst finish (laeuft durchs Gate),
            # sonst naechste echte Aktion. Eine zweite aktionslose Antwort
            # gilt als bewusstes Prosa-Ende. Reine Frage-Antwort-Laeufe
            # (nichts davon trifft zu) enden wie bisher sofort.
            kaputt = [p for p in set(TOUCHED) if os.path.isfile(p)
                      and validate_path(p)[0] == "bad"]
            if kaputt and prose_nudges < 3:
                # Hartnaeckig statt hoeflich: solange geschriebene Dateien
                # nachweislich UNGUELTIG sind, beendet Prosa den Lauf nicht
                # (real beobachtet: 'ist nur ein Linting-Problem' + Ausstieg
                # mit 5 kaputten Dateien).
                prose_nudges += 1
                obs = ("Deine Antwort enthielt KEINEN action-Block — und es "
                       "sind noch UNGUELTIGE Dateien offen: "
                       + ", ".join(sorted(kaputt)[:5]) +
                       ". Ein Prosa-Ende wird deshalb NICHT akzeptiert. "
                       "Korrigiere die Dateien jetzt (read_file + edit_file) "
                       "und gib danach finish aus.")
                print(f"{C.RED}⚠ Prosa-Ende abgelehnt: ungueltige Dateien "
                      f"offen ({prose_nudges}/3).{C.RESET}")
                messages.append({"role": "user", "content": obs})
                continue
            if (TOUCHED or CHECK or EXPECTED_FILES) and not prose_end_nudged:
                prose_end_nudged = True
                obs = ("Deine Antwort enthielt KEINEN action-Block — blosse "
                       "Ankuendigungen ('Ich lese zuerst ...') oder Texte wie "
                       "'(edit_file ausgefuehrt: ...)' fuehren KEINE Aktion aus, "
                       "das war nur Prosa. Wenn die Aufgabe fertig ist: gib die "
                       "finish-Aktion aus. Wenn nicht: gib die naechste echte "
                       "Aktion als ```action Block aus (z.B. read_file).")
                print(f"{C.YELLOW}⚠ Antwort ohne Aktion in einem Arbeits-Lauf "
                      f"— einmalige Rueckfrage statt stillem Ende.{C.RESET}")
                messages.append({"role": "user", "content": obs})
                continue
            # Keine Aktion -> Modell ist mit einer Textantwort fertig.
            return reply

        if "_parse_error" in action:
            parse_error_streak += 1
            if parse_error_streak >= 4:
                # Trotz Eskalationsstufe 2 wiederholt sich das Problem — in der
                # Praxis beobachtet: zwischen den fehlgeschlagenen Versuchen
                # lag eine unabhaengige, ERFOLGREICHE Aktion (z.B. ein read_file
                # oder run), die den Zaehler zurueckgesetzt haette, waere er
                # naiv auf JEDE erfolgreiche Aktion zurueckgesetzt worden —
                # daher zaehlt dieser Streak NUR erfolgreiche SCHREIB-Aktionen
                # als Reset (s.u.), nicht beliebige Zwischenschritte. Staerkste
                # Eskalation: konkrete alternative Strategie vorschlagen statt
                # nur zu bremsen.
                obs = (f"FEHLER: dein action-JSON ist jetzt {parse_error_streak}x insgesamt "
                       f"ungueltig ({action['_parse_error']}) — das Problem liegt vermutlich "
                       f"an der schieren Groesse des Inhalts. Teile die Datei auf: schreibe "
                       f"zuerst ein MINIMALES Geruest per write_file (z.B. nur die Struktur "
                       f"mit Platzhalter-Kommentaren), pruefe es (ast.parse/npm run build), "
                       f"und ergaenze den Rest DANACH in mehreren kleinen edit_file-Schritten "
                       f"statt eines einzigen grossen write_file.")
            elif parse_error_streak >= 2:
                # Wiederholtes JSON-Escaping-Problem (in der Praxis beobachtet:
                # dasselbe falsche '\>' o.ae. wird trotz eigener Korrektur-
                # Ankuendigung im Text identisch wiederholt). Der generische
                # Hinweis allein loest das nicht — eine konkrete Ausweich-
                # strategie schon. Der Parser versteht das Fence-Format IMMER
                # (unabhaengig vom --fence-Flag), aber das Modell kennt es nur,
                # wenn der System-Prompt es lehrt — deshalb hier das Format
                # konkret VORFUEHREN statt nur darauf zu verweisen: damit
                # entfaellt das JSON-Escaping des Dateiinhalts komplett, was
                # genau die Fehlerquelle ist.
                obs = (f"FEHLER: dein action-JSON ist jetzt {parse_error_streak}x in Folge "
                       f"ungueltig ({action['_parse_error']}), vermutlich wegen eines "
                       f"Escaping-Problems. Wiederhole NICHT denselben Text. BESSERE "
                       f"ALTERNATIVE: lass das 'content'-Feld im JSON komplett weg und "
                       f"liefere den Dateiinhalt ROH (ohne jedes Escaping) in einem "
                       f"separaten ```content Block direkt dahinter — so:\n"
                       f"```action\n"
                       f"{{\"action\":\"write_file\",\"path\":\"datei.txt\"}}\n"
                       f"```\n"
                       f"```content\n"
                       f"hier der komplette Dateiinhalt, roh, ohne Escaping\n"
                       f"```\n"
                       f"Das funktioniert auch fuer write_files (je Datei ein "
                       f"```content Block, in derselben Reihenfolge wie die Pfade) "
                       f"und fuer edit_file — dort 'old'/'new' weglassen und statt-"
                       f"dessen einen ```old und einen ```new Block (roh, ohne "
                       f"Escaping) hinter den action-Block setzen.")
            else:
                obs = (f"FEHLER: dein action-JSON war ungueltig ({action['_parse_error']}). "
                       f"Bitte gib einen einzelnen validen ```action``` Block aus.")
            print(f"{C.RED}{obs}{C.RESET}")
            messages.append({"role": "user", "content": obs})
            continue
        if action.get("action") in ("write_file", "write_files", "edit_file"):
            # Nur ein erfolgreicher SCHREIB-Versuch zeigt, dass das eigentliche
            # Problem (JSON-Encoding von Dateiinhalt) geloest ist — ein
            # zwischengeschobenes read_file/run/list_dir etc. soll den Zaehler
            # NICHT zuruecksetzen, sonst kann sich das Muster "2x scheitern,
            # harmlose Aktion, 2x scheitern, ..." endlos wiederholen, ohne je
            # die staerkere Eskalation zu erreichen.
            parse_error_streak = 0

        if "_fence_error" in action:
            obs = f"FEHLER: {action.pop('_fence_error')} Sende die Aktion bitte erneut."
            print(f"{C.RED}{obs}{C.RESET}")
            messages.append({"role": "user", "content": obs})
            continue

        koerz_fehler = repair_and_coerce_action(action)
        if koerz_fehler:
            print(f"{C.RED}{koerz_fehler.splitlines()[0][:120]}{C.RESET}")
            messages.append({"role": "user", "content": koerz_fehler})
            continue

        name = action.get("action")

        if war_unvollstaendig and name in ("write_file", "write_files",
                                           "edit_file"):
            # Halbe Datei sieht als JSON oft komplett aus — nicht schreiben.
            obs = ("Deine Antwort blieb trotz automatischer Fortsetzungen "
                   "UNVOLLSTAENDIG — die Schreibaktion wird deshalb NICHT "
                   "ausgefuehrt (Gefahr halber Dateiinhalte). Sende sie "
                   "erneut und KLEINER: weniger Dateien pro Block bzw. die "
                   "Datei in mehreren edit_file-Schritten aufbauen.")
            print(f"{C.RED}⚠ Schreibaktion aus unvollstaendiger Antwort "
                  f"verweigert.{C.RESET}")
            messages.append({"role": "user", "content": obs})
            continue

        if name == "plan" and not analyse_active:
            # plan ausserhalb der Analyse-Phase: kein Fehler, sondern sanft
            # in die Umsetzung weiterleiten.
            messages.append({"role": "user", "content":
                "Plan notiert. Setze ihn jetzt direkt mit Aktionen um "
                "(read_file/edit_file/write_file/run)."})
            continue
        if analyse_active:
            analyse_steps += 1
            if name == "plan":
                punkte = action.get("punkte") or action.get("points") or []
                if isinstance(punkte, str):
                    punkte = [punkte]
                punkte = [str(p).strip() for p in punkte if str(p).strip()]
                if not punkte:
                    obs = ("PLAN ABGELEHNT — 'punkte' muss eine nicht-leere "
                           "Liste konkreter Aenderungsschritte sein (je Punkt "
                           "Dateipfad + Aenderung).")
                elif not READ_FILES:
                    obs = ("PLAN ABGELEHNT — du hast noch keine einzige Datei "
                           "gelesen. Lies erst die betroffenen Dateien "
                           "(read_file), dann plane.")
                else:
                    PLAN_POINTS[:] = punkte
                    if _write_plan_file(punkte):
                        info(f"Plan gesichert: {MC_PLAN} (ueberlebt Abbrueche).")
                    nummeriert = "\n".join(f"{i}. {p}"
                                           for i, p in enumerate(punkte, 1))
                    print(f"\n{C.CYAN}{C.BOLD}── Aenderungsplan ─────────────"
                          f"────────────{C.RESET}\n{nummeriert}")
                    analyse_active = False
                    messages[0]["content"] = (system_prompt(FENCE)
                        + ("\n\n" + SYSTEM_CONTEXT if SYSTEM_CONTEXT else ""))
                    messages.append({"role": "user", "content":
                        "ANALYSE ABGESCHLOSSEN — dein Aenderungsplan:\n"
                        + nummeriert +
                        f"\nDer Plan liegt zusaetzlich in {MC_PLAN} — hake dort "
                        "erledigte Punkte per edit_file ab ('- [ ]' -> '- [x]'), "
                        "damit ein Folgelauf den Stand kennt. Ab jetzt sind "
                        "Schreibaktionen freigeschaltet. Setze die Punkte "
                        "NACHEINANDER um: kleine gezielte edit_file-Aenderungen "
                        "bevorzugen. Wenn alle Punkte umgesetzt (oder begruendet "
                        "verworfen) sind, pruefe das Ergebnis und gib finish aus."})
                    continue
                print(f"{C.RED}⚠ {obs.splitlines()[0][:120]}{C.RESET}")
                messages.append({"role": "user", "content": obs})
                continue
            if name in ("write_file", "write_files", "edit_file", "run",
                        "finish"):
                obs = (f"ANALYSE-PHASE — '{name}' ist noch gesperrt. Verstehe "
                       "erst den Bestand (read_file/grep/find/list_dir) und "
                       "gib dann den Aenderungsplan aus: "
                       '{"action":"plan","punkte":["<datei>: <aenderung>", '
                       '...]}. Danach werden Schreibaktionen freigeschaltet.')
                print(f"{C.YELLOW}⚠ Analyse-Phase: {name} gesperrt.{C.RESET}")
                messages.append({"role": "user", "content": obs})
                continue

        if name == "finish":
            # Deterministischer Finish-Check: in der Aufgabe genannte Dateien
            # muessen existieren, geschriebene muessen valide sein. Sonst wird
            # das finish zurueckgewiesen (max. MAX_FINISH_REJECTS mal), damit
            # ein "Prosa-fertig" ohne geschriebene Dateien nicht durchrutscht.
            # Genannte Pfade per Suffix aufloesen ('src/App.jsx' findet
            # 'frontend/src/App.jsx') und beim finish MITVALIDIEREN — sonst
            # kann ein Reparatur-Lauf 'fertig' melden, waehrend die in der
            # Aufgabe genannte Datei weiterhin kaputt ist (nur GESCHRIEBENE
            # Dateien wurden bisher geprueft, und die Reparatur kann ja auch
            # an der falschen Stelle erfolgt sein).
            resolved = {p: _resolve_project_file(p) for p in EXPECTED_FILES}
            missing = [p for p, rp in resolved.items() if rp is None]
            to_check = sorted(set(TOUCHED)
                              | {rp for rp in resolved.values() if rp})
            still_bad = [p for p in to_check
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
                check_finish_pending = True
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
            # Ohne Plan-Phase gibt es keine selbst genannten Pruefschritte, an
            # denen sich das Modell messen laesst — dann genuegte dem Gate
            # bisher EIN beliebiger erfolgreicher run (real beobachtet: ein
            # einziger ast.parse-Syntaxcheck, waehrend die im Prompt verlangten
            # funktionalen curl-Tests nie liefen). Einmalige Nachfrage: das
            # Modell muss pro Aufgabenteil benennen, WAS es real ausgefuehrt
            # hat, und Fehlendes nachholen. Kostet maximal einen Umlauf.
            if CHECK and not CHECK_PLAN and not check_probe_done:
                check_probe_done = True
                obs = ("FINISH-NACHFRAGE (Check-Modus) — bevor ich das finish "
                       "akzeptiere: Liste kurz auf, (1) aus welchen Teilen die "
                       "Aufgabe besteht (z.B. Backend, Frontend/Build) und "
                       "(2) welches Kommando du fuer JEDEN dieser Teile real "
                       "ausgefuehrt hast und was dabei herauskam. Ein reiner "
                       "Syntax-Check zaehlt nicht als Funktionstest. Fehlt fuer "
                       "einen Teil die echte Pruefung (z.B. Frontend nie gebaut, "
                       "Endpunkt nie per curl getestet), fuehre sie JETZT aus und "
                       "behebe, was auffaellt. Danach gib erneut finish aus.")
                print(f"{C.YELLOW}⚠ {obs.splitlines()[0][:120]}{C.RESET}")
                messages.append({"role": "user", "content": obs})
                continue
            # Plan-Nachfrage: das finish wird gegen den EIGENEN Aenderungsplan
            # aus der Analyse-Phase gehalten (einmalig) — dasselbe Prinzip wie
            # beim Check-Modus: das Modell an seinem eigenen Versprechen
            # messen, nicht an einer abstrakten Regel.
            if PLAN_POINTS and not plan_probe_done:
                plan_probe_done = True
                obs = ("FINISH-NACHFRAGE — dein Aenderungsplan aus der "
                       "Analyse-Phase:\n"
                       + "\n".join(f"{i}. {p}"
                                   for i, p in enumerate(PLAN_POINTS, 1))
                       + "\nIst JEDER Punkt umgesetzt oder bewusst verworfen "
                       "(dann kurz begruenden)? Setze Fehlendes JETZT um und "
                       "gib danach erneut finish aus; ist wirklich alles "
                       "erledigt, gib einfach erneut finish aus.")
                print(f"{C.YELLOW}⚠ Plan-Nachfrage vor dem finish.{C.RESET}")
                messages.append({"role": "user", "content": obs})
                continue
            # Notizen-Nachfrage (einmalig, nur wenn Code geschrieben wurde und
            # die Projekt-Notizen NICHT angefasst wurden): die Selbstpflege-
            # Regel im System-Prompt allein greift unzuverlaessig — real
            # beobachtet beim CSV-Export, wo der neue Endpunkt nie in den
            # Notizen landete. Kostet maximal einen Umlauf.
            if (TOUCHED and not notes_probe_done
                    and os.path.normpath(MC_NOTES) not in
                    {os.path.normpath(p) for p in TOUCHED}):
                notes_probe_done = True
                obs = ("FINISH-NACHFRAGE — bevor ich abschliesse: Hast du in "
                       "diesem Lauf FESTLEGUNGEN getroffen oder geaendert, die "
                       "spaetere Laeufe kennen muessen (neue Endpunkte, feste "
                       "Ports, Feld-/Spaltennamen, Startkommandos, gewaehlte "
                       f"Bibliotheken)? Falls ja: ergaenze sie JETZT stichpunkt"
                       f"artig in {MC_NOTES} (edit_file bzw. write_file, kurz "
                       "halten) und gib danach erneut finish aus. Falls nein: "
                       "gib einfach erneut finish aus.")
                print(f"{C.YELLOW}⚠ Notizen-Nachfrage vor dem finish.{C.RESET}")
                messages.append({"role": "user", "content": obs})
                continue
            # Diff-Selbstreview (einmalig): das eigene Gesamtwerk noch einmal
            # im Zusammenhang sehen, bevor 'fertig' gilt — faengt vergessene
            # Dateien, Debug-Reste und ungewollte Aenderungen. Nur mit Git
            # (sauberer Ausgangszustand), sonst zeigt das Diff Fremdes.
            if GIT_ROLLBACK and TOUCHED and not diff_probe_done:
                diff_probe_done = True
                zusammenfassung = _git_diff_summary()
                if zusammenfassung:
                    obs = ("FINISH-NACHFRAGE — dein Gesamtwerk dieses Laufs als "
                           "Diff:\n" + zusammenfassung +
                           "\nLetzter Blick: Passt das vollstaendig zur Aufgabe "
                           "— nichts vergessen, keine Debug-Reste (print/"
                           "console.log), keine ungewollten Aenderungen? "
                           "GELOESCHTE Zeilen (-) brauchen besondere "
                           "Rechtfertigung: nenne fuer JEDE Entfernung "
                           "bestehender Funktionalitaet den Grund — im Zweifel "
                           "wiederherstellen. Falls etwas auffaellt, korrigiere "
                           "es JETZT und gib danach finish aus; sonst gib "
                           "einfach erneut finish aus.")
                    print(f"{C.YELLOW}⚠ Diff-Selbstreview vor dem finish.{C.RESET}")
                    messages.append({"role": "user", "content": obs})
                    continue
            if missing or still_bad:
                print(f"{C.RED}Achtung: finish trotz offener Probleme akzeptiert "
                      f"(fehlend: {len(missing)}, ungueltig: {len(still_bad)}).{C.RESET}")
            else:
                CLEAN_FINISH = True  # nur OHNE offene Probleme gilt der Lauf als "sauber"
            if PLAN_POINTS and CLEAN_FINISH and os.path.isfile(MC_PLAN):
                try:
                    os.remove(MC_PLAN)  # erledigt — kein Geisterplan fuer Folgelaeufe
                except OSError:
                    pass
            summary = action.get("summary", "Fertig.")
            print(f"\n{C.GREEN}{C.BOLD}✓ {summary}{C.RESET}")
            _save_transcript(messages)
            return summary

        handler = DISPATCH.get(name)
        if not handler:
            obs = f"FEHLER: unbekannte Aktion '{name}'."
            print(f"{C.RED}{obs}{C.RESET}")
            messages.append({"role": "user", "content": obs})
            continue

        # Schleifen-Erkennung fuer NUR-LESE-Aktionen: dieselbe Aktion direkt
        # hintereinander (real beobachtet: dreimal read_file derselben Datei)
        # pumpt jedes Mal den kompletten Inhalt erneut in den Kontext und
        # treibt kleine Kontextfenster in den stillen Overflow. Schreib-/run-
        # Aktionen sind ausgenommen (ein wiederholter curl nach einem Fix ist
        # legitim).
        if name in ("read_file", "read_files", "list_dir", "find", "grep"):
            if raw and raw == last_ro_raw:
                obs = (f"HINWEIS: exakt diese {name}-Aktion hast du im vorigen "
                       f"Schritt bereits ausgefuehrt — das Ergebnis steht oben "
                       f"und hat sich nicht geaendert. Nutze es und mache jetzt "
                       f"den NAECHSTEN Schritt (z.B. die konkrete Aenderung per "
                       f"edit_file).")
                print(f"{C.YELLOW}⚠ Wiederholte Lese-Aktion abgefangen.{C.RESET}")
                messages.append({"role": "user", "content": obs})
                continue
            last_ro_raw = raw
        else:
            last_ro_raw = None

        ok, result = handler(action)
        marker = C.GREEN + "✓" if ok else C.RED + "✗"
        print(f"{marker}{C.RESET} {C.DIM}{result.splitlines()[0][:100]}{C.RESET}")
        # Prosa-Waechter wieder scharf schalten: die Rueckfrage war bisher
        # EINMALIG pro Lauf — real beobachtet, dass ein Modell sie frueh
        # verbraucht und der Lauf viel spaeter (nach Dutzenden echten
        # Aktionen) doch still per Prosa-Ankuendigung endet. Nach jeder
        # ausgefuehrten Aktion ist eine erneute einmalige Rueckfrage fair.
        prose_end_nudged = False

        # Check-Modus-Buchhaltung: nur ein VORDERGRUND-run mit exit=0 zaehlt als
        # Pruefung (ein gestarteter Server allein beweist nichts — der folgende
        # curl-Test ist dann der Vordergrund-run).
        if name == "run" and ok and result.startswith("exit=0"):
            RAN_SINCE_WRITE = True
            if check_finish_pending:
                # Finish-Wiedervorlage: ohne diesen Anstoss verlor das Modell
                # nach der Check-Zurueckweisung den Faden (real beobachtet:
                # Pruefung laengst erfolgt, aber statt finish begann es, das
                # Projekt 'zuerst zu untersuchen' — bis ins Schrittlimit).
                check_finish_pending = False
                result += ("\n[HINWEIS VOM TOOL] Dein frueheres finish wurde nur "
                           "wegen fehlender Pruefung zurueckgewiesen — jetzt gab "
                           "es einen erfolgreichen run (exit=0). Fehlt noch eine "
                           "konkrete Pruefung, fuehre GENAU DIE noch aus; sonst "
                           "gib JETZT finish aus. Beginne NICHT, das Projekt neu "
                           "zu erkunden.")
                print(f"{C.YELLOW}⚠ finish-Wiedervorlage angehaengt (Pruefung "
                      f"erfolgt — jetzt abschliessen).{C.RESET}")

        # Geschriebene Dateien fuer Rollback merken und (bekannte Typen) validieren.
        valed = ""
        if ok and name in ("write_file", "write_files", "edit_file"):
            paths = written_paths(name, action)
            # Reine Notizen-Pflege (MC-NOTIZEN.md nach der Finish-Nachfrage)
            # ist kein Code — sie soll den Check-Modus nicht erneut scharf
            # schalten (sonst: Notiz ergaenzt -> finish wird wieder abgelehnt).
            if any(os.path.normpath(p) != os.path.normpath(MC_NOTES)
                   for p in paths):
                RAN_SINCE_WRITE = False
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
    # Erzwungene Uebergabe statt Abbruch mitten in einer Aktion: ein letzter
    # Request, der ausdruecklich KEINE Aktion mehr erlaubt. So endet auch ein
    # gescheiterter Lauf mit einem brauchbaren Zustandsbericht im Verlauf —
    # und der Nutzer (oder ein Folge-Lauf) weiss, wo es weitergeht.
    try:
        messages.append({"role": "user", "content":
            "SCHRITTLIMIT ERREICHT — es sind KEINE Aktionen mehr moeglich, "
            "gib keinen action-Block mehr aus. Fasse als reiner Text zusammen: "
            "(1) Was ist fertig und funktioniert? (2) Was fehlt oder ist "
            "ungeprueft? (3) Womit sollte ein Folge-Lauf konkret weitermachen?"})
        print(f"{C.CYAN}{C.BOLD}── Uebergabe ────────────────────────────{C.RESET}")
        uebergabe = chat_stream(messages, model)
        if uebergabe.strip():
            messages.append({"role": "assistant", "content": uebergabe})
    except (CtxOverflowError, NetRetryError, SystemExit):
        pass  # Uebergabe ist Kuer — ein Fehler hier soll nichts verschlimmern
    return None


def _current_settings(model):
    """Die zur Laufzeit umstellbaren Einstellungen als Dict — Grundlage fuer
    /settings und die benannten Profile (/profil speichern|laden)."""
    return {"model": model, "base_url": BASE_URL, "check": CHECK,
            "analyse": ANALYSE, "fence": FENCE, "verbose": VERBOSE,
            "prune": PRUNE, "max_steps": MAX_STEPS,
            "keep_context": KEEP_CONTEXT, "yes": AUTO_YES,
            "context_length": CONTEXT_LENGTH, "think": THINK,
            "api_key": API_KEY}


def _settings_report(model):
    """Textbericht fuer /settings ohne Argument. api_key wird NIE im
    Klartext gezeigt (Terminal-Scrollback/Logs) -- _current_settings()
    selbst liefert den echten Wert weiterhin unveraendert, das brauchen
    /profil speichern|laden, um den Key tatsaechlich wiederherzustellen."""
    zeilen = ["Aktuelle Einstellungen (/settings <name> <wert> zum Aendern):"]
    for k, v in _current_settings(model).items():
        if k == "api_key":
            v = "gesetzt (verborgen)" if v else "(nicht gesetzt)"
        zeilen.append(f"  {k:14s} {v}")
    zeilen.append("Profile: /profil speichern <name> · /profil laden <name> "
                  "· /profil liste")
    return "\n".join(zeilen)


def _apply_setting(key, wert):
    """Setzt eine Laufzeit-Einstellung. Gibt (ok, meldung, prompt_neu) zurueck
    — prompt_neu=True, wenn die System-Message neu gebaut werden muss (fence/
    check stecken im Prompt-Text). 'model' behandelt der Aufrufer selbst."""
    global BASE_URL, CHECK, ANALYSE, FENCE, VERBOSE, PRUNE
    global MAX_STEPS, KEEP_CONTEXT, AUTO_YES, CONTEXT_LENGTH, THINK, API_KEY
    key = key.strip().lower()
    if key == "base_url":
        BASE_URL = str(wert).rstrip("/")
        _LOADED_CTX_CACHE.clear()
        _LOADED_CTX_TOKENS.clear()
        _LOCAL_ENGINE_CACHE.clear()
        return True, f"base_url = {BASE_URL}", False
    if key == "api_key":
        API_KEY = str(wert).strip()
        return True, ("api_key = " + ("gesetzt (verborgen)" if API_KEY
                                      else "geleert")), False
    if key in ("check", "analyse", "fence", "verbose", "prune", "yes", "think"):
        v = _truthy(wert)
        if key == "check":
            CHECK = v
        elif key == "analyse":
            ANALYSE = v
        elif key == "fence":
            FENCE = v
        elif key == "verbose":
            VERBOSE = v
        elif key == "prune":
            PRUNE = v
        elif key == "think":
            THINK = v
        else:
            AUTO_YES = v
        return True, f"{key} = {v}", key in ("check", "fence")
    if key in ("max_steps", "keep_context"):
        try:
            v = max(1, int(str(wert).strip()))
        except ValueError:
            return False, f"FEHLER: '{wert}' ist keine Zahl.", False
        if key == "max_steps":
            MAX_STEPS = v
        else:
            KEEP_CONTEXT = v
        return True, f"{key} = {v}", False
    if key == "context_length":
        try:
            v = max(0, int(str(wert).strip()))
        except ValueError:
            return False, f"FEHLER: '{wert}' ist keine Zahl.", False
        CONTEXT_LENGTH = v
        return True, f"context_length = {v or '(Engine-Default)'}", False
    gueltig = ", ".join(_current_settings("").keys())
    return False, (f"FEHLER: unbekannte Einstellung '{key}'. "
                   f"Verfuegbar: {gueltig}"), False


def main():
    global AUTO_YES, BASE_URL, PROXY, CA_BUNDLE, INSECURE, VERBOSE, MAX_STEPS, VALIDATE, GIT_ROLLBACK, KEEP_CONTEXT, PRUNE, FENCE, CHECK, ANALYSE, RESUME, MODE, THINK
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
    ap.add_argument("--no-validate", action="store_true",
                    help="Validierung geschriebener Dateien (py/json/yaml/php) abschalten")
    ap.add_argument("--keep-context", type=int, default=KEEP_CONTEXT, metavar="N",
                    help=f"So viele letzte Schritte bleiben bei einer Kuerzung "
                         f"vollstaendig im Kontext (default {KEEP_CONTEXT}); gekuerzt "
                         f"wird erst, wenn die Historie das geladene Kontextfenster "
                         f"zu sprengen droht (schont den Prompt-Cache des Servers)")
    ap.add_argument("--no-prune", action="store_true",
                    help="Kontext-Beschneidung abschalten (volle Historie senden)")
    ap.add_argument("--fence", action="store_true",
                    help="Fence-Modus erzwingen (ist bereits der Default): Datei-"
                         "inhalte und edit_file-old/new als rohe ```-Bloecke statt "
                         "als JSON-Strings (vermeidet Escaping-Fehler)")
    ap.add_argument("--no-fence", action="store_true",
                    help="Fence-Modus abschalten (Dateiinhalte als JSON-Strings); "
                         "der Parser versteht unabhaengig davon immer beide Formate")
    ap.add_argument("--no-think", action="store_true",
                    help="Reasoning/Thinking abschalten (reasoning_effort=none + "
                         "enable_thinking=false im Request) -- gegen Modelle, die "
                         "das Antwort-Token-Budget beim Nachdenken aufbrauchen, "
                         "bevor sichtbarer Text entsteht (z.B. gemma4 ueber vMLX); "
                         "von Endpoints ohne Reasoning folgenlos ignoriert")
    ap.add_argument("--resume", action="store_true",
                    help=f"Verlauf nach jedem Schritt in {MC_VERLAUF} sichern "
                         f"und einen dort gesicherten Verlauf beim Start "
                         f"fortsetzen (abgebrochene Laeufe wiederaufnehmen)")
    ap.add_argument("--analyse", action="store_true",
                    help="Zweistufig bei Bestandscode: erst NUR lesen/suchen und "
                         "einen nummerierten Aenderungsplan ausgeben (plan-Aktion), "
                         "erst danach werden Schreibaktionen freigeschaltet — "
                         "gegen den Neubau-Reflex bei Aenderungen an bestehenden "
                         "Projekten")
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
    ANALYSE = ANALYSE or args.analyse
    RESUME = args.resume
    KEEP_CONTEXT = args.keep_context
    PRUNE = not args.no_prune
    if args.no_fence:
        FENCE = False
    elif args.fence:
        FENCE = True
    if args.no_think:
        THINK = False
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
    # sich dann auf dieses Verzeichnis.
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
    cwd_warnung = _suspicious_cwd_warning()
    if cwd_warnung:
        print(f"{C.RED}{cwd_warnung}{C.RESET}")

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
        info(f"Kontext-Beschneidung aktiv: gekuerzt wird erst bei Kontextdruck "
             f"(schont den Prompt-Cache des Servers); dann bleiben die letzten "
             f"{KEEP_CONTEXT} Schritte vollstaendig (--no-prune schaltet ab).")
    if FENCE:
        info("Fence-Modus aktiv: Dateiinhalte als rohe ```content Bloecke "
             "(kein JSON-Escaping).")
    if ANALYSE:
        info("Analyse-Modus aktiv (--analyse): bei Bestandscode erst "
             "verstehen + Aenderungsplan, dann aendern.")

    # Projektueberblick als Kontext: damit der Agent vorhandene Dateien kennt und
    # bei ungenauer Benennung die richtige trifft, statt eine neue anzulegen.
    overview = project_overview()
    listing = "\n".join(overview) if overview else "(keine Dateien)"
    context_msg = (
        f"Arbeitsverzeichnis: {os.getcwd()}\n"
        f"Vorhandene Dateien (rekursiv):\n{listing}\n\n"
        f"Wenn der Nutzer eine Datei ungenau benennt, ordne sie einer dieser Dateien "
        f"zu (find hilft beim unscharfen Suchen), statt blind eine neue anzulegen.")
    # Deterministischer Bestands-Kontext: Steckbrief (Stack, Kommandos, Git)
    # und Struktur-Uebersicht (Funktionen/Klassen/Routen) — beides ohne
    # Modell-Aufruf erzeugt. Ein Modell, das Struktur statt nur Dateinamen
    # sieht, iteriert eher, statt neu zu bauen.
    brief = repo_brief()
    if brief:
        context_msg += "\n\nProjekt-Steckbrief:\n" + "\n".join(brief)
    outline = code_outline()
    if outline:
        context_msg += ("\n\nCode-Struktur (automatisch extrahiert, "
                        "Z<n> = Zeile):\n" + "\n".join(outline))
    global SYSTEM_CONTEXT
    SYSTEM_CONTEXT = context_msg

    # System-Prompt und Projektueberblick in EINER system-Message buendeln.
    # Manche Chat-Templates (z.B. Ornith-GGUF) brechen bei zwei aufeinander-
    # folgenden system-Rollen sofort leer ab — eine kombinierte ist universell
    # vertraeglicher.
    messages = [{"role": "system", "content": system_prompt(FENCE) + "\n\n" + context_msg}]
    if RESUME:
        alte = _load_transcript()
        if alte:
            messages.extend(alte)
            info(f"Verlauf fortgesetzt: {len(alte)} Nachrichten aus {MC_VERLAUF}.")

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
        if mc_terminal and task_text.strip().startswith("/"):
            art, wert, sflags = mc_terminal.expand_input(task_text, args.model)
            if art == "print":
                print(wert)
                return
            if art == "model":
                info(f"Modellwechsel im Einmal-Modus bitte per --model. "
                     f"Aktuell: {args.model}")
                return
            if art == "settings_show":
                print(_settings_report(args.model))
                return
            if art in ("setting", "models", "model_pick", "model_reset",
                       "mode_show", "mode", "profil_save", "profil_load"):
                info("Dieses Kommando gibt es im interaktiven Modus "
                     "(mc.py ohne Aufgabe starten).")
                return
            if art == "task":
                task_text = wert
                if sflags.get("check") or sflags.get("analyse"):
                    CHECK = CHECK or sflags.get("check", False)
                    ANALYSE = ANALYSE or sflags.get("analyse", False)
                    messages[0]["content"] = (system_prompt(FENCE)
                                              + "\n\n" + SYSTEM_CONTEXT)
                    info("Skill-Flags aktiv: " + ", ".join(
                        k for k in ("check", "analyse") if sflags.get(k)))
                info(f"Skill expandiert ({len(task_text)} Zeichen Aufgabe).")
        global CURRENT_TASK
        CURRENT_TASK = task_text
        EXPECTED_FILES[:] = expected_files_from_task(task_text)
        if EXPECTED_FILES:
            info(f"Finish-Check aktiv: {len(EXPECTED_FILES)} in der Aufgabe "
                 f"genannte Datei(en) werden am Ende geprueft.")
        hints = task_hints(task_text)
        if hints:
            info("Ist-Zustand erkannt (bestehendes Projekt/Dateien) — Hinweise "
                 "an die Aufgabe angehaengt.")
        zusatz = terse_task_hint(task_text) + qa_task_hint(task_text)
        if "knapp gehalten" in zusatz:
            info("Knappheits-Stupser angehaengt (kurzer Auftrag, Bestand vorhanden).")
        if "eine FRAGE" in zusatz:
            info("Frage-Weiche aktiv: nur lesen und antworten.")
        messages.append({"role": "user", "content": task_text + hints + zusatz})
        if plan_mode and not plan_phase(messages, args.model):
            return
        result = run_task(messages, args.model)
        after_run(result if isinstance(result, str) else "")
        return

    # Interaktiver Modus
    info("Interaktiv. Gib eine Aufgabe ein (oder 'exit' / Ctrl-D zum Beenden).")
    if plan_mode:
        info("Plan-Modus aktiv (--plan): erst Plan + Bestaetigung, dann Umsetzung.")
    if mc_terminal and mc_terminal.init_readline():
        info("Terminal-Komfort aktiv: Pfeil-hoch-History, Tab vervollstaendigt "
             "/Kommandos, /help zeigt Skills.")
    while True:
        try:
            print()
            user = input(rl_prompt(f"{C.GREEN}{C.BOLD}du [{MODE}]> {C.RESET}")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user and not sys.stdin.isatty():
            print(user)  # Echo im Pipe-Betrieb — sonst fehlen die Eingaben im Log
        if not user:
            continue
        if user.lower() in ("exit", "quit", "q"):
            break
        sflags = {}
        if mc_terminal:
            art, wert, sflags = mc_terminal.expand_input(user, args.model)
            if art == "print":
                print(wert)
                continue
            if art == "model":
                args.model = wert
                info(f"Modell fuer diese Sitzung gewechselt: {wert}")
                continue
            if art == "models":
                try:
                    eintraege = list_models()
                    print("Modelle am Endpoint:")
                    for mid, preis in eintraege[:60]:
                        print("  " + mid + (f"  ({preis})" if preis else ""))
                    if len(eintraege) > 60:
                        print(f"  ... ({len(eintraege)} gesamt)")
                except (Exception, SystemExit) as e:
                    print(f"Modell-Liste nicht abrufbar: {e}")
                continue
            if art == "model_pick":
                try:
                    eintraege = list_models()
                except (Exception, SystemExit) as e:
                    print(f"Modell-Liste nicht abrufbar: {e}")
                    continue
                if not eintraege:
                    print("Keine Modelle gefunden.")
                    continue
                print("Modelle am Endpoint:")
                for i, (mid, preis) in enumerate(eintraege[:60], 1):
                    marker = "  (aktuell)" if mid == args.model else ""
                    print(f"  {i:>2}) {mid}" + (f"  ({preis})" if preis else "") + marker)
                if len(eintraege) > 60:
                    print(f"  ... ({len(eintraege)} gesamt, erste 60 gezeigt)")
                print()
                auswahl = input(rl_prompt(
                    f"{C.GREEN}Nummer waehlen (Enter=abbrechen)> {C.RESET}")).strip()
                if auswahl.isdigit() and 1 <= int(auswahl) <= min(len(eintraege), 60):
                    args.model = eintraege[int(auswahl) - 1][0]
                    info(f"Modell fuer diese Sitzung gewechselt: {args.model}")
                elif auswahl:
                    print("Ungueltige Auswahl.")
                continue
            if art == "model_reset":
                ok_r, meldung_r = reset_model(args.model, CONTEXT_LENGTH or None)
                print((f"{C.GREEN}" if ok_r else f"{C.RED}") + meldung_r + C.RESET)
                if ok_r:
                    _LOADED_CTX_CACHE.pop(args.model, None)
                    _LOADED_CTX_TOKENS.pop(args.model, None)
                continue
            if art == "settings_show":
                print(_settings_report(args.model))
                continue
            if art == "setting":
                key, val = wert
                if key.strip().lower() == "model":
                    args.model = val
                    info(f"model = {val}")
                    continue
                ok_s, meldung, prompt_neu = _apply_setting(key, val)
                print(meldung)
                if ok_s and prompt_neu:
                    messages[0]["content"] = _system_message_for_mode()
                    info("System-Prompt neu aufgebaut (fence/check geaendert).")
                continue
            if art == "profil_save":
                pfad = mc_terminal.save_profile(wert, _current_settings(args.model))
                print(f"Profil gespeichert: {pfad}" if pfad
                      else "Profil-Speichern fehlgeschlagen (Name/Schreibrecht?).")
                continue
            if art == "profil_load":
                prof = mc_terminal.load_profile(wert)
                if not prof:
                    print(f"Profil '{wert}' nicht gefunden — /profil liste "
                          f"zeigt alle.")
                    continue
                prompt_neu = False
                for k, v in prof.items():
                    if k == "model":
                        args.model = v
                        continue
                    ok_s, _m, pn = _apply_setting(k, v)
                    prompt_neu = prompt_neu or (ok_s and pn)
                if prompt_neu:
                    messages[0]["content"] = _system_message_for_mode()
                print(f"Profil '{wert}' geladen.")
                print(_settings_report(args.model))
                continue
            if art == "mode_show":
                print(f"Aktueller Modus: {MODE} — /mode dev|chat zum Wechseln.")
                continue
            if art == "mode":
                MODE = wert
                messages[0]["content"] = _system_message_for_mode()
                info(f"Modus gewechselt: {MODE}" + (
                    " (nur Unterhaltung, kein Dev-Prompt)" if MODE == "chat"
                    else " (Werkzeuge/Aktionen aktiv)"))
                continue
            if art == "task":
                user = wert
                info(f"Skill expandiert ({len(user)} Zeichen Aufgabe).")
        if MODE == "chat":
            messages.append({"role": "user", "content": user})
            try:
                reply = chat_stream(messages, args.model)
            except CtxOverflowError:
                print(f"{C.RED}Kontext-Ueberlauf gemeldet — /mode dev oder "
                      f"Verlauf kuerzen.{C.RESET}")
                messages.pop()
            except (NetRetryError, SystemExit) as e:
                print(f"{C.RED}{e}{C.RESET}")
                messages.pop()
            else:
                if reply.strip():
                    messages.append({"role": "assistant", "content": reply})
                else:
                    print(f"{C.YELLOW}(keine Antwort — evtl. Kontextfenster "
                          f"ueberschritten){C.RESET}")
                    messages.pop()
            continue
        globals()["CURRENT_TASK"] = user
        EXPECTED_FILES[:] = expected_files_from_task(user)
        hints = task_hints(user)
        if hints:
            info("Ist-Zustand erkannt (bestehendes Projekt/Dateien) — Hinweise "
                 "an die Aufgabe angehaengt.")
        zusatz = terse_task_hint(user) + qa_task_hint(user)
        if "knapp gehalten" in zusatz:
            info("Knappheits-Stupser angehaengt (kurzer Auftrag, Bestand vorhanden).")
        if "eine FRAGE" in zusatz:
            info("Frage-Weiche aktiv: nur lesen und antworten.")
        messages.append({"role": "user", "content": user + hints + zusatz})
        if plan_mode and not plan_phase(messages, args.model):
            continue
        # Skill-Flags (check/analyse) gelten nur fuer DIESE Aufgabe: Globals
        # setzen, System-Prompt neu bauen, nach dem Lauf zuruecksetzen.
        prev_check, prev_analyse = CHECK, ANALYSE
        if sflags.get("check") or sflags.get("analyse"):
            CHECK = CHECK or sflags.get("check", False)
            ANALYSE = ANALYSE or sflags.get("analyse", False)
            messages[0]["content"] = _system_message_for_mode()
            info("Skill-Flags aktiv fuer diese Aufgabe: " + ", ".join(
                k for k in ("check", "analyse") if sflags.get(k)))
        result = run_task(messages, args.model)
        if (CHECK, ANALYSE) != (prev_check, prev_analyse):
            CHECK, ANALYSE = prev_check, prev_analyse
            messages[0]["content"] = _system_message_for_mode()
        after_run(result if isinstance(result, str) else "")
    if mc_terminal:
        mc_terminal.save_history()


if __name__ == "__main__":
    AUTO_YES = False
    main()
