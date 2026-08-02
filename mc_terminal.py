"""
mc_terminal - Terminal-Komfort fuer mc.py (optionales Modul)
============================================================

Slash-Kommandos, /skills und readline-History fuer den interaktiven Modus.
Bewusst als EIGENES Modul: mc.py laeuft auch ohne diese Datei unveraendert
weiter (Ein-Datei-Betrieb bleibt moeglich) — ist sie vorhanden, wird der
Komfort automatisch aktiv.

Skills sind Aufgaben-VORLAGEN als Textdateien (.md/.txt):
  ~/.mc/skills/<name>.md   (global)
  mc_skills/<name>.md      (pro Projekt — gewinnt bei Namensgleichheit)

Namenskonvention: alles, was zu mc gehoert (Module, Verzeichnisse),
traegt das Praefix mc_ — so bleibt es im Projekt erkennbar.

Aufbau einer Skill-Datei (Kopfzeilen optional):
  ---
  check: true
  analyse: true
  beschreibung: Kurztext fuer die /skills-Liste
  ---
  Der eigentliche Aufgaben-Text. $ARGUMENTS wird durch das ersetzt,
  was hinter dem Kommando steht.

Aufruf: /<name> [argumente]  — oder als erstes Wort ohne Slash, wenn es
exakt einem Skill-Namen entspricht (Bare-Word-Dispatch). Auch im
Einmal-Modus: python3 mc.py "/name argumente".
"""

import difflib
import os
import re

try:
    import readline
except ImportError:  # z.B. Windows ohne readline-Modul
    readline = None

HISTORY_FILE = os.path.join(os.path.expanduser("~"), ".mc", "history")
HISTORY_MAX = 200
SKILL_DIRS = (os.path.join(os.path.expanduser("~"), ".mc", "skills"),
              "mc_skills")
SKILL_EXTS = (".md", ".txt")
BUILTINS = {
    "/help": "Diese Uebersicht",
    "/skills": "Verfuegbare Skills auflisten",
    "/model": "Aktuelles Modell anzeigen bzw. wechseln: /model <id>",
}
_TRUTHY = ("1", "true", "yes", "ja", "wahr")


