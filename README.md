# mc — Mini Coding Tool

Ein kleiner agentischer Coding-Assistent für die Kommandozeile, der gegen
OpenAI-kompatible Ollama-Schnittstellen läuft (standardmäßig ein lokales Ollama).

Das Modell bekommt eine Aufgabe, plant in kleinen Schritten und kann dabei
Dateien lesen/schreiben, Verzeichnisse auflisten und Shell-Kommandos ausführen.
Keine externen Dependencies — nur die Python-Standardbibliothek.

> ## ⚠️ Warnung — Benutzung auf eigene Gefahr
>
> `mc` lässt ein KI-Modell **Dateien anlegen, überschreiben und löschen** sowie
> **beliebige Shell-Kommandos ausführen**. Dadurch kann es Daten verändern oder
> unwiderruflich vernichten und im schlimmsten Fall **dein Computersystem
> beschädigen**. Das Modell kann sich irren oder unerwartete Befehle erzeugen.
>
> - **Benutzung erfolgt vollständig auf eigene Gefahr** — der Autor übernimmt
>   **keinerlei Haftung** für Schäden, Datenverlust oder Folgekosten (siehe
>   [`LICENSE`](LICENSE)).
> - Nutze `mc` nur in **Verzeichnissen/Umgebungen, deren Inhalt du zur Not
>   verlieren kannst** — idealerweise in einem Repo mit sauberem Git-Stand,
>   einem Container oder einer VM.
> - Prüfe jede Schreib-/Run-Aktion vor dem Bestätigen. Das Flag **`--yes`**
>   schaltet alle Rückfragen ab und ist entsprechend **gefährlich** — bewusst
>   und nur in isolierten Umgebungen einsetzen.

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

### Cloud-Endpoints (OpenRouter & Co.)

Da `mc` nur die OpenAI-kompatible API spricht, läuft es auch gegen Cloud-Anbieter
wie **OpenRouter**. Dafür braucht es nur die Basis-URL und einen **API-Key**:

```bash
# Key setzen (NICHT ins Repo committen!)
export MC_API_KEY="sk-or-v1-…"           # OpenRouter-Key

python3 mc.py \
  --base-url https://openrouter.ai/api/v1 \
  --model "z-ai/glm-5.2" \
  "schreib hello.py"
```

Der Key wird als `Authorization: Bearer …` gesendet. Alternativ zum Env-Var
`MC_API_KEY` geht das nicht per Flag (bewusst, damit der Key nicht in der
Shell-History / Prozessliste landet).

**Token- & Kostenanzeige:** `mc` fordert pro Request `usage` an und summiert am
Ende einer Aufgabe Tokens und — falls der Endpoint sie liefert (OpenRouter via
`usage.cost`) — die **Kosten in USD**:

```text
Σ 7 Requests · 24130 Tokens (prompt 18044 + completion 6086) · Kosten: $0.0123
```

Bei lokalem Ollama gibt es keine Kosten (nur Tokens, falls der Server sie meldet).

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
| `--plan`         | Erst Plan zeigen + bestätigen lassen, dann umsetzen   |
| `--proxy URL`    | HTTP(S)-Proxy (z. B. Zscaler/Firmennetz)              |
| `--ca-bundle P`  | Pfad zu eigenem CA-Zertifikat (z. B. Zscaler-Root)    |
| `--insecure`     | TLS-Prüfung abschalten (nur als Notnagel)             |
| `-v`, `--verbose`| Passive Statuszeilen (Verbindung, Anfrage, Antwort)   |
| `--yes`          | Alle Schreib-/Run-Aktionen ohne Rückfrage ausführen   |
| `-h`, `--help`   | Hilfe anzeigen                                         |

### Plan-Modus (`--plan`)

Mit `--plan` legt der Agent nicht sofort los, sondern **erstellt zuerst einen
Plan** (geplante Dateien, Schritte, Annahmen), zeigt ihn und **fragt nach**, bevor
er etwas ändert:

