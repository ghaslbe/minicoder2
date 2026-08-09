# Tests fuer die deterministischen Teile von mc.py (Parser, Gates, Matching).
# Ausfuehren:  python3 -m pytest tests/ -q
# Bewusst OHNE Netzwerk/LLM — alles, was hier getestet wird, ist reiner
# Python-Code, der unabhaengig vom Modell funktionieren muss.

import importlib.util
import json
import os
import shutil
import sys

import pytest

# Konfig-Datei des Nutzers (~/.mc.json) darf die Tests nicht beeinflussen.
os.environ["MC_CONFIG"] = os.path.join(os.path.dirname(__file__), "no-such-config.json")

_SPEC = importlib.util.spec_from_file_location(
    "mc", os.path.join(os.path.dirname(__file__), "..", "mc.py"))
mc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mc)
mc.AUTO_YES = True  # sonst haengen Handler-Tests an confirm()/input()


@pytest.fixture(autouse=True)
def _clean_state(tmp_path, monkeypatch):
    """Jeder Test: eigenes Arbeitsverzeichnis, leerer Aufgaben-Zustand."""
    monkeypatch.chdir(tmp_path)
    mc.READ_FILES.clear()
    mc.OVERWRITE_REJECTS.clear()
    mc.WRITE_HISTORY.clear()
    mc.TOUCHED.clear()
    mc.PLAN_POINTS.clear()
    mc.LOSS_WARNED_NAMES.clear()
    mc.EXPLORED = False
    mc.HAS_CODE = None
    yield


# --------------------------- Action-Parsing --------------------------------

def test_action_json_klassisch():
    action, _ = mc.extract_action(
        'Text davor.\n```action\n{"action":"read_file","path":"a.py"}\n```')
    assert action == {"action": "read_file", "path": "a.py"}


def test_action_in_json_fence_wird_erkannt():
    # Real beobachtet (E2E-Test): Modell labelt den Block ```json statt
    # ```action — mit action-Feld zaehlt er trotzdem.
    action, _ = mc.extract_action(
        '```json\n{"action":"read_file","path":"app.py"}\n```')
    assert action == {"action": "read_file", "path": "app.py"}
    # Gefenctes JSON OHNE action-Feld bleibt Prosa (z.B. Beispiel-Payload)
    action, _ = mc.extract_action('```json\n{"name":"test"}\n```')
    assert action is None


def test_write_file_content_fence():
    action, _ = mc.extract_action(
        '```action\n{"action":"write_file","path":"b.py"}\n```\n'
        '```content\nprint("hi")\n```')
    assert action["content"] == 'print("hi")\n'


def test_edit_file_old_new_fences():
    action, _ = mc.extract_action(
        '```action\n{"action":"edit_file","path":"a.py"}\n```\n'
        '```old\nx = 1\n```\n```new\nx = 2\n```')
    assert action["old"] == "x = 1"
    assert action["new"] == "x = 2"


def test_json_felder_haben_vorrang_vor_fences():
    action, _ = mc.extract_action(
        '```action\n{"action":"edit_file","path":"a.py","old":"J","new":"K"}\n```\n'
        '```old\nFENCE\n```\n```new\nFENCE\n```')
    assert action["old"] == "J" and action["new"] == "K"


def test_write_file_ohne_content_und_fence_gibt_fehler():
    action, _ = mc.extract_action(
        '```action\n{"action":"write_file","path":"b.py"}\n```')
    assert "_fence_error" in action


def test_langer_zaun_fuer_inhalt_mit_backticks():
    action, _ = mc.extract_action(
        '```action\n{"action":"write_file","path":"x.md"}\n```\n'
        '````content\n```python\ncode\n```\n````')
    assert action["content"] == "```python\ncode\n```\n"


# ------------------------------ Truncation ---------------------------------

def test_truncate_zeigt_kopf_und_ende_und_spillt():
    s = "ANFANG" + "x" * 20000 + "ENDE"
    out = mc.truncate(s)
    assert out.startswith("ANFANG") and "ENDE" in out
    assert "ausgelassen" in out
    # Volle Ausgabe bleibt als Spill-Datei nachschlagbar
    assert "gespeichert unter" in out
    pfad = out.split("gespeichert unter ")[1].split(" —")[0].strip()
    with open(pfad, encoding="utf-8") as f:
        assert f.read() == s
    os.remove(pfad)


def test_looks_truncated_offener_fence_und_net_abort():
    assert mc._looks_truncated("```action\n{\"a\":", None) is True
    assert mc._looks_truncated("alles gut", "net_abort") is True
    assert mc._looks_truncated("fertig.", "stop") is False


# ---------------------------- Overwrite-Gate -------------------------------

def test_gate_lehnt_ungelesene_existierende_datei_ab():
    with open("alt.py", "w") as f:
        f.write("x = 1\n")
    assert "ABGELEHNT" in mc._overwrite_gate("alt.py")


def test_gate_nach_read_file_offen():
    with open("alt.py", "w") as f:
        f.write("x = 1\n")
    mc.do_read_file({"path": "alt.py"})
    assert mc._overwrite_gate("alt.py") == ""


def test_gate_overwrite_flag_und_neue_datei():
    with open("alt.py", "w") as f:
        f.write("x = 1\n")
    assert mc._overwrite_gate("alt.py", force=True) == ""
    assert mc._overwrite_gate("neu.py") == ""


def test_gate_notausgang_nach_max_rejects():
    with open("alt.py", "w") as f:
        f.write("x = 1\n")
    for _ in range(mc.MAX_OVERWRITE_REJECTS):
        assert mc._overwrite_gate("alt.py") != ""
    assert mc._overwrite_gate("alt.py") == ""


def test_write_files_overwrite_pro_datei():
    with open("alt.py", "w") as f:
        f.write("x = 1\n")
    ok, msg = mc.do_write_files({"files": [
        {"path": "alt.py", "content": "y = 2\n", "overwrite": True},
        {"path": "neu.py", "content": "z = 3\n"}]})
    assert ok, msg


# ------------------------- Generator-Konflikt ------------------------------

def test_generator_auf_volles_verzeichnis_abgelehnt():
    os.makedirs("frontend")
    with open("frontend/package.json", "w") as f:
        f.write("{}")
    msg = mc._generator_conflict("npm create vite@latest frontend -- --template react")
    assert "ABGELEHNT" in msg


def test_generator_auf_neues_ziel_erlaubt():
    assert mc._generator_conflict("npm create vite@latest brandneu -- --template react") == ""
    assert mc._generator_conflict("npm install && npm run build") == ""


# ---------------------------- DANGEROUS_RUN --------------------------------

@pytest.mark.parametrize("cmd", [
    "sudo rm -rf /",
    "rm -rf /",
    "dd if=/dev/zero of=/dev/sda",
    "del /s /q C:\\projekt",
    "DEL /S alles",
    "rmdir /s /q build",
    "rd /s altesdir",
    "format c:",
    "reg delete HKLM\\Software",
    "diskpart",
])
def test_destruktive_kommandos_erkannt(cmd):
    assert mc.DANGEROUS_RUN.search(cmd), cmd


@pytest.mark.parametrize("cmd", [
    "rm -rf node_modules",
    "npm run build",
    "python3 -m pytest",
    "del.py ausfuehren",
    "git status",
])
def test_harmlose_kommandos_erlaubt(cmd):
    assert not mc.DANGEROUS_RUN.search(cmd), cmd


# ------------------------------ edit_file ----------------------------------

def test_edit_whitespace_toleranz_am_zeilenende():
    with open("a.py", "w") as f:
        f.write("def f():   \n    return 1   \n")
    ok, msg = mc.do_edit_file({"path": "a.py",
                               "old": "def f():\n    return 1",
                               "new": "def f():\n    return 2"})
    assert ok, msg
    assert "return 2" in open("a.py").read()


def test_edit_fehltreffer_liefert_aehnlichste_stelle():
    with open("a.py", "w") as f:
        f.write("def rechne():\n    return summe + 1\n")
    ok, msg = mc.do_edit_file({"path": "a.py",
                               "old": "def rechne():\n    return sume + 1",
                               "new": "x"})
    assert not ok
    assert "AEHNLICHSTE Stelle" in msg
    assert "summe + 1" in msg  # der ECHTE Dateitext zum Kopieren


def test_edit_replace_all_fuer_umbenennung():
    with open("a.py", "w") as f:
        f.write("alt = 1\nprint(alt)\nreturn alt\n")
    ok, msg = mc.do_edit_file({"path": "a.py", "old": "alt", "new": "neu",
                               "replace_all": True})
    assert ok, msg
    inhalt = open("a.py").read()
    assert "alt" not in inhalt and inhalt.count("neu") == 3


def test_edit_mehrdeutig_nennt_replace_all():
    with open("a.py", "w") as f:
        f.write("x\nx\n")
    ok, msg = mc.do_edit_file({"path": "a.py", "old": "x", "new": "y"})
    assert not ok and "replace_all" in msg


# ------------------------- Aufgaben-Anreicherung ----------------------------

def test_expected_files_mit_windows_backslash():
    files = mc.expected_files_from_task(r"erstelle backend\app.py und lies docs/readme.md")
    assert "backend/app.py" in files
    assert "docs/readme.md" in files


