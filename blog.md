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

**Aber: Gegentests zogen die Euphorie gerade.** Derselbe Q3_K_L mehrfach
wiederholt — und das Bild wurde unruhig. Über **sechs** aufgezeichnete Läufe lagen
die geschriebenen Dateien bei **0, 4, 5, 6, 6 und 7** (letzteres 6 + eine
`vite.config.js` obendrauf), die Zeiten zwischen **97 s und 1007 s** — Faktor zehn.
Nur etwa die **Hälfte** der Läufe ergab eine vollständige App. Mal war ein
`write_files`-Block inhaltlich kaputtes JSON (`Expecting ':'`), das abgelehnt und
**nie nachgeliefert** wurde (das Modell erklärte sich in Prosa für fertig, ohne
`finish`); mal kam schlicht eine leere Antwort. Auto-Continuation half hier nicht,
weil das JSON nicht *abgeschnitten*, sondern *inhaltlich falsch* war — ein anderer
Fehlertyp.

Die Lehre ist deutlicher als erhofft: Der makellose erste Lauf (168 s, 6/6) war
**nicht repräsentativ, sondern das obere Ende**. Protokoll-Disziplin und
Vollständigkeit schwanken bei diesem Modell massiv von Lauf zu Lauf. **Ein
Single-Run-Benchmark lügt** — und genau deshalb sind tool-seitige Absicherungen
(Auto-Continuation, und als nächster Schritt eine *Validierung der geschriebenen
Dateien mit automatischem Retry*) kein Luxus, sondern das, was aus einem
unzuverlässigen Modell ein brauchbares Ergebnis macht.

Die Lektion ist die schönste des ganzen Projekts: **Ein neues Modell ist der beste
Test für das eigene Werkzeug.** Ornith deckte einen Bug auf, der seit dem ersten Tag
schlummerte — zwei System-Messages, von jedem anderen Modell verziehen, von einem
strengeren Chat-Template gnadenlos bestraft. Hätte ich nie gefunden, ohne ein Modell
zu testen, das genau dort empfindlich ist.

Nachgelegt: Die von Ornith gebaute App war beim Nachprüfen **funktional fehlerfrei**
— der volle CRUD-Zyklus lief per UI und API, und das Backend hatte als einziges
*sowohl* Eingabe-Validierung (400) *als auch* 404-Handling, das qwopus' „schönere"
App fehlte. Anschließend bekam Ornith denselben Erweiterungsauftrag wie qwopus
(Hilfeseite mit eigener URL via Hash-Routing): in einem Durchlauf umgesetzt
(`window.location.hash` + `hashchange`, Nav, ausführliche Hilfe, CRUD erhalten) —
auch hier griff der System-Message-Fix. Fazit zu Ornith: schnell, protokolltreu,
funktional sauber; beim Styling minimal (Inline-Styles statt CSS-Datei), aber
keineswegs nackt.