```text
── Plan ──
1. Projektstruktur: einkaufsliste/ mit main.py, shopping_list.py, cli.py …
2. Funktionen: anzeigen, hinzufügen, entfernen, speichern/laden …
...
Plan ok? [Enter]=ja · Text=Änderungswunsch · n=abbrechen>
```

- **Enter** → Plan wird umgesetzt.
- **Text eingeben** → fließt als Änderungswunsch in den Plan ein.
- **`n`** → abbrechen, nichts wird geändert.

Die Plan-Phase ist **deterministisch im Tool** umgesetzt (nicht dem Modell
überlassen) und damit zuverlässig — gut für größere/mehrdeutige Aufgaben. Ohne
`--plan` arbeitet `mc` direkt los. (Mit `--yes` ist die Plan-Phase aus, da dort
nichts bestätigt wird.) Unabhängig davon kann der Agent über die `ask`-Aktion
jederzeit selbst nachfragen, wenn etwas unklar ist.

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
| `ask`         | `{"action":"ask","question":"..."}`                           | fragt     |
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

#### Auto-Continuation bei abgeschnittenen Antworten

Lange Antworten (große Multi-File-Blöcke) können **abgeschnitten** werden — sei es
durch ein Ausgabe-Token-Limit oder einen **Proxy/Verbindungsabbruch** mitten im
Stream. Dann fehlt das schließende `}` / der ```` ``` ````-Fence, und der
Action-Block wäre ungültig. `mc` erkennt das an **zwei** Signalen und fordert das
Modell automatisch zur **Fortsetzung** auf (bis zu 4×), bevor geparst wird:

- `finish_reason == "length"` (offizielles Token-Limit-Signal), **und**
- ein **Strukturcheck**: offener ```` ```action ````-Block ohne schließenden Fence
  — dieser fängt auch **Proxy-Abbrüche** ab, bei denen gar kein `finish_reason`
  ankommt.

Die abgeschnittenen Teile werden zusammengefügt, sodass am Ende ein vollständiger,
gültiger Block entsteht. Das ist die robuste Wurzel-Lösung — größen- und
modellunabhängig, **ohne** kaputtes JSON im Parser zu flicken.

`mc` zeigt bei jeder Fortsetzung **immer** (auch ohne `-v`) die erkannte Ursache,
damit man Gegenmaßnahmen treffen kann:

```text
⚠ Antwort abgeschnitten: Token-Limit (Ausgabe gekappt). Fordere Fortsetzung 1/4 …
⚠ Antwort abgeschnitten: Verbindung/Proxy hat den Stream abgebrochen — ggf. Proxy-/Netzwerk-Timeout erhoehen. Fordere Fortsetzung 1/4 …
```

So sieht man, ob ein **Token-Limit** (Modell/Server-Seite) oder ein
**Proxy-/Verbindungsabbruch** vorlag — im zweiten Fall hilft es, den Proxy- bzw.
Netzwerk-Timeout zu erhöhen.

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

### Modell-Challenge: dieselbe CRUD-App von mehreren Modellen

Gleiche Aufgabe an mehrere Modelle (je `--yes --max-steps 30`, 10-Min-Timeout):
eine **Personenverwaltung** mit Flask + SQLite Backend (CRUD-API für
name/adresse/telefon) und React-Frontend (Tabelle + Anlegen/Bearbeiten/Löschen).
Lokale Modelle auf dem Mac mini M4 Pro (24 GB, `num_ctx` 128k); Cloud-Modelle via
OpenRouter.

