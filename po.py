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

# Derselbe Dateiname/dasselbe Format wie mc.py's eigene MC_PLAN/
# _write_plan_file() (mc.py:147/3388) -- so erkennt auch ein normaler,
# nicht von po.py orchestrierter mc.py-Lauf einen offenen Plan ueber
# seine eigene task_hints()-Logik, falls die Kette hier abbricht.
PLAN_FILE_NAME = "mc_plan.md"
PLAN_ITEM_RE = re.compile(r"^- \[( |x)\] (\d+)\.\s*(.+)$", re.MULTILINE)

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

Otherwise, produce the actual work item(s). Bring real product/design ideas
of your own beyond the bare minimum the user described -- that is your job
here, not just relaying the request verbatim -- but stay within the scope of
what was actually asked for and grounded in the EXISTING PROJECT CONTEXT
below (extend what is there, don't propose rewriting things that already
work). Two shapes here:

If the work is ONE self-contained unit, finalize a single spec:
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

If the work naturally splits into several separately-buildable steps (e.g.
distinct features/layers that can each be built and finished on their own,
not just an arbitrary word-count split), produce an ordered plan instead --
each step becomes its OWN later coding-agent run with a FRESH, empty context,
so each step's instruction must be fully self-contained (assume the agent
building step 2 has no memory of step 1's conversation, only of the files
step 1 actually left behind):
```decision
{"type": "plan"}
```
```summary
<one short sentence for the user, German, plain text>
```
```step-1
<full, self-contained instruction for step 1, German, plain text>
```
```step-2
<full, self-contained instruction for step 2, German, plain text>
```
(as many ```step-N blocks as needed, numbered in build order)

Rules:
- Ask AT MOST one question in the whole conversation before finalizing --
  do not interrogate the user with several rounds of questions.
- The ```decision``` JSON block contains ONLY the "type" field -- nothing
  else goes inside it.
- Prefer a single "spec" for anything reasonably small. Only use "plan" when
  the work genuinely has multiple independent, sequentially-buildable parts
  -- splitting for its own sake creates overhead, not reliability."""

DECISION_RE = re.compile(r"```decision\s*(.*?)```", re.DOTALL)
QUESTION_RE = re.compile(r"```question\s*\n?(.*?)```", re.DOTALL)
SUMMARY_RE = re.compile(r"```summary\s*\n?(.*?)```", re.DOTALL)
INSTRUCTION_RE = re.compile(r"```instruction\s*\n?(.*?)```", re.DOTALL)
STEP_RE = re.compile(r"```step-(\d+)\s*\n?(.*?)```", re.DOTALL)


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
      {'type': 'plan', 'summary': str, 'steps': [{'num': int, 'instruction': str}, ...], 'raw': str}
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
        return {"type": "error", "error": f"HTTP {e.code} vom Endpoint: {body}",
                "raw": "", "retryable": False}, history
    except Exception as e:
        return {"type": "error", "error": str(e), "raw": "", "retryable": False}, history

    history = history + [{"role": "assistant", "content": reply}]

    m = DECISION_RE.search(reply)
    if not m:
        return {"type": "error", "error": "Keine gueltige Entscheidung erhalten.",
                "raw": reply, "retryable": True}, history
    try:
        decision_type = json.loads(m.group(1)).get("type")
    except json.JSONDecodeError:
        return {"type": "error", "error": "Ungueltiges JSON im decision-Block.",
                "raw": reply, "retryable": True}, history

    if decision_type == "question":
        qm = QUESTION_RE.search(reply)
        if not qm:
            return {"type": "error", "error": "Kein ```question-Block gefunden.",
                    "raw": reply, "retryable": True}, history
        return {"type": "question", "question": qm.group(1).strip(), "raw": reply}, history

    if decision_type == "spec":
        sm = SUMMARY_RE.search(reply)
        im = INSTRUCTION_RE.search(reply)
        if not sm or not im:
            return {"type": "error", "error": "Kein ```summary-/```instruction-Block gefunden.",
                    "raw": reply, "retryable": True}, history
        return {"type": "spec", "summary": sm.group(1).strip(),
                "instruction": im.group(1).strip(), "raw": reply}, history

    if decision_type == "plan":
        sm = SUMMARY_RE.search(reply)
        step_matches = STEP_RE.findall(reply)
        if not sm or not step_matches:
            return {"type": "error", "error": "Kein ```summary-/```step-N-Block gefunden.",
                    "raw": reply, "retryable": True}, history
        # Manche Modelle emittieren einen leeren step-N-Block und direkt
        # danach denselben step-N nochmal mit echtem Inhalt (Selbstkorrektur
        # mitten in der Antwort) -- bei doppelter Nummer gewinnt der LETZTE
        # nicht-leere Treffer, nicht einfach "erster Treffer gewinnt".
        by_num = {}
        for n, text in step_matches:
            n = int(n)
            text = text.strip()
            if text or n not in by_num:
                by_num[n] = text
        steps = [{"num": n, "instruction": by_num[n]} for n in sorted(by_num)]
        leer = [s["num"] for s in steps if not s["instruction"]]
        if leer:
            return {"type": "error", "error": f"Leere(r) Schritt-Block(e) im Plan: {leer}.",
                    "raw": reply, "retryable": True}, history
        return {"type": "plan", "summary": sm.group(1).strip(), "steps": steps, "raw": reply}, history

    return {"type": "error", "error": "Unbekannter Entscheidungstyp.",
            "raw": reply, "retryable": True}, history