def test_expected_files_ignoriert_urls():
    files = mc.expected_files_from_task("lade https://example.com/lib.js herunter")
    assert files == []


def test_task_hints_erkennt_bestehendes_projekt():
    os.makedirs("frontend")
    with open("frontend/package.json", "w") as f:
        f.write("{}")
    hints = mc.task_hints("erweitere die app")
    assert "FURTHER DEVELOPMENT" in hints
    assert "generator" in hints


def test_task_hints_leer_bei_leerem_verzeichnis():
    assert mc.task_hints("bau mir eine app") == ""


def test_task_hints_liest_projekt_notizen():
    with open(mc.MC_NOTES, "w") as f:
        f.write("- Backend-Port: 5010 (FEST)\n- Feld heisst 'geburtstag'\n")
    hints = mc.task_hints("mach irgendwas")
    assert "Backend-Port: 5010" in hints
    assert "STICK TO THIS" in hints


def test_system_prompt_lehrt_notizen():
    assert "MC-NOTIZEN.md" in mc.system_prompt(True)


def test_system_prompt_lehrt_basedir_und_kein_debug():
    # Lektionen aus dem OpenRouter-Testlauf: relative DB-Pfade und debug=True
    # tauchten in fertigen Apps auf, weil der Prompt sie nicht verbot.
    sp = mc.system_prompt(True)
    assert "BASE_DIR" in sp
    assert "debug=True" in sp


def test_system_prompt_lehrt_sqlite_view_delete():
    sp = mc.system_prompt(True)
    assert "SQLite" in sp
    assert "DELETE" in sp


def test_system_prompt_lehrt_dateien_aufteilen():
    sp = mc.system_prompt(True)
    assert "SPLIT BY CONCERN" in sp
    assert "one component per" in sp
    assert "Python/backend" in sp


def test_plan_phase_verlangt_datei_aufteilung(monkeypatch):
    monkeypatch.setattr(mc, "chat_stream", lambda messages, model: "1. Plan")
    monkeypatch.setattr("builtins.input", lambda *a: "")
    messages = [{"role": "system", "content": "sys"}]
    mc.plan_phase(messages, "m")
    ask_text = messages[1]["content"]
    assert "Datei-Aufteilung" in ask_text


def test_check_prompt_verlangt_eingabe_validierung():
    # Kleine Modelle testen wortwoertlich, was der Prompt nennt — leeres
    # Pflichtfeld muss deshalb explizit als Pruef-Fall dastehen.
    assert "EMPTY required field" in mc.CHECK_PROMPT


# --------------------------- Kontext-Beschneidung ---------------------------

def _historie(n_steps, size=900):
    """Baut eine Message-Historie mit n Schritten (assistant-Action + Ergebnis)."""
    msgs = [{"role": "system", "content": "S"}]
    for i in range(n_steps):
        msgs.append({"role": "assistant",
                     "content": f'```action\n{{"action":"write_file","path":"f{i}","content":"{"x"*size}"}}\n```'})
        msgs.append({"role": "user", "content": "[Ergebnis von write_file]\n" + "y" * size})
    return msgs


def test_prune_kuerzt_alte_schritte_und_laesst_neue():
    msgs = _historie(8)
    alt_len = len(msgs[1]["content"])
    mc.prune_messages(msgs, keep=2)
    assert len(msgs[1]["content"]) < alt_len          # alt: gekuerzt
    assert len(msgs[-1]["content"]) > 600             # juengst: unangetastet
    assert msgs[0]["content"] == "S"                  # System-Prompt: nie


def test_maybe_prune_laesst_passende_historie_unangetastet(monkeypatch):
    # Grosses geladenes Fenster -> KEINE Kuerzung, das Praefix bleibt stabil
    # (Voraussetzung fuer den Prompt-Cache-Hit des Servers).
    monkeypatch.setitem(mc._LOADED_CTX_TOKENS, "m", 100000)
    msgs = _historie(8)
    vorher = [m["content"] for m in msgs]
    mc.maybe_prune(msgs, "m")
    assert [m["content"] for m in msgs] == vorher


def test_maybe_prune_kuerzt_bei_kontextdruck(monkeypatch):
    # Historie ~15k Zeichen, Fenster 8000 Token -> Schwelle (~10k) gerissen:
    # aeltere Schritte werden im Batch gekuerzt, die juengsten bleiben voll.
    monkeypatch.setitem(mc._LOADED_CTX_TOKENS, "m", 8000)
    msgs = _historie(8)
    mc.maybe_prune(msgs, "m")
    assert len(msgs[1]["content"]) < 900              # alt: gekuerzt
    assert len(msgs[-1]["content"]) > 600             # juengst: unangetastet


def test_maybe_prune_notfallstufe_wenn_normale_kuerzung_nicht_reicht(monkeypatch):
    # Winziges Fenster: selbst nach normaler Kuerzung (KEEP_CONTEXT volle
    # Schritte) zu gross -> Notfall-Kuerzung auf den letzten Schritt.
    monkeypatch.setitem(mc._LOADED_CTX_TOKENS, "m", 3000)
    msgs = _historie(8)
    mc.maybe_prune(msgs, "m")
    assert len(msgs[-3]["content"]) < 900             # vorletzter Schritt: gekuerzt
    assert len(msgs[-1]["content"]) > 600             # letzter Schritt: voll


def test_maybe_prune_ohne_fensterinfo_wie_bisher(monkeypatch):
    # Fenster nicht abfragbar (kein LM Studio) -> altes Verhalten:
    # sofort kuerzen, Ueberlauf-Schutz vor Cache-Optimierung.
    monkeypatch.setitem(mc._LOADED_CTX_TOKENS, "m", 0)
    msgs = _historie(8)
    mc.maybe_prune(msgs, "m")
    assert len(msgs[1]["content"]) < 900


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_loaded_ctx_noch_nicht_geladen_wird_nicht_gecacht(monkeypatch):
    # LM Studio laedt JIT erst beim ersten Chat-Request: davor ist
    # loaded_context_length leer. Das darf NICHT als 0 gecacht werden,
    # sonst bliebe das Lazy Pruning den ganzen Lauf deaktiviert.
    monkeypatch.setattr(mc, "_LOADED_CTX_TOKENS", {})
    payload = {"data": [{"id": "m", "loaded_context_length": None}]}
    monkeypatch.setattr(mc.urllib.request, "urlopen",
                        lambda req, timeout=5: _FakeResp(payload))
    assert mc._loaded_ctx_tokens("m") == 0
    assert "m" not in mc._LOADED_CTX_TOKENS          # transient: kein Cache
    payload["data"][0]["loaded_context_length"] = 8192
    assert mc._loaded_ctx_tokens("m") == 8192        # zieht nach dem Laden nach
    assert mc._LOADED_CTX_TOKENS["m"] == 8192        # jetzt gecacht


def test_loaded_ctx_kein_lm_studio_wird_gecacht(monkeypatch):
    # Endpoint fehlt/unerreichbar (z.B. Ollama) -> definitiv: 0 cachen,
    # damit nicht jeder Schritt einen vergeblichen HTTP-Versuch macht.
    monkeypatch.setattr(mc, "_LOADED_CTX_TOKENS", {})

    def _boom(req, timeout=5):
        raise OSError("connection refused")

    monkeypatch.setattr(mc.urllib.request, "urlopen", _boom)
    assert mc._loaded_ctx_tokens("m") == 0
    assert mc._LOADED_CTX_TOKENS["m"] == 0


# --------------------------- JSX/TSX-Validierung ----------------------------

def _fake_checker(tmpdir, exit_code, message=""):
    """Legt eine gefaelschte node_modules/.bin/esbuild an (Shell-Skript)."""
    bindir = os.path.join(tmpdir, "node_modules", ".bin")
    os.makedirs(bindir, exist_ok=True)
    p = os.path.join(bindir, "esbuild")
    with open(p, "w") as f:
        f.write(f'#!/bin/sh\necho "{message}" >&2\nexit {exit_code}\n')
    os.chmod(p, 0o755)


@pytest.mark.skipif(sys.platform == "win32", reason="Shell-Skript-Fake")
def test_jsx_validierung_meldet_parse_fehler(tmp_path):
    _fake_checker(str(tmp_path), 1, "error: Adjacent JSX elements")
    with open("App.jsx", "w") as f:
        f.write("kaputt")
    status, msg = mc.validate_path("App.jsx")
    assert status == "bad"
    assert "Adjacent JSX" in msg


@pytest.mark.skipif(sys.platform == "win32", reason="Shell-Skript-Fake")
def test_jsx_validierung_ok_bei_exit_0(tmp_path):
    _fake_checker(str(tmp_path), 0)
    with open("App.jsx", "w") as f:
        f.write("export default 1")
    assert mc.validate_path("App.jsx")[0] == "ok"


def test_jsx_validierung_skip_ohne_checker():
    with open("App.jsx", "w") as f:
        f.write("egal")
    assert mc.validate_path("App.jsx")[0] == "skip"


