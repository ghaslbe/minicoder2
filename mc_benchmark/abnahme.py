#!/usr/bin/env python3
"""Identische Abnahme-Batterie fuer alle Benchmark-Apps.
Aufruf: python3 abnahme.py <app-verzeichnis>
Startet den Server (app.py/main.py/server.py, ggf. venv), feuert die
curl-Faelle ab und gibt ein JSON-Ergebnis aus. DB wird vorher beiseite
gelegt, damit jede App mit leerem Bestand geprueft wird."""
import glob
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE_URL = "http://127.0.0.1:5020"
D = os.path.abspath(sys.argv[1])


def req(method, path, body=None, ctype="application/json", raw=None):
    url = BASE_URL + path
    data = raw.encode() if raw is not None else (
        json.dumps(body).encode() if body is not None else None)
    r = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, str(e)


def find_entry():
    for name in ("app.py", "main.py", "server.py", "run.py"):
        p = os.path.join(D, name)
        if os.path.exists(p):
            return p
    for p in sorted(glob.glob(os.path.join(D, "**", "app.py"), recursive=True)):
        if ".venv" not in p and "node_modules" not in p:
            return p
    return None


entry = find_entry()
out = {"dir": os.path.basename(D), "entry": entry and os.path.relpath(entry, D)}
if not entry:
    out["fatal"] = "kein app.py/main.py/server.py gefunden"
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(0)

# Statische Checks
src = open(entry, errors="replace").read()
out["debug_true"] = "debug=True" in src
out["basedir_pattern"] = ("__file__" in src and "abspath" in src) or "BASE_DIR" in src

# Frische DB
for db in glob.glob(os.path.join(D, "**", "*.db"), recursive=True):
    if ".venv" not in db and not db.endswith(".vom-lauf"):
        shutil.move(db, db + ".vor-abnahme")

py = os.path.join(D, ".venv", "bin", "python")
if not os.path.exists(py):
    py = os.path.join(os.path.dirname(entry), ".venv", "bin", "python")
if not os.path.exists(py):
    py = "python3"
proc = subprocess.Popen([py, entry], cwd=os.path.dirname(entry),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
ok = False
for _ in range(20):
    time.sleep(0.5)
    code, _b = req("GET", "/")
    if code == 200:
        ok = True
        break
if not ok:
    out["fatal"] = "Server startet nicht / GET / liefert kein 200"
    proc.terminate()
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(0)

t = {}
t["get_html"] = req("GET", "/")[0]
t["liste_leer"] = req("GET", "/api/persons")[0]
code, body = req("POST", "/api/persons", {"name": "Grün, Änne",
    "adresse": "Hauptstr. 1", "telefon": "089-123", "email": "a@b.de",
    "geburtstag": "1990-05-01"})
t["post_voll"] = code
pid = None
try:
    pid = json.loads(body).get("id")
except Exception:
    pass
if pid is None:  # Fallback: ID aus der Liste fischen
    try:
        pid = json.loads(req("GET", "/api/persons")[1])[0]["id"]
    except Exception:
        pid = 1
t["post_ohne_name"] = req("POST", "/api/persons", {"adresse": "x"})[0]
t["post_leerer_name"] = req("POST", "/api/persons", {"name": "  "})[0]
code, body = req("POST", "/api/persons", raw="name=test",
                 ctype="application/x-www-form-urlencoded")
t["post_kein_json"] = code
t["post_kein_json_antwort_json"] = body.strip().startswith("{")
t["get_einzeln"] = req("GET", f"/api/persons/{pid}")[0]
t["get_unbekannt"] = req("GET", "/api/persons/999999")[0]
code, body = req("PUT", f"/api/persons/{pid}", {"telefon": "089-999"})
t["put_partiell"] = code
t["put_unbekannt"] = req("PUT", "/api/persons/999999", {"name": "x"})[0]
t["delete"] = req("DELETE", f"/api/persons/{pid}")[0]
t["delete_nochmal"] = req("DELETE", f"/api/persons/{pid}")[0]
t["umlaut_roundtrip"] = False
code, body = req("POST", "/api/persons", {"name": "Ößterreich"})
if code in (200, 201):
    t["umlaut_roundtrip"] = "Ößterreich" in req("GET", "/api/persons")[1] or \
        "\\u00d6\\u00dfterreich".lower() in req("GET", "/api/persons")[1].lower()
out["tests"] = t

# Bewertung: Kernfaelle und Validierungsfaelle getrennt zaehlen.
kern = {"get_html": (200,), "liste_leer": (200,), "post_voll": (200, 201),
        "get_einzeln": (200,), "get_unbekannt": (404,),
        "put_unbekannt": (404,), "delete": (200, 204),
        "delete_nochmal": (404,)}
valid = {"post_ohne_name": (400, 422), "post_leerer_name": (400, 422),
         "post_kein_json": (400, 415, 422)}
out["kern_bestanden"] = sum(t[k] in v for k, v in kern.items())
out["kern_gesamt"] = len(kern)
out["valid_bestanden"] = sum(t[k] in v for k, v in valid.items())
out["valid_gesamt"] = len(valid)
out["put_semantik"] = ("partiell" if t["put_partiell"] in (200,)
                       else "strikt" if t["put_partiell"] in (400, 422)
                       else f"?({t['put_partiell']})")
proc.terminate()
time.sleep(1)
print(json.dumps(out, ensure_ascii=False))
