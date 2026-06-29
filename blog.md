# mc — Ein Mini-Coding-Tool bauen und damit LLMs challengen

*Erfahrungsbericht über Entstehung, Benchmark und Weiterentwicklung von `mc`.*

Was als „kannst du mal die Schnittstelle testen und ein kleines Coding-Tool
bauen?" begann, wurde zu einem ausgewachsenen Experiment: ein eigener agentischer
Coding-Assistent in reinem Python — und ein Benchmark, der ein Dutzend LLMs vor
dieselbe React+Flask-Aufgabe stellt. Hier die gesammelten Erfahrungen.

---

## 1. Die Ausgangslage: ein Ollama-Endpoint ohne Tool-Calling

Ausgangspunkt war ein **OpenAI-kompatibler Ollama-Endpoint** (`/v1`), der auf einem
**Mac mini M4 Pro, 24 GB** lief. Erste Tests:

- `/v1/models`, `/v1/chat/completions`, Streaming → ✅ funktionieren, kein API-Key.
- **Natives OpenAI Tool-/Function-Calling → ❌ HTTP 400.** Das `tools`-Feld wird
  vom Proxy abgelehnt.

Daraus folgte die zentrale Designentscheidung: **ein text-basiertes
Action-Protokoll**. Das Modell gibt pro Antwort genau einen ```` ```action ````-Block
mit JSON aus; das Tool parst ihn, führt die Aktion aus (read/write/list/find/run)
und speist das Ergebnis zurück. Unabhängig von Function-Calling — läuft daher mit
praktisch jedem Modell.

**Lektion:** „OpenAI-kompatibel" heißt nicht „alle OpenAI-Features". Immer erst
die echte Capability testen, nicht die Doku glauben.

---

## 2. Das Tool wächst mit den Problemen

Statt alles vorab zu planen, wuchs `mc.py` entlang echter Hürden:

- **`write_files`** (mehrere Dateien in einem Schritt) — nötig, sobald Projekte
  aus vielen Dateien in vielen Verzeichnissen bestehen.
- **`find` mit unscharfer Suche** — weil das Modell „hello world" sagte und blind
  eine neue `hello.py` anlegte, statt die existierende `helloworld.py` zu finden.
  Dazu ein Projektüberblick beim Start, damit der Agent sieht, was es gibt.
- **`--plan`** — deterministische Plan-Phase im Tool (Plan zeigen → bestätigen →
  umsetzen). Wichtige Erkenntnis: das Modell hält sich *nicht* zuverlässig an die
  Anweisung, selbst zu planen/fragen — also muss das Tool es erzwingen.
- **Kosten-/Token-Tracking** — `usage` pro Request, Summe am Ende; bei OpenRouter
  inklusive `cost` in USD.

Und später, getrieben vom Benchmark und der echten App (siehe Abschnitt 6):

- **Auto-Continuation** — abgeschnittene Antworten erkennen und automatisch
  fortsetzen lassen (statt am unvollständigen JSON zu scheitern).
- **`edit_file`** — gezieltes Ersetzen statt die ganze Datei neu zu schreiben:
  spart Tokens und vermeidet eben jene Abschneidungen.
- **Warte-Spinner** — sichtbares Lebenszeichen, während ein lokales Modell denkt.

**Lektion:** Bei Agenten gilt — was zuverlässig passieren soll, gehört ins Tool,
nicht in den Prompt. Modell-Disziplin ist keine Verlasslichkeit.

---

## 3. Die Firmennetz-Odyssee

Ein langer Seitenstrang: das Tool sollte auch hinter Unternehmens-Proxys laufen.
Die Fehler kamen in Wellen, jeder mit eigener Ursache:

1. `getaddrinfo failed` → DNS wird nicht lokal aufgelöst (Proxy nötig).
2. `remote end closed connection without response` → Proxy erreicht, aber weist
   ab (Login/falscher Port). Dieser Fehler ist technisch ein `OSError`, **kein**
   `URLError` — musste extra abgefangen werden.
3. `timed out` bei einem lokalen Proxy-Port → lokaler Agent spricht evtl. SOCKS statt HTTP.

Eingebaut: `--proxy`, `--ca-bundle`, `--insecure`, SOCKS-Support (PySocks),
`--debug-net` (DNS-/TCP-Test + Registry/PAC-Auslese unter Windows) und Klartext-
Hinweise zu jedem Fehlertyp. Am Ende stellte sich heraus: direktes `curl` ging —
der Firmenproxy tunnelte transparent, ein expliziter Proxy war gar nicht nötig.

**Lektion:** Netzwerkfehler in Firmenumgebungen sind vielschichtig. Gute
Fehlermeldungen mit konkreten nächsten Schritten sind Gold wert. Und: erst `curl`
testen, bevor man Proxys konfiguriert.

---

## 4. Kontextfenster & Ollama

- Die native `/api`-Schicht (für `num_ctx`, `/api/show`) ist auf dem Endpoint mit
  **401 gesperrt** — nur die offene `/v1`-Schicht ist nutzbar.
- Über `/v1` werden Generierungs-Parameter (`num_ctx`, `max_tokens`, `stop`)
  **stillschweigend verworfen**. Empirisch getestet: `max_tokens:1` → ignoriert.
- `num_ctx` lässt sich nur per nativer `/api/chat` (`options.num_ctx`) oder
  serverseitig (`OLLAMA_CONTEXT_LENGTH`) setzen. Der Betreiber stellte schließlich
  **128k** ein.

**Lektion:** Das Kontextfenster ist eine Server-Eigenschaft, kein Client-Wunsch.
Und 128k auf 24 GB ist teuer — der KV-Cache bremst große Modelle spürbar.

---

## 5. Der große Modell-Benchmark

**Aufgabe (für alle identisch):** eine „Personenverwaltung" — Flask + SQLite
Backend mit CRUD-API (GET/POST/PUT/DELETE für name/adresse/telefon) plus React-
Frontend (Tabelle + Formular zum Anlegen/Bearbeiten/Löschen). Erfolg = 6 Dateien,
valider Code, FE↔BE konsistent.

### Gesamtergebnis

| Modell | Wo | Zeit | Dateien | Kosten | Ergebnis |
|---|---|---:|:---:|---:|---|
| **z-ai/glm-5.2** | ☁️ Cloud | 48 s | 6/6 | $0.0174 | ✅ vollständig |
| **deepseek/deepseek-v4-pro** | ☁️ Cloud | 55 s | 6/6 | $0.0101 | ✅ vollständig |
| **google/gemma-4-26b-a4b-it** | ☁️ Cloud | 48 s | 6/6 | $0.0014 | ✅ vollständig |
| **Ornith-1.0-35B** (Q3_K_L) | 💻 Lokal | 168 s | 6/6 | – | ✅ vollständig — **schnellster lokaler Volllauf**; agentisch trainiert (nach System-Message-Fix, s. 6.7) |
| **qwen3-coder:30b** | 💻 Lokal | 593 s | 6/6 | – | ✅ vollständig |
| **gemma4:26b-mlx** | 💻 Lokal | 261 s | 6/6 | – | ✅ vollständig, 2× schneller als qwen |
| gemma3:4b | 💻 Lokal | 189 s | 2 | – | ⚠️ nur DB-Stub, kein `@app.route`, kein Frontend |
| gemma3:12b | 💻 Lokal | 186 s | 0 | – | ❌ JSON ungültig (doppelter `files`-Key/Escapes) |
| gpt-oss:20b | 💻 Lokal | 1 s | 0 | – | ❌ leere Antwort (Reasoning, `/v1`-inkompatibel) |
| **qwopus3.6:27b** (Q4) | 💻 Lokal | 339 s | 0→6 | – | ❌→✅ **bester Code von allen** (inkl. CSS!); 1. Versuch an fehlendem `}` gescheitert, nach dem Fix (Abschnitt 6) vollständig (6/6) |
| gemma-4-12B-coder-fable5 (Q4) | 💻 Lokal | 464 s | 0 | – | ❌ JSON dauerhaft kaputt (`\\n`, single-quotes) |
| gemma-4-12B-coder-fable5 (Q8) | 💻 Lokal | ~710 s | 0 | – | ❌ dito, abgebrochen |
| coe-gemma4-coding (14B) | 💻 Lokal | abgebr. | 0 | – | ❌ nur kaputter „thought"-Stream (endlose Punkte) |
| qwen3.6:27b-mlx | 💻 Lokal | – | – | – | ⏳ Server HTTP 500 (nie lauffähig) |
| qwen3.6:35b-mlx | 💻 Lokal | – | – | – | ⏳ Server HTTP 500 (nie lauffähig) |

### Die wichtigsten Erkenntnisse

**1. Protokoll-Disziplin schlägt Code-Qualität.** Das ist DAS Leitmotiv. Gleich
drei Modelle scheiterten *nur* am JSON-Mantel, nicht am Code — am bittersten
`qwopus3.6:27b`, das mit Abstand den schönsten Code lieferte (sauberes Backend
*und* ein durchgestyltes Frontend mit CSS), aber an einem einzigen fehlenden `}`
zerschellte. Was dahintersteckte und wie es *sauber* behoben wurde (nicht durch
Parser-Flicken, sondern an der Wurzel), ist die Geschichte von Abschnitt 6.

**2. Coding-Spezialisten gewinnen lokal — aber nicht jeder „coder".** `qwen3-coder`
und `gemma4:26b-mlx` ziehen es sauber durch. Die explizit als „coder"/„coding"
benannten Finetunes (fable, coe-gemma4) waren dagegen die *schlechtesten* — sie
beherrschten das Ausgabeprotokoll nicht. Ein Label ist keine Garantie.

**3. Cloud schlägt lokal bei Tempo — und manchmal sogar beim Preis.** Die Cloud-
Modelle lieferten in ~50 s für 1–2 Cent, rund 12× schneller als das lokale 30B.

**4. Gleiches Modell, lokal vs. Cloud — die überraschende Pointe.** `gemma4:26b`
gibt es beidseitig:
- Lokal: 261 s, „kostenlos" (nur Strom).
- Cloud (OpenRouter): 48 s, $0.0014.

Rechnet man den Strom des Mac mini mit (~90 W real unter Last, 0,33 €/kWh →
~0,00082 ct/s), kostet der **lokale** Lauf **~0,22 ct** — *mehr* als die Cloud
(**~0,13 ct**) und ist dabei 5× langsamer. „Lokal = umsonst" stimmt nur, wenn der
Rechner ohnehin läuft. Bei teuren Cloud-Modellen (glm-5.2, ~1,7 ct) dreht sich das
wieder zugunsten lokal.

**5. „In der Modellliste" ≠ „einsatzbereit".** Die qwen3.6-MLX-Modelle tauchten in
`/v1/models` auf, gaben aber konsistent HTTP 500 — nie lauffähig. Immer erst einen
Health-Check, bevor man benchmarkt.

**6. Reasoning-Modelle brauchen ihren Channel.** `gpt-oss:20b` und coe-gemma4
(„thought"-Stil) lieferten über die `/v1`-Schicht nichts Brauchbares.

---

## 6. Vom Benchmark zur echten Anwendung

Der Benchmark warf eine Frage auf, die zum interessantesten Teil des Projekts
wurde: Das beste Modell scheiterte an einer Lappalie — und beim Versuch, das zu
beheben, wuchs das Tool von einem Code-Generator zu einem echten Agenten, der eine
laufende App iterativ weiterentwickelt. Dieser Bogen in fünf Schritten.

### 6.1 Der qwopus-Fall: warum „der beste Code" trotzdem 0 Dateien ergab

`qwopus3.6:27b` war der spannendste Einzelfall des ganzen Benchmarks. Es lieferte
den mit Abstand schönsten Code — sauberes SQLite-Backend mit Validierung *und* ein
durchgestyltes React-Frontend (CSS, Erfolgs-/Fehlermeldungen, Edit/Delete) — und
schrieb am Ende **null Dateien**. Lohnt sich, genau hinzusehen, weil es viel über
die Mechanik solcher Tools verrät.

**Die Obduktion.** Der Action-Block endete exakt mit `}]` — der `files`-Array war
vollständig geschlossen, aber das **äußere `}` des Objekts und der schließende
```` ``` ````-Fence fehlten. Der gesamte *Inhalt* war da (alle 6 Dateien bis zum
letzten Zeichen), nur die zwei abschließenden Struktur-Zeichen nicht.