@pytest.mark.skipif(sys.platform == "win32", reason="Shell-Skript-Fake")
def test_jsx_warnungen_als_nicht_blockierender_hinweis(tmp_path):
    _fake_checker(str(tmp_path), 0, "warning eslint(no-unused-vars): 'setSortOrder' never used")
    with open("App.jsx", "w") as f:
        f.write("export default 1")
    status, msg = mc.validate_path("App.jsx")
    assert status == "ok" and "setSortOrder" in msg
    out = mc.validate_written(["App.jsx"])
    assert "nicht blockierend" in out and "setSortOrder" in out


# ---------------------- HTML: eingebettetes <script>-JS --------------------

def test_extract_inline_scripts_ignoriert_src_und_importmap():
    html = '''<script src="app.js"></script>
    <script type="importmap">{"imports":{}}</script>
    <script type="module">const x = 1;</script>'''
    out = mc._extract_inline_scripts(html)
    assert out == ["const x = 1;"]


@pytest.mark.skipif(sys.platform == "win32", reason="Shell-Skript-Fake")
def test_html_validierung_nutzt_esbuild_wenn_vorhanden(tmp_path):
    _fake_checker(str(tmp_path), 1, "error: Unexpected token")
    with open("spiel.html", "w") as f:
        f.write('<script type="module">kaputt(</script>')
    status, msg = mc.validate_path("spiel.html")
    assert status == "bad" and "Unexpected token" in msg


def test_html_ohne_script_wird_uebersprungen(tmp_path):
    with open("seite.html", "w") as f:
        f.write("<html><body>Text</body></html>")
    assert mc.validate_path("seite.html")[0] == "skip"


@pytest.mark.skipif(not shutil.which("node"), reason="node nicht installiert")
def test_html_validierung_node_fallback_erkennt_syntaxfehler(tmp_path):
    # Kein node_modules/esbuild vorhanden -- genau der reale Fall (einzelne
    # index.html mit CDN-Three.js, kein npm-Projekt). node --check als
    # Fallback muss den echten Bug fangen: fehlendes Argument.
    with open("spiel.html", "w") as f:
        f.write('''<script type="module">
import * as THREE from 'three';
const g = new THREE.BoxGeometry(50, , 50);
</script>''')
    status, msg = mc.validate_path("spiel.html")
    assert status == "bad"
    assert "SyntaxError" in msg


@pytest.mark.skipif(not shutil.which("node"), reason="node nicht installiert")
def test_html_validierung_node_fallback_erkennt_gueltiges_esm(tmp_path):
    # .mjs-Endung fuer node ist entscheidend: als .js wuerde node auf
    # CommonJS zurueckfallen und faelschlich bei 'import' meckern.
    with open("spiel.html", "w") as f:
        f.write('''<script type="module">
import * as THREE from 'three';
const g = new THREE.BoxGeometry(50, 1, 50);
</script>''')
    assert mc.validate_path("spiel.html")[0] == "ok"


def test_resolve_project_file_suffix():
    os.makedirs("frontend/src")
    with open("frontend/src/App.jsx", "w") as f:
        f.write("x")
    assert mc._resolve_project_file("src/App.jsx") == os.path.normpath("frontend/src/App.jsx")
    assert mc._resolve_project_file("frontend/src/App.jsx") == "frontend/src/App.jsx"
    assert mc._resolve_project_file("gibtsnicht/App.jsx") is None


def test_resolve_project_file_mehrdeutig_gibt_none():
    os.makedirs("a/src"); os.makedirs("b/src")
    for d in ("a", "b"):
        with open(f"{d}/src/App.jsx", "w") as f:
            f.write("x")
    assert mc._resolve_project_file("src/App.jsx") is None


# ------------------------ Prozess-/Port-Bewusstsein -------------------------

@pytest.mark.parametrize("out", [
    "OSError: [Errno 48] Address already in use",
    "Error: listen EADDRINUSE: address already in use :::5010",
    "OSError: [WinError 10048] Normalerweise darf jede Socketadresse ...",
    "[Errno 98] Address already in use",
])
def test_addr_in_use_erkannt(out):
    hint = mc._addr_in_use_hint(out)
    assert "Port" in hint and "NICHT den Port" in hint


def test_addr_in_use_nennt_laufende_bg_prozesse():
    import subprocess as sp
    p = sp.Popen("sleep 3", shell=True)
    mc.BG_PROCS.append(p)
    try:
        hint = mc._addr_in_use_hint("EADDRINUSE")
        assert f"pid={p.pid}" in hint
        assert "sleep 3" in hint
    finally:
        p.kill()
        mc.BG_PROCS.remove(p)


def test_harmlose_ausgabe_ohne_hint():
    assert mc._addr_in_use_hint("Server laeuft auf Port 5010") == ""


def test_kill_hint_plattform():
    hint = mc._kill_hint(1234)
    if sys.platform == "win32":
        assert "taskkill" in hint
    else:
        assert hint == "kill 1234"


# --------------------------- Runaway-Erkennung ------------------------------

def test_runaway_zeichen_dauerlauf():
    assert mc._looks_runaway("ok bis hier\n" + "Y" * 200) is True


def test_runaway_wort_wiederholung():
    assert mc._looks_runaway("prosa davor\n" + "GO " * 30) is True


def test_runaway_lange_zeile_ohne_umbruch():
    assert mc._looks_runaway("normal\n" + "af9c" * 700) is True  # 2800 Z., kein \n


def test_runaway_lange_zeile_im_offenen_fence_erlaubt():
    text = "```action\n" + '{"content":"' + "x y " * 700  # offener Fence
    assert mc._looks_runaway(text) is False


def test_runaway_gesunde_antworten():
    assert mc._looks_runaway("Ich lege die Datei an.\n```action\n{}\n```\n") is False
    code = "\n".join("  const x%d = compute(%d);" % (i, i) for i in range(200))
    assert mc._looks_runaway(code) is False


# ------------------------------- read_file ----------------------------------

def test_read_file_klein_komplett():
    with open("a.py", "w") as f:
        f.write("zeile1\nzeile2\nzeile3\n")
    ok, msg = mc.do_read_file({"path": "a.py"})
    assert ok and "zeile2" in msg and "4 Zeilen" in msg


def test_read_file_zeilenbereich():
    with open("a.py", "w") as f:
        f.write("\n".join(f"zeile{i}" for i in range(1, 101)))
    ok, msg = mc.do_read_file({"path": "a.py", "from": 40, "to": 42})
    assert ok and "Zeilen 40-42" in msg
    assert "zeile40" in msg and "zeile42" in msg and "zeile43" not in msg


def test_read_file_gross_mit_nachlade_hinweis():
    with open("gross.py", "w") as f:
        f.write("kopf\n" + ("x" * 78 + "\n") * 400 + "ende\n")  # > 24000 Zeichen
    ok, msg = mc.do_read_file({"path": "gross.py"})
    assert ok
    assert "from" in msg and "Mitte ausgelassen" in msg
    assert msg.strip().endswith("ende") or "ende" in msg[-50:]


# --------------------------- Shell-Lese-Schleifen ---------------------------

def test_shell_read_registriert_und_warnt_ab_drittem_mal():
    mc.SHELL_READS.clear()
    with open("App.jsx", "w") as f:
        f.write("x")
    assert mc._shell_read_hint("sed -n '1,50p' App.jsx") == ""
    assert os.path.normpath("App.jsx") in mc.READ_FILES  # Gate-Konsistenz
    assert mc._shell_read_hint("sed -n '50,100p' App.jsx") == ""
    hint = mc._shell_read_hint("cat App.jsx")
    assert "3. Mal" in hint and "read_file" in hint


def test_shell_read_ignoriert_nicht_lese_kommandos():
    mc.SHELL_READS.clear()
    with open("a.py", "w") as f:
        f.write("x")
    assert mc._shell_read_hint("python3 a.py") == ""
    assert mc._shell_read_hint("npm run build") == ""


# ------------------------------ Konfiguration -------------------------------

def test_extra_headers_konfig_und_env(monkeypatch):
    monkeypatch.setattr(mc, "CONFIG", {"headers": {"X-A": "1", "X-B": "conf"}})
    monkeypatch.setattr(mc, "EXTRA_HEADERS_RAW", "X-B: env; X-C: 3")
    out = mc.extra_headers()
    assert out == {"X-A": "1", "X-B": "env", "X-C": "3"}  # Env schlaegt Konfig


def test_system_prompt_fence_und_json_varianten():
    sp_fence = mc.system_prompt(True)
    sp_json = mc.system_prompt(False)
    assert "```old" in sp_fence and "```content" in sp_fence
    assert '"old":"<exact snippet>"' in sp_json
    assert "@@" not in sp_fence and "@@" not in sp_json  # alle Platzhalter ersetzt


# ----------------------- Weiterentwicklungs-Paket ---------------------------

def test_write_files_string_eintraege_stuerzen_nicht_ab():
    # Real beobachtet (Harness-Crash): files als ["app.py", ...] statt Objekte.
    ok, msg = mc.do_write_files({"files": ["app.py", "b.py"]})
    assert ok is False
    assert "fehlt der Inhalt" in msg  # sauberer Fehler statt AttributeError


def test_write_files_ohne_content_wird_gemeldet():
    ok, msg = mc.do_write_files({"files": [{"path": "a.py"}]})
    assert ok is False and "a.py" in msg