| Modell | Wo | Zeit | Dateien | Kosten | Ergebnis |
|---|---|---:|:---:|---:|---|
| **z-ai/glm-5.2** | ☁️ Cloud (OpenRouter) | **48 s** | 6/6 | $0.0174 | ✅ vollständig, alle 4 Endpunkte, sauberes CRUD-Frontend |
| **deepseek/deepseek-v4-pro** | ☁️ Cloud (OpenRouter) | 55 s | 6/6 | $0.0101 | ✅ vollständig, alle 4 Endpunkte, sauberes CRUD-Frontend |
| **google/gemma-4-26b-a4b-it** | ☁️ Cloud (OpenRouter) | 48 s | 6/6 | $0.0014 | ✅ vollständig — dasselbe Modell wie lokal `gemma4:26b-mlx`, nur ~5× schneller |
| **Ornith-1.0-35B (Q3_K_L)** | 💻 Lokal (Mac mini) | **168 s** | 6/6 | – | ✅ vollständig, alle 4 Endpunkte, Edit/Delete — **schnellster lokaler Volllauf**; agentisch trainiert, traf das Protokoll diszipliniert (nach System-Message-Fix, s. u.) |
| **qwen3-coder:30b** | 💻 Lokal (Mac mini) | 593 s | 6/6 | – | ✅ vollständig |
| **gemma4:26b-mlx** | 💻 Lokal (Mac mini) | 261 s | 6/6 | – | ✅ vollständig, alle 4 Endpunkte, Frontend mit Edit/Delete |
| gemma3:4b | 💻 Lokal (Mac mini) | 189 s | 2 | – | ⚠️ nur DB-Stub (kein `@app.route`), kein Frontend |
| gemma3:12b | 💻 Lokal (Mac mini) | 186 s | 0 | – | ❌ Code ok, aber `write_files`-JSON ungültig → nichts geschrieben |
| **qwopus3.6:27b (Q4_K_M)** | 💻 Lokal (Mac mini) | 338 s | 6/6 | – | ✅ vollständig (inkl. CSS-Styling & Bearbeiten) — **erst nach Prompt-Fix**: 1. Versuch scheiterte an abgeschnittenem JSON (fehlendes letztes `}`) |
| gemma-4-12B-coder-fable5 (Q4_K_M) | 💻 Lokal (Mac mini) | 464 s | 0 | – | ❌ Coder-Finetune, aber JSON dauerhaft kaputt (`\\n`, single-quotes) → nichts geschrieben |
| coe-gemma4-coding (14B) | 💻 Lokal (Mac mini) | abgebr. | 0 | – | ❌ produzierte nur einen kaputten „thought"-Stream (endlose Punkte), unbrauchbar |
| gpt-oss:20b | 💻 Lokal (Mac mini) | 1 s | 0 | – | ❌ leere Antwort (Reasoning-Modell, `/v1`-inkompatibel) |
| qwen3.6:35b-mlx | 💻 Lokal (Mac mini) | – | – | – | ⏳ Server liefert HTTP 500 (Modell noch nicht lauffähig) |
| qwen3.6:27b-mlx | 💻 Lokal (Mac mini) | – | – | – | ⏳ Server liefert HTTP 500 (Modell noch nicht lauffähig) |

> **Lokal** = Ollama auf dem Mac mini M4 Pro (24 GB, `num_ctx` 128k), kostenlos
> aber langsam. **Cloud** = OpenRouter (bezahlt pro Token, dafür sehr schnell).

Alle ✅-Apps bestanden Python-`ast`- und JSON-Checks und sind FE↔BE konsistent
(Felder, Port 5000, Endpunkte). Kosten = Summe aller Requests der Aufgabe laut
OpenRouter `usage.cost`.

**Erkenntnisse:**

- **Agentisch trainierte Modelle treffen das Protokoll am besten.** `Ornith-1.0-35B`
  (speziell für agentisches Coding trainiert) lieferte die volle App in **168 s** —
  der **schnellste lokale Volllauf** überhaupt, schneller als qwen3-coder (593 s),
  gemma4:26b (261 s) und qwopus (338 s). Es traf die Action-Blöcke diszipliniert,
  obwohl es ein Reasoning-Modell ist (viel internes „Denken", sauberer finaler
  Output). Allerdings deckte es auch einen **latenten Tool-Bug** auf (siehe unten).