**War es das Token-Limit?** Naheliegende Vermutung — aber die Messung widerspricht
einem *globalen* Limit: qwopus' Block war ~9.500 Zeichen, `qwen3-coder` hatte mit
~12.000 einen *größeren* Block erfolgreich rausgegeben. Der Unterschied lag in der
**Strategie**: die erfolgreichen lokalen Modelle (`qwen3-coder`, `gemma4:26b`)
verteilten die App auf **mehrere** `write_files`-Schritte (erst Backend, dann
Frontend). qwopus presste **alles in einen einzigen Mega-Block** — und der Stream
endete einen Wimpernschlag zu früh.

**Die zwei sauberen Hypothesen:**
1. *Truncation am Ausgabe-Ende* — Ollamas `num_predict`-Default greift, weil der
   eine Block zu groß wurde. (Entscheidbar über `finish_reason == "length"`.)
2. *Modell-Slip* — Array `]` geschlossen, äußeres `}` schlicht vergessen.

**Was wir NICHT gemacht haben: am Parser tricksen.** Der naheliegende Hack — im
Tool fehlende Klammern automatisch ergänzen — würde qwopus „retten", aber auf
Kosten der Sauberkeit: dann würde das Tool kaputtes JSON stillschweigend
zurechtbiegen und könnte auch echte Fehler verschleiern. Stattdessen die saubere
Lösung an der Wurzel: **dem Agenten beibringen, `write_files` in kleinere Batches
zu splitten** (max. 2–3 Dateien bzw. ~200 Zeilen pro Block, große Projekte über
mehrere Schritte). Genau das, was die Gewinner ohnehin taten. Kleinere,
vollständige Ausgaben statt einem großen, abschneidbaren Block.