def test_edit_kaskade_zeilengetrimmt_mit_einrueckung():
    with open("a.py", "w") as f:
        f.write("def f():\n        x = 1\n        return x\n")
    # Modell zitiert den Block ohne Einrueckung — Kaskade findet ihn trotzdem
    # und passt 'new' an die echte Einrueckung an.
    ok, msg = mc.do_edit_file({"path": "a.py", "old": "x = 1\nreturn x",
                               "new": "x = 2\nreturn x"})
    assert ok is True
    inhalt = open("a.py").read()
    assert "        x = 2" in inhalt          # Einrueckung uebernommen
    assert "Hinweis" in msg                    # Modell erfaehrt von der Toleranz


def test_edit_kaskade_doppelt_escaped():
    with open("a.txt", "w") as f:
        f.write("zeile1\nzeile2\n")
    ok, _ = mc.do_edit_file({"path": "a.txt", "old": "zeile1\\nzeile2",
                             "new": "zeile1\\nNEU"})
    assert ok is True
    assert "NEU" in open("a.txt").read()


def test_edit_kaskade_blockanker_bei_halluzinierter_mitte():
    with open("a.py", "w") as f:
        f.write("def g():\n    a = 1\n    b = 2\n    c = 3\n    return c\n")
    # Mitte stimmt nicht exakt (halluzinierte Variablennamen), Anker passen.
    ok, _ = mc.do_edit_file({
        "path": "a.py",
        "old": "def g():\n    a = 1\n    b = 20\n    c = 3\n    return c",
        "new": "def g():\n    return 42"})
    assert ok is True
    assert "return 42" in open("a.py").read()


def test_edit_kaskade_mehrdeutig_wird_abgelehnt():
    with open("a.py", "w") as f:
        f.write("  x = 1\n\n  x = 1\n")
    ok, msg = mc.do_edit_file({"path": "a.py", "old": "x = 1", "new": "x = 2"})
    assert ok is False and "eindeutig" in msg


def test_neubau_bremse_greift_bei_bestand_ohne_blick():
    with open("bestand.py", "w") as f:
        f.write("print('alt')\n")
    ok, msg = mc.do_write_file({"path": "neu.py", "content": "print('neu')\n"})
    assert ok is False and "NEUBAU-BREMSE" in msg
    assert not os.path.exists("neu.py")


def test_neubau_bremse_offen_nach_suche():
    with open("bestand.py", "w") as f:
        f.write("print('alt')\n")
    mc.do_grep({"pattern": "gibtesnicht"})  # auch LEERES Ergebnis schaltet frei
    ok, _ = mc.do_write_file({"path": "neu.py", "content": "print('neu')\n"})
    assert ok is True


def test_neubau_bremse_ignoriert_leeres_projekt():
    ok, _ = mc.do_write_file({"path": "neu.py", "content": "print('neu')\n"})
    assert ok is True  # Greenfield: keine Bremse


def test_repo_brief_erkennt_python_und_node():
    with open("requirements.txt", "w") as f:
        f.write("flask\n")
    with open("package.json", "w") as f:
        f.write('{"dependencies": {"react": "^19"}, "scripts": {"build": "x"}}')
    brief = "\n".join(mc.repo_brief())
    assert "Python" in brief and "react" in brief
    assert "pip install -r requirements.txt" in brief
    assert "npm run build" in brief


def test_code_outline_python_mit_routen():
    with open("app.py", "w") as f:
        f.write("import x\n\n@app.route('/api/p')\ndef liste():\n    pass\n\n"
                "class Dienst:\n    def start(self):\n        pass\n")
    out = "\n".join(mc.code_outline())
    assert "def liste()" in out and "route /api/p" in out
    assert "class Dienst" in out and "start" in out


def test_code_outline_ignoriert_venv_unabhaengig_vom_namen():
    # Regression: eine Virtualenv mit untypischem Namen (z.B. 'whisper-env'
    # statt 'venv'/'.venv') wurde bisher wie ein normaler Projektordner
    # durchsucht -- Hunderte fremder site-packages-Funktionen landeten im
    # Code-Outline und blaehten den System-Prompt sinnlos auf.
    with open("app.py", "w") as f:
        f.write("def echt_projekt():\n    pass\n")
    os.makedirs("whisper-env/lib/site-packages", exist_ok=True)
    with open("whisper-env/pyvenv.cfg", "w") as f:
        f.write("home = /usr/bin\n")
    with open("whisper-env/lib/site-packages/fremd.py", "w") as f:
        f.write("def fremde_funktion():\n    pass\n")
    out = "\n".join(mc.code_outline())
    assert "echt_projekt" in out
    assert "fremde_funktion" not in out and "whisper-env" not in out


def test_is_venv_dir():
    os.makedirs("normaler_ordner", exist_ok=True)
    os.makedirs("irgendeine-env", exist_ok=True)
    with open("irgendeine-env/pyvenv.cfg", "w") as f:
        f.write("x\n")
    assert mc._is_venv_dir("irgendeine-env") is True
    assert mc._is_venv_dir("normaler_ordner") is False
    assert mc._is_venv_dir("nicht-vorhanden-xyz") is False


def test_terse_hint_nur_bei_kurzem_auftrag_mit_bestand():
    with open("app.py", "w") as f:
        f.write("x = 1\n")
    assert "kept brief" in mc.terse_task_hint("fix die liste")
    assert mc.terse_task_hint("Baue eine ausfuehrliche Anwendung mit "
                              "vielen Details und Erklaerungen dazu") == ""
    os.remove("app.py")
    mc.HAS_CODE = None
    assert mc.terse_task_hint("fix die liste") == ""  # Greenfield: kein Stupser


def test_qa_hint_erkennt_fragen_und_ignoriert_auftraege():
    assert "QUESTION" in mc.qa_task_hint("warum ist die liste leer?")
    assert "QUESTION" in mc.qa_task_hint("wo wird der port gesetzt")
    assert mc.qa_task_hint("baue eine app") == ""
    assert mc.qa_task_hint("ergaenze das feld beruf?") == ""  # Imperativ schlaegt '?'


def test_ctx_overflow_parser():
    assert mc._parse_ctx_overflow(
        '{"error": "maximum context length is 32768 tokens, however you '
        'requested 45123 tokens"}') == 32768
    assert mc._parse_ctx_overflow("Context size has been exceeded.") == 0
    assert mc._parse_ctx_overflow("Invalid API key") is None


def test_confirm_freitext_wird_ablehnungsgrund(monkeypatch):
    monkeypatch.setattr(mc, "AUTO_YES", False)
    monkeypatch.setattr("builtins.input", lambda *a: "nimm Port 5030")
    assert mc.confirm("Aktion?") is False
    assert "5030" in mc.user_reject_msg()          # Freitext = Anweisung
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    assert mc.confirm("Aktion?") is False
    assert mc.user_reject_msg() == "Abgelehnt durch den Benutzer."


def test_read_file_meintest_du_bei_tippfehler():
    with open("personen.py", "w") as f:
        f.write("x = 1\n")
    ok, msg = mc.do_read_file({"path": "personnen.py"})
    assert not ok
    assert "Meintest du" in msg and "personen.py" in msg


def test_analyse_prompt_ohne_schreibaktionen():
    sp = mc.system_prompt(True, analyse=True)
    assert "plan" in sp and "read_file" in sp and "grep" in sp
    assert "write_file" not in sp and "edit_file" not in sp
    assert "run " not in sp  # keine run-Aktion im Analyse-Protokoll


# ------------------------- Kontext-Paket (Ideen) ----------------------------

def test_read_files_batch_und_limit():
    with open("a.py", "w") as f:
        f.write("A = 1\n")
    with open("b.py", "w") as f:
        f.write("B = 2\n")
    ok, msg = mc.do_read_files({"paths": ["a.py", "b.py"]})
    assert ok and "A = 1" in msg and "B = 2" in msg
    ok, msg = mc.do_read_files({"paths": [f"f{i}.py" for i in range(9)]})
    assert not ok and "maximal" in msg
    ok, _ = mc.do_read_files({"paths": []})
    assert not ok


def test_plan_datei_und_wiederaufnahme_hinweis():
    assert mc._write_plan_file(["app.py: Feld ergaenzen", "index.html: Spalte"])
    inhalt = open(mc.MC_PLAN, encoding="utf-8").read()
    assert "- [ ] 1. app.py: Feld ergaenzen" in inhalt
    hints = mc.task_hints("mach weiter")
    assert "OPEN change plan" in hints
    with open(mc.MC_PLAN, "w", encoding="utf-8") as f:
        f.write("# Plan\n\n- [x] 1. fertig\n")  # alles abgehakt
    assert "OPEN" not in mc.task_hints("mach weiter")


def test_plan_datei_zaehlt_nicht_als_bestand():
    mc._write_plan_file(["x"])
    mc.HAS_CODE = None
    assert mc._project_has_code() is False


def test_git_diff_summary_zeigt_aenderungen():
    import subprocess
    subprocess.run(["git", "init", "-q"], check=True)
    with open("datei.py", "w") as f:
        f.write("x = 1\n")
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=T",
                    "commit", "-qm", "start"], check=True)
    with open("datei.py", "w") as f:
        f.write("x = 2\n")
    out = mc._git_diff_summary()
    assert "datei.py" in out and "Diff:" in out


