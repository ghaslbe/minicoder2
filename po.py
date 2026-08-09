"""po.py -- Product-Owner-Miniagent zwischen vibelove und mc.py.

Nimmt einen knappen Nutzer-Wunsch entgegen und wandelt ihn in eine
ausformulierte, kreative Aufgabenbeschreibung fuer mc.py um -- ODER stellt
GENAU EINE Rueckfrage, wenn etwas echt mehrdeutig ist. Anders als mc.py:
keine Datei-/Shell-Aktionen, kein Fence-/JSON-Werkzeugprotokoll -- nur ein
einziger, klar strukturierter ```decision```-JSON-Block pro Antwort.

mc.py bekommt dadurch weiterhin exakt das, wofuer es gebaut ist: eine
praezise, bereits durchdachte Aufgabe -- die Kreativitaet/Ideenarbeit passiert
hier, VOR mc.py, nicht im Coding-Agenten selbst (der soll klein, zuverlaessig
und woertlich bleiben)."""

import json
import os
import re
import urllib.error
import urllib.request

IGNORE_DIRS = {'.git', 'node_modules', '__pycache__', 'dist', '.venv', 'venv'}

PO_SYSTEM_PROMPT = """You are a product owner working with a user to turn a short
feature request into a clear, creative, well-specified task for a coding agent
that will implement it afterward. You do NOT write code or files yourself --
you only think about WHAT should be built and WHY, not HOW.

Always reply in German (the user is German-speaking), with a short line of
reasoning first, then exactly ONE ```decision``` fenced JSON block, followed
by ONE more fenced block with the actual text content. Free text goes RAW
into its own fenced block, NEVER as a JSON string value -- long text inside a
JSON string is exactly the kind of escaping mistake small models make (a
stray quote or newline breaks the whole JSON). Two possible shapes:

Ask exactly ONE clarifying question -- ONLY if something is genuinely
ambiguous or a real product decision is needed (not for details you could
reasonably decide yourself):
```decision
{"type": "question"}
```
```question
<the one specific question, German, plain text>
```

Otherwise, finalize a concrete, detailed task description ready for the
coding agent. Bring real product/design ideas of your own beyond the bare
minimum the user described -- that is your job here, not just relaying the
request verbatim -- but stay within the scope of what was actually asked for
and grounded in the EXISTING PROJECT CONTEXT below (extend what is there,
don't propose rewriting things that already work):
```decision
{"type": "spec"}
```
```summary
<one short sentence for the user, German, plain text>
```
```instruction
<the full, self-contained, detailed task text for the coding agent, German,
plain text -- precise enough for a coding agent with NO memory of this
conversation that only sees this text as its task>
```

Rules:
- Ask AT MOST one question in the whole conversation before finalizing --
  do not interrogate the user with several rounds of questions.
- The ```decision``` JSON block contains ONLY the "type" field -- nothing
  else goes inside it."""

DECISION_RE = re.compile(r"```decision\s*(.*?)```", re.DOTALL)
QUESTION_RE = re.compile(r"```question\s*\n?(.*?)```", re.DOTALL)
SUMMARY_RE = re.compile(r"```summary\s*\n?(.*?)```", re.DOTALL)
INSTRUCTION_RE = re.compile(r"```instruction\s*\n?(.*?)```", re.DOTALL)


def _extra_headers():
    """Zusaetzliche HTTP-Header aus MC_HEADERS ('Name: Wert', getrennt durch
    ';' oder Zeilenumbruch) -- dieselbe Env-Var wie mc.py's eigene
    extra_headers(), damit ein Endpoint, der z.B. einen Routing-Header
    braucht, fuer po.py und mc.py gleichermassen funktioniert, ohne den
    Header irgendwo im Code oder in einer Konfigdatei festzuschreiben."""
    out = {}
    raw = os.environ.get("MC_HEADERS", "")
    for part in re.split(r"[;\n]", raw):
        part = part.strip()
        if not part or ":" not in part:
            continue
        name, val = part.split(":", 1)
        name = name.strip()
        if name:
            out[name] = val.strip()
    return out