**Die allgemeine Lektion:** Bei Text-Protokoll-Agenten ist nicht die maximale
*Gesamt*-Ausgabe das Risiko, sondern die maximale *Einzel-Antwort*. Lieber viele
kleine, garantiert vollständige Schritte als einen großen, der an der letzten
Klammer zerbricht. Robustheit kommt aus der Aufteilung, nicht aus Nachsicht beim
Parsen.

**Nachtrag — der Re-Run.** Mit der Batch-Splitting-Anweisung im System-Prompt lief
qwopus3.6 ein zweites Mal: **338 s, 6/6 Dateien, vollständig** — sauberes
SQLite-Backend mit allen 4 Endpunkten *und* ein React-Frontend inklusive
Bearbeiten-Funktion (`editingId`/`edit(person)`/PUT) und CSS. Damit ist qwopus3.6
nachträglich erfolgreich und gehört qualitativ zu den besten lokalen Ergebnissen.

Interessant: Es packte auch diesmal viel in einen großen Block — der kam nun aber
vollständig durch. Ob das an der expliziten Splitting-Anweisung lag oder schlicht
daran, dass die Antwort diesmal nicht abgeschnitten wurde, lässt sich ohne
`finish_reason` nicht zu 100 % trennen. Aber der Effekt ist da, und der Weg war
sauber: an der Wurzel ansetzen (kompaktere Ausgaben anstoßen), statt das Tool
kaputtes JSON zurechtbiegen zu lassen. **Bester Code — und beim zweiten Anlauf
auch das vollständige Ergebnis.**