def _split_frontmatter(text):
    """Trennt optionale ---Kopfzeilen--- vom Vorlagen-Text."""
    m = re.match(r"---[ \t]*\n(.*?)\n---[ \t]*\n(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip()
    return meta, m.group(2)


def load_skills():
    """Skill-Name -> {path, meta, body}. Projekt-Skills ueberschreiben
    globale gleichen Namens (deshalb wird das globale Verzeichnis zuerst
    gelesen). Wird pro Eingabe neu geladen — Aenderungen an Skill-Dateien
    wirken sofort, ohne Neustart."""
    skills = {}
    for d in SKILL_DIRS:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            base, ext = os.path.splitext(fn)
            if ext.lower() not in SKILL_EXTS or not base:
                continue
            try:
                with open(os.path.join(d, fn), encoding="utf-8") as f:
                    meta, body = _split_frontmatter(f.read())
            except OSError:
                continue
            skills[base.lower()] = {"path": os.path.join(d, fn),
                                    "meta": meta, "body": body.strip()}
    return skills


def render_skill(skill, args):
    """Setzt die Argumente in die Vorlage ein: explizit via $ARGUMENTS,
    sonst angehaengt (damit '/fix backend' auch ohne Platzhalter wirkt)."""
    body = skill["body"]
    if "$ARGUMENTS" in body:
        return body.replace("$ARGUMENTS", args)
    return body + ("\n\n" + args if args else "")


def skill_flags(skill):
    """Kopfzeilen-Flags eines Skills (check/analyse) als Dict."""
    meta = skill.get("meta", {})
    return {k: str(meta.get(k, "")).lower() in _TRUTHY
            for k in ("check", "analyse")}


def _suggest(word, candidates, n=3):
    return difflib.get_close_matches(word, candidates, n=n, cutoff=0.5)


def skills_overview(skills):
    """Menschlich lesbare /skills-Liste."""
    if not skills:
        return ("Keine Skills gefunden. Lege Vorlagen als .md/.txt ab in:\n  "
                + "\n  ".join(SKILL_DIRS))
    zeilen = ["Verfuegbare Skills (/name [argumente]):"]
    for name in sorted(skills):
        meta = skills[name]["meta"]
        desc = meta.get("beschreibung") or meta.get("description") or ""
        flags = [k for k, v in skill_flags(skills[name]).items() if v]
        suffix = (" [" + ",".join(flags) + "]") if flags else ""
        zeilen.append(f"  /{name}{suffix}" + (f" — {desc}" if desc else ""))
    return "\n".join(zeilen)


def expand_input(user, model=""):
    """Verarbeitet eine Eingabe VOR dem Modell-Aufruf.

    Rueckgabe (art, wert, flags):
      ("task", text, flags)  Skill expandiert — text ist die neue Aufgabe,
                             flags z.B. {"check": True, "analyse": False}
      ("print", text, {})    Info ausgeben (Hilfe/Liste/Fehlermeldung),
                             danach auf die naechste Eingabe warten
      ("model", id, {})      Modellwechsel gewuenscht
      ("pass", user, {})     keine Slash-/Skill-Eingabe — unveraendert
                             als normale Aufgabe weiterreichen
    """
    text = user.strip()
    skills = load_skills()
    first = text.split(None, 1)[0].lower() if text else ""

    # Bare-Word-Dispatch: erstes Wort ist exakt ein Skill-Name.
    if not text.startswith("/") and first in skills:
        rest = text[len(first):].strip()
        sk = skills[first]
        return "task", render_skill(sk, rest), skill_flags(sk)
    if not text.startswith("/"):
        return "pass", user, {}

    cmd, _, rest = text.partition(" ")
    cmd = cmd.lower()
    rest = rest.strip()
    if cmd == "/help":
        zeilen = ["Eingebaute Kommandos:"]
        zeilen += [f"  {k} — {v}" for k, v in BUILTINS.items()]
        return "print", "\n".join(zeilen) + "\n\n" + skills_overview(skills), {}
    if cmd == "/skills":
        return "print", skills_overview(skills), {}
    if cmd == "/model":
        if rest:
            return "model", rest, {}
        return "print", f"Aktuelles Modell: {model}", {}
    name = cmd[1:]
    if name in skills:
        sk = skills[name]
        return "task", render_skill(sk, rest), skill_flags(sk)
    kandidaten = ([f"/{s}" for s in skills] + list(BUILTINS))
    tipps = _suggest(cmd, kandidaten)
    msg = f"Unbekanntes Kommando: {cmd}."
    if tipps:
        msg += " Meintest du: " + ", ".join(tipps) + "?"
    msg += " (/help zeigt alles)"
    return "print", msg, {}


def _completer_factory():
    """Ganze-Zeile-Vervollstaendigung fuer '/'-Eingaben: Kandidaten werden
    bei JEDEM Tab neu aus den Skill-Verzeichnissen erzeugt (dynamisch)."""
    state_matches = []

    def complete(text, state):
        nonlocal state_matches
        if state == 0:
            line = readline.get_line_buffer()
            if not line.startswith("/"):
                return None
            cands = sorted([f"/{s} " for s in load_skills()]
                           + [b + " " for b in BUILTINS])
            state_matches = [c for c in cands if c.startswith(line)]
        return state_matches[state] if state < len(state_matches) else None

    return complete


def init_readline():
    """History (persistent) + Tab-Vervollstaendigung aktivieren.
    Gibt True zurueck, wenn readline verfuegbar ist."""
    if readline is None:
        return False
    try:
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        readline.set_history_length(HISTORY_MAX)
        try:
            readline.read_history_file(HISTORY_FILE)
        except OSError:
            pass
        readline.set_completer_delims("")  # ganze Zeile vervollstaendigen
        readline.set_completer(_completer_factory())
        readline.parse_and_bind("tab: complete")
        return True
    except Exception:
        return False


def save_history():
    if readline is None:
        return
    try:
        readline.write_history_file(HISTORY_FILE)
    except OSError:
        pass