Am aufschlussreichsten war der **Werkzeug-Instinkt**: Beim Hilfe-Umbau (die ganze
`App.jsx` wird strukturell zur Router-Komponente) wählte Ornith `write_file` — eine
Vollneufassung, sinnvoll, weil sich die Datei fundamental ändert. Beim *nächsten*
Auftrag, die Datenbank in der Hilfe zu dokumentieren (rein additiv: ein Abschnitt
einfügen), wählte es auf die Anweisung hin sauber **`edit_file`** — gezieltes
Einfügen, und platzierte den Abschnitt klug zwischen „Navigation" und „Technische
Details", *ohne* die Redundanz, in die qwopus beim selben Auftrag gelaufen war
(zweiter „Datenbank"-Abschnitt neben einem bestehenden). Genau **das** ist der
Unterschied eines agentisch trainierten Modells: nicht nur Code schreiben, sondern
das *richtige Werkzeug* für die jeweilige Änderung wählen und den Bestand
respektieren. Werkzeug (`edit_file`) **und** Modellurteil griffen hier zum ersten
Mal perfekt ineinander.

#### Quant-Vergleich: wenn Kompression die Disziplin frisst

Die größeren Ornith-Quants (Q5/Q6/Q8 des 35B) passten nicht in die 24 GB — sie
brachen beim Laden sofort ab (Ollama meldet das als „context deadline exceeded",
faktisch ein OOM). Also wurde eine *stärker* komprimierte Variante getestet,
`IQ3_XS` (~14–15 GB, imatrix). Sie lud — und lieferte den direkten Beleg, was
aggressive Quantisierung kostet:

| | Q3_K_L | IQ3_XS |
|---|---:|---:|
| Zeit | 168 s | 234 s |
| Dateien | 6/6 | **5/6** (`package.json` fehlte) |
| Auto-Continuation | 0× | **3×** |
| Backend | Validierung + 404 | nur Validierung |

Die stärkere Kompression machte das Modell nicht nur langsamer, sondern **weniger
formdiszipliniert**: Antworten rissen dreimal mitten im Action-Block ab, und eine
Datei ging dabei ganz verloren. Bezeichnend: alle drei Abbrüche kamen mit
`finish_reason=stop` — also fing sie **nur der Strukturcheck** der Auto-Continuation
(offener ```` ```action ````-Block), nicht das offizielle Token-Limit-Signal. Genau
der Fall, für den das zweite Erkennungssignal eingebaut worden war. Ohne ihn hätte
`IQ3_XS` *null* Dateien geschrieben; mit ihm immerhin 5 von 6. Schöner geht der
Wert dieser Robustheits-Mechanik kaum zu zeigen — und zugleich die Lehre: **für
agentische Aufgaben lieber einen Hauch weniger Kompression**, denn das Erste, was
unter aggressivem Quant leidet, ist nicht die Sprache, sondern die *Genauigkeit*
beim Einhalten des Formats.

### 6.8 Der Gegenpol: Cloud = Zuverlässigkeit

Nach der lokalen Achterbahn der Kontrast: dieselbe CRUD-Aufgabe **fünfmal** mit
`google/gemma-4-26b-a4b-it` über OpenRouter. Ergebnis: **5 von 5 vollständig**
(6/6 Dateien), jedes Mal valides Backend mit allen vier Endpunkten (3 von 5 sogar
mit 404-Handling), **null** Validierungsfehler, in 26–189 s für je ~0,1–0,4 Cent.
Wo das lokale 35B zwischen 0/6 und 7 schwankte, lieferte das Cloud-Modell stur ab.

Und dieselbe Erweiterung wie bei den anderen — Hash-Routing mit eigener `#/hilfe`-
URL, Navigation, ausführliche Hilfe, Footer, CRUD erhalten — lief in einem
sauberen `read_file → write_file → finish` durch, ohne Auto-Continuation, ohne
Validierungsfehler, für 0,19 Cent. Im Browser verifiziert: beide Routen schalten
korrekt, Daten kommen live aus SQLite.

**Doch dann die Gegenprobe — dasselbe Gemma *lokal*:** `gemma4:26b-mlx` fünfmal
über den Mac mini. Ergebnis: ebenfalls **5 von 5 vollständig** (6/6), nur
langsamer — **285–492 s** (Faktor ~1,7 Zeit-Varianz) statt der Sekunden in der
Cloud. Wichtig: Die *Zeit* schwankt überall (Inferenz ist nie exakt gleich, hängt
an Last/KV-Cache), aber die *Vollständigkeit* war bei Gemma beidseits stabil 6/6 —
anders als bei Ornith, wo auch das Ergebnis selbst zwischen 0 und 7 Dateien
sprang. Damit fällt die einfache „Cloud = zuverlässig, lokal = wackelig"-These:
**Verlässlichkeit hängt am Modell, nicht am Ort.** Gemma liefert lokal *und* in der Cloud stur ab; Ornith schwankt lokal
massiv. Die Achterbahn war ein *Ornith*-Problem, kein *Lokal*-Problem.

Und hier zahlten sich die Robustheits-Mechaniken erstmals *sichtbar im Erfolg* aus:
Von den fünf lokalen Gemma-Läufen wurden **drei vom Tool gerettet** — bei zweien
schlug die **Validierung** an (eine geschriebene Datei war ungültig), das Modell
korrigierte sie nach der Rückmeldung, und der Lauf endete trotzdem mit sechs
*validen* Dateien; bei einem dritten fing die **Auto-Continuation** eine
abgeschnittene Antwort ab (das Modell merkte selbst an, es schreibe „nun in
kleineren Blöcken"). Ohne diese Netze wären drei der fünf Läufe unvollständig
gewesen — *mit* ihnen waren alle fünf komplett.

Noch ein Datenpunkt, der die These stützt: die **MoE-Variante**
`qwen3-coder-30B-A3B` (nur 3B aktive Parameter, UD-Q4-Quant). Die Hoffnung war
„wenig aktive Parameter = schnell". Realität auf 24 GB: **kein** Tempovorteil
(248–845 s, im Schnitt eher langsamer als das dichte 30B mit 593 s — der
Flaschenhals ist die Bandbreite/das Laden der vollen Gewichte, nicht die aktiven
Parameter) und über fünf Läufe nur **2/5 vollständig**, mit zwei *Totalausfällen*
(0 Dateien: JSON-Fehler, dann Prosa-„fertig" ohne `finish`). Bezeichnend: gegen so
einen kompletten Abbruch hilft auch die Robustheits-Mechanik nicht — es gab nichts
zu validieren und nichts fortzusetzen. Architektur-Tricks (MoE) ändern weder am
Tempo noch an der Verlässlichkeit etwas; beides bleibt eine Frage des konkreten
Modells.

Das ist der ehrliche Schlusspunkt des Modellteils: **Verlässlichkeit ist eine
Modell-Eigenschaft** — manche Modelle (Gemma) treffen das Protokoll stur, andere
(Ornith, qwen3-coder-A3B) schwanken stark, unabhängig von Cloud, lokal oder
MoE-Architektur. Die Cloud gewinnt vor
allem beim *Tempo* (Sekunden statt Minuten) und Komfort, für Centbruchteile; lokal
punktet mit offline/umsonst/Datenschutz. Und genau für die wackligen Modelle ist
die Tool-Mechanik (Auto-Continuation, Validierung+Retry, Rollback) das, was aus
„mal klappt's, mal nicht" ein verlässliches Ergebnis macht — wie die geretteten
drei Gemma-Läufe zeigen.

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

---

## 9. Ein zweiter Marathon: Hardware-Vergleich und ~20 Modelle gegen eine harte Regel

Die naheliegende Anschlussfrage nach dem ersten Benchmark: Wie sehr hängt das
Ergebnis eigentlich von der *Hardware* ab, und wie viele der theoretisch
verfügbaren Modelle halten einer echten Prüfung überhaupt stand, wenn man
nicht nach einem, sondern nach mehreren Läufen urteilt? Ein zweiter
Marathon-Tag mit zwei Mac-Rechnern (M1 Max 32 GB, Mac mini M4 Pro 16 GB im
LAN), drei gemieteten GPUs und am Ende rund zwanzig getesteten Modellen gab
darauf eine überraschend eindeutige Antwort.

### 9.1 GPUs mieten: die vast.ai-Lotterie

Dieselbe CRUD-Aufgabe auf gemieteten RTX 3090/4090/5090 laufen zu lassen
klang nach einer Nachmittagsübung. Tatsächlich ging der größte Teil der Zeit
in eine ganz andere Erkenntnis: **die Instanz-Lotterie schlägt die
Modell-Lotterie.** Für vier erfolgreiche Läufe wurden rund zehn Instanzen
gemietet — kaputte GPU-Durchreichung (`failed to inject CDI devices`),
Container mit permanent verweigertem SSH, ein Host, der mitten im
Modell-Download offline ging. Erst zwei Sicherungen machten den Prozess
verlässlich:

- **Reliability-Filter** (`reliability2 >= 0.98`) bei der Angebotssuche —
  filtert die schlechtesten Vermieter-Hosts von vornherein raus.
- **SSH-Probe vor der Nutzung**: eine Instanz gilt erst als „gesund", wenn sie
  nicht nur `running` meldet, sondern auch binnen 2 Minuten wirklich per SSH
  antwortet. Ein Host, der `running` sagt, aber nie eine SSH-Session zulässt,
  ist ein Totalausfall — nur eben einer, der ohne die Probe erst nach dem
  vollen Timeout auffliegt.

Ergebnis, sobald ein Host wirklich lief (`gemma4:26b`, GGUF, dieselbe
CRUD-Aufgabe):

| System | Beste CRUD-Zeit | Notiz |
|---|---:|---|
| RTX 5090 (guter Host) | 109 s | zweiter Host derselben GPU: 314 s — Faktor 3 Varianz! |
| Mac mini M4 Pro (16 GB, MLX) | 142 s | schlägt die eigene M1-Max-Schwester |
| MacBook M1 Max (32 GB, MLX) | 152 s | |
| RTX 4090 | 169 s | |
| RTX 3090 | 240 s | |

**Lektion:** Auf Mietplattformen ist die Host-zu-Host-Varianz bei *derselben*
GPU (109 s vs. 314 s, Faktor 3) mindestens so groß wie die Varianz zwischen
GPU-Generationen. Ein Einzellauf auf einer gemieteten Instanz sagt fast nichts
— erst der beste von mehreren Läufen ist aussagekräftig. Und: Apple Silicon
mit MLX-Builds ist für dieses Format überraschend konkurrenzfähig — der
kleine M4 Pro (16 GB) schlägt eine waschechte RTX 4090.

### 9.2 mc.py wird robuster: fünf neue Sicherheitsnetze

Aus den Fehlerbildern des Tages entstanden fünf gezielte Erweiterungen —
wieder nach der alten Regel: was zuverlässig passieren soll, gehört ins Tool,
nicht in den Prompt.

1. **`grep`-Aktion** — Inhaltssuche (`Datei:Zeile`) für Änderungen an
   Bestandscode, statt viele Dateien komplett zu lesen.
2. **`write_files`-Batch-Limit** (max. 3 Dateien pro Block) — **im Tool
   erzwungen**, nicht nur erbeten. Verifiziert im ersten Testlauf danach:
   ein Modell versuchte einen 4-Dateien-Block, bekam ihn abgelehnt, teilte
   selbst auf — und der Lauf war am Ende sauber 6/6.
3. **Finish-Verifikation** — beim `finish` prüft `mc` deterministisch, ob
   alle in der Aufgabe genannten Dateien existieren und valide sind. Fängt
   das „Prosa-fertig ohne geschriebene Dateien"-Muster ab, *sofern* das
   Modell überhaupt einen `finish`-Action-Block sendet (siehe 9.3.4 für die
   Lücke, die trotzdem noch offen blieb).
4. **Kontext-Beschneidung** — ältere Schritte werden auf Kurzfassungen
   reduziert (Dateiinhalte standen bis dahin doppelt in der Historie: einmal
   im Action-Block, einmal im Tool-Ergebnis). Härtetest mit
   `--keep-context 1`: Modell sah ab Schritt 3 nur noch Kurzfassungen seiner
   eigenen Arbeit — lieferte trotzdem 6/6 mit korrekter FE↔BE-Konsistenz
   (Feldnamen, Port), weil der Aufgabentext selbst nie gekürzt wird.
5. **Fence-Modus** (`--fence`) — der große Wurf gegen die häufigste
   Fehlerklasse des ersten Benchmarks: Escaping-Fehler beim Verpacken ganzer
   Dateien in JSON-Strings. Im Fence-Modus enthält der Action-Block nur
   Metadaten, der Dateiinhalt folgt roh in einem ` ```content `-Block danach
   — das Format, auf das Modelle am besten trainiert sind. Erster
   Praxislauf: 7 content-Blöcke, **0** JSON-Escaping-Fehler, 6/6 Dateien.
   Bewusst **opt-in**, weil der Parser beide Formate ohnehin gleichzeitig
   versteht und der Nutzen erst über mehrere Läufe hinweg belegt werden
   sollte statt per Bauchgefühl zum Default zu werden.

### 9.3 Der Modell-Marathon und die 400-Sekunden-Regel

Der eigentliche Kern des Tages: rund zwanzig Modelle — von winzigen
Gemma-Varianten (E2B/E4B) bis zu experimentellen HuggingFace-Community-
Finetunes („heretic", diverse Custom-Quants) — gegen dieselbe CRUD-Aufgabe,
mit einer vom Nutzer eingeführten, schonungslos einfachen Regel: **jeder
Modell-Erfolg, der länger als 400 Sekunden braucht, gilt als Schrott und
wird gelöscht — unabhängig von der sonstigen Erfolgsquote.**

Das sortierte radikal aus. Übrig blieben am Ende nur drei bis vier
Kandidaten von zwanzig:

| Modell | Beste Zeit | Erfolgsquote | Urteil |
|---|---:|:---:|---|
| **gemma4:26b** (MLX/GGUF) | 138–286 s | 6/6 über beide Maschinen | ✅ einziges durchgehend zuverlässiges Modell |
| **Qwopus3.6-27B** | 320–371 s | 2/3 | ✅ behalten |
| **Ornith-1.0-35B** | 69–92 s | 1/3, aber extrem schnell | ⚠️ behalten als Tempo-Kandidat |
| qwen3.6:27b-mlx | 379 s (bester Erfolg) | 2/3 | ⚠️ knapp bestanden, grenzwertig |

Gelöscht wurden — aus ganz unterschiedlichen Gründen — u. a.:
`gemma4:e2b`/`e4b` (riesiger Token-Overhead, 33–50 % Erfolg trotz kleinerer
Modellgröße), `gemma4:12b-mlx` (JSON-Bug + Endlosschleife),
`qwen3.6:35b-mlx` (Swap-Thrashing), `qwen3-coder:30b` und
`Qwen3-Coder-30B-A3B` (funktionierten, aber 515–1200 s — nach der 400 s-Regel
trotzdem raus), `DeepSeek-R1-Distill-14B` (0 von 3 Läufen vollständig),
`Qwable-5-27B-Coder` und der `heretic`-Finetune, sowie `Qwen3.6-27B-MTP`
(1/3, mit gleich zwei unterschiedlichen Fehlerarten).

**Vier neue Fehlerklassen, die der erste Benchmark noch nicht kannte:**

1. **Regel-Verletzung trotz expliziter Anweisung.** `gemma4:e2b` bekam den
   Auftrag „KEINE npm- oder pip-Installation" — und führte trotzdem
   `npm create vite@latest` und `npm install` aus. Ergebnis technisch
   sogar vollständig (der Finish-Check hatte eine fehlende Datei
   nachgefordert), aber mit 13 ungebetenen Vite-Gerüst-Dateien im Schlepptau.
2. **Speicher-Kapazitätsgrenze, kein Modellfehler.** `qwen3.6:35b-mlx`
   (21 GB) lud mit Ollamas 128k-Kontext-Default — auf einem 32-GB-Rechner
   ergab das **23,4 von 24 GB belegten Swap**. Bestätigt per
   `memory_pressure`/`vm.swapusage`, nicht geraten. Das Modell selbst war
   nicht kaputt, die Kombination aus Modellgröße und Kontextfenster war es.
3. **„Prosa-fertig" umgeht den neuen Finish-Check.** Ein Modell schrieb nur
   eine von sechs Dateien echt, behauptete dann in reinem Fließtext (ohne
   jeden Action-Block), alles sei fertig. Weil `mc.py` bei *fehlendem*
   Action-Block sofort den Task beendet (`if action is None: return reply`),
   griff die eigens gebaute Finish-Verifikation gar nicht — die prüft nur,
   wenn das Modell tatsächlich ein `finish` sendet. Eine Lücke, die live im
   Test auffiel und noch offen ist.
4. **Falsches Fence-Label statt falscher Inhalt.** `Qwen3.6-27B-MTP` schrieb
   einen inhaltlich einwandfreien, valide geparsten JSON-Action-Block — aber
   in einen ` ```json `-Fence statt ` ```action `. Der Parser sucht per Regex
   gezielt nach `action`, hat den Block also schlicht nicht gesehen. Anders
   als das Escaping-Problem aus Abschnitt 6 ist hier nicht der *Inhalt*
   kaputt, sondern nur das *Label* — eine dritte, komplett neue
   Fehlerdimension.

### 9.4 Eine externe Bestätigung: wir sind nicht allein

Mitten im Marathon fiel die Frage: *Ist es nicht komisch, dass so wenige
Modelle überhaupt funktionieren?* Ein unabhängiger Vergleich
([glukhov.org, OpenCode-LLM-Vergleich](https://www.glukhov.org/ai-devtools/opencode/llms-comparison/))
mit einem komplett anderen Agenten-Tool kam praktisch auf dieselbe Quote:
6 von 25 Modellen (24 %) funktionierten gut — bei uns etwa 3–4 von 20
(15–20 %). Auffälligster Parallel-Fund: **derselbe 27B-Kandidat lief mit
einem Quant bei 100 % Fehlerquote, mit einem anderen Quant desselben
Anbieters bei nur 5 %** — praktisch deckungsgleich mit unserem eigenen
Ornith-Befund (Q3_K_L vs. IQ3_XS) aus Abschnitt 6.7. Zwei unabhängige Tools,
zwei unabhängige Testreihen, derselbe Befund: **Quantisierung frisst zuerst
Formatdisziplin, nicht Intelligenz** — und die meisten verfügbaren Modelle
scheitern nicht an der Aufgabe, sondern am Protokoll drumherum.

Ein Unterschied lohnt die Erwähnung: Der externe Vergleich schließt explizit
„Tool-Calling-Qualität ist wichtiger als reine Geschwindigkeit" — fast das
Gegenteil der 400-Sekunden-Regel dieses Tages, die auch *funktionierende*
Modelle (`qwen3-coder:30b`, `Qwable` auf einem Host) allein wegen der Zeit
aussortierte. Beide Haltungen sind legitim; welche zählt, hängt schlicht
davon ab, ob einem Wartezeit oder Korrektheit wichtiger ist.

### 9.5 Technische Fußnoten, die trotzdem Zeit kosteten

Keine davon hat mit LLMs zu tun — trotzdem hat jede einzelne für handfeste
Verzögerungen gesorgt:

- **macOS liefert Bash 3.2 aus**, nicht 4+. `declare -A` (assoziative
  Arrays) bricht mit „invalid option" ab, aber eben nicht laut genug, um
  sofort aufzufallen — ein Batch-Skript lief minutenlang mit vertauschten
  Modellnamen, bevor der Fehler auffiel. Fix: zwei parallele indizierte
  Arrays statt einer Map. `bash -n script.sh` vor jedem Start prüfen.
- **macOS hat kein GNU `timeout`.** Jeder Batch, der `timeout 1200 …`
  nutzte, scheiterte sofort mit `command not found` — nicht offensichtlich,
  weil die Fehlermeldung im Log unterging. Ersatz: ein Bash-Wrapper aus
  Hintergrundprozess + `kill -TERM` nach Ablauf der Frist.
- **PATH-Fallstricke in nicht-interaktiven Shells.** `ollama` und `python3`
  „nicht gefunden" trotz funktionierendem interaktivem Terminal — nohup-
  Hintergrundprozesse erben nicht automatisch den vollen PATH. Immer mit
  absolutem Pfad (`/usr/local/bin/ollama`) statt bloßem Kommandonamen
  arbeiten, sobald ein Skript nicht-interaktiv läuft.
- **Sehr lange Hintergrund-Tool-Aufrufe können ohne Fehlermeldung enden.**
  Ein Batch-Skript wurde nach einiger Laufzeit lautlos beendet (0-Byte-Log,
  „was stopped"), obwohl nichts im Skript selbst dafür sprach. Robuste
  Lösung: lange Läufe immer mit `nohup … & disown` **innerhalb** der
  Shell starten, nicht nur auf die Hintergrund-Ausführung des Werkzeugs
  selbst verlassen — dieselbe Lektion, die SSH-Batches auf der Miet-GPU von
  Anfang an befolgten und die dort stundenlang stabil liefen.
- **Ein hängender Download muss nicht am eigenen Netz liegen.** Ein
  Modell-Pull blieb zweimal exakt an derselben Datei bei „context deadline
  exceeded" stehen. Ein roher `curl -v` auf genau diese URL zeigte: TLS-
  Verbindung steht, Anfrage wird gesendet, **0 Bytes Antwort nach 40
  Sekunden** — ein serverseitiges Problem bei Huggingface, kein Client-
  Fehler. Ohne den direkten `curl`-Test hätte man leicht am eigenen Setup
  gesucht.

### 9.6 Die Cloud-Gegenprobe: OpenRouter

Nach dem eher mageren lokalen Ergebnis (3–4 von rund 20 Modellen brauchbar)
lag die Gegenprobe nahe: Wie schneiden güns­tige Cloud-Modelle bei derselben
Aufgabe ab, wenn die Hardware nicht mehr limitiert? Zwölf Modelle über
OpenRouter, ausgewählt nach Rang auf den [OpenRouter-Rankings](https://openrouter.ai/rankings)
plus ein paar gezielte Ergänzungen (u. a. Codestral als dediziertes
Mistral-Coding-Modell), je ein Screening-Lauf:

| Modell | Zeit | Kosten | Parameter |
|---|---:|---:|---|
| **z-ai/glm-5.2** | 12 s | $0.0265 | 115B |
| **mistralai/codestral-2508** | 29 s | $0.0054 | dediziertes Coder-Modell |
| **stepfun/step-3.7-flash** | 29 s | $0.0054 | – |
| **minimax/minimax-m3** | 49 s | $0.0079 | 157B |
| **deepseek/deepseek-v4-pro** | 50 s | $0.0172 | 91B |
| mistralai/mixtral-8x22b-instruct | 51 s | $0.1224 ⚠️ | 8×22B |
| **openai/gpt-oss-120b** | 64 s | $0.0015 | 120B |
| **xiaomi/mimo-v2.5** | 88 s | $0.0027 | 109B |
| **tencent/hy3-preview** | 151 s | $0.0040 | 130B |
| **deepseek/deepseek-v4-flash** | 223 s | $0.0024 | 235B |
| **qwen/qwen3-235b-a22b-2507** | 306 s | $0.0059 | 235B (22B aktiv) |
| mistralai/mistral-small-24b | – | HTTP 429, unentschieden | 24B |

**11 von 12 lieferten 6/6 Dateien — jedes einzelne davon deutlich unter der
400-Sekunden-Grenze.** Der einzige Ausreißer (`mistral-small-24b`) scheiterte
nicht am Modell, sondern zweimal in Folge an einem echten
Infrastruktur-Rate-Limit beim Upstream-Provider — bestätigt durch die
HTTP-429-Fehlermeldung im Log, kein Formatfehler.

Das ist der schärfste Kontrast des ganzen Tages: **~15–20 % Erfolgsquote
lokal gegen ~92 % in der Cloud**, bei Kosten von großteils unter einem Cent
pro Lauf. Zwei Dinge lohnen die Einordnung, damit daraus keine falsche
Schlussfolgerung wird:

- **Es ist kein fairer Vergleich derselben Modelle.** Die Cloud-Kandidaten
  sind überwiegend große, gut betreute Flaggschiff-Deployments ohne
  aggressive Consumer-Quantisierung — genau die Kombination, von der
  Abschnitt 9.3 und der externe Vergleich zeigen, dass sie Formatdisziplin
  kostet. Der faire Vergleich ist nicht „Cloud schlägt lokal", sondern
  „unquantisierte/kaum quantisierte Modelle schlagen aggressiv quantisierte
  Consumer-Varianten" — Cloud ist nur der bequemste Weg, an Erstere zu
  kommen.
- **Der lokale gpt-oss-Fall kippt in der Cloud komplett.** `gpt-oss:20b`
  scheiterte lokal (Abschnitt 5, Blog-Ersttest) mit einer komplett leeren
  Antwort — Reasoning-Modelle geben ihren Denk-Kanal über Ollamas lokale
  `/v1`-Schicht oft nicht im sichtbaren `content`-Feld aus. Dieselbe
  Modellfamilie (`openai/gpt-oss-120b`) lief über OpenRouter in 64 Sekunden
  sauber durch — der Cloud-Provider surfaced den Content offenbar korrekt.
  Ein Modell, zwei Zugangswege, zwei völlig unterschiedliche Ergebnisse.
- **Der Ausreißer bei den Kosten kam nicht vom Modell allein.** `mixtral-8x22b`
  brauchte mit 124.297 Prompt-Tokens rund das Zehnfache aller anderen
  Kandidaten (10–35k) — ein `write_files`-Block mit 4 Dateien wurde vom
  Batch-Limit abgelehnt, der nötige Korrekturschritt plus ein für diese
  Textmenge ungewöhnlich ineffizienter Tokenizer trieben die Rechnung auf
  $0.12. Erfolgreich (6/6), aber zwanzigmal teurer als der Median.

**Wie groß wären diese Modelle eigentlich lokal?** Bei Mixture-of-Experts-
Architekturen (die meisten hier) zählt für den RAM-Bedarf die
**Gesamtparameterzahl**, nicht die „aktiven" Parameter — alle Experten müssen
im Speicher liegen, unabhängig davon, wie viele pro Token tatsächlich rechnen:

| Modell | Parameter | RAM bei Q4 (praxistauglich) | Passt auf 32 GB (M1 Max)? |
|---|---:|---:|:---:|
| `mistral-small-24b` | 24B | ~13 GB | ✅ |
| `codestral-2508` | ~22B | ~12 GB | ⚠️ theoretisch ja — **aber API-exklusiv**, keine offenen Gewichte verfügbar |
| `deepseek-v4-pro` | 91,2B | ~50 GB | ❌ |
| `gpt-oss-120b` | 120B | ~66 GB | ❌ |
| `glm-5.2` | 115B | ~63 GB | ❌ |
| `mimo-v2.5` | 109B | ~60 GB | ❌ |
| `hy3-preview` | 130B | ~72 GB | ❌ |
| `minimax-m3` | 157B | ~86 GB | ❌ |
| `mixtral-8x22b` (8×22B) | 176B | ~97 GB | ❌ |
| `deepseek-v4-flash` / `qwen3-235b-a22b` | 235B | ~129 GB | ❌ |

**Nur 1 der 12 Kandidaten wäre auf einem Consumer-Mac überhaupt ladbar** —
`mistral-small-24b`. `codestral-2508` passt zwar von der Größe her, ist aber
**API-exklusiv**: Mistral hat für diese Version keine offenen Gewichte
veröffentlicht, RAM-Rechnung hin oder her. Der Rest bräuchte selbst bei
aggressiver Quantisierung 50–129 GB RAM: Mac-Studio-Ultra-Territorium oder
mehrere High-End-GPUs, nicht ein einzelner Consumer-Rechner. Das ist die
eigentliche Erklärung hinter dem 92%-vs-15%-Graben aus Abschnitt 9.3: die
Cloud-Modelle sind nicht „klüger trainiert" — sie sind schlicht 5- bis
15-mal größer als alles, was lokal überhaupt in den Speicher passt, und
laufen dort typischerweise kaum bis gar nicht quantisiert.

**Die Gegenprobe: passt ≠ funktioniert.** `codestral:22b` und
`mistral-small:24b` liefen lokal (Ollama-Library, Q4_K_M) — mit
ernüchterndem Ergebnis, **0 von 3 Läufen** bei beiden:

- `codestral:22b` schrieb **wörtliche Platzhalter statt echtem Code**
  (`"from flask import Flask, request ... # rest of your app.py code"`) und
  erklärte dem Nutzer unaufgefordert, er solle die Platzhalter selbst durch
  echten Code ersetzen — ein fundamentales Missverständnis der Aufgabe, kein
  Formatfehler. Grund: Ollamas offizielles `codestral`-Tag zeigt auf **v0.1**,
  Mistrals Originalversion von 2024 — die über OpenRouter getestete
  `codestral-2508` ist nicht nur neuer, sondern **API-exklusiv**: Mistral hat
  dafür nie offene Gewichte veröffentlicht. Ein lokaler Nachbau war also von
  vornherein unmöglich, nicht nur unwahrscheinlich. „Gleicher Name" heißt
  hier nicht „gleiches Modell" — teils heißt es sogar „gibt es lokal gar
  nicht".
- `mistral-small:24b` brauchte im einzigen abgeschlossenen Lauf **1004
  Sekunden** und schrieb am Ende **0 von 6 Dateien** — es wiederholte in
  allen zehn Schritten denselben JSON-Escaping-Fehler, ohne ihn je zu
  korrigieren, obwohl das Tool ihn jedes Mal exakt benannte.

**Lektion:** Die RAM-Rechnung sagt nur, ob ein Modell *technisch ladbar*
ist — nichts darüber, ob die *lokal verfügbare Version* mit der in der
Cloud getesteten identisch ist, und nichts über Formatdisziplin. Wer einen
Cloud-Befund lokal nachstellen will, muss zuerst die tatsächliche
Modellversion hinter dem Ollama-Tag prüfen (Datum, Digest, Quant) — sonst
vergleicht man zwei verschiedene Modelle unter demselben Namen.

**Nachtrag: der Kontext-Fensterknoten.** Vier weitere Kandidaten
(`devstral:24b`, zwei „abliterated"/„OBLITERATED"-Uncensoring-Finetunes und
ein Devstral-Import bei Q6_K) scheiterten zunächst noch drastischer — nicht
mal ein triviales „PONG" kam binnen 45–60 Sekunden zurück. Grund: Ollama
setzt für frisch importierte GGUF-Modelle automatisch ein sehr großes
Kontextfenster (128k, teils sogar bei nur 14-GB-Dateien) — der KV-Cache
dafür ließ den tatsächlichen RAM-Bedarf auf 31–41 GB explodieren, weit über
das Dateigewicht hinaus, mit sichtbarem CPU/GPU-Split als Symptom. Der
Reparaturversuch — ein eigenes Modelfile mit `PARAMETER num_ctx 16384` via
`ollama create` — behob das Kapazitätsproblem tatsächlich: drei der vier
antworteten danach normal, eines (`huihui-devstral2-24b`) durchlief sogar
die komplette CRUD-Aufgabe **vollständig** (6/6 Dateien). Nur eben in
**859 Sekunden** — mehr als doppelt so lang wie die 400-Sekunden-Grenze.
**Lektion:** Ein reduziertes Kontextfenster kann ein „antwortet gar nicht"
in ein „arbeitet korrekt" verwandeln — aber es macht aus einem
speichergedrängten 24B-Modell auf Consumer-Hardware kein schnelles. Kapazität
und Tempo sind zwei verschiedene Probleme mit zwei verschiedenen Lösungen;
eines zu beheben, behebt das andere nicht automatisch mit.

**Lektion:** Bei Cloud-APIs zahlt sich die Investition in ein robustes
Protokoll-Tool doppelt aus — nicht weil Cloud-Modelle das Format öfter
brechen (tun sie kaum), sondern weil ein einzelner Ausreißer wie Mixtral
sofort sichtbar macht, wo das Tool eingreift und wo nicht. Und: bei
Centbeträgen pro Lauf lohnt sich für den produktiven Einsatz kaum noch die
stundenlange lokale Fehlersuche von Abschnitt 9.3 — außer Offline-Betrieb
oder Datenschutz sind harte Anforderungen.

#### Kosten vs. Geschwindigkeit: korrelieren kaum

Ein Streudiagramm der elf erfolgreichen Läufe (Kosten × Zeit, beide
logarithmisch) räumt mit der naheliegenden Annahme auf, „billig = langsam"
oder „schnell = teuer" seien verlässliche Faustregeln:

| Modell | Zeit | Kosten | Einordnung |
|---|---:|---:|---|
| `openai/gpt-oss-120b` | 64 s | **$0.0015** | **Gesamtsieger** — am günstigsten *und* ordentlich schnell |
| `mistralai/codestral-2508` | 29 s | $0.0054 | schnell *und* günstig — dominiert `qwen3-235b` klar |
| `stepfun/step-3.7-flash` | 29 s | $0.0054 | dito |
| `z-ai/glm-5.2` | **12 s** | $0.0265 | am schnellsten, aber Aufpreis dafür — höherer Pro-Token-Preis lohnt sich hier trotzdem, weil wenig generiert wird |
| `qwen/qwen3-235b-a22b-2507` | 306 s | $0.0059 | **strikt dominiert**: langsamer *und* teurer als Codestral/Step — kein Kompromiss, einfach schlechter auf beiden Achsen |
| `mistralai/mixtral-8x22b` | 51 s | $0.1224 | Ausreißer, kein Modell-Merkmal (siehe oben: Batch-Limit-Korrektur + ineffizienter Tokenizer) |

**Lektion:** Der Pro-Token-Preis eines Modells sagt fast nichts über die
tatsächlichen Kosten *einer Aufgabe* aus — die hängen von der Tokenmenge ab,
und die wiederum von Fehlerquote und Antwortlänge, nicht vom Preisschild.
`qwen3-235b-a22b` wird von zwei anderen Modellen auf *beiden* Achsen
gleichzeitig geschlagen (schneller **und** günstiger) — bei so einem Befund
lohnt sich kein Kompromiss-Argument mehr, das Modell ist schlicht dominiert.
Umgekehrt zeigt `gpt-oss-120b`: das güns­tigste Modell muss nicht das
langsamste sein — hier fallen niedriger Preis und brauchbares Tempo
zusammen.

### 9.7 Die entscheidende Kontrollfrage: Liegt es an der Größe?

Nach einem Tag voller großer Cloud-Modelle (91B–235B) blieb eine Lücke: Wir
hatten nie ein wirklich *kleines* Modell unter fairen Cloud-Bedingungen
(volle Präzision, gutes Serving) getestet. Genau das trennt zwei
Erklärungen, die den ganzen Tag über verschwommen nebeneinander standen —
„Cloud-Modelle sind einfach größer" versus „lokale Quantisierung zerstört
Formatdisziplin". Fünf kleine (8B–24B) Modelle über OpenRouter, je ein
Screening-Lauf, beantworteten das eindeutig:

| Modell | Cloud-Ergebnis | Lokales Ergebnis (selbes/verwandtes Modell) |
|---|---|---|
| `openai/gpt-oss-20b` | ✅ 41 s, 6/6, $0.0014 | ❌ lokal `gpt-oss:20b`: leere Antwort, 0 Dateien (Abschnitt 5) |
| `mistralai/mistral-small-24b` | ✅ 64 s, 6/6, $0.0017 | ❌ lokal `mistral-small:24b`: 1004 s, 0/6, derselbe JSON-Fehler zehnmal wiederholt (9.6) |
| `google/gemma-3-12b-it` | ✅ 124 s, 6/6, $0.0027 | ❌ lokal `gemma3:12b`: 0 Dateien, ungültiges JSON (Abschnitt 5, Original-Benchmark) |
| `qwen/qwen3-14b` | ✅ 317 s, 6/6 (+1 Extra), $0.0103 | — (nicht lokal getestet) |
| `qwen/qwen3-8b` | ⏸️ HTTP 429 (Rate-Limit „Alibaba") nach 3 valider Dateien | — unentschieden, kein Modellfehler |

**Drei von fünf sind exakt dieselben oder direkt verwandte Modelle, die
heute bereits lokal gescheitert waren — und alle drei liefen in der Cloud
tadellos, bei gleicher oder sogar kleinerer Parametergröße.** Das ist die
sauberste kontrollierte Beobachtung des gesamten Tages: Modellgröße scheidet
als Erklärung aus. `gpt-oss-20b` ist besonders eindeutig — exakt dasselbe
Modell, nur der Zugangsweg unterscheidet sich, und das Ergebnis kippt von
„nichts" zu „vollständig in 41 Sekunden".

**Was übrig bleibt, sind die beiden bereits vermuteten Ursachen:**
Quantisierung (Q4-Consumer-Gewichte statt cloud-typischem FP16/FP8) und
Serving-Schicht (`gpt-oss`s Reasoning-Kanal kommt über Ollamas lokale `/v1`
nicht im sichtbaren `content`-Feld an, über OpenRouter schon). Ein 12–24B-
Modell reicht als *Fähigkeit* völlig aus, um die CRUD-Aufgabe zu lösen — das
belegen alle vier funktionierenden Cloud-Läufe hier eindrucksvoll, drei
davon sogar unter 130 Sekunden. Es scheitert lokal nicht an Intelligenz,
sondern an der Kombination aus Kompression und Infrastruktur.

**Lektion, die den ganzen Tag zusammenfasst:** Die Frage „welches Modell
sollte ich benutzen" ist unvollständig ohne die Zusatzfrage „auf welcher
Infrastruktur". Dasselbe Modell kann an einem Nachmittag beides sein — ein
kompletter Totalausfall und eine 41-Sekunden-Erfolgsgeschichte —, je
nachdem, wie stark es komprimiert wurde und ob die Serving-Schicht seinen
vollen Output tatsächlich durchreicht.

### 9.8 Noch eine Serving-Schicht: LM Studio auf derselben Maschine

Als letzte Variable des Tages: LM Studio, parallel zu Ollama auf demselben
Mac installiert, unter einer LAN-IP erreichbar. Erste, unangenehme
Entdeckung per `ifconfig`: **diese LAN-IP war die eigene Maschine** — LM
Studio und Ollama teilen sich denselben 32-GB-Speicherpool. Ein Vorab-Check
gegen LM Studio, während im Hintergrund noch ein Ollama-Batch lief, hat
prompt zwei Testläufe kontaminiert (auffällig kurze Totalausfälle statt der
erwarteten Ergebnisse). **Lektion: zwei lokale Inferenz-Server auf einer
Maschine sind kein „mehr Kapazität", sondern ein gemeinsamer, ehrlicherweise
unsichtbarer Wettbewerb um denselben RAM.** Ab da strikt seriell getestet.

**Ein Kontrast, der auffiel:** Wo Ollama beim zu großen Kontextfenster
stillschweigend in Swap-Thrashing abrutschte (Abschnitt 9, Devstral-Fall),
**verweigerte LM Studio das Laden aktiv** mit einer klaren Fehlermeldung
(„Model loading was stopped due to insufficient system resources"), sobald
ein Modell (Mistral-Small 3.2, 6-bit) zu groß für den verfügbaren Speicher
war. Kein stiller Fehlschlag, sondern ein sofortiger, verständlicher
Abbruch — deutlich nutzerfreundlicher.

**Der eigentliche Fund: natives MLX ist spürbar schneller als GGUF/llama.cpp
für dasselbe Modell.** `Devstral-Small-2-24B` lief:
- über Ollama/GGUF (Q4_K_M, mit `num_ctx`-Fix aus Abschnitt 9): **859 s**
- über LM Studio/MLX (4-bit, natives Format): **483 s**

Fast doppelt so schnell bei vergleichbarer Quantisierungsstufe — beide 6/6
Dateien vollständig, `483 s` reißt die 400-Sekunden-Grenze aber immer noch.

**Die komplette MLX-Runde über LM Studio:**

| Modell | Quant | Ergebnis | 400s-Urteil |
|---|---|---|:---:|
| `mistral-small-3.2-24b` | 6-bit | ❌ Ladeverweigerung — LM Studios eigener Sicherheitscheck lehnte ab, bevor Speicher überlaufen konnte | — |
| `mistralai/devstral-small-2-2512` | 4-bit | ✅ 483 s, 6/6 | ❌ knapp drüber |
| **`qwen/qwen3.6-27b`** | 4-bit | ✅ **390 s, 6/6** | ✅ **bestanden** |
| `openai/gpt-oss-20b` | MXFP4 | ⚠️ 244 s, 2/6 — derselbe JSON-Fehler zweimal unkorrigiert wiederholt, dann ein vom Batch-Limit abgelehnter 6-Dateien-Block | unentschieden |

**`qwen/qwen3.6-27b` ist der einzige klare Gewinner dieser Runde** — vollständig
und unter der Grenze. Damit gesellt es sich zu `gemma4:26b-mlx`, `Qwopus3.6-27B`
und `Ornith-1.0-35B` als vierter tatsächlich brauchbarer Kandidat des gesamten
Tages, bemerkenswert stabiler als dieselbe Modellfamilie über Ollama (dort 2/3,
mit einer leeren Reasoning-Antwort unterwegs, siehe 9.3).

`gpt-oss-20b` bleibt über alle drei Serving-Wege hinweg das unklarste Bild des
Tages: makellos über OpenRouter (41 s, 6/6), inkonsistent über Ollama (Teilerfolg
plus leere Antworten), jetzt teilweise über LM Studio (2/6 mit wiederholten
JSON-Fehlern). Kein sauberer Erfolg, aber auch kein reiner Totalausfall mehr wie
ursprünglich im Abschnitt-5-Benchmark angenommen — eher ein Modell mit spürbar
schwankender Formdisziplin, die je nach Serving-Weg unterschiedlich oft auffliegt.

**Fazit:** Für dieselbe Modellklasse auf Apple Silicon ist die Serving-Software
selbst eine messbare Variable — nicht nur Quantisierung und Modellwahl. MLX über
LM Studio schlägt GGUF über Ollama beim Tempo spürbar (Devstral: 483 s vs. 859 s)
und bei der Zuverlässigkeit (Qwen3.6-27B: 390 s/6-6 vs. 2/3 mit Aussetzer), auch
wenn die 400-Sekunden-Latte für 24B-Modelle auf diesem Rechner insgesamt hoch
bleibt.

### 9.9 Zwei weitere Lektionen: Systemspeicher und neue Fehlerbilder

Eine Nachladerunde mit neun weiteren LM-Studio-Modellen (Ornith in mehreren
Größen/Quants, ein dediziertes Coder-Modell, eine komplett andere Architektur)
brachte zwei zusätzliche Erkenntnisse — eine über Infrastruktur, eine über
Modellverhalten.

**Speicherdruck ist nicht nur „welches Modell ist geladen".** `ornith-1.0-35b-mlx`
(20 GB) wurde von LM Studios eigenem Sicherheitscheck zweimal verweigert — auch
nachdem das vorher getestete `gemma-4-e2b` per Idle-Timeout automatisch entladen
worden war. `memory_pressure` zeigte die Ursache: nur ~627 MB echtes „Free" bei
32 GB Gesamt-RAM, verursacht durch die Summe aller **gleichzeitig laufenden
Anwendungen** — mehrere Chrome-Instanzen (inkl. Chrome Canary) mit etlichen
Tabs/Renderer-Prozessen, LM Studios eigene Electron-Oberfläche, dazu die
laufende Coding-Session selbst. Nach dem Schließen beider Chrome-Varianten
stieg der freie Speicher spürbar, reichte aber immer noch nicht für die vollen
20 GB. **Lektion:** Auf einem geteilten Consumer-Rechner ist der verfügbare
LLM-Speicher nicht `RAM_total − Modellgröße`, sondern
`RAM_total − Modellgröße − alles andere, was gerade offen ist` — Browser-Tabs
zählen im Ernstfall mit.

**Ein Rätsel blieb `ornith-1.0-9b`:** Drei verschiedene Quantisierungen von drei
verschiedenen Publishern (4-bit, 6-bit, MXFP8) wurden geladen; die erste
(`mlx-community`, 4-bit) scheiterte konsistent mit einer generischen
Ladefehlermeldung — auffällig, weil LM Studio den Eintrag als `"type": "vlm"`
(Vision-Language-Model) klassifizierte, obwohl Ornith ein reines Text-Coding-
Modell ist. Ob Metadaten-Fehler in der Konvertierung oder echtes
Kompatibilitätsproblem: ohne tieferen Dateizugriff nicht abschließend klärbar.

**Ein neuer Fehlertyp: die Wiederholungsschleife.** `liquid/lfm2-24b-a2b` (andere
Architektur, Liquid Foundation Models) scheiterte auf eine Art, die der ganze
Tag noch nicht gezeigt hatte — kein JSON-Fehler, keine leere Antwort, sondern
eine **degenerierte Wiederholungsschleife**: derselbe deutsche Satz
(„Damit ist die komplette Einrichtung abgeschlossen…") am Stück, bis die
Antwort abriss, nie ein einziger `action`-Block. Bereits der triviale
Vorab-Test hatte das angedeutet — statt „PONG" bekam es eine ungefragte
Erklärung, was Pong überhaupt ist. Schwache Instruktionsfolgetreue plus
Wiederholungsanfälligkeit sind hier offensichtlich verwandte Symptome
derselben Modellschwäche.

**Zwischenstand der erweiterten Runde:**

| Modell | Ergebnis |
|---|---|
| `google/gemma-4-e2b` | ✅ 677 s, 6/6 — aber 144.506 Tokens, 17 JSON-Fehler; bestätigt: zu klein fürs Protokoll, unabhängig vom Serving |
| `ornith-1.0-9b` (4-bit + 6-bit, `mlx-community`) | ❌ generischer Ladefehler bei beiden Quant-Stufen — reproduzierbar, publisherspezifisch |
| `ornith-1.0-35b-mlx` (`ToPo-ToPo`) | ⏸️ nicht testbar — Systemspeicher reicht nicht |
| `ornith-1.0-35b-mlx-oq4` (`deepsweet`) | ⏸️ korrekt als Text-Modell klassifiziert, aber ebenfalls am Speicher gescheitert |
| **`microsoft/phi-4`** | ✅ **347 s, 6/6** — sauber, nur ein korrekt behandeltes Batch-Limit unterwegs |
| `qwen/qwen2.5-coder-32b` | ⏸️ konsistent am Speicher gescheitert, auch mit viel freiem RAM — schlicht zu groß für dieses System |
| `bonsai-8b-mlx` (1-bit) | ❌ generischer Ladefehler, kein Speicherproblem — vermutlich korrupt/inkompatibel |
| `qwen/qwen3.6-35b-a3b` | ⏸️ nicht testbar — dieselbe Größenklasse, die auf Ollama das Swap-Desaster verursachte |
| `liquid/lfm2-24b-a2b` | ❌ 234 s, 0/6 — Wiederholungsschleife, neuer Fehlertyp |
| `zai-org/glm-4.6v-flash` | ❌ abgebrochen nach >20 Min, 0/6 — hing bei Schritt 7 in wiederholten, fast identischen JSON-Fehlern fest, keine Selbstkorrektur. Bereits im Vorab-Test auffällig: echote die Anweisung zurück statt „PONG" zu antworten |

**Eine kleine Detektivarbeit am Rande:** Ornith-1.0-35B existiert in mehreren
Community-Konvertierungen — `ToPo-ToPo` und `mlx-community` (fürs 9B)
klassifizieren es fälschlich als `"type": "vlm"` (Vision-Language-Model),
`deepsweet`s Konvertierung dagegen korrekt als `"type": "llm"`. Das VLM-
Missverständnis ist also **publisherspezifisch bei der Konvertierung**, kein
grundsätzliches Problem mit Ornith selbst — erklärt aber möglicherweise, warum
ausgerechnet die fehlklassifizierten Varianten mit einem generischen Ladefehler
statt einem klaren Ressourcen-Hinweis scheiterten.

**Ergebnis der kompletten LM-Studio-Session (13 Modelle/Varianten getestet):**
Nur zwei bestehen die 400-Sekunden-Regel klar — `qwen/qwen3.6-27b` (390 s) und
`microsoft/phi-4` (347 s). Devstral-Small-2 und `gemma-4-e2b` liefern zwar
vollständigen, korrekten Code, aber zu langsam. Der Rest scheitert an
Systemspeicher (zu groß für diese Maschine, unabhängig vom Modell selbst),
generischen Ladefehlern (vermutlich Konvertierungsprobleme einzelner
Publisher) oder echten Modellschwächen (Wiederholungsschleifen, unkorrigierte
JSON-Fehler). Damit zieht sich das Bild des ganzen Tages bis in die letzte
Testrunde durch: **die Trefferquote bleibt niedrig — nicht weil gute Modelle
fehlen, sondern weil Größe, Speicher, Konvertierungsqualität und
Formatdisziplin alle gleichzeitig passen müssen.**

### 9.10 Gesamtübersicht: alle Systeme, alle Ergebnisse

Zum Abschluss die komplette Liste — vier Zugangswege, über 40 Testläufe,
sortiert nach System und Serving-Software. „✅ Sieger" heißt: mindestens ein
Lauf mit 6/6 Dateien **und** unter 400 Sekunden.

#### Lokal — Ollama, M1 Max (32 GB)

| Modell | Beste Zeit | Ergebnis |
|---|---:|---|
| **`gemma4:26b-mlx`** | 138 s | ✅ Sieger — 3/3 |
| `qwen3.6:27b-mlx` | 379 s | ⚠️ 2/3 (1× leere Reasoning-Antwort) |
| `qwen3.6:27b-coding-nvfp4` | — | nicht getestet |
| `gemma4:e2b` | 315 s | ⚠️ 2/3 uneinheitlich |
| `gemma4:e4b` | 354 s | ⚠️ 1/3 |
| `gemma4:12b-mlx` | — | ❌ JSON-Escaping-Bug + Endlosschleife |
| `qwen3.6:35b-mlx` | — | ❌ Swap-Thrashing (128k-Kontext, 21 GB Modell) |
| `DeepSeek-R1-Distill-14B` (Q2_K) | — | ❌ 0/3 vollständig |
| `Qwable-5-27B-Coder` | — | ❌ Timeout, 0/6 |
| `gemma-4-26B-A4B-heretic` | — | nicht vollständig getestet (abgebrochen) |
| `Qwen3.6-27B-MTP` (IQ3_XXS) | 341 s | ⚠️ 1/3 (Fence-Label-Bug in einem Lauf) |
| `codestral:22b` (v0.1) | — | ❌ 0/3, schrieb Platzhalter statt Code |
| `mistral-small:24b` (Q4_K_M) | — | ❌ 1004 s, 0/6, unkorrigierter JSON-Fehler |
| `mistral-small:24b` (Q8_0) | — | ⏸️ abgebrochen (RAM-Enge: 30 GB von 32 GB) |
| `devstral:24b` (128k Kontext) | — | ❌ keine Antwort — 36 GB RAM-Explosion |
| `devstral2-24b` (`num_ctx`-Fix, 16k) | 859 s | ⚠️ 1 Erfolg, aber weit über 400 s |
| `gpt-oss:20b` | 126 s | ⚠️ inkonsistent — mal Content, mal leer |

#### Lokal — Ollama, Mac mini M4 Pro (16 GB, LAN)

| Modell | Beste Zeit | Ergebnis |
|---|---:|---|
| **`gemma4:26b-mlx`** | 146 s | ✅ Sieger — 3/3 |
| `Qwopus3.6-27B` (Q4_K_M) | 320 s | ✅ Sieger — 2/3 |
| `qwen3-coder:30b` | 862 s | ⚠️ 1/3, über 400 s |
| `Qwen3-Coder-30B-A3B` | 515 s | ⚠️ 2/3, über 400 s |
| `Ornith-1.0-35B` (Q3_K_L) | 92 s | ⚠️ 1/3, aber sehr schnell |
| `gemma4:e2b` | 496 s | ⚠️ 1/3, über 400 s |
| `gemma4:e4b` | 493 s | ⚠️ 1/3, über 400 s |
| `gpt-oss:20b`, `phi4-reasoning:14b` | — | nicht getestet (vorab als Reasoning-Modelle ausgeschlossen) |

#### Gemietete GPUs — vast.ai (`gemma4:26b`, GGUF)

| GPU | Beste Zeit | Ergebnis |
|---|---:|---|
| **RTX 5090** (guter Host) | 109 s | ✅ Sieger |
| RTX 5090 (anderer Host) | 314 s | ⚠️ dieselbe GPU, Faktor-3-Varianz |
| **RTX 4090** | 169 s | ✅ Sieger |
| **RTX 3090** | 240 s | ✅ Sieger |

#### Cloud — OpenRouter (17 Modelle/Läufe)

| Modell | Zeit | Kosten | Ergebnis |
|---|---:|---:|---|
| **`z-ai/glm-5.2`** | 12 s | $0.0265 | ✅ |
| **`mistralai/codestral-2508`** | 29 s | $0.0054 | ✅ |
| **`stepfun/step-3.7-flash`** | 29 s | $0.0054 | ✅ |
| **`openai/gpt-oss-20b`** | 41 s | $0.0014 | ✅ |
| **`minimax/minimax-m3`** | 49 s | $0.0079 | ✅ |
| **`deepseek/deepseek-v4-pro`** | 50 s | $0.0172 | ✅ |
| **`mistralai/mixtral-8x22b`** | 51 s | $0.1224 | ✅ (teuer, Ausreißer) |
| **`openai/gpt-oss-120b`** | 64 s | $0.0015 | ✅ |
| **`mistralai/mistral-small-24b-2501`** | 64 s | $0.0017 | ✅ |
| **`xiaomi/mimo-v2.5`** | 88 s | $0.0027 | ✅ |
| `qwen/qwen3-8b` | — | — | 429 Rate-Limit, unentschieden |
| **`google/gemma-3-12b-it`** | 124 s | $0.0027 | ✅ |
| **`tencent/hy3-preview`** | 151 s | $0.0040 | ✅ |
| **`deepseek/deepseek-v4-flash`** | 223 s | $0.0024 | ✅ |
| `qwen/qwen3-14b` | 317 s | $0.0103 | ✅ (schwächster Erfolg) |
| **`qwen/qwen3-235b-a22b-2507`** | 306 s | $0.0059 | ✅ |
| `mistralai/mistral-small-24b-2501` (1. Versuch) | — | — | 429 Rate-Limit, unentschieden |

**16 von 17 Läufen erfolgreich, alle unter 400 s.** Mit Abstand die höchste
Trefferquote des Tages.

#### LM Studio — MLX/GGUF, dieselbe M1-Max-Maschine

| Modell | Beste Zeit | Ergebnis |
|---|---:|---|
| **`qwen/qwen3.6-27b`** (4-bit) | 390 s | ✅ Sieger |
| **`microsoft/phi-4`** (Q4_K_M) | 347 s | ✅ Sieger |
| `mistralai/devstral-small-2-2512` (4-bit) | 483 s | ⚠️ vollständig, über 400 s |
| `google/gemma-4-e2b` (4-bit) | 677 s | ⚠️ vollständig, aber 144k Tokens/17 Fehler |
| `openai/gpt-oss-20b` (MXFP4) | 244 s | ❌ 2/6, JSON-Fehler |
| `liquid/lfm2-24b-a2b` (4-bit) | 234 s | ❌ 0/6, Wiederholungsschleife |
| `zai-org/glm-4.6v-flash` (4-bit) | — | ❌ abgebrochen (>20 Min), JSON-Fehlerschleife |
| `mistral-small-3.2-24b` (6-bit) | — | ⏸️ Ladeverweigerung (Sicherheitscheck) |
| `qwen/qwen2.5-coder-32b` (4-bit) | — | ⏸️ konsistent zu groß fürs System |
| `qwen/qwen3.6-35b-a3b` (4-bit) | — | ⏸️ zu groß |
| `ornith-1.0-35b-mlx` (`ToPo-ToPo`) | — | ⏸️ zu groß |
| `ornith-1.0-35b-mlx-oq4` (`deepsweet`) | — | ⏸️ zu groß |
| `ornith-1.0-9b` (4-bit + 6-bit) | — | ❌ generischer Ladefehler, publisherspezifisch |
| `bonsai-8b-mlx` (1-bit) | — | ❌ generischer Ladefehler |

**Gesamtsieger des Tages, alle Zugangswege zusammengenommen:** `gemma4:26b`
(lokal auf beiden Macs *und* über gemietete GPUs zuverlässig), praktisch
jedes Cloud-Modell über OpenRouter, sowie lokal via MLX/LM-Studio
`qwen3.6:27b` und `phi-4`. Die gemeinsame Eigenschaft aller Gewinner: keiner
davon ist aggressiv quantisiert (Cloud: kaum/keine Kompression; lokal:
durchweg 4-bit oder besser, nie Q2/Q3) — genau die Lektion aus Abschnitt 9.7,
hier ein letztes Mal über alle vier Systeme hinweg bestätigt.

### 9.11 Nachschlag: sechs Qwopus3.6-27B-Konvertierungen im Vergleich

Nach Redaktionsschluss noch eine letzte, besonders lehrreiche Runde:
`Qwopus3.6-27B` war einer der Top-Kandidaten auf dem M4 Pro (Abschnitt-9.3-
Marathon, 2/3, bester Code). Sechs verschiedene MLX-Konvertierungen desselben
Basismodells, von sechs verschiedenen Community-Publishern, alle in etwa
gleicher Größenklasse — ein sauberer natürlicher Vergleichstest für
Konvertierungsqualität statt Modellqualität:

| Publisher / Variante | Ergebnis |
|---|---|
| `Jackrong` — Standard 4-bit (`v2-mlx`) | ✅ **368 s, 6/6 — nur 3 Schritte, null Fehler** — bester Lauf der ganzen LM-Studio-Session |
| `nom666` — MTP + „Speed" 4-bit | ⚠️ 1079 s, 6/6 — vollständig, aber trotz „Speed"-Namen fast 3× langsamer als die Standardversion, viele JSON-Fehler unterwegs |
| `jedisct1` — MTP 4-bit (ohne „Speed") | ❌ generischer Ladefehler |
| `zecanard` — 2-bit Mixed (2/6-Layer-Mix) | *(Download nicht abgeschlossen)* |
| `mlx-community` — 35B-A3B-Coder-Variante | ⏸️ Systemspeicher reicht nicht |
| `fritskarl` — 35B-A3B-Coder OQ4+MTP | ⏸️ Systemspeicher reicht nicht |

**Die auffälligste Erkenntnis:** Bei identischem Basismodell und identischer
Bit-Tiefe (4-bit) schwankt das Ergebnis zwischen „bester Lauf des Tages" und
„fast eine Sekunde-Grenze verfehlt, 3× langsamer" — abhängig einzig von der
**MTP-Zusatzoptimierung** (Multi-Token-Prediction, eigentlich für mehr
Geschwindigkeit gedacht) und der Konvertierungssorgfalt des jeweiligen
Publishers. Eine dritte MTP-Variante ließ sich gar nicht erst laden. Das
ergänzt die Quantisierungs-Lektion des Tages um eine weitere Variable, die
genauso wenig auf den ersten Blick sichtbar ist: **dieselbe Bit-Tiefe von
zwei verschiedenen Publishern ist nicht dasselbe Modell.**

### 9.12 Der schönste Präzisionsbeweis des Tages: dasselbe 9B-Modell, 4-bit vs. 8-bit

Eine letzte Runde, diesmal mit fünf neuen `Jackrong`-Konvertierungen (derselbe
Publisher, der bereits den saubersten Lauf des Tages lieferte, Abschnitt 9.11):

| Modell | Ergebnis |
|---|---|
| `Qwopus3.5-9B-v3` (4-bit) | ❌ 169 s, 0/6 — JSON-Fehler, dann gab das Modell auf und **erfand eine falsche Aktion** (`write_file` statt `write_files`) |
| **`Qwopus3.5-9B-v3` (8-bit)** | ✅ **289 s, 6/6** — ein JSON-Fehler, aber selbst korrigiert |
| `Qwopus3.5-27B-v3` (4-bit) | ⚠️ 1007 s, 6/6 — vollständig, aber weit über 400 s |
| `Qwen3.5-9B „Claude-4.6-Opus-Reasoning-Distilled"` | ❌ 31 s, 0/6 — schien eine valide `write_files`-Aktion zu senden, doch es landete nichts auf der Platte, kein Fehler geloggt (ungeklärt, vermutlich Verbindungsabbruch) |
| `Qwen3.5-9B „DeepSeek-V4-Flash-Distilled"` | ❌ 512 s, 0/6 — Antwort brach mitten im Code ab, nie ein valider `action`-Block erreicht |

**Der Kernbefund dieser Runde — derselbe 9B-Modellkern, zwei Quant-Stufen,
sonst nichts verändert:** Die 4-bit-Version scheitert nach zwei JSON-Fehlern
komplett und beginnt, Aktionen zu erfinden, die `mc.py` gar nicht kennt. Die
8-bit-Version desselben Modells löst genau dasselbe Problem einmal auf,
korrigiert sich selbst und liefert alle sechs Dateien in unter fünf Minuten.
Kein anderer Vergleich des Tages zeigt den Effekt der Quantisierung so
sauber isoliert — gleicher Publisher, gleiche Konvertierung, gleiches
Basismodell, nur die Bit-Tiefe unterscheidet sich.

Die beiden „Distilled"-Varianten (angeblich aus Reasoning-Traces von Claude
Opus bzw. DeepSeek V4 destilliert) enttäuschten beide auf unterschiedliche
Art — einmal mit einem rätselhaften Datenverlust trotz scheinbar korrekter
Aktion, einmal mit einer nie abgeschlossenen Antwort. Für dieses Format
brachte die Destillation keinen sichtbaren Vorteil gegenüber den
undestillierten Geschwistermodellen.

### 9.13 Gemma 4 im großen Stil: die beste Trefferquote des Tages

Ein letzter, besonders ergiebiger Nachschlag: zehn Gemma-4-Varianten (plus
eine Gemma-3-12B zum Vergleich) über mehrere Größen, Publisher und
Quantisierungsstufen. `gemma4:26b-mlx` war schon der Gesamtsieger des Tages
via Ollama (Abschnitt 9.10) — hier die native LM-Studio/MLX-Gegenprobe:

| Modell | Ergebnis |
|---|---|
| `google/gemma-4-e4b` | ❌ 1818 s (30 Min!), 5/6 — setzt die durchgehend schwache e4b-Bilanz über alle Serving-Wege fort |
| **`gemma-4-12b-it-mlx` (4-bit)** | ✅ **311 s, 6/6** |
| `gemma-4-12b-it-mlx` (8-bit) | ⚠️ 698 s, 6/6 — langsamer UND mehr Fehler als 4-bit (Gegenbeispiel zur Präzisions-These!) |
| `google/gemma-3-12b` | ⚠️ 1011 s, 6/6 — vollständig, weit über 400 s |
| `google/gemma-4-26b-a4b` (Standard) | ❌ 359 s, 5/6 — erfand wieder die falsche Aktion `write_file` bei der letzten Datei |
| **`google/gemma-4-26b-a4b-qat`** | ✅ **103 s, 6/6 — 4 Schritte, null Fehler** — schnellster vollständiger Erfolg der ganzen LM-Studio-Session |
| **`gemma-4-26b-a4b-it@4bit`** (`lmstudio-community`) | ✅ **141 s, 6/6** |
| **`gemma-4-26b-a4b-it@mxfp4`** | ✅ **140 s, 6/6** |
| `gemma-4-26b-a4b-it-oq3` (aggressiv 3-bit) | ⚠️ 491 s, 6/6 — vollständig, aber über 400 s |
| **`fakerockert543/gemma-4-26b-a4b-it-mlx`** | ✅ **175 s, 6/6** |

**Fünf von zehn Varianten sind klare Gewinner — die beste Trefferquote der
gesamten LM-Studio-Session**, deutlich besser als bei Qwen3.6/Qwopus/Phi-4
zusammen. Die Gemma-4-26B-A4B-Architektur (MoE) scheint über MLX auf Apple
Silicon außergewöhnlich gut zu laufen, fast unabhängig von Publisher oder
Quant-Stufe zwischen 4-bit und MXFP4 — nur die Standard-Google-Version
(mit dem `write_file`-Bug) und die aggressive OQ3-Kompression fielen ab.

**Der QAT-Befund verdient besondere Erwähnung:** `gemma-4-26b-a4b-qat`
(Quantization-Aware Training — das Modell wurde von Google bereits *für*
Quantisierung trainiert, nicht nachträglich komprimiert) lieferte mit
**103 Sekunden den schnellsten vollständigen Erfolg der gesamten
LM-Studio-Session**, bei nur vier Schritten und null Fehlern. Das ist
genau die Hypothese, die den ganzen Tag im Raum stand: Wenn Quantisierung
das Problem ist, sollte ein Modell, das für Quantisierung *trainiert* wurde,
robuster sein als eines, das nachträglich komprimiert wurde. Der Beleg
dafür ist so eindeutig, wie er heute nur einmal auftauchte.

---

## Anhang: Die `mc`-Aufrufe & Prompts

Zur Nachvollziehbarkeit die tatsächlich verwendeten Aufrufe. `$BASE` steht für die
OpenAI-kompatible Endpoint-URL (`--base-url …/v1`), `$MODEL` für die jeweilige
Modell-ID. Alle Läufe mit `--yes` (keine Rückfragen) und einem `--max-steps`-Limit.

### Benchmark-Aufgabe (identisch für alle Modelle)

```bash
python3 mc.py --base-url $BASE --model $MODEL --yes --max-steps 30 "$PROMPT"
```

`$PROMPT`:

> Erstelle eine einfache CRUD-Webanwendung 'Personenverwaltung'.
> BACKEND in backend/ : Flask + SQLite (Datei personen.db), Tabelle person mit
> Spalten id (autoincrement), name, adresse, telefon. REST-API mit flask-cors:
> GET /api/persons (alle), POST /api/persons (anlegen), PUT /api/persons/<id>
> (bearbeiten), DELETE /api/persons/<id> (loeschen). Dateien backend/app.py und
> backend/requirements.txt. Die Tabelle beim Start automatisch anlegen.
> FRONTEND in frontend/ : React-App. Dateien frontend/package.json,
> frontend/public/index.html, frontend/src/index.js, frontend/src/App.jsx.
> App.jsx zeigt alle Personen in einer Tabelle und erlaubt Anlegen, Bearbeiten und
> Loeschen ueber ein Formular; spricht das Backend per fetch auf
> http://localhost:5000 an. Nutze die write_files-Aktion, um mehrere Dateien auf
> einmal zu schreiben. Lege nur Dateien an, KEINE npm- oder pip-Installation.

### Iteration 1 — Footer + Erklärseite (auf die bestehende App)

> Erweitere die bestehende React-App (frontend/src/App.jsx …). Lies App.jsx zuerst.
> 1) Fuege einen Footer am Seitenende ein mit dem Text '(c) qwopus 2026'.
> 2) Fuege eine einfache Unterseite/Ansicht 'Ueber diese App' hinzu … ueber einen
> useState-Umschalter/Tab …, KEIN react-router. Behalte die bestehende CRUD-Funktion
> bei.

### Iteration 2 — Komponenten-Split + echtes Routing + ausführliche Hilfe

> Ueberarbeite das React-Frontend (frontend/src/). Lies zuerst App.jsx.
> 1) TEILE das Frontend auf: Personenverwaltung und Hilfe in EIGENE Komponenten
> (PersonenView.jsx, HilfeView.jsx) und importiere sie in App.jsx.
> 2) ECHTES URL-Routing OHNE Bibliotheken (kein react-router) ueber
> window.location.hash. '#/' zeigt die Personenverwaltung, '#/hilfe' die Hilfe …
> 3) Die HilfeView soll AUSFUEHRLICH erklaeren wie die Anwendung funktioniert …
> Nutze edit_file fuer kleine Aenderungen und write_files fuer neue Dateien.