### 6.2 Vom Pflaster zur echten Lösung: Auto-Continuation

Die Batch-Splitting-Anweisung war ehrlich gesagt ein Pflaster — sie *hofft*, dass
das Modell kleine Blöcke macht. Bei einer größeren App hätte es wieder alles in
einen Mega-Block gepackt und wäre wieder abgeschnitten worden. Die robuste Lösung
muss die **Abschneidung selbst behandeln**, nicht die Modell-Disziplin.

Eingebaut: **Auto-Continuation**. `chat_stream` erkennt eine abgeschnittene Antwort
und fordert das Modell automatisch zur Fortsetzung auf (bis 4×), bevor geparst
wird — die Teile werden zusammengefügt. Bewusst **zwei** Erkennungssignale:

1. `finish_reason == "length"` — das offizielle Token-Limit-Signal.
2. **Strukturcheck**: offener ```` ```action ````-Block ohne schließenden Fence.

Punkt 2 ist der Clou — und kam erst durch einen Einwand zustande: *Vielleicht hat
gar nicht das Token-Limit abgeschnitten, sondern ein Proxy die Verbindung gekappt.*
Genau. Bei einem Proxy-Abbruch kommt **gar kein** `finish_reason` (er bleibt
`None`) — Signal 1 würde das verfehlen. Der Strukturcheck fängt es trotzdem. Beide
Fälle sind mit simulierten Tests verifiziert (Token-Limit *und* `finish_reason
== None`), und gegen einen echten Endpoint (`max_tokens=8` → real
`finish_reason:"length"`).

Das ist die eigentliche Lehre des ganzen qwopus-Strangs: Erst ein Pflaster
(Prompt), dann die Frage „was, wenn es größer wird?", dann der Hinweis „könnte auch
der Proxy gewesen sein" — und am Ende eine Lösung, die *beide* Ursachen
größenunabhängig abdeckt, ohne je kaputtes JSON zu flicken. Gute Fehlerbehandlung
entsteht selten beim ersten Wurf, sondern indem man die Annahmen hinterfragt.

### 6.3 Vom Code zur laufenden App — und zurück zum Editor

Statische Checks (Syntax, JSON, Endpunkte) sind das eine; läuft die App auch? Also
ausprobiert: qwopus' CRUD-App wirklich gestartet — Flask-Backend hoch (Port 5000
war von macOS AirPlay belegt, also 5055), drei Kontakte per API angelegt, das
React-Frontend im Browser geladen. Ergebnis: **funktioniert** — Liste aus SQLite,
Anlegen, Bearbeiten (Formular füllt sich), Löschen, gestylte Oberfläche.

Der schönere Test kam danach: `mc` auf **dieselbe, bestehende App** loslassen mit
der Aufgabe, einen Footer `(c) qwopus 2026` und eine Erklär-Unterseite zu ergänzen.
qwopus3.6 hat `App.jsx` zuerst **gelesen** (nicht blind überschrieben), dann eine
Tab-Navigation über einen `useState`-Umschalter eingezogen (kein react-router, wie
gefordert), die „Über diese App"-Seite gebaut, den Footer gesetzt und das CSS in
`index.html` ergänzt — die bestehende CRUD-Logik blieb unangetastet. Im Browser
verifiziert: beide Tabs schalten um, Footer durchgehend sichtbar.

Das schließt den Kreis: dasselbe Mini-Tool, das die App erzeugt hat, kann sie auch
**chirurgisch weiterentwickeln** — lesen, verstehen, gezielt ändern. Genau das
unterscheidet einen Agenten von einem reinen Code-Generator. Und es bestätigt
nochmal die Modellwahl: qwopus liefert nicht nur schönen Code von null, es geht
auch sauber mit vorhandenem Code um.

### 6.4 Refactor: Komponenten, echtes Routing, ausführliche Hilfe

Der nächste Schritt war ein echter Umbau statt nur additiver Ergänzung: das
Frontend **aufteilen**, der Hilfe eine **eigene URL** geben und sie ausführlicher
machen. qwopus3.6 hat das in einem Durchlauf erledigt:

- `App.jsx` zur reinen **Router-Komponente** geschrumpft (Hash-Routing über
  `window.location.hash` + `hashchange`-Listener mit Cleanup, kein react-router).
- CRUD nach `PersonenView.jsx`, Hilfe nach `HilfeView.jsx` ausgelagert.
- Die Hilfe unter **`#/hilfe`** (eigene URL ≠ Startseite) zu einer richtigen
  Anleitung ausgebaut: „Was macht die App", Navigation inkl. URL-Erklärung,
  Schritt-für-Schritt für Anlegen/Bearbeiten/Löschen, Hinweis auf das
  Flask+SQLite-Backend.