def test_transcript_roundtrip_ohne_system(monkeypatch):
    monkeypatch.setattr(mc, "RESUME", True)
    msgs = [{"role": "system", "content": "S"},
            {"role": "user", "content": "aufgabe"},
            {"role": "assistant", "content": "antwort"}]
    mc._save_transcript(msgs)
    assert mc._load_transcript() == msgs[1:]  # System-Message bleibt draussen


def test_explore_validierung_und_dispatch():
    ok, msg = mc.do_explore({})
    assert not ok and "task" in msg
    assert mc.DISPATCH["explore"] is mc.do_explore
    assert mc.DISPATCH["read_files"] is mc.do_read_files


def test_system_prompt_lehrt_read_files_und_explore():
    sp = mc.system_prompt(True)
    assert "read_files" in sp and "explore" in sp
    spa = mc.system_prompt(True, analyse=True)
    assert "read_files" in spa and "explore" in spa


# --------------------------- Laufzeit-Settings ------------------------------

def test_apply_setting_bool_int_und_unbekannt(monkeypatch):
    ok, msg, neu = mc._apply_setting("check", "true")
    assert ok and "check = True" in msg and neu is True   # steckt im Prompt
    ok, msg, neu = mc._apply_setting("max_steps", "60")
    assert ok and mc.MAX_STEPS == 60 and neu is False
    ok, msg, _ = mc._apply_setting("max_steps", "abc")
    assert not ok and "keine Zahl" in msg
    ok, msg, _ = mc._apply_setting("quatsch", "1")
    assert not ok and "unbekannte Einstellung" in msg
    mc._apply_setting("check", "false")
    mc._apply_setting("max_steps", "40")


def test_apply_setting_think(monkeypatch):
    ok, msg, neu = mc._apply_setting("think", "false")
    assert ok and mc.THINK is False and "think = False" in msg and neu is False
    mc._apply_setting("think", "true")
    assert mc.THINK is True


def test_apply_setting_api_key_wird_nie_im_klartext_gemeldet(monkeypatch):
    ok, msg, neu = mc._apply_setting("api_key", "geheim-123")
    assert ok and mc.API_KEY == "geheim-123" and neu is False
    assert "geheim-123" not in msg
    assert "verborgen" in msg
    ok2, msg2, _ = mc._apply_setting("api_key", "")
    assert ok2 and mc.API_KEY == "" and "geleert" in msg2
    mc.API_KEY = ""


def test_settings_report_zeigt_api_key_nie_im_klartext(monkeypatch):
    monkeypatch.setattr(mc, "API_KEY", "geheim-123")
    bericht = mc._settings_report("m")
    assert "geheim-123" not in bericht
    assert "gesetzt (verborgen)" in bericht


def test_current_settings_liefert_echten_api_key_fuer_profile(monkeypatch):
    # _current_settings() (anders als _settings_report()) muss den ECHTEN
    # Wert liefern -- /profil speichern|laden braucht ihn, um den Key
    # tatsaechlich wiederherstellen zu koennen.
    monkeypatch.setattr(mc, "API_KEY", "geheim-123")
    assert mc._current_settings("m")["api_key"] == "geheim-123"


def test_apply_setting_base_url_leert_caches(monkeypatch):
    mc._LOADED_CTX_TOKENS["x"] = 123
    ok, msg, _ = mc._apply_setting("base_url", "http://neu:1234/v1/")
    assert ok and mc.BASE_URL == "http://neu:1234/v1"
    assert mc._LOADED_CTX_TOKENS == {}
    mc._apply_setting("base_url", "http://localhost:1234/v1")


def test_settings_report_zeigt_alles():
    rep = mc._settings_report("test-modell")
    for k in ("model", "base_url", "check", "analyse", "max_steps", "yes"):
        assert k in rep
    assert "test-modell" in rep


# --------------------------- Verlust-Waechter -------------------------------

def test_verlust_waechter_meldet_verschwundene_elemente(monkeypatch):
    monkeypatch.setattr(mc, "CURRENT_TASK", "ergaenze einen download-button")
    alt = '<iframe id="previewFrame"></iframe>\n<button id="restartViteBtn">X</button>'
    neu = '<button id="downloadBtn">Download</button>'
    warnung = mc._loss_warning("index.html", alt, neu)
    assert "VERLUST-WAECHTER" in warnung
    assert "previewFrame" in warnung and "restartViteBtn" in warnung


def test_verlust_waechter_meldet_denselben_namen_nur_einmal(monkeypatch):
    # Regression: ein Modell, das dieselbe Datei mehrfach komplett neu
    # schreibt, verlor/gewann denselben Namen ueber mehrere Versuche hinweg
    # unterschiedlich -- der Waechter feuerte dann bei JEDEM Versuch erneut
    # fuer denselben, laengst kommentierten Verlust und verleitete ein
    # schwaches Modell zu einem kuenstlichen Wiedereinbau (z.B. totes
    # console.log), nur um die Meldung loszuwerden.
    monkeypatch.setattr(mc, "CURRENT_TASK", "ergaenze details")
    alt = 'const CONFIG = 1;\nfunction foo() {}'
    neu = 'function foo() {}'
    erste = mc._loss_warning("game.html", alt, neu)
    assert "VERLUST-WAECHTER" in erste and "CONFIG" in erste
    # Zweiter Schreibvorgang verliert CONFIG erneut (z.B. nach einem
    # zwischenzeitlichen Wiedereinbau) -- soll NICHT nochmal gemeldet werden.
    zweite = mc._loss_warning("game.html", alt, neu)
    assert zweite == ""


def test_verlust_waechter_meldet_neuen_verlust_trotz_bereits_gewarnter_namen(monkeypatch):
    monkeypatch.setattr(mc, "CURRENT_TASK", "ergaenze details")
    mc._loss_warning("game.html", 'const CONFIG = 1;\nfunction foo() {}', 'function foo() {}')
    # CONFIG schon gemeldet -- ein NEUER Verlust (bar) muss trotzdem durchkommen.
    dritte = mc._loss_warning("game.html", 'function foo() {}\nfunction bar() {}',
                              'function foo() {}')
    assert "VERLUST-WAECHTER" in dritte
    assert "bar" in dritte and "CONFIG" not in dritte


def test_verlust_waechter_neue_formulierung_erklaert_keine_code_fixes():
    warnung = mc._loss_warning("a.html", 'function foo(){}', '')
    assert "kein Code-Fix noetig" in warnung
    assert "console.log" in warnung


def test_verlust_waechter_schweigt_bei_loesch_auftrag(monkeypatch):
    monkeypatch.setattr(mc, "CURRENT_TASK", "entferne das iframe bitte")
    alt = '<iframe id="previewFrame"></iframe>'
    assert mc._loss_warning("index.html", alt, "") == ""


def test_verlust_waechter_schweigt_ohne_verlust(monkeypatch):
    monkeypatch.setattr(mc, "CURRENT_TASK", "ergaenze etwas")
    alt = 'def rechne():\n    pass\n'
    neu = alt + '\ndef neu():\n    pass\n'
    assert mc._loss_warning("a.py", alt, neu) == ""


def test_verlust_waechter_greift_bei_edit_file(monkeypatch):
    monkeypatch.setattr(mc, "CURRENT_TASK", "mach eine werkzeugleiste dazu")
    with open("seite.html", "w") as f:
        f.write('<div>\n<iframe id="previewFrame" src="x"></iframe>\n</div>\n')
    mc.do_read_file({"path": "seite.html"})
    ok, msg = mc.do_edit_file({
        "path": "seite.html",
        "old": '<iframe id="previewFrame" src="x"></iframe>',
        "new": '<div id="toolbar">Leiste</div>'})
    assert ok
    assert "VERLUST-WAECHTER" in msg and "previewFrame" in msg


# --------------------------- Duplikat-Waechter ------------------------------

def test_duplikat_waechter_meldet_verdoppelte_zeile():
    zeile = '<a href="#anmeldung" className="bg-purple">Jetzt anmelden</a>'
    alt = "<nav>\n  " + zeile + "\n</nav>\n"
    neu = "<nav>\n  " + zeile + "\n  " + zeile + "\n</nav>\n"
    w = mc._duplicate_warning("App.jsx", alt, neu)
    assert "DUPLIKAT-WAECHTER" in w and "Jetzt anmelden" in w


def test_duplikat_waechter_schweigt_bei_neuem_und_bestand():
    zeile = '<a href="#anmeldung" className="bg-purple">Jetzt anmelden</a>'
    # neue einmalige Zeile: kein Duplikat
    assert mc._duplicate_warning("a", "<nav>\n</nav>", "<nav>\n" + zeile + "\n</nav>") == ""
    # Duplikat existierte schon vorher: nicht nachtreten
    doppelt = zeile + "\n" + zeile
    assert mc._duplicate_warning("a", doppelt, doppelt + "\nx") == ""
    # kurze Wiederholungen (</div>) sind normal
    assert mc._duplicate_warning("a", "</div>", "</div>\n</div>") == ""