def refine_retrying(user_message, project_context, history, base_url, model, api_key, attempts=3):
    """Wie refine(), aber wiederholt automatisch bei RETRYABLE Fehlern
    (kaputtes Protokoll-Format -- bei einem kleinen Modell mit mehrteiliger
    strukturierter Ausgabe ein erwartbarer gelegentlicher Aussetzer, kein
    systematisches Problem). Echte HTTP-/Netzwerkfehler werden NICHT
    wiederholt (retryable=False) -- die aendern sich durch reines Neu-
    Versuchen typischerweise nicht und sollen nicht mehrfach denselben
    vermutlich falsch konfigurierten Endpunkt treffen."""
    last = None
    for _ in range(attempts):
        decision, new_history = refine(user_message, project_context, history,
                                        base_url, model, api_key)
        if decision["type"] != "error" or not decision.get("retryable"):
            return decision, new_history
        last = (decision, new_history)
    return last


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


def write_plan_file(steps, project_dir):
    """Schreibt den Plan im GLEICHEN Format wie mc.py's eigene
    _write_plan_file() (mc.py:3388): '- [ ] N. <text>'. Absichtlich
    dieselbe Konvention, nicht nur Namensgleichheit -- ein einzelner
    mc.py-Lauf ausserhalb dieser Kette erkennt einen offenen Plan ueber
    dieselbe task_hints()-Logik."""
    path = os.path.join(project_dir, PLAN_FILE_NAME)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Aenderungsplan (po.py)\n\n" + "\n".join(
                f"- [ ] {s['num']}. {s['instruction'].splitlines()[0][:200]}"
                for s in steps) + "\n")
        return path
    except OSError:
        return None


def read_plan_status(project_dir):
    """Liest den aktuellen Abhak-Status aus mc_plan.md. Gibt eine Liste von
    {'num': int, 'text': str, 'done': bool} zurueck (leer, falls keine
    Plan-Datei existiert)."""
    path = os.path.join(project_dir, PLAN_FILE_NAME)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return []
    return [{"num": int(num), "text": text.strip(), "done": mark == "x"}
            for mark, num, text in PLAN_ITEM_RE.findall(content)]


def mark_step_done(project_dir, step_num):
    """Hakt Schritt step_num in mc_plan.md ab -- als Sicherheitsnetz, falls
    der einzelne mc.py-Lauf fuer diesen Schritt es selbst vergessen hat
    (er wird explizit dazu angewiesen, aber small models vergessen Dinge)."""
    path = os.path.join(project_dir, PLAN_FILE_NAME)
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        content = re.sub(rf"^- \[ \] {step_num}\.", f"- [x] {step_num}.", content,
                          count=1, flags=re.MULTILINE)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError:
        pass