def _call_llm(messages, base_url, model, api_key, timeout=90):
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {"model": model, "messages": messages, "stream": False}
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    headers.update(_extra_headers())
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        obj = json.loads(resp.read().decode("utf-8", "replace"))
    return obj["choices"][0]["message"]["content"]


def refine(user_message, project_context, history, base_url, model, api_key):
    """Fuehrt EINEN Schritt des Produktdialogs aus.
    'history' ist die bisherige [{role, content}, ...]-Liste (ohne
    System-Prompt, wird von vibelove zwischen Aufrufen gehalten).
    Gibt (decision, neue_history) zurueck, decision ist eines von:
      {'type': 'question', 'question': str, 'raw': str}
      {'type': 'spec', 'summary': str, 'instruction': str, 'raw': str}
      {'type': 'error', 'error': str, 'raw': str}
    'raw' ist die vollstaendige, unverarbeitete Modell-Antwort (inkl. der
    kurzen Begruendung vor dem ```decision-Block) -- fuers Terminal/die
    Detail-Ansicht, damit man sieht, WARUM der Product Owner entschieden
    hat, was er entschieden hat, nicht nur das Ergebnis."""
    history = history + [{"role": "user", "content": user_message}]
    messages = [{"role": "system",
                 "content": PO_SYSTEM_PROMPT + "\n\nEXISTING PROJECT CONTEXT:\n" + project_context}]
    messages.extend(history)

    try:
        reply = _call_llm(messages, base_url, model, api_key)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        return {"type": "error", "error": f"HTTP {e.code} vom Endpoint: {body}", "raw": ""}, history
    except Exception as e:
        return {"type": "error", "error": str(e), "raw": ""}, history

    history = history + [{"role": "assistant", "content": reply}]

    m = DECISION_RE.search(reply)
    if not m:
        return {"type": "error", "error": "Keine gueltige Entscheidung erhalten.", "raw": reply}, history
    try:
        decision_type = json.loads(m.group(1)).get("type")
    except json.JSONDecodeError:
        return {"type": "error", "error": "Ungueltiges JSON im decision-Block.", "raw": reply}, history

    if decision_type == "question":
        qm = QUESTION_RE.search(reply)
        if not qm:
            return {"type": "error", "error": "Kein ```question-Block gefunden.", "raw": reply}, history
        return {"type": "question", "question": qm.group(1).strip(), "raw": reply}, history

    if decision_type == "spec":
        sm = SUMMARY_RE.search(reply)
        im = INSTRUCTION_RE.search(reply)
        if not sm or not im:
            return {"type": "error", "error": "Kein ```summary-/```instruction-Block gefunden.", "raw": reply}, history
        return {"type": "spec", "summary": sm.group(1).strip(),
                "instruction": im.group(1).strip(), "raw": reply}, history

    return {"type": "error", "error": "Unbekannter Entscheidungstyp.", "raw": reply}, history


def gather_project_context(project_dir, max_files=60):
    """Kurzer Kontext-Text ueber ein Projektverzeichnis fuer refine() --
    damit Vorschaege am Bestehenden ansetzen statt generisch/losgeloest zu
    sein (dieselbe Absicht wie mc.py's eigene task_hints()). Genutzt sowohl
    von vibelove (ueber die aktive Projekt-Instanz) als auch von der
    eigenstaendigen Kommandozeile weiter unten."""
    parts = [f"Projektverzeichnis: {os.path.abspath(project_dir)}"]
    notes_path = os.path.join(project_dir, 'MC-NOTIZEN.md')
    if os.path.isfile(notes_path):
        try:
            with open(notes_path, 'r', encoding='utf-8', errors='replace') as f:
                parts.append("Projekt-Notizen:\n" + f.read()[:1500])
        except OSError:
            pass
    files = []
    for dirpath, dirnames, filenames in os.walk(project_dir):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith('.')]
        for fn in filenames:
            files.append(os.path.relpath(os.path.join(dirpath, fn), project_dir).replace(os.sep, '/'))
    if files:
        files.sort()
        parts.append("Vorhandene Dateien:\n" + "\n".join(files[:max_files]))
    else:
        parts.append("Das Projekt ist noch leer (Neubau).")
    return "\n\n".join(parts)