def test_duplikat_waechter_greift_bei_edit_file(monkeypatch):
    monkeypatch.setattr(mc, "CURRENT_TASK", "bau die navbar um")
    zeile = '<a href="#kauf" className="cta-button-gross">Jetzt kaufen und sparen</a>'
    with open("s.html", "w") as f:
        f.write("<nav>\n" + zeile + "\n<span>Menu-Ende-Markierung hier</span>\n</nav>\n")
    mc.do_read_file({"path": "s.html"})
    ok, msg = mc.do_edit_file({"path": "s.html",
                               "old": "<span>Menu-Ende-Markierung hier</span>",
                               "new": zeile + "\n<span>Menu-Ende-Markierung hier</span>"})
    assert ok
    assert "DUPLIKAT-WAECHTER" in msg


def test_generator_toleriert_werkzeug_zubehoer():
    # Neues Projekt kommt seit Auto-Git mit .git/.gitignore zur Welt —
    # fuer Generatoren ist so ein Ordner trotzdem praktisch leer.
    os.makedirs("frontend/.git")
    with open("frontend/.gitignore", "w") as f:
        f.write("node_modules/\n")
    assert mc._generator_conflict("npm create vite@latest frontend -- --template react") == ""
    with open("frontend/echte-datei.js", "w") as f:
        f.write("x")
    assert "ABGELEHNT" in mc._generator_conflict("npm create vite@latest frontend")


def test_generator_lenkt_punkt_ziel_um():
    with open(".gitignore", "w") as f:
        f.write("node_modules/\n")
    msg = mc._generator_conflict("npm create vite@latest .")
    assert "UNTERORDNER" in msg and "frontend" in msg


# --------------------------- Referenz-Waechter ------------------------------

def test_referenz_waechter_meldet_klassen_ohne_regel():
    os.makedirs("src")
    with open("src/App.jsx", "w") as f:
        f.write('const App = () => <div className="kachel titel">x</div>;\n')
    with open("src/index.css", "w") as f:
        f.write(".titel { color: red; }\n")
    w = mc._reference_warning("src/App.jsx")
    assert "REFERENZ-WAECHTER" in w and "kachel" in w and "titel" not in w.split(":")[2]


def test_referenz_waechter_meldet_ungemountete_komponente():
    os.makedirs("src")
    with open("src/App.jsx", "w") as f:
        f.write("const Sichtbar = () => <div/>;\nconst Verwaist = () => <p/>;\n"
                "const App = () => <main><Sichtbar/></main>;\n"
                "export default App;\n// <App/> wird in main gemountet\n")
    w = mc._reference_warning("src/App.jsx")
    assert "Verwaist" in w and "Sichtbar" not in w


def test_referenz_waechter_schweigt_bei_tailwind_und_sauber():
    os.makedirs("src")
    with open("index.html", "w") as f:
        f.write('<script src="https://cdn.tailwindcss.com"></script>\n')
    with open("src/App.jsx", "w") as f:
        f.write('const App = () => <div className="bg-red-500">x</div>; // <App/>\n')
    assert mc._reference_warning("src/App.jsx") == ""  # Tailwind: kein Abgleich
    os.remove("index.html")
    with open("src/index.css", "w") as f:
        f.write(".alles-da { color: red; }\n")
    with open("src/App.jsx", "w") as f:
        f.write('const App = () => <div className="alles-da">x</div>; // <App/>\n')
    assert mc._reference_warning("src/App.jsx") == ""


def test_referenz_waechter_ignoriert_backend_dateien():
    assert mc._reference_warning("server.py") == ""


# --------------------------- Toleranz-Paket ---------------------------------

def test_koerzierung_typen():
    a = {"action": "read_file", "path": "a.py", "from": "10", "to": "20"}
    assert mc.repair_and_coerce_action(a) == ""
    assert a["from"] == 10 and a["to"] == 20
    a = {"action": "edit_file", "path": "x", "old": "a", "new": "b",
         "replace_all": "true"}
    assert mc.repair_and_coerce_action(a) == "" and a["replace_all"] is True
    a = {"action": "run", "command": "ls", "timeout": "abc"}
    fehler = mc.repair_and_coerce_action(a)
    assert "timeout" in fehler and "abc" in fehler  # eigene Argumente gespiegelt


def test_form_reparatur():
    # Aliase
    a = {"action": "bash", "command": "ls"}
    mc.repair_and_coerce_action(a)
    assert a["action"] == "run"
    # files als Dict
    a = {"action": "write_files", "files": {"a.py": "x", "b.py": "y"}}
    mc.repair_and_coerce_action(a)
    assert a["files"] == [{"path": "a.py", "content": "x"},
                          {"path": "b.py", "content": "y"}]
    # doppelt JSON-kodierte Liste
    a = {"action": "read_files", "paths": '["a.py", "b.py"]'}
    mc.repair_and_coerce_action(a)
    assert a["paths"] == ["a.py", "b.py"]
    # Einzelwert -> Liste
    a = {"action": "read_files", "paths": "nur-eine.py"}
    mc.repair_and_coerce_action(a)
    assert a["paths"] == ["nur-eine.py"]


def test_json_mit_rohen_steuerzeichen_parst():
    action, _ = mc.extract_action(
        '```action\n{"action":"write_file","path":"a.txt","content":"zeile1\nzeile2"}\n```')
    assert action["action"] == "write_file"
    assert "zeile1" in action["content"]


def test_edit_kaskade_unicode_toleranz():
    with open("a.js", "w") as f:
        f.write('const s = "Hallo – Welt";\n')  # echter Gedankenstrich
    mc.do_read_file({"path": "a.js"})
    ok, msg = mc.do_edit_file({"path": "a.js",
                               "old": 'const s = "Hallo - Welt";',
                               "new": 'const s = "Servus";'})
    assert ok, msg
    assert "Servus" in open("a.js").read()


def test_ledger_block():
    mc.READ_FILES.add("gelesen.py")
    mc.TOUCHED.append("geschrieben.py")
    block = mc._ledger_block()
    assert "KONTOBUCH" in block
    assert "geschrieben.py" in block and "gelesen.py" in block
    mc.TOUCHED.clear(); mc.READ_FILES.clear()
    assert mc._ledger_block() == ""


def test_send_size_info(monkeypatch):
    monkeypatch.setattr(mc, "_LOADED_CTX_TOKENS", {})
    messages = [{"role": "system", "content": "x" * 1800},
                {"role": "user", "content": "y" * 200}]
    info = mc._send_size_info(messages, "m")
    assert "2000 Zeichen" in info
    assert "1111 Token" in info  # 2000 / 1.8 abgerundet
    assert "bekanntes Fenster" not in info

    mc._LOADED_CTX_TOKENS["m"] = 10326
    info2 = mc._send_size_info(messages, "m")
    assert "bekanntes Fenster: 10326 Token" in info2


def test_trunc_marker_konstante():
    assert mc.TRUNC_MARKER.startswith("\n[mc:")


def test_system_message_fuer_modus(monkeypatch):
    monkeypatch.setattr(mc, "MODE", "dev")
    monkeypatch.setattr(mc, "SYSTEM_CONTEXT", "STECKBRIEF-MARKER")
    dev_content = mc._system_message_for_mode()
    assert "STECKBRIEF-MARKER" in dev_content
    assert dev_content != mc.CHAT_SYSTEM_PROMPT

    monkeypatch.setattr(mc, "MODE", "chat")
    chat_content = mc._system_message_for_mode()
    assert chat_content == mc.CHAT_SYSTEM_PROMPT
    assert "STECKBRIEF-MARKER" not in chat_content
    assert "mode dev" in chat_content  # Hinweis, wie man zurueckschaltet


# --------------------- Reasoning-Erkennung (_chat_once) ---------------------

class _FakeStreamResp:
    def __init__(self, lines, status=200):
        self._lines = lines
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self._lines)


class _FakeStreamOpener:
    """Reicht vorgefertigte SSE-Zeilen durch build_opener().open(...) und
    protokolliert die gesendeten Request-Bodies (fuer Payload-Assertions)."""

    def __init__(self, lines, capture=None):
        self.lines = lines
        self.capture = capture if capture is not None else []

    def open(self, req, timeout=300):
        self.capture.append(json.loads(req.data.decode()))
        return _FakeStreamResp(self.lines)


_SSE_NUR_CONTENT = [
    b'data: {"choices":[{"delta":{"content":"OK"}}]}',
    b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
    b"data: [DONE]",
]


def test_chat_once_erkennt_reasoning_content(monkeypatch):
    lines = [
        b'data: {"choices":[{"delta":{"reasoning_content":"Denk"}}]}',
        b'data: {"choices":[{"delta":{"reasoning_content":"en..."}}]}',
        b'data: {"choices":[{"delta":{"content":"Hallo"}}]}',
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        b"data: [DONE]",
    ]
    monkeypatch.setattr(mc, "build_opener", lambda: _FakeStreamOpener(lines))
    monkeypatch.setattr(mc, "THINK", True)
    text, fr = mc._chat_once([{"role": "user", "content": "hi"}], "m")
    assert text == "Hallo"
    assert fr == "stop"
    assert mc.LAST_REASONING_CHARS == len("Denk") + len("en...")


def test_chat_once_ohne_reasoning_zaehlt_null(monkeypatch):
    monkeypatch.setattr(mc, "build_opener", lambda: _FakeStreamOpener(_SSE_NUR_CONTENT))
    monkeypatch.setattr(mc, "THINK", True)
    mc.LAST_REASONING_CHARS = 999
    text, _ = mc._chat_once([{"role": "user", "content": "hi"}], "m")
    assert text == "OK"
    assert mc.LAST_REASONING_CHARS == 0  # bei JEDEM Aufruf zurueckgesetzt