def ensure_clean_worktree(project_dir):
    """Committet liegen gebliebene Aenderungen VOR dem naechsten Schritt --
    dieselbe Logik wie vibelove's stelle_sauberen_arbeitsbaum_sicher()
    (vibelove/server.py:157): mc.py's eigene Git-Absicherung verlangt einen
    SAUBEREN Arbeitsbaum bei JEDEM Lauf, sonst bleibt GIT_ROLLBACK fuer den
    GESAMTEN Lauf deaktiviert -- bei einer Kette aus mehreren Schritten
    wuerde ein einziger dreckiger Zwischenstand sonst JEDEN nachfolgenden
    Schritt committlos machen. Ohne eigenes Git-Repo passiert nichts."""
    import subprocess
    if not os.path.isdir(os.path.join(project_dir, '.git')):
        return
    try:
        status = subprocess.run(['git', 'status', '--porcelain'], cwd=project_dir,
                                 capture_output=True, text=True, timeout=10)
        if not status.stdout.strip():
            return
        subprocess.run(['git', 'add', '-A'], cwd=project_dir, capture_output=True, timeout=15)
        subprocess.run(['git', 'commit', '-m', 'po.py: Zwischenstand (vor naechstem Schritt gesichert)'],
                        cwd=project_dir, capture_output=True, text=True, timeout=15)
    except Exception:
        pass


def _run_mc(instruction, args, extra_mc_flags=()):
    """Startet mc.py als Subprozess mit EINER Aufgabe -- gemeinsame Logik
    fuer den Einzel-Auftrag-Pfad (spec) und JEDEN Schritt der Ketten-
    Ausfuehrung (plan). Gibt den Returncode zurueck.

    Sichert VORHER einen sauberen Arbeitsbaum (ensure_clean_worktree):
    mc.py's eigene Git-Absicherung braucht einen sauberen Baum beim
    Start, sonst ist sie fuer den GESAMTEN Lauf stillschweigend
    deaktiviert -- egal ob dies der einzige Aufruf (spec) oder einer
    von mehreren (plan) ist."""
    ensure_clean_worktree(args.dir)
    import subprocess
    import sys
    mc_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mc.py")
    cmd = [sys.executable, mc_path, "--dir", args.dir,
           "--base-url", args.base_url, "--model", args.model]
    if args.check:
        cmd.append("--check")
    for flag in extra_mc_flags:
        cmd.append(flag)
    if args.yes:
        cmd.append("--yes")
    cmd.append(instruction)
    env = os.environ.copy()
    if args.api_key:
        env["MC_API_KEY"] = args.api_key
    return subprocess.run(cmd, env=env).returncode


def run_plan_chained(steps, args):
    """Fuehrt einen mehrteiligen Plan aus: JEDER Schritt bekommt einen
    EIGENEN, frischen mc.py-Lauf (kein gemeinsamer, wachsender Kontext ueber
    mehrere Schritte hinweg -- genau das Problem, das lange Einzel-Laeufe
    dieser Session wiederholt zeigten: Details aus der Mitte einer langen
    Aufgabe gingen verloren). Der Plan wird als mc_plan.md abgelegt (gleiches
    Format wie mc.py's eigene _write_plan_file), damit auch ein einzelner,
    nicht ueber diese Kette laufender mc.py-Aufruf einen offenen Plan ueber
    seine eigene task_hints()-Logik erkennt.

    Nach JEDEM Schritt wird angehalten und das Ergebnis gezeigt, bevor es
    weitergeht (--yes-all-steps ueberspringt das) -- Kapitel 57: ein
    automatischer Verifikations-/Fortschritts-Schritt darf nicht selbst
    entscheiden, er soll nur informieren."""
    plan_path = write_plan_file(steps, args.dir)
    if plan_path:
        print(f"Plan gespeichert: {plan_path}", flush=True)

    for step in steps:
        status = read_plan_status(args.dir)
        entry = next((s for s in status if s["num"] == step["num"]), None)
        if entry and entry["done"]:
            print(f"\n=== Schritt {step['num']}/{len(steps)}: bereits erledigt, ueberspringe ===", flush=True)
            continue

        print(f"\n=== Schritt {step['num']}/{len(steps)} von {len(steps)} ===", flush=True)
        print(step["instruction"][:300] + ("..." if len(step["instruction"]) > 300 else ""), flush=True)

        per_step_instruction = (
            f"Dies ist SCHRITT {step['num']} eines mehrteiligen Plans "
            f"(gespeichert in {PLAN_FILE_NAME}). Setze GENAU diesen einen "
            f"Schritt um, sonst NICHTS -- auch wenn in {PLAN_FILE_NAME} "
            f"noch weitere offene Punkte stehen, die sind fuer SPAETERE, "
            f"eigene Laeufe. Hake am Ende NUR Punkt {step['num']} in "
            f"{PLAN_FILE_NAME} ab ('- [ ]' -> '- [x]').\n\n"
            f"Schritt {step['num']}: {step['instruction']}"
        )

        if args.no_run:
            print("(--no-run aktiv, mc.py wird fuer diesen Schritt nicht gestartet.)", flush=True)
            continue

        rc = _run_mc(per_step_instruction, args)

        status = read_plan_status(args.dir)
        entry = next((s for s in status if s["num"] == step["num"]), None)
        checked = bool(entry and entry["done"])
        if not checked:
            print(f"⚠ Schritt {step['num']}: mc.py hat den Punkt in {PLAN_FILE_NAME} "
                  f"NICHT selbst abgehakt (returncode {rc}) -- unklar, ob er wirklich "
                  f"fertig ist. Bitte pruefen.", flush=True)

        if step is steps[-1]:
            break
        if getattr(args, "yes_all_steps", False):
            continue
        try:
            fb = input("Weiter zum naechsten Schritt? [Enter]=ja · n=abbrechen> ").strip()
        except EOFError:
            fb = ""
        if fb.lower() in ("n", "nein", "no", "q", "abbrechen"):
            print("Kette angehalten. Restliche Schritte bleiben offen in "
                  f"{PLAN_FILE_NAME} und koennen spaeter fortgesetzt werden.", flush=True)
            return