def _main():
    import argparse
    import subprocess
    import sys

    ap = argparse.ArgumentParser(
        description="po.py -- Produktdialog vor mc.py: verwandelt einen knappen "
                     "Wunsch in eine ausformulierte Aufgabe, stellt bei Bedarf EINE "
                     "Rueckfrage, und stoesst danach mc.py mit der fertigen Aufgabe an.")
    ap.add_argument("wunsch", nargs="*", help="Erster Wunsch (ohne Angabe: interaktive Nachfrage)")
    ap.add_argument("--dir", default=".", help="Projektverzeichnis (Kontext + wird an mc.py weitergereicht)")
    ap.add_argument("--base-url", default=os.environ.get("MC_BASE_URL", "http://localhost:1234/v1"))
    ap.add_argument("--model", default=os.environ.get("MC_MODEL", "gemma-4-26b-a4b-it@mxfp4"))
    ap.add_argument("--api-key", default=os.environ.get("MC_API_KEY", ""))
    ap.add_argument("--no-run", action="store_true",
                     help="mc.py NICHT automatisch starten -- nur die fertige Aufgabe ausgeben")
    ap.add_argument("--check", action="store_true", help="an mc.py durchgereicht (--check)")
    ap.add_argument("--plan", action="store_true", help="an mc.py durchgereicht (--plan)")
    ap.add_argument("--yes", action="store_true", help="an mc.py durchgereicht (--yes)")
    args = ap.parse_args()

    context = gather_project_context(args.dir)
    history = []

    nachricht = " ".join(args.wunsch).strip()
    if not nachricht:
        try:
            nachricht = input("Was soll gebaut/geaendert werden? ").strip()
        except EOFError:
            nachricht = ""
    if not nachricht:
        print("Kein Wunsch erhalten.")
        return

    while True:
        decision, history = refine(nachricht, context, history,
                                    args.base_url, args.model, args.api_key)
        if decision["type"] == "error":
            print(f"Fehler: {decision['error']}")
            return
        if decision["type"] == "question":
            print(f"\n🧭 Product Owner: {decision['question']}")
            try:
                nachricht = input("> ").strip()
            except EOFError:
                nachricht = ""
            if not nachricht:
                print("Abgebrochen (keine Antwort erhalten).")
                return
            continue

        # decision["type"] == "spec"
        print(f"\n🧭 Product Owner: {decision['summary']}\n")
        print("--- Auftragstext ---")
        print(decision["instruction"])
        print("---------------------")
        try:
            fb = input("Auftrag ok? [Enter]=bauen · Text=Aenderungswunsch · n=abbrechen> ").strip()
        except EOFError:
            fb = ""
        if fb.lower() in ("n", "nein", "no", "q", "abbrechen"):
            print("Abgebrochen.")
            return
        if fb:
            nachricht = fb
            continue
        break

    instruction = decision["instruction"]
    if args.no_run:
        print("\n(--no-run aktiv, mc.py wird nicht gestartet.)")
        return

    mc_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mc.py")
    cmd = [sys.executable, mc_path, "--dir", args.dir,
           "--base-url", args.base_url, "--model", args.model]
    if args.check:
        cmd.append("--check")
    if args.plan:
        cmd.append("--plan")
    if args.yes:
        cmd.append("--yes")
    cmd.append(instruction)
    env = os.environ.copy()
    if args.api_key:
        env["MC_API_KEY"] = args.api_key
    subprocess.run(cmd, env=env)


if __name__ == "__main__":
    _main()