### Iteration 3 — Bug-Fix per `edit_file` (404 in DELETE-Route)

> In backend/app.py gibt die DELETE-Route faelschlich immer {ok:true} zurueck, auch
> wenn die id nicht existiert. Aendere NUR diese Route mit edit_file so, dass sie
> 404 mit {'error':'nicht gefunden'} zurueckgibt, wenn keine Zeile geloescht wurde
> (pruefe cur.rowcount). Nutze edit_file, nicht write_file.

### Iteration 4 — DB-/API-Doku in die Hilfe einfügen (`edit_file`)

> Erweitere die Hilfeseite … um zwei zusaetzliche Abschnitte … 1) 'Datenbank':
> SQLite (Datei personen.db), Tabelle 'person' mit Spalten … 2) 'API-Endpunkte':
> GET/POST/PUT/DELETE /api/persons … Lies … zuerst und aendere NUR die noetige
> Stelle mit der edit_file-Aktion … schreibe NICHT die ganze Datei neu.

### Ornith — gleiche Erweiterungen am eigenen Build

> (Hilfe + Routing) Erweitere die React-App in frontend/src/App.jsx. Lies sie zuerst.
> 1) ECHTES URL-Routing OHNE Bibliotheken ueber window.location.hash … '#/hilfe'
> zeigt eine Hilfeseite … 2) Navigation oben … 3) Die Hilfeseite beschreibt die App
> ausfuehrlich … 4) Behalte die bestehende CRUD-Funktion komplett bei …

> (DB-Doku) Erweitere die Hilfeseite in frontend/src/App.jsx (Funktion
> renderHelpPage) … Fuege einen NEUEN Abschnitt 'Datenbank' ein … Tabelle 'person'
> mit Spalten id (INTEGER, PRIMARY KEY, AUTOINCREMENT), name/adresse/telefon (TEXT,
> NOT NULL) … REST-Endpunkte … Aendere NUR die noetige Stelle mit edit_file …

### Weitere nützliche Aufrufe

```bash
python3 mc.py --list-models                       # Modelle des Endpoints
python3 mc.py --debug-net                          # DNS/TCP/Proxy-Diagnose
python3 mc.py --plan "<aufgabe>"                   # erst Plan zeigen + bestaetigen
python3 mc.py -v "<aufgabe>"                        # mit Statuszeilen/Spinner
python3 mc.py --proxy http://USER:PASS@host:port … # hinter Firmenproxy
```