def _main():
    import argparse

    ap = argparse.ArgumentParser(
        description="po.py -- Produktdialog vor mc.py: verwandelt einen knappen "
                     "Wunsch in eine ausformulierte Aufgabe (oder einen mehrteiligen "
                     "Plan), stellt bei Bedarf EINE Rueckfrage, und stoesst danach "
                     "mc.py mit der fertigen Aufgabe an -- bei einem Plan als Kette "
                     "einzelner, frischer mc.py-Laeufe, einer je Schritt.")
    ap.add_argument("wunsch", nargs="*", help="Erster Wunsch (ohne Angabe: interaktive Nachfrage)")
    ap.add_argument("--dir", default=".", help="Projektverzeichnis (Kontext + wird an mc.py weitergereicht)")
    ap.add_argument("--base-url", default=os.environ.get("MC_BASE_URL", "http://localhost:1234/v1"))
    ap.add_argument("--model", default=os.environ.get("MC_MODEL", "gemma-4-26b-a4b-it@mxfp4"))
    ap.add_argument("--api-key", default=os.environ.get("MC_API_KEY", ""))
    ap.add_argument("--no-run", action="store_true",
                     help="mc.py NICHT automatisch starten -- nur die fertige Aufgabe/den Plan ausgeben")
    ap.add_argument("--check", action="store_true", help="an mc.py durchgereicht (--check)")
    ap.add_argument("--plan", action="store_true", help="an mc.py durchgereicht (--plan, nur bei Einzel-Auftraegen)")
    ap.add_argument("--yes", action="store_true", help="an mc.py durchgereicht (--yes)")
    ap.add_argument("--yes-all-steps", action="store_true",
                     help="bei einem mehrteiligen Plan NICHT nach jedem Schritt anhalten")
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
        decision, history = refine_retrying(nachricht, context, history,
                                             args.base_url, args.model, args.api_key)
        if decision["type"] == "error":
            print(f"Fehler: {decision['error']}")
            if decision.get("raw"):
                print("--- letzte Modell-Antwort (zur Diagnose) ---")
                print(decision["raw"])
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

        if decision["type"] == "plan":
            print(f"\n🧭 Product Owner: {decision['summary']}\n")
            print(f"--- Plan ({len(decision['steps'])} Schritte) ---")
            for s in decision["steps"]:
                erste_zeile = s["instruction"].splitlines()[0] if s["instruction"] else "(leer)"
                print(f"  {s['num']}. {erste_zeile}")
            print("---------------------")
            try:
                fb = input("Plan ok? [Enter]=Kette starten · Text=Aenderungswunsch · n=abbrechen> ").strip()
            except EOFError:
                fb = ""
            if fb.lower() in ("n", "nein", "no", "q", "abbrechen"):
                print("Abgebrochen.")
                return
            if fb:
                nachricht = fb
                continue
            run_plan_chained(decision["steps"], args)
            return

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

    extra = ["--plan"] if args.plan else []
    _run_mc(instruction, args, extra_mc_flags=extra)


if __name__ == "__main__":
    _main()