def test_chat_once_think_false_setzt_abschalt_felder(monkeypatch):
    capture = []
    monkeypatch.setattr(mc, "build_opener", lambda: _FakeStreamOpener(_SSE_NUR_CONTENT, capture))
    monkeypatch.setattr(mc, "THINK", False)
    mc._chat_once([{"role": "user", "content": "hi"}], "m")
    assert capture[0]["reasoning_effort"] == "none"
    assert capture[0]["enable_thinking"] is False
    assert capture[0]["chat_template_kwargs"] == {"enable_thinking": False}


def test_chat_once_think_true_ohne_abschalt_felder(monkeypatch):
    capture = []
    monkeypatch.setattr(mc, "build_opener", lambda: _FakeStreamOpener(_SSE_NUR_CONTENT, capture))
    monkeypatch.setattr(mc, "THINK", True)
    mc._chat_once([{"role": "user", "content": "hi"}], "m")
    assert "reasoning_effort" not in capture[0]
    assert "enable_thinking" not in capture[0]
    assert "chat_template_kwargs" not in capture[0]


# --------------------- /model-reset: Endpunkt-Erkennung + Reload -----------

class _FakeOpener:
    """Ordnet Anfragen anhand des URL-Suffixes einer Antwort (oder Exception)
    zu und protokolliert (Methode, URL, JSON-Body) fuer Assertions."""

    def __init__(self, routes, calls=None):
        self.routes = routes
        self.calls = calls if calls is not None else []

    def open(self, req, timeout=5):
        body = json.loads(req.data.decode()) if req.data else None
        self.calls.append((req.get_method(), req.full_url, body))
        for suffix, payload in self.routes.items():
            if req.full_url.endswith(suffix):
                if isinstance(payload, Exception):
                    raise payload
                return _FakeResp(payload)
        raise OSError("unerwartete URL im Test: " + req.full_url)


def test_endpoint_root():
    assert mc._endpoint_root("http://host:1234/v1") == "http://host:1234"
    assert mc._endpoint_root("http://host:1234/v1/") == "http://host:1234"
    assert mc._endpoint_root("http://host:11434") == "http://host:11434"


def test_detect_local_engine_lmstudio(monkeypatch):
    monkeypatch.setattr(mc, "BASE_URL", "http://host:1234/v1")
    opener = _FakeOpener({"/api/v0/models": {"data": [{"id": "m", "state": "loaded"}]}})
    monkeypatch.setattr(mc, "build_opener", lambda: opener)
    assert mc._detect_local_engine() == "lmstudio"


def test_detect_local_engine_ollama(monkeypatch):
    monkeypatch.setattr(mc, "BASE_URL", "http://host:11434/v1")
    opener = _FakeOpener({"/api/tags": {"models": [{"name": "m"}]}})
    monkeypatch.setattr(mc, "build_opener", lambda: opener)
    assert mc._detect_local_engine() == "ollama"