Im Browser verifiziert: beide Routen schalten korrekt um, die URL ändert sich
sichtbar, Footer bleibt. Bemerkenswert: für diesen grundlegenden Umbau hat das
Modell `App.jsx` komplett neu geschrieben (write_file) statt `edit_file` — eine
vertretbare Entscheidung, weil sich die Datei fundamental ändert. `edit_file`
glänzt bei *punktuellen* Änderungen (wie der DELETE-404-Fix), nicht bei
Totalumbauten. Dass das Modell hier das richtige Werkzeug wählte, ist selbst ein
gutes Zeichen.

### 6.5 Kleinigkeit mit großer Wirkung: der Warte-Spinner

Bei lokalen Modellen vergehen zwischen Anfrage und erstem Token oft viele
Sekunden, in denen nichts passiert — man weiß nicht, ob es hängt. Ein
Spinner-Thread (`⠋ Modell denkt (7s)…`) füllt genau diese Lücke. Bewusst nur im
TTY aktiv (bei Pipe/Redirect/Hintergrundlauf passiv, sonst voller Steuerzeichen-
Müll in den Logs) und idempotent beendet. Klein, aber genau die Art Politur, die
ein Werkzeug von „funktioniert" zu „benutzt sich gut" hebt.

### 6.6 `edit_file`: nur die Stelle ändern, nicht die ganze Datei

