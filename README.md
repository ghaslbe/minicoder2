# mc — Mini Coding Tool

Ein kleiner agentischer Coding-Assistent für die Kommandozeile, der gegen
OpenAI-kompatible Ollama-Schnittstellen läuft (standardmäßig ein lokales Ollama).

Das Modell bekommt eine Aufgabe, plant in kleinen Schritten und kann dabei
Dateien lesen/schreiben, Verzeichnisse auflisten und Shell-Kommandos ausführen.
Keine externen Dependencies — nur die Python-Standardbibliothek.

## Hintergrund: warum ein eigenes Protokoll?

Nicht jeder Ollama-Endpoint unterstützt **natives OpenAI Tool-/Function-Calling** —
manche Proxies antworten auf das `tools`-Feld mit `HTTP 400`. Deshalb nutzt `mc`
ein **text-basiertes Action-Protokoll** und ist unabhängig von Function-Calling:

1. Das Modell gibt pro Antwort genau **einen** ` ```action `-Block mit JSON aus.
2. `mc` parst den Block, führt die Aktion aus.
3. Das Ergebnis wird als nächste Nachricht zurück an das Modell gespeist.
4. Das wiederholt sich, bis das Modell eine `finish`-Aktion ausgibt.

## Ollama / OpenAI-kompatibel

`mc` spricht ausschließlich die **OpenAI-kompatible Chat-API**
(`/v1/chat/completions` + `/v1/models`). Damit läuft es gegen **jeden
Ollama-Server** und jede andere OpenAI-kompatible Schnittstelle — einfach
`MC_BASE_URL` umstellen.

Lokales Ollama (Standard-Port 11434, der Default):

```bash
ollama serve                                   # Ollama läuft lokal
ollama pull qwen3-coder:30b                     # Modell holen
python3 mc.py "schreib hello.py"                # nutzt http://localhost:11434/v1
```

Entfernter Endpoint:

```bash
MC_BASE_URL=https://dein-endpoint.example/v1 \
MC_MODEL=qwen3-coder:30b \
  python3 mc.py "schreib hello.py"
```

> Hinweis: Natives Tool-Calling ist **nicht** erforderlich. `mc` nutzt sein
> eigenes Text-Action-Protokoll und funktioniert deshalb auch mit Ollama-
> Servern und Modellen, die kein Function-Calling unterstützen.

## Voraussetzungen

- Python 3.7+
- Ein erreichbarer Ollama- bzw. OpenAI-kompatibler Endpoint
  (Default `http://localhost:11434/v1`)

## Installation

Keine. Die Datei `mc.py` einfach ausführen:

```bash
python3 mc.py
```

Optional ausführbar machen:

```bash
chmod +x mc.py
./mc.py
```

## Benutzung

```bash
python3 mc.py                                   # interaktiver Chat
python3 mc.py "schreib fizzbuzz.py und führ es aus"   # Prompt direkt mitgeben
python3 mc.py --model gpt-oss:20b "..."         # anderes Modell
python3 mc.py --base-url http://server:11434/v1 "..."  # anderer Server
python3 mc.py --list-models                      # Modelle des Servers auflisten
python3 mc.py --yes "..."                        # ohne Rückfragen (Vorsicht!)
```

**Prompt mitgeben:** alles nach den Optionen wird als Aufgabe genommen
(`python3 mc.py "deine aufgabe"`). Ohne Prompt startet der interaktive Modus —
dort beendet `exit`, `quit` oder `Ctrl-D` die Sitzung.

**Server & Modell mitgeben:** per Flag (`--base-url`, `--model`) oder per
Env-Variable (`MC_BASE_URL`, `MC_MODEL`); das Flag hat Vorrang.

**Modelle auflisten:** `python3 mc.py --list-models` fragt `/models` am Server ab
und zeigt alle IDs (kombinierbar mit `--base-url`).

### Optionen

