#!/usr/bin/env python3
"""
mc_benchmark/runner.py — sequenzieller Modell-Benchmark fuer mc.py
==================================================================

Laesst mehrere Modelle nacheinander dieselbe CRUD-Aufgabe bauen (siehe
Blog-Kapitel 19): je Modell ein frisches Verzeichnis unter
mc_benchmark/laeufe/<slug>/, 20-Minuten-Limit, Wandzeit wird gemessen,
Port 5020 wird zwischen den Laeufen freigeraeumt. Danach jede App mit
abnahme.py pruefen:  python3 mc_benchmark/abnahme.py mc_benchmark/laeufe/<slug>

Aufruf:
  export MC_API_KEY="sk-or-..."        # OpenRouter-Key (nie committen!)
  python3 mc_benchmark/runner.py

Modelle unten in MODELS eintragen (OpenRouter-ID, Kurzname).
"""
import json
import os
import re
import subprocess
import sys
import time

HIER = os.path.dirname(os.path.abspath(__file__))
MC = os.path.join(HIER, "..", "mc.py")
BASE = os.path.join(HIER, "laeufe")
TASK = ("Baue eine einfache CRUD-Anwendung in Python fuer Personendaten: "
        "Flask + SQLite. REST-API unter /api/persons (GET Liste, GET einzeln, "
        "POST, PUT, DELETE) mit den Feldern name, adresse, telefon, email, "
        "geburtstag. Dazu eine einfache HTML-Oberflaeche unter /, die Personen "
        "anzeigt und Anlegen, Bearbeiten und Loeschen kann. Der Server laeuft "
        "FEST auf Port 5020. Lege auch eine requirements.txt an.")
TIMEOUT_S = 1200

MODELS = [
    ("deepseek/deepseek-v4-flash-0731", "ds-flash-0731"),
    ("qwen/qwen3.7-flash", "qwen37-flash"),
    ("google/gemma-4-26b-a4b-it", "gemma4-26b"),
]

if len(sys.argv) > 1:
    # Modell-IDs als Argumente ueberschreiben die Liste oben:
    #   python3 mc_benchmark/runner.py anbieter/modell [weitere ...]
    MODELS = [(mid, re.sub(r"[^a-z0-9.-]+", "-", mid.split("/")[-1].lower()))
              for mid in sys.argv[1:]]

if not os.environ.get("MC_API_KEY"):
    sys.exit("MC_API_KEY fehlt (OpenRouter-Key als Env-Variable setzen).")

results = []
for model_id, slug in MODELS:
    d = os.path.join(BASE, slug)
    os.makedirs(d, exist_ok=True)
    env = dict(os.environ, MC_CONFIG="/nonexistent")  # ~/.mc.json neutralisieren
    print(f"=== START {model_id} -> {d}", flush=True)
    t0 = time.time()
    rc = None
    with open(os.path.join(d, "mc-run.log"), "w") as lf:
        try:
            p = subprocess.run(
                ["python3", MC, "--base-url", "https://openrouter.ai/api/v1",
                 "--model", model_id, "--yes", "--check", "--verbose", TASK],
                cwd=d, env=env, stdin=subprocess.DEVNULL,
                stdout=lf, stderr=subprocess.STDOUT, timeout=TIMEOUT_S)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            rc = "TIMEOUT"
    dt = round(time.time() - t0)
    # Sicherheitsnetz: haengengebliebene Test-Server des Laufs beenden.
    subprocess.run(["pkill", "-f", "app.py"], capture_output=True)
    time.sleep(2)
    summe = ""
    try:
        with open(os.path.join(d, "mc-run.log"), errors="replace") as f:
            for line in f:
                if line.startswith("Σ") or "Kosten:" in line:
                    summe = line.strip()
    except OSError:
        pass
    entry = {"model": model_id, "slug": slug, "seconds": dt, "exit": rc,
             "summe": summe}
    results.append(entry)
    print("=== ERGEBNIS " + json.dumps(entry, ensure_ascii=False), flush=True)

with open(os.path.join(BASE, "ergebnisse.json"), "w") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print(f"=== FERTIG: {len(results)} Laeufe -> {os.path.join(BASE, 'ergebnisse.json')}",
      flush=True)