Bis hierher schrieb `mc` bei jeder Änderung die **komplette** Datei neu — auch für
eine 3-Zeilen-Ergänzung wandern 200 Zeilen über die Leitung. Teuer an Tokens, und
genau das Truncation-Risiko von 6.1 in groß: je länger die Datei, desto eher reißt
der Stream ab. Die Antwort: eine **`edit_file`**-Aktion, die einen *exakten,
eindeutigen* Textausschnitt ersetzt — mit Eindeutigkeitsprüfung (Fehler bei 0 oder
mehreren Treffern), so wie es Cursor und Claude Code machen.

Zwei echte Tests mit qwopus3.6:

1. **Punktuelle Korrektur** — die DELETE-Route gab fälschlich immer `{ok:true}`
   zurück (kein 404). Auftrag: „ändere NUR diese Route mit edit_file". Ergebnis:
   sauber gepatcht (`cur.rowcount == 0 → 404`), per `curl` verifiziert.
2. **Additive Erweiterung** — zwei Abschnitte (Datenbank-Schema, API-Endpunkte) ans
   Ende der Hilfeseite einfügen. Log: **1× read_file, 1× edit_file, 0× write_file**
   — genau richtig. Die 3831-Zeichen-Datei wuchs auf 5271, der Rest blieb
   unangetastet.

Ehrliche Einordnung: **mechanisch perfekt** — gezieltes Einfügen, kein Full-Rewrite,
Datei intakt. Inhaltlich gab's aber eine kleine Schwäche: das Modell hängte einen
neuen „Datenbank"-Abschnitt an, ohne zu merken, dass schon ein „Datenbank &
Backend"-Abschnitt existierte → leichte Redundanz. Das ist kein Tool-Fehler (es tat
exakt das Verlangte: am Ende einfügen), sondern fehlende Kontext-Aufmerksamkeit des
Modells. Lektion: `edit_file` löst das *Mechanik*-Problem (Tokens, Truncation)
zuverlässig; ob die Änderung inhaltlich *klug* platziert ist, bleibt am Modell.
Beides zusammen — gezieltes Werkzeug **und** ein Modell, das den Bestand versteht —
macht erst einen guten Editier-Agenten.

### 6.7 Ornith-1.0: das agentische Modell — und der Bug, den es aufdeckte

Spät kam ein besonders passender Kandidat dazu: **Ornith-1.0-35B**, ein Modell, das
*speziell für agentisches Coding* trainiert wurde („Self-Scaffolding" — es lernt im
RL, sein eigenes Orchestrierungs-Gerüst mitzuerzeugen). Genau die Sorte Modell, die
ein Action-Protokoll diszipliniert treffen sollte.