| Flag             | Bedeutung                                              |
|------------------|--------------------------------------------------------|
| `--model M`      | Modell wählen (Default `qwen3-coder:30b`)              |
| `--base-url URL` | Server-Basis-URL (Default `http://localhost:11434/v1`)|
| `--list-models`  | Verfügbare Modelle des Servers anzeigen und beenden   |
| `--max-steps N`  | Max. Agenten-Schritte pro Aufgabe (Default 40)        |
| `--proxy URL`    | HTTP(S)-Proxy (z. B. Zscaler/Firmennetz)              |
| `--ca-bundle P`  | Pfad zu eigenem CA-Zertifikat (z. B. Zscaler-Root)    |
| `--insecure`     | TLS-Prüfung abschalten (nur als Notnagel)             |
| `-v`, `--verbose`| Passive Statuszeilen (Verbindung, Anfrage, Antwort)   |
| `--yes`          | Alle Schreib-/Run-Aktionen ohne Rückfrage ausführen   |
| `-h`, `--help`   | Hilfe anzeigen                                         |

### Verbose-Modus

Mit `-v` / `--verbose` (oder `MC_VERBOSE=1`) gibt `mc` passive Statuszeilen aus —
praktisch zum Nachvollziehen, wo es z. B. hinter einem Proxy hängt:

```text
$ python3 mc.py -v --list-models
· verbinde mit https://server/v1/models …
· verbunden (HTTP 200), lese Modell-Liste …

$ python3 mc.py -v "..."
· verbinde mit https://server/v1/chat/completions …
· verbunden (HTTP 200), frage Modell 'qwen3-coder:30b', warte auf Antwort …
· Antwort beginnt …
· Antwort vollständig (53 Zeichen).
```

Ein gesetzter Proxy wird ebenfalls geloggt (Passwort wird maskiert).

### Firmennetz / Zscaler

In Umgebungen mit Zscaler (oder anderem Firmenproxy) schlägt der direkte Zugriff
oft fehl:

- **`getaddrinfo failed`** → DNS wird nicht direkt aufgelöst, der Traffic muss
  durch den Proxy. Proxy setzen:

  ```bash
  python3 mc.py --proxy http://dein-proxy:8080 --list-models
  # oder per Env-Variable:
  export HTTPS_PROXY=http://dein-proxy:8080
  ```

- **`remote end closed connection without response`** (bzw. *connection reset /
  refused*) → der Proxy wird zwar erreicht, weist die Verbindung aber ab. Meist:
  Proxy braucht **Login**, oder **falscher Host/Port**. Zugangsdaten mitgeben und
  Proxy gegenprüfen:

  ```bash
  python3 mc.py --proxy http://USER:PASS@proxy:8080 "..."
  echo $HTTPS_PROXY                                  # echten Proxy prüfen
  curl -v -x http://proxy:8080 https://server/v1/models   # direkt testen
  ```

- **`CERTIFICATE_VERIFY_FAILED`** → Zscaler bricht HTTPS mit eigenem Zertifikat
  auf. Firmen-CA angeben (empfohlen) oder Prüfung umgehen:

  ```bash
  python3 mc.py --ca-bundle /pfad/zur/zscaler-root.pem "..."
  python3 mc.py --insecure "..."        # nur als Notnagel
  ```