- **Ein neues Modell findet alte Tool-Bugs.** Ornith brach zunächst *sofort leer*
  ab (`data: [DONE]` ohne Inhalt). Ursache war nicht das Modell, sondern dass `mc`
  **zwei aufeinanderfolgende `system`-Messages** schickte (Prompt + Projekt-
  überblick) — Orniths Chat-Template verträgt das nicht. Die anderen Modelle
  tolerierten es stillschweigend. Fix: beide zu **einer** System-Message gebündelt.
- **Cloud schlägt lokal deutlich bei Tempo/Aufwand:** `glm-5.2` und
  `deepseek-v4-pro` liefern die komplette App in **~50 s für 1–2 Cent** — rund
  **12× schneller** als das lokale `qwen3-coder:30b` (≈10 Min), das dafür
  kostenlos und offline ist.
- **Gleiches Modell, lokal vs. Cloud:** `gemma4:26b` liefert beidseitig die volle
  App — lokal in **261 s**, via OpenRouter (`google/gemma-4-26b-a4b-it`) in
  **48 s für $0.0014**. Der Tempogewinn ist ~5×; lokal punktet mit Offline-Betrieb
  und Datenschutz.

#### Was kostet „lokal" wirklich? (Strom)

„Lokal = kostenlos" gilt nur, wenn der Rechner ohnehin läuft. Rechnet man den
**Strom** mit, ergibt sich (Mac mini M4 Pro, **0,33 €/kWh**):

Apple nennt **155 W** max. Dauerleistung; real messen Tester unter Last aber nur
**~65–95 W**. Für LLM-Inferenz (GPU-lastig) ist **~90 W** eine vernünftige Annahme
→ rund **0,00082 ct/s**.

Formel: `Kosten = Leistung(kW) × Dauer(h) × Strompreis(€/kWh)`

| Modell (lokal) | Dauer | Strom @ ~90 W |
|---|---:|---:|
| qwen3-coder:30b | 593 s | ~0,49 ct |
| gemma4:26b-mlx | 261 s | ~0,22 ct |

**Pointe:** Beim selben Modell `gemma4:26b` kostet der lokale Lauf **~0,22 ct
Strom**, der Cloud-Lauf über OpenRouter nur **~0,13 ct** ($0.0014) — und ist dabei
5× schneller. Ein günstiges Cloud-Modell kann das lokale Setup also auch beim
*Preis* schlagen, sobald man Strom einrechnet. Bei teureren Cloud-Modellen
(z. B. `glm-5.2` mit ~1,7 ct) kehrt sich das wieder um — da ist lokal billiger.

> Stromverbrauchs-Quellen siehe unten. Rechnerkauf, Abschreibung und Leerlauf
> sind hier bewusst ausgeklammert — es geht nur um die reine Energie pro Lauf.
- **Coding-Spezialist gewinnt lokal — aber nicht allein:** `qwen3-coder:30b` und
  `gemma4:26b-mlx` ziehen die Multi-File-Aufgabe sauber durch; gemma4 ist dabei
  mit 261 s sogar gut 2× schneller. Die kleineren Gemmas (4b/12b) scheitern.
- **Frisch geladene Modelle erst prüfen:** `qwen3.6:27b/35b-mlx` tauchten zwar in
  der Modellliste auf, lieferten aber serverseitig **HTTP 500** — also (noch)
  nicht lauffähig (Download/MLX-Konvertierung oder fehlende Ollama-Unterstützung).
  Ein Listen-Eintrag heißt nicht automatisch „einsatzbereit".
