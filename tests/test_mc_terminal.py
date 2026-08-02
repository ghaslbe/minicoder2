# Tests fuer mc_terminal.py (Slash-Kommandos, /skills) — ohne Netzwerk/LLM.
# Ausfuehren:  python3 -m pytest tests/ -q

import importlib.util
import os

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "mc_terminal", os.path.join(os.path.dirname(__file__), "..", "mc_terminal.py"))
mt = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mt)


@pytest.fixture(autouse=True)
def _skill_dirs(tmp_path, monkeypatch):
    """Jeder Test: eigene Skill-Verzeichnisse (global + projekt)."""
    monkeypatch.chdir(tmp_path)
    global_dir = tmp_path / "global-skills"
    projekt_dir = tmp_path / ".mc-skills"
    global_dir.mkdir()
    monkeypatch.setattr(mt, "SKILL_DIRS", (str(global_dir), str(projekt_dir)))
    yield global_dir, projekt_dir


def _skill(d, name, text):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(str(d), name), "w", encoding="utf-8") as f:
        f.write(text)


def test_frontmatter_wird_getrennt():
    meta, body = mt._split_frontmatter("---\ncheck: true\nbeschreibung: X\n---\nAufgabe hier")
    assert meta == {"check": "true", "beschreibung": "X"}
    assert body == "Aufgabe hier"


def test_ohne_frontmatter_bleibt_alles_body():
    meta, body = mt._split_frontmatter("Nur Text")
    assert meta == {} and body == "Nur Text"


def test_projekt_skill_ueberschreibt_globalen(_skill_dirs):
    global_dir, projekt_dir = _skill_dirs
    _skill(global_dir, "fix.md", "GLOBAL")
    _skill(projekt_dir, "fix.md", "PROJEKT")
    assert mt.load_skills()["fix"]["body"] == "PROJEKT"


def test_render_mit_und_ohne_platzhalter():
    sk = {"body": "Pruefe $ARGUMENTS genau."}
    assert mt.render_skill(sk, "backend") == "Pruefe backend genau."
    sk2 = {"body": "Pruefe alles."}
    assert mt.render_skill(sk2, "backend") == "Pruefe alles.\n\nbackend"
    assert mt.render_skill(sk2, "") == "Pruefe alles."


def test_expand_skill_mit_flags(_skill_dirs):
    _, projekt_dir = _skill_dirs
    _skill(projekt_dir, "gewicht.md",
           "---\ncheck: true\n---\nErgaenze das Feld $ARGUMENTS.")
    art, wert, flags = mt.expand_input("/gewicht huftgold")
    assert art == "task"
    assert wert == "Ergaenze das Feld huftgold."
    assert flags == {"check": True, "analyse": False}


def test_bare_word_dispatch(_skill_dirs):
    _, projekt_dir = _skill_dirs
    _skill(projekt_dir, "review.md", "Review: $ARGUMENTS")
    art, wert, _ = mt.expand_input("review backend/app.py")
    assert art == "task" and wert == "Review: backend/app.py"


def test_normale_eingabe_geht_durch(_skill_dirs):
    art, wert, _ = mt.expand_input("baue eine app")
    assert art == "pass" and wert == "baue eine app"


def test_unbekanntes_kommando_mit_vorschlag(_skill_dirs):
    _, projekt_dir = _skill_dirs
    _skill(projekt_dir, "gewicht.md", "X")
    art, wert, _ = mt.expand_input("/gewich")
    assert art == "print"
    assert "Meintest du" in wert and "/gewicht" in wert


def test_skills_liste_und_model(_skill_dirs):
    _, projekt_dir = _skill_dirs
    _skill(projekt_dir, "fix.md", "---\nbeschreibung: Repariert\nanalyse: ja\n---\nX")
    art, wert, _ = mt.expand_input("/skills")
    assert art == "print" and "/fix" in wert and "Repariert" in wert and "analyse" in wert
    art, wert, _ = mt.expand_input("/model neu-modell")
    assert art == "model" and wert == "neu-modell"
    art, wert, _ = mt.expand_input("/model", model="altes-modell")
    assert art == "print" and "altes-modell" in wert