def test_detect_local_engine_unbekannt(monkeypatch):
    monkeypatch.setattr(mc, "BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(mc, "build_opener", lambda: _FakeOpener({}))
    assert mc._detect_local_engine() is None


def test_detect_local_engine_vmlx_vor_ollama_erkannt(monkeypatch):
    # vMLX bildet AUCH /api/tags nach (Ollama-Kompatibilitaet) -- die
    # 'owned_by': 'vmlx-engine'-Erkennung ueber /v1/models MUSS zuerst
    # greifen, sonst wuerde es faelschlich als 'ollama' erkannt.
    monkeypatch.setattr(mc, "BASE_URL", "http://host:8000/v1")
    opener = _FakeOpener({
        "/v1/models": {"data": [{"id": "m", "owned_by": "vmlx-engine"}]},
        "/api/tags": {"models": [{"name": "m"}]},
    })
    monkeypatch.setattr(mc, "build_opener", lambda: opener)
    assert mc._detect_local_engine() == "vmlx"


def test_detect_local_engine_omlx(monkeypatch):
    monkeypatch.setattr(mc, "BASE_URL", "http://host:8000/v1")
    opener = _FakeOpener({
        "/v1/models": {"data": [{"id": "m", "owned_by": "omlx"}]},
    })
    monkeypatch.setattr(mc, "build_opener", lambda: opener)
    assert mc._detect_local_engine() == "omlx"


def test_loaded_ctx_omlx_max_context_window_bewusst_ignoriert(monkeypatch):
    # Real erprobt: oMLX' max_context_window (/v1/models/status) ist das
    # THEORETISCHE Konfigurationsmaximum, nicht das tatsaechlich nutzbare
    # Fenster -- gemeldet 262144, ein echter Kontext-Ueberlauf traf aber
    # schon bei ~22000 gesendeten Token ein (siehe Blog Kapitel 45). Wuerde
    # _loaded_ctx_tokens() das blind uebernehmen, bliebe das Lazy-Pruning
    # die ganze Zeit inaktiv. Deshalb bewusst: ctx bleibt 0 (Fenster
    # 'unbekannt'), maybe_prune() kuerzt dann sicherheitshalber jeden
    # Schritt -- die reaktive Selbstkalibrierung ueber CtxOverflowError
    # bleibt der einzige Weg, das reale Fenster zu erfahren.
    monkeypatch.setattr(mc, "_LOADED_CTX_TOKENS", {})
    monkeypatch.setattr(mc, "BASE_URL", "http://host:8000/v1")
    monkeypatch.setattr(mc, "API_KEY", "1234")

    def _fake_urlopen(req, timeout=5):
        raise OSError("nicht gefunden: " + req.full_url)

    monkeypatch.setattr(mc.urllib.request, "urlopen", _fake_urlopen)
    assert mc._loaded_ctx_tokens("m") == 0
    assert mc._LOADED_CTX_TOKENS["m"] == 0


def test_reset_model_omlx_uebernimmt_geladenen_wert(monkeypatch):
    monkeypatch.setattr(mc, "BASE_URL", "http://host:8000/v1")
    opener = _FakeOpener({
        "/v1/models": {"data": [{"id": "m", "owned_by": "omlx"}]},
        "/v1/models/m/unload": {"status": "ok"},
        "/v1/models/m/load": {"status": "ok"},
        "/v1/models/status": {"models": [{"id": "m", "max_context_window": 262144}]},
    })
    monkeypatch.setattr(mc, "build_opener", lambda: opener)
    ok, msg = mc.reset_model("m", 262144)
    assert ok and "262144" in msg and "oMLX" in msg and "Admin" not in msg


def test_reset_model_omlx_meldet_fehlende_admin_rechte_bei_abweichung(monkeypatch):
    monkeypatch.setattr(mc, "BASE_URL", "http://host:8000/v1")
    opener = _FakeOpener({
        "/v1/models": {"data": [{"id": "m", "owned_by": "omlx"}]},
        "/v1/models/m/unload": {"status": "ok"},
        "/v1/models/m/load": {"status": "ok"},
        "/v1/models/status": {"models": [{"id": "m", "max_context_window": 262144}]},
    })
    monkeypatch.setattr(mc, "build_opener", lambda: opener)
    ok, msg = mc.reset_model("m", 16384)
    assert ok and "262144" in msg and "16384" in msg and "Admin" in msg


def test_loaded_ctx_vmlx_fallback(monkeypatch):
    # LM Studios /api/v0/models fehlt (vMLX bildet das nicht nach) -- Fallback
    # auf vMLX' eigenen /v1/models/{model}/capabilities -> max_prompt_tokens
    # (das TATSAECHLICH nutzbare Fenster, nicht das theoretische Maximum).
    monkeypatch.setattr(mc, "_LOADED_CTX_TOKENS", {})
    monkeypatch.setattr(mc, "BASE_URL", "http://host:8000/v1")

    def _fake_urlopen(req, timeout=5):
        if req.full_url.endswith("/api/v0/models"):
            raise OSError("kein LM Studio")
        assert "capabilities" in req.full_url
        return _FakeResp({"max_prompt_tokens": 10326})

    monkeypatch.setattr(mc.urllib.request, "urlopen", _fake_urlopen)
    assert mc._loaded_ctx_tokens("m") == 10326
    assert mc._LOADED_CTX_TOKENS["m"] == 10326


def test_reset_model_vmlx_meldet_ehrlich_keine_laufzeit_aenderung(monkeypatch):
    monkeypatch.setattr(mc, "BASE_URL", "http://host:8000/v1")
    opener = _FakeOpener({
        "/v1/models": {"data": [{"id": "m", "owned_by": "vmlx-engine"}]},
        "/v1/models/m/capabilities": {"max_prompt_tokens": 10326},
    })
    monkeypatch.setattr(mc, "build_opener", lambda: opener)
    ok, msg = mc.reset_model("m", 32768)
    assert not ok
    assert "10326" in msg and "--max-prompt-tokens" in msg


def test_reset_model_lmstudio(monkeypatch):
    # load_config.context_length in der Load-Antwort ist nur ein Echo der
    # Anfrage -- reset_model() muss NACH dem Laden per /api/v0/models
    # nachfragen, was tatsaechlich geladen wurde (real beobachtet:
    # angefordert 8192/16384/65536, geladen jedesmal nur 4352).
    monkeypatch.setattr(mc, "BASE_URL", "http://host:1234/v1")
    opener = _FakeOpener({
        "/api/v0/models": {"data": [{"id": "modell-x", "state": "loaded",
                                     "loaded_context_length": 32768}]},
        "/api/v1/models/unload": {"ok": True},
        "/api/v1/models/load": {"load_config": {"context_length": 32768}},
    })
    monkeypatch.setattr(mc, "build_opener", lambda: opener)
    ok, msg = mc.reset_model("modell-x", 32768)
    assert ok and "32768" in msg and "LM Studio" in msg and "ACHTUNG" not in msg
    unload_call = next(c for c in opener.calls if c[1].endswith("unload"))
    assert unload_call[2] == {"instance_id": "modell-x"}
    load_call = next(c for c in opener.calls if c[1].endswith("/api/v1/models/load"))
    assert load_call[2] == {"model": "modell-x", "context_length": 32768}


def test_reset_model_lmstudio_kappt_kleiner_als_angefordert(monkeypatch):
    # Genau der real beobachtete Fall: 32768 angefordert, aber die Engine
    # kappt (Modell-/Hardware-Grenze) auf 4352 -- muss als ACHTUNG erkennbar
    # sein statt den geforderten Wert unkritisch zurueckzumelden.
    monkeypatch.setattr(mc, "BASE_URL", "http://host:1234/v1")
    opener = _FakeOpener({
        "/api/v0/models": {"data": [{"id": "modell-x", "state": "loaded",
                                     "loaded_context_length": 4352}]},
        "/api/v1/models/unload": {"ok": True},
        "/api/v1/models/load": {"load_config": {"context_length": 32768}},
    })
    monkeypatch.setattr(mc, "build_opener", lambda: opener)
    ok, msg = mc.reset_model("modell-x", 32768)
    assert ok and "ACHTUNG" in msg and "4352" in msg and "32768" in msg


def test_reset_model_ollama(monkeypatch):
    monkeypatch.setattr(mc, "BASE_URL", "http://host:11434/v1")
    opener = _FakeOpener({
        "/api/tags": {"models": [{"name": "altes-modell"}]},
        "/api/generate": {"done": True},
    })
    monkeypatch.setattr(mc, "build_opener", lambda: opener)
    ok, msg = mc.reset_model("qwen", 8192)
    assert ok and "8192" in msg and "Ollama" in msg
    gen_calls = [c for c in opener.calls if c[1].endswith("/api/generate")]
    assert len(gen_calls) == 2
    assert gen_calls[0][2]["keep_alive"] == 0
    assert gen_calls[1][2]["options"] == {"num_ctx": 8192}


def test_reset_model_unbekannter_endpunkt(monkeypatch):
    monkeypatch.setattr(mc, "BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(mc, "build_opener", lambda: _FakeOpener({}))
    ok, msg = mc.reset_model("irgendein/modell", 32768)
    assert not ok
    assert "nicht" in msg.lower()


# ------------------- cache_control fuer Cloud-Endpunkte ---------------------

def test_is_local_engine_gecacht(monkeypatch):
    monkeypatch.setattr(mc, "_LOCAL_ENGINE_CACHE", {})
    monkeypatch.setattr(mc, "BASE_URL", "http://host:1234/v1")
    calls = {"n": 0}

    def _fake_detect():
        calls["n"] += 1
        return "lmstudio"

    monkeypatch.setattr(mc, "_detect_local_engine", _fake_detect)
    assert mc._is_local_engine() is True
    assert mc._is_local_engine() is True
    assert calls["n"] == 1  # zweiter Aufruf kommt aus dem Cache, keine neue Sonde


def test_payload_messages_lokal_unveraendert(monkeypatch):
    monkeypatch.setattr(mc, "_is_local_engine", lambda: True)
    msgs = [{"role": "system", "content": "Systemtext"},
            {"role": "user", "content": "hallo"}]
    assert mc._payload_messages(msgs) is msgs


def test_payload_messages_cloud_bekommt_cache_control(monkeypatch):
    monkeypatch.setattr(mc, "_is_local_engine", lambda: False)
    msgs = [{"role": "system", "content": "Systemtext"},
            {"role": "user", "content": "hallo"}]
    out = mc._payload_messages(msgs)
    assert out is not msgs                       # Original bleibt unberuehrt
    assert msgs[0]["content"] == "Systemtext"     # ... auch inhaltlich
    assert out[0]["content"] == [
        {"type": "text", "text": "Systemtext", "cache_control": {"type": "ephemeral"}}]
    assert out[1] is msgs[1]                      # restliche Nachrichten unveraendert


def test_payload_messages_ohne_system_prompt_unveraendert(monkeypatch):
    monkeypatch.setattr(mc, "_is_local_engine", lambda: False)
    msgs = [{"role": "user", "content": "hallo"}]
    assert mc._payload_messages(msgs) is msgs


# ------------ Abbruch bei leerer Antwort raeumt den Verlauf auf -------------

def test_run_task_entfernt_unbeantwortete_nachricht_nach_abbruch(monkeypatch):
    # Regression: nach 3x leerer Antwort blieb die unbeantwortete
    # user-Nachricht (mit Hinweisen der gescheiterten Aufgabe) im Verlauf
    # haengen -- ein spaeterer Zug (z.B. nach /mode chat) bezog sich dann
    # verwirrend noch darauf.
    monkeypatch.setattr(mc, "chat_stream", lambda messages, model: "")
    monkeypatch.setattr(mc, "LAST_REASONING_CHARS", 0)
    messages = [{"role": "system", "content": "sys"},
                {"role": "user", "content": "hallo + Hinweise"}]
    ergebnis = mc.run_task(messages, "m")
    assert ergebnis is None
    assert messages == [{"role": "system", "content": "sys"}]  # Rest sauber


# ------------------- Warnung bei System-/Temp-Arbeitsverzeichnis -----------

def test_suspicious_cwd_warning_tempdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mc.tempfile, "gettempdir", lambda: str(tmp_path))
    w = mc._suspicious_cwd_warning()
    assert w is not None and "Temp-Verzeichnis" in w


def test_suspicious_cwd_warning_unterordner_von_tempdir(tmp_path, monkeypatch):
    sub = tmp_path / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)
    monkeypatch.setattr(mc.tempfile, "gettempdir", lambda: str(tmp_path))
    w = mc._suspicious_cwd_warning()
    assert w is not None


def test_suspicious_cwd_warning_normales_projekt(tmp_path, monkeypatch):
    projekt = tmp_path / "mein-projekt"
    projekt.mkdir()
    monkeypatch.chdir(projekt)
    monkeypatch.setattr(mc.tempfile, "gettempdir", lambda: "/nonexistent-tempdir-xyz")
    assert mc._suspicious_cwd_warning() is None


def test_suspicious_cwd_warning_home(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mc.tempfile, "gettempdir", lambda: "/nonexistent-tempdir-xyz")
    monkeypatch.setattr(mc.os.path, "expanduser",
                        lambda p: str(tmp_path) if p == "~" else p)
    w = mc._suspicious_cwd_warning()
    assert w is not None and "Home" in w


# ------------- Fence-Nahtstellen nach Fortsetzungen reparieren -------------

def test_fix_fence_seams_fuegt_fehlenden_zeilenumbruch_ein():
    # Genau der real beobachtete Fall: eine Fortsetzung klebt direkt (ohne
    # \n) an den vorherigen Text -- die schliessende Fence wurde dadurch
    # nicht mehr erkannt ("write_file ohne Inhalt" trotz vollstaendigem
    # Inhalt).
    kaputt = '```action\n{"a":1}\n```\n\n```content\nzeile1\ncanJump = false;```\n'
    repariert = mc._fix_fence_seams(kaputt)
    assert "false;\n```" in repariert
    assert "false;```" not in repariert


def test_fix_fence_seams_laesst_saubere_fences_unveraendert():
    sauber = '```action\n{"a":1}\n```\n\n```content\nzeile1\nzeile2\n```\n'
    assert mc._fix_fence_seams(sauber) == sauber


def test_chat_stream_repariert_naht_nach_fortsetzung(monkeypatch):
    # End-to-End: erster Aufruf bricht MITTEN im content-Block ab (finish_
    # reason=length), die Fortsetzung klebt ohne Zeilenumbruch an -- die
    # schliessende Fence darf trotzdem gefunden werden.
    calls = [
        ('```action\n{"action":"write_file","path":"a.txt"}\n```\n\n'
         '```content\nzeile1\ncanJump = false;', "length"),
        ("}\n```\n", "stop"),
    ]

    def fake_retry(messages, model):
        return calls.pop(0)

    monkeypatch.setattr(mc, "_chat_once_retry", fake_retry)
    monkeypatch.setattr(mc, "LAST_REASONING_CHARS", 0)
    text = mc.chat_stream([{"role": "user", "content": "mach was"}], "m")
    action, tail = mc.extract_action(text)
    fehler = mc._attach_fence_contents(action, tail)
    assert fehler == ""
    assert action["content"].strip().endswith("false;}")
