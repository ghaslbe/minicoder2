# Tests fuer die deterministischen Teile von mc.py (Parser, Gates, Matching).
# Ausfuehren:  python3 -m pytest tests/ -q
# Bewusst OHNE Netzwerk/LLM — alles, was hier getestet wird, ist reiner
# Python-Code, der unabhaengig vom Modell funktionieren muss.

import importlib.util
import os
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
    yield


# --------------------------- Action-Parsing --------------------------------

def test_action_json_klassisch():
    action, _ = mc.extract_action(
        'Text davor.\n```action\n{"action":"read_file","path":"a.py"}\n```')
    assert action == {"action": "read_file", "path": "a.py"}


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

def test_truncate_zeigt_kopf_und_ende():
    s = "ANFANG" + "x" * 20000 + "ENDE"
    out = mc.truncate(s)
    assert out.startswith("ANFANG") and out.endswith("ENDE")
    assert "ausgelassen" in out


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
    assert "WEITERENTWICKLUNG" in hints
    assert "Generator" in hints


def test_task_hints_leer_bei_leerem_verzeichnis():
    assert mc.task_hints("bau mir eine app") == ""


def test_task_hints_liest_projekt_notizen():
    with open(mc.MC_NOTES, "w") as f:
        f.write("- Backend-Port: 5010 (FEST)\n- Feld heisst 'geburtstag'\n")
    hints = mc.task_hints("mach irgendwas")
    assert "Backend-Port: 5010" in hints
    assert "HALTE DICH DARAN" in hints


def test_system_prompt_lehrt_notizen():
    assert "MC-NOTIZEN.md" in mc.system_prompt(True)


# --------------------------- Kontext-Beschneidung ---------------------------

def test_prune_kuerzt_alte_schritte_und_laesst_neue():
    msgs = [{"role": "system", "content": "S"}]
    for i in range(8):
        msgs.append({"role": "assistant",
                     "content": f'```action\n{{"action":"write_file","path":"f{i}","content":"{"x"*900}"}}\n```'})
        msgs.append({"role": "user", "content": f"[Ergebnis von write_file]\n" + "y" * 900})
    alt_len = len(msgs[1]["content"])
    mc.prune_messages(msgs, keep=2)
    assert len(msgs[1]["content"]) < alt_len          # alt: gekuerzt
    assert len(msgs[-1]["content"]) > 600             # juengst: unangetastet
    assert msgs[0]["content"] == "S"                  # System-Prompt: nie


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
    assert '"old":"<exakter ausschnitt>"' in sp_json
    assert "@@" not in sp_fence and "@@" not in sp_json  # alle Platzhalter ersetzt