- **`timed out` bei einem lokalen Proxy** (z. B. `127.0.0.1:9001`) → der Port
  lauscht zwar, spricht aber ein anderes Protokoll. Lokale Firmen-Agents (Zscaler
  u. a.) sind oft **SOCKS**-Proxies, kein reines HTTP. Erst Typ bestimmen:

  ```bash
  curl.exe -v -k --proxy http://127.0.0.1:9001    https://chat.hcim.de/v1/models
  curl.exe -v -k --proxy socks5h://127.0.0.1:9001 https://chat.hcim.de/v1/models
  ```

  Die Variante, die JSON liefert, ist die richtige. Für SOCKS dann `mc` mit
  `socks5h://` aufrufen (siehe „SOCKS-Proxy" unten).

- **Proxy ermitteln, falls unbekannt:**

  ```bash
  python3 mc.py --debug-net          # DNS-Test + System-Proxy / PAC-URL / Registry
  ```

  Unter Windows steckt der Proxy hinter Zscaler meist in einer **PAC-Datei**
  (`AutoConfigURL`), nicht in einem festen Host. `--debug-net` zeigt die PAC-URL;
  diese im Browser öffnen und den `PROXY host:port`-Eintrag für den Zielhost
  übernehmen.

Entsprechende Env-Variablen: `MC_PROXY`, `MC_CA_BUNDLE` (sowie die Standard-Vars
`HTTP_PROXY` / `HTTPS_PROXY`, die `mc` automatisch beachtet). `mc` gibt bei
solchen Fehlern direkt einen passenden Hinweis aus.

#### SOCKS-Proxy

Für SOCKS-Proxies (häufig bei lokalen Zscaler-/SASE-Agents) wird das Paket
**PySocks** benötigt:

```bash
python -m pip install PySocks
python3 mc.py --proxy socks5h://127.0.0.1:9001 --base-url https://server/v1 -v --list-models
```

`socks5h://` löst DNS **am Proxy** auf — wichtig, wenn der lokale Rechner externe
Namen nicht selbst auflösen kann (das war die Ursache von `getaddrinfo failed`).
Unterstützt: `socks5://`, `socks5h://`, `socks4://`, `socks4a://`.

### Umgebungsvariablen

| Variable        | Default                     | Zweck                                  |
|-----------------|-----------------------------|----------------------------------------|
| `MC_BASE_URL`   | `http://localhost:11434/v1` | Basis-URL der Schnittstelle            |
| `MC_MODEL`      | `qwen3-coder:30b`           | Default-Modell                         |
| `MC_API_KEY`    | *(leer)*                    | Optionaler Bearer-Token, falls nötig   |
| `MC_PROXY`      | *(leer)*                    | HTTP(S)-Proxy (Zscaler/Firmennetz)     |
| `MC_CA_BUNDLE`  | *(leer)*                    | Pfad zu eigenem CA-Zertifikat          |
| `MC_VERBOSE`    | *(leer)*                    | `1` = passive Statuszeilen einschalten |
| `MC_MAX_STEPS`  | `40`                        | Max. Agenten-Schritte pro Aufgabe      |

## Aktionen des Agenten

| Aktion       | JSON                                                            | Rückfrage |
|--------------|----------------------------------------------------------------|-----------|
| `read_file`   | `{"action":"read_file","path":"..."}`                         | nein      |
| `write_file`  | `{"action":"write_file","path":"...","content":"..."}`        | **ja**    |
| `write_files` | `{"action":"write_files","files":[{"path":"...","content":"..."}, ...]}` | **ja** |
| `list_dir`    | `{"action":"list_dir","path":"..."}`                          | nein      |
| `find`        | `{"action":"find","pattern":"..."}`                           | nein      |
| `run`         | `{"action":"run","command":"..."}`                            | **ja**    |
| `finish`      | `{"action":"finish","summary":"..."}`                         | —         |

### Projektkontext & Datei-Erkennung

Damit der Agent nicht ins Leere rät, bekommt er:

- **beim Start einen rekursiven Dateiüberblick** des Arbeitsverzeichnisses (so
  „sieht" er, was existiert);
- die **`find`-Aktion** mit **unscharfer** Suche — Groß-/Kleinschreibung sowie
  Leer- und Sonderzeichen werden ignoriert, d. h. „hello world" findet
  `helloworld.py` oder `HelloWorld.js`;
- die Regel, eine bestehende Datei beim „Ändern" erst zu **suchen** und zu
  bearbeiten, statt blind eine neue anzulegen.

(Ordner wie `.git`, `__pycache__`, `node_modules`, `venv` werden dabei übersprungen.)

### Größere Projekte (viele Dateien / Verzeichnisse)

Für Projekte wie ein React-Frontend mit Flask-Backend:

- **`write_files`** legt mehrere Dateien (über mehrere Verzeichnisse) in **einem**
  Schritt an — verschachtelte Pfade werden automatisch erstellt.
- **`--max-steps N`** (Default 40) anheben, falls viele Schritte nötig sind.
- Der Agent wird angewiesen, für **neue Gerüste** offizielle Generatoren via `run`
  zu nutzen (z. B. `npm create vite@latest frontend -- --template react`) und
  danach gezielt einzelne Dateien anzupassen.

Beispiel:

```bash
python3 mc.py --base-url https://server/v1 --max-steps 60 \
  "Erstelle ein Flask-Backend (backend/) mit /api/hello und ein React-Frontend (frontend/). Nutze write_files."
```

Hinweis: Realistisch wird das erst mit ausreichend großem **Kontextfenster** des
Servers (`num_ctx`, siehe oben) — bei kleinem Default „vergisst" das Modell bei
vielen Dateien früh Angelegtes.

#### Praxistest: Todo-App (Flask + React)

Getestet mit `qwen3-coder:30b` und `num_ctx = 128k`. Auftrag: *„Erstelle eine
kleine Todo-App: Flask-Backend mit REST-API (GET/POST/DELETE /api/todos) in
backend/ inkl. requirements.txt, und ein React-Frontend in frontend/ … nutze
write_files."*

Ergebnis: **6 Dateien in nur 3 Schritten** (zwei `write_files`-Bündel + `finish`):

```
backend/app.py            Flask + Flask-CORS, In-Memory-Todos, GET/POST/DELETE
backend/requirements.txt  Flask==2.3.3, Flask-CORS==4.0.0
frontend/package.json     React 18, react-scripts
frontend/src/index.js     ReactDOM-Einstiegspunkt
frontend/src/App.jsx      Todos anzeigen/hinzufügen/löschen, fetch -> :5000
frontend/public/index.html
```

Der Code war in sich stimmig und syntaktisch gültig (Python-`ast`- und
JSON-Check bestanden); Frontend und Backend passten zusammen (Port 5000, CORS
aktiv). Für ein kleines Projekt also durchaus brauchbar — als Gerüst zum
Weiterarbeiten, nicht als fertiges Produkt.

> Server-Setup beim Test: Ollama auf einem **Mac mini M4 Pro (24 GB RAM)**.
> `qwen3-coder:30b` (≈18 GB im Q4-Quant) passt damit in den Unified Memory;
> ein großes `num_ctx` (128k) kostet zusätzlich KV-Cache-Speicher, läuft auf
> 24 GB aber noch. Die Antwortzeit pro Schritt liegt dadurch bei einigen
> Sekunden bis ~1–2 Minuten je nach Dateimenge.

## Verfügbare Modelle

Vom jeweiligen Endpoint abfragbar:

```bash
curl -s "$MC_BASE_URL/models" | python3 -m json.tool
# bzw. lokal:  curl -s http://localhost:11434/v1/models | python3 -m json.tool
```

Welche Modelle bereitstehen, hängt vom Server ab. Fürs Coden eignet sich
z. B. `qwen3-coder:30b`.

## Sicherheit

- **Bestätigung** vor jedem Schreibvorgang und jedem Shell-Kommando
  (außer mit `--yes`).
- Schrittlimit von **25 Schritten** pro Aufgabe.
- **120 s** Timeout pro Shell-Kommando.
- Tool-Ausgaben an das Modell werden auf **8000 Zeichen** gekürzt.

Trotzdem gilt: `run` führt beliebige Shell-Kommandos aus. `mc` am besten in
einem Projektverzeichnis nutzen, dem du vertraust — und `--yes` nur bewusst.

## Beispiel

```text
$ python3 mc.py --yes "Erstelle fizzbuzz.py das FizzBuzz von 1 bis 15 ausgibt, führe es dann aus."

── Schritt 1 ─────────────────────────────
Ich lege die Datei an.
✓ OK, 182 Zeichen nach fizzbuzz.py geschrieben.

── Schritt 2 ─────────────────────────────
» run python fizzbuzz.py
✓ exit=127

── Schritt 3 ─────────────────────────────
Python nicht gefunden, ich versuche python3.
» run python3 fizzbuzz.py
✓ exit=0

✓ FizzBuzz von 1 bis 15 erfolgreich erstellt und ausgeführt.
```

## Ideen für Erweiterungen

- Diff-/Patch-basiertes Editieren statt kompletter Datei-Überschreibung
- Git-Kontext (Branch, Diff) automatisch in den Prompt geben
- Persistenz der Konversation zwischen Sitzungen
- Konfigurierbare Allow-/Deny-Liste für `run`-Kommandos

## Lizenz & Haftung

Lizenziert unter der **MIT-Lizenz** — siehe [`LICENSE`](LICENSE).

Die Software wird **komplett ohne jegliche Gewährleistung und ohne jede Haftung**
bereitgestellt; die Nutzung erfolgt auf eigenes Risiko. Das Tool kann auf
Anweisung eines Sprachmodells Dateien überschreiben und beliebige Shell-Kommandos
ausführen — der Autor haftet nicht für daraus entstehende Schäden, Datenverluste
oder Kosten. Details im Haftungsausschluss in der `LICENSE`-Datei.

## Dateien

| Datei              | Inhalt                                       |
|--------------------|----------------------------------------------|
| `mc.py`            | Das komplette Tool                           |
| `README.md`        | Diese Datei                                  |
| `requirements.txt` | Abhängigkeiten (keine — nur Stdlib-Hinweis)  |
| `LICENSE`          | MIT-Lizenz + Haftungsausschluss              |
