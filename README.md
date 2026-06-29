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
| `--yes`          | Alle Schreib-/Run-Aktionen ohne Rückfrage ausführen   |
| `-h`, `--help`   | Hilfe anzeigen                                         |

### Umgebungsvariablen

| Variable        | Default                     | Zweck                                  |
|-----------------|-----------------------------|----------------------------------------|
| `MC_BASE_URL`   | `http://localhost:11434/v1` | Basis-URL der Schnittstelle            |
| `MC_MODEL`      | `qwen3-coder:30b`           | Default-Modell                         |
| `MC_API_KEY`    | *(leer)*                    | Optionaler Bearer-Token, falls nötig   |

## Aktionen des Agenten

| Aktion       | JSON                                                            | Rückfrage |
|--------------|----------------------------------------------------------------|-----------|
| `read_file`  | `{"action":"read_file","path":"..."}`                          | nein      |
| `write_file` | `{"action":"write_file","path":"...","content":"..."}`         | **ja**    |
| `list_dir`   | `{"action":"list_dir","path":"..."}`                           | nein      |
| `run`        | `{"action":"run","command":"..."}`                             | **ja**    |
| `finish`     | `{"action":"finish","summary":"..."}`                          | —         |

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