Der erste Lauf: **0 Sekunden, 0 Dateien, leere Antwort.** Sieht aus wie ein
Totalausfall — war aber keiner. Im direkten Test generierte das Modell sauberen Code
(non-streaming *und* streaming). Der Unterschied lag in `mc`s Request. Systematisch
isoliert ergab sich: eine `system`-Message → das Modell generiert; **zwei
aufeinanderfolgende `system`-Messages** → es sendet sofort `data: [DONE]` ohne einen
einzigen Token. `mc` schickte aber genau zwei (den Action-Prompt und den
Projektüberblick als separate System-Nachrichten). Orniths Chat-Template verträgt
das nicht; alle bisherigen Modelle hatten es stillschweigend toleriert. Fix: beide
zu **einer** System-Message bündeln — universell verträglicher.

Mit dem Fix lief Ornith dann glänzend: **168 s, 6/6 Dateien, vollständige App** mit
allen vier Endpunkten und Edit-Funktion — der **schnellste lokale Volllauf des
ganzen Benchmarks**, schneller als qwen3-coder (593 s), gemma4 (261 s) und qwopus
(338 s). Und das, obwohl es ein Reasoning-Modell ist, das viel „denkt" (für ein
schlichtes „PONG" verbrauchte es ~250 Tokens). Die agentische Spezialisierung zeigt
sich: Es traf die Action-Blöcke sauber, ohne sie im Reasoning zu vergraben.

Die Lektion ist die schönste des ganzen Projekts: **Ein neues Modell ist der beste
Test für das eigene Werkzeug.** Ornith deckte einen Bug auf, der seit dem ersten Tag
schlummerte — zwei System-Messages, von jedem anderen Modell verziehen, von einem
strengeren Chat-Template gnadenlos bestraft. Hätte ich nie gefunden, ohne ein Modell
zu testen, das genau dort empfindlich ist.

---

## 7. Stromkosten-Rechnung (Mac mini M4 Pro)

- Apple-Spec: 155 W max. Dauerleistung; real unter Last gemessen ~65–95 W.
- Annahme LLM-Inferenz (GPU-lastig): ~90 W → ~0,00082 ct/s bei 0,33 €/kWh.
- `Kosten = Leistung(kW) × Dauer(h) × Strompreis`

| Lokaler Lauf | Dauer | Strom @ ~90 W |
|---|---:|---:|
| qwen3-coder:30b | 593 s | ~0,49 ct |
| gemma4:26b-mlx | 261 s | ~0,22 ct |

Quellen: Apple Support 103253, eclecticlight.co (M4 Pro Power), nextpit Mac-mini-Review.

---

## 8. Fazit

Ein nützliches Agenten-Tool braucht erstaunlich wenig: ~600 Zeilen Python, kein
Function-Calling, ein robustes Text-Protokoll. Der Engpass ist selten das Können
der Modelle, sondern ihre **Formdisziplin** — und genau da entscheidet das Tool
(kompakte Ausgabe-Blöcke, erzwungene Phasen) mehr als der Modellname.

Beste Allrounder im Test: **qwen3-coder:30b** und **gemma4:26b** (lokal),
**glm-5.2** / **deepseek-v4-pro** / **gemma-4-26b** (Cloud, schnell & günstig).
Der eigentliche Gewinner im Verlauf war aber **qwopus3.6**: zuerst die größte
verschenkte Chance (bester Code, ein Zeichen zu wenig), nach der Robustheits-Kur
dann das Modell, mit dem die ganze App entstand, lief und iterativ erweitert wurde.

Die wichtigste Erkenntnis steckt nicht in der Rangliste, sondern im Weg dorthin:
Jede echte Verbesserung — Auto-Continuation, `edit_file`, der Spinner — kam aus
einem konkreten Schmerz, nicht aus Vorausplanung. Und sie landete im **Tool**, nie
im Prompt. Ein Agent ist nur so gut wie seine Fähigkeit, mit den Unzulänglichkeiten
der Modelle umzugehen — abgeschnittene Antworten, vergessene Klammern, lange
Wartezeiten. Genau dort, nicht in der Code-Generierung, wird ein nützliches
Werkzeug gemacht.