- **Protokoll-Disziplin schlägt Code-Qualität — und die Lösung ist Aufteilung,
  nicht Nachsicht.** Mehrere Modelle scheiterten *nur* am JSON-Mantel, nicht am
  Code: `gemma3:12b` (doppelter `files`-Key/Escapes), die `fable`-Coder-Finetunes
  (`\\n`/single-quotes) und besonders **`qwopus3.6:27b`, das den besten Code aller
  Modelle lieferte** (Backend + gestyltes Frontend), aber im 1. Versuch an einem
  fehlenden `}` scheiterte — die Antwort war hinter einem einzigen Mega-Block
  abgeschnitten. **Die saubere Behebung** war nicht, den Parser kaputtes JSON
  flicken zu lassen, sondern die Abschneidung selbst zu behandeln:
  **Auto-Continuation** (siehe oben) erkennt abgeschnittene Antworten — per
  `finish_reason` *und* Strukturcheck (fängt auch Proxy-Abbrüche) — und fordert
  automatisch die Fortsetzung an, bis der Block vollständig ist. Damit ist das
  Problem größen- und modellunabhängig gelöst, statt sich auf Modell-Disziplin
  (kleine Blöcke) zu verlassen. Lektion: Bei Text-Protokoll-Agenten ist die
  maximale *Einzel*-Antwort das Risiko — robust wird es erst, wenn das Tool
  abgeschnittene Antworten erkennt und zusammensetzt.
- **Reasoning-Modelle** wie `gpt-oss:20b` brauchen ihren Reasoning-Channel —
  über die OpenAI-`/v1`-Schicht kommt hier nichts an.
- **128k Kontext ist auf 24 GB teuer:** bremst das lokale 30B stark; in der Cloud
  spielt das keine Rolle.

### Live-Verifikation: generierte App starten & erweitern

Die von `qwopus3.6` generierte CRUD-App wurde nicht nur statisch geprüft, sondern
**wirklich gestartet**: Flask-Backend (SQLite) hochgefahren, drei Kontakte per API
angelegt, und das React-Frontend im Browser geladen. Ergebnis: funktionsfähige
Oberfläche — Liste aus der DB, Anlegen/**Bearbeiten** (Formular füllt sich,
„Speichern"/„Abbrechen")/Löschen, sauberes CSS.

Anschließend wurde `mc` auf **dieselbe, bestehende App** angesetzt:

> „Füge einen Footer `(c) qwopus 2026` hinzu und eine Unterseite, die die App
> erklärt — ohne zusätzliche Bibliotheken, CRUD beibehalten."

`mc` (qwopus3.6) hat dafür `App.jsx` zuerst **gelesen**, dann gezielt erweitert:
eine Tab-Navigation (Personenverwaltung / „Über diese App") über einen
`useState`-Umschalter (kein react-router), eine Erklärseite und den Footer — und
das Styling in `index.html` ergänzt. Die bestehende CRUD-Funktion blieb intakt.
Das zeigt, dass `mc` nicht nur grüne Wiese kann, sondern **bestehenden Code findet,
liest und chirurgisch erweitert**.

In einer zweiten Iteration wurde das Frontend **refaktoriert**: die Logik in
eigene Komponenten aufgeteilt (`PersonenView.jsx`, `HilfeView.jsx`), echtes
**Hash-Routing** ohne Library eingebaut (`window.location.hash` + `hashchange`),
sodass die Hilfe eine **eigene URL** (`#/hilfe`) hat, und die Hilfeseite zu einer
ausführlichen Schritt-für-Schritt-Anleitung ausgebaut. Auch das live im Browser
verifiziert (beide URLs, korrekte Ansichten, Footer durchgehend). `mc` bewältigt
also auch strukturelle Umbauten, nicht nur additive Ergänzungen.

Für **punktuelle** Änderungen an großen Dateien nutzt `mc` die
[`edit_file`-Aktion](#aktionen-des-agenten) (gezieltes Ersetzen statt die ganze
Datei neu zu schreiben). In der Praxis bestätigt: Die fehlerhafte DELETE-Route
(404-Fix) und das Einfügen von Datenbank-/API-Abschnitten in die Hilfe liefen
jeweils mit **1× read_file + 1× edit_file** — ohne die komplette Datei zu senden,
was Tokens spart und das Truncation-Risiko vermeidet.

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
- Schrittlimit pro Aufgabe (Default **40**, via `--max-steps` / `MC_MAX_STEPS`).
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
