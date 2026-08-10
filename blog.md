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

### 9.14 Die LM-Studio-Bestenliste: unter 450 Sekunden, ohne Fehler

Aus allen 40+ LM-Studio-Testläufen des Tages (Abschnitte 9.8–9.13) die
Modelle, die **vollständig (6/6 Dateien), unter 450 Sekunden und ganz ohne
eine einzige `FEHLER`-Meldung** durchliefen:

| Modell | Zeit | Schritte | Fehler |
|---|---:|---:|---:|
| **`google/gemma-4-26b-a4b-qat`** | 103 s | 4 | 0 |
| **`qwopus3.6-27b-v2-mlx`** (Jackrong) | 368 s | 3 | 0 |
| **`qwen/qwen3.6-27b`** | 390 s | 5 | 0 |

Nur drei von über 40 Läufen schaffen beides gleichzeitig: schnell **und**
absolut sauber, ohne dass die Validierung/Retry-Mechanik von `mc.py`
überhaupt eingreifen musste. Knapp daneben — vollständig und unter 450 s,
aber mit genau einem (selbst korrigierten oder korrekt abgefangenen) Fehler:

| Modell | Zeit | Fehler |
|---|---:|---:|
| `gemma-4-26b-a4b-it@4bit` (lmstudio-community) | 141 s | 1 |
| `gemma-4-26b-a4b-it@mxfp4` | 140 s | 1 |
| `fakerockert543/gemma-4-26b-a4b-it-mlx` | 175 s | 1 |
| `microsoft/phi-4` | 347 s | 1 |
| `mlx-qwopus3.5-9b-v3@8bit` (Jackrong) | 289 s | 1 |

Auffällig: **vier der acht Modelle in beiden Tabellen sind Gemma-4-26B-A4B-
Varianten** — dieselbe Architektur, die auch die beste Trefferquote der
gesamten Session lieferte (Abschnitt 9.13).

### 9.15 Tiefenprüfung: läuft der Code auch wirklich?

Bisher zählte als „Erfolg", wenn `mc.py` 6/6 Dateien meldete, wenige
`FEHLER`-Zeilen im Log auftauchten und die Zeit unter 400–450 s blieb. Das
sagt nur, ob das Modell dem JSON-Aktionsprotokoll gefolgt ist — nicht, ob
die erzeugte Anwendung tatsächlich funktioniert. Für alle acht „Gewinner"
aus 9.14 folgte deshalb eine echte Prüfung: Code lesen, Backend mit den
exakt gepinnten Requirements in einer frischen virtuellen Umgebung starten,
Frontend mit `npm start` hochfahren, per Browser-Automatisierung Anlegen und
Bearbeiten live durchklicken, dazu gezielt Edge Cases wie „PUT/DELETE auf
eine nicht existierende ID" testen.

**Drei Modelle bestanden ohne jede Einschränkung:**

- **`qwen/qwen3.6-27b`** — sauberster Code der ganzen Session: korrekte
  404-Behandlung bei PUT/DELETE, Eingabevalidierung, Flask-`g`-Objekt
  lehrbuchmäßig verwendet.
- **`qwopus3.6-27b-v2-mlx`** — ebenfalls korrekte 404-Behandlung, HTML5-
  Pflichtfeld-Validierung im Frontend.
- **`gemma-4-26b-a4b-it@4bit`** (`lmstudio-community`) — korrekte
  404-Behandlung, sauberes Bearbeiten-Prefill, **und mit 132–141 s über
  zwei Läufe die zweitschnellste Variante des ganzen Tages** — schnell UND
  makellos, der eigentliche Gesamtsieger dieser Tiefenprüfung.

**Zwei Modelle funktionierten, aber mit einer stillen Lücke:** sowohl
`google/gemma-4-26b-a4b-qat` als auch `gemma-4-26b-a4b-it@mxfp4` geben bei
PUT/DELETE auf eine nicht existierende ID fälschlich `200 OK` zurück, statt
`404` — kein Absturz, aber falsches API-Verhalten. `mxfp4` hatte zusätzlich
**zwei halluzinierte Konfigurationswerte** in `package.json`
(`"not op_viewer"` als Browserslist-Query, `"react-app/cr-error"` als
ESLint-Erweiterung — beides erfundene, nicht existierende Werte), die das
Frontend ohne manuellen Fix gar nicht erst kompilieren ließen.

**Ein Modell fiel bei der Tiefenprüfung deutlich durch:** `microsoft/phi-4`
bestand die schnelle Metrik (347 s, 6/6, 1 Fehler) klaglos, hatte aber den
mit Abstand kaputtesten Code der Session:
- `requirements.txt` pinnt `Flask==2.1.0`, aber nicht Werkzeug — ein
  blanker `pip install -r requirements.txt` installiert eine inkompatible
  Werkzeug-Version und der Import schlägt sofort fehl.
- GET auf eine nicht existierende ID crasht mit `500 Internal Server Error`
  (`TypeError: 'NoneType' object is not iterable`) statt eines sauberen
  404 — keine Null-Prüfung vor `dict(person)`.
- PUT/DELETE auf eine nicht existierende ID geben fälschlich Erfolg zurück.
- `package.json` pinnt React 17, aber `index.js` nutzt die React-18-
  exklusive `createRoot`-API aus `react-dom/client` — das Frontend
  kompiliert so gar nicht.
- **Der schwerwiegendste Fund:** Der „Bearbeiten"-Button ruft direkt
  `editPerson(id)` auf, ohne das Formular vorher mit den Daten der
  Person zu befüllen. Ein Klick auf „Bearbeiten" sendet den aktuellen
  (meist leeren) Formularinhalt als PUT-Body — **live im Browser bestätigt:
  ein Klick hat eine komplette Personenzeile mit Leerstrings überschrieben.**
  Kein Rendering-Fehler, keine Fehlermeldung — die Funktion sieht im Betrieb
  aus wie sie funktioniert, zerstört aber im Hintergrund Daten.

**Und zwei Modelle entpuppten sich als nicht deterministisch:** Ein zweiter,
unabhängiger Testlauf mit identischem Prompt lieferte bei zwei der acht
„Gewinner" ein komplett anderes Ergebnis als beim ersten Mal:

- `mlx-qwopus3.5-9b-v3@8bit` lief zuerst sauber durch (289 s, 6/6). Im
  zweiten Lauf verpackte dasselbe Modell alle drei Aktionsblöcke in
  ` ```json ` statt ` ```action ` — der exakte Fence-Label-Bug aus
  Abschnitt 9.3 — und `mc.py` ignorierte alles. Ergebnis: 0/6 Dateien,
  obwohl das JSON selbst gültig war (das Backend hätte ohnehin gecrasht:
  `CORS(app)` aufgerufen, aber nie importiert).
- `fakerockert543/gemma-4-26b-a4b-it-mlx` lief zuerst tadellos durch
  (175 s, 6/6). Im zweiten Lauf verfing sich das Modell in einer
  Selbstzweifel-Schleife („Actually, let me use write_files… Wait, I'll
  just do it now. Actually, let me…“) und schrieb nie einen vollständigen
  Aktionsblock — dieselbe Wiederholungsschleife wie bei `liquid/lfm2-24b-a2b`
  in Abschnitt 9.9. Manuell nach mehreren Minuten abgebrochen, 2/6 Dateien.

**Fazit dieser Tiefenprüfung:** Das Tagesmaß „Dateien vollständig, Zeit
unter 400 s, wenig `FEHLER`" ist eine notwendige, aber keine hinreichende
Bedingung für brauchbaren Code — und noch nicht einmal eine zuverlässige
Vorhersage für den nächsten Lauf desselben Modells. Von acht geprüften
„Gewinnern" sind drei uneingeschränkt vertrauenswürdig, zwei brauchbar mit
kleinen Lücken, einer mit ernsthaften, produktionsgefährlichen Bugs — und
zwei zeigten bei einer bloßen Wiederholung ein komplett anderes Bild.
**Ein einzelner erfolgreicher Lauf ist kein Qualitätsnachweis, egal wie
schnell und sauber er aussah.**

### 9.16 Ein dritter Lauf für die Top 5: wie stabil ist stabil?

Nach dem Nichtdeterminismus-Fund in 9.15 ein dritter, unabhängiger Testlauf
für die fünf Modelle ohne ernsthafte Code-Probleme (die drei uneingeschränkt
guten plus die zwei mit der kleinen 404-Lücke) — reine Zeit-/Erfolgsmessung,
keine erneute Code-Tiefenprüfung:

| Modell | Lauf 1 | Lauf 2 | Lauf 3 | Bild |
|---|---:|---:|---:|---|
| **`gemma-4-26b-a4b-it@4bit`** | 141 s | 132 s | 125 s | ✅ bemerkenswert konsistent — immer schnell, immer 6/6 |
| **`gemma-4-26b-a4b-it@mxfp4`** | 140 s | 132 s | 133 s | ✅ ebenso konsistent |
| `qwen/qwen3.6-27b` | 390 s | 291 s | 206 s | ✅ immer 6/6, 0 Fehler — wird sogar schneller |
| `google/gemma-4-26b-a4b-qat` | 103 s | 229 s | 230 s | ⚠️ steigt an, pendelt sich bei ~230 s ein, bleibt unter 400 s |
| `qwopus3.6-27b-v2-mlx` | 368 s | 296 s | **891 s** | ⚠️ dritter Lauf reißt die Grenze massiv, trotzdem 6/6 |

**Die beiden Publisher-`lmstudio-community`-Varianten (4-bit und MXFP4)
sind über drei unabhängige Läufe die mit Abstand konstantesten Ergebnisse
des gesamten Tages** — Zeiten schwanken nur um wenige Sekunden, nie ein
Totalausfall. `qwen3.6-27b` bleibt ebenfalls durchgehend zuverlässig, wenn
auch mit größerer Zeitschwankung. `qwopus3.6-27b-v2-mlx` dagegen zeigt: Auch
ein Modell, das zweimal sauber und schnell lief, kann beim dritten Versuch
mehr als das Sechsfache der ursprünglichen Zeit brauchen — bei ansonsten
identischem Setup, identischem Prompt, identischer Maschine.

**Endgültiges Fazit nach 9.14–9.16:** Wer aus diesem Tag ein einzelnes
Modell für den produktiven lokalen Einsatz mitnehmen möchte, sollte zu
**`gemma-4-26b-a4b-it@4bit`** (oder der MXFP4-Schwester) greifen — nicht
weil es am schnellsten *war*, sondern weil es als einziges Modell sowohl
die Code-Tiefenprüfung bestand als auch über drei Läufe hinweg praktisch
keine Varianz zeigte. Genau diese Kombination aus Korrektheit und
Reproduzierbarkeit fehlte bei jedem anderen Kandidaten des Tages.

### 9.17 Der letzte Test: läuft der Gewinner auch auf der kleineren Maschine?

Eine letzte, naheliegende Frage: Hält sich der Tagessieger auch auf der
zweiten Maschine, dem Mac mini M4 Pro? Zwei Hürden mussten dafür erst aus
dem Weg:

**Erste Hürde — Ollama auf der M4 Pro war nur lokal erreichbar.** Ein
Verbindungsversuch von der M1 Max aus schlug mit „Connection refused" fehl.
`lsof` auf der M4 Pro bestätigte: Ollama lauschte nur auf `localhost:11434`,
nicht auf der LAN-Schnittstelle — obwohl `launchctl setenv OLLAMA_HOST
0.0.0.0` gesetzt war und die App per SSH neu gestartet wurde. Die
Ollama.app ignorierte die Umgebungsvariable beim Neustart über `open -a`
konsequent. **Lektion: Bei modernen Ollama-App-Versionen für macOS reicht
die klassische `OLLAMA_HOST`-Umgebungsvariable oft nicht mehr — es gibt
einen eigenen Schalter in den App-Einstellungen, der Vorrang hat und sich
nicht per SSH/Kommandozeile umgehen lässt.** Diese Baustelle blieb ungelöst.

**Zweite Hürde umgangen: LM Studio auf der M4 Pro.** Statt Ollama zu
reparieren, wurde LM Studio direkt auf der M4 Pro gestartet und
`mlx-community/gemma-4-26b-a4b-it` (4-bit) dort geladen — sofort über die
LAN-Schnittstelle erreichbar, ganz ohne die Ollama-Bind-Problematik.

**Das Ergebnis übertraf die Erwartungen:** Trotz der M4 Pro mit „nur"
24 GB RAM (nicht 16 GB, wie zunächst angenommen) statt der 32 GB der M1
Max lief derselbe Modelltyp **schneller** als auf der großen Maschine:

| Maschine | Zeit | Ergebnis |
|---|---:|---|
| M1 Max, 32 GB (`lmstudio-community`-Build) | 125–141 s | ✅ 6/6, 0–1 Fehler |
| **M4 Pro, 24 GB (`mlx-community`-Build)** | **84 s** | ✅ **6/6, 5 Schritte, 0 Fehler** |

Der neuere M4-Chip gleicht den kleineren Speicherpuffer (24 GB minus ~15,6 GB
Modell = rund 8,4 GB Luft, spürbar enger als die 16,4 GB auf der M1 Max)
offenbar mit höherer Rohleistung mehr als aus. **Der Tagessieger
`gemma-4-26b-a4b` in der 4-bit-MLX-Variante ist damit nicht nur korrekt und
reproduzierbar, sondern auch über zwei komplett unterschiedliche
Apple-Silicon-Generationen hinweg tragfähig** — der bestmögliche Abschluss
für einen Tag, der mit derselben Modellfamilie (`gemma4:26b-mlx` via Ollama,
Abschnitt 9.1) begonnen hatte.

### 9.18 Noch mehr Gemma 4 auf der M4 Pro: QAT gewinnt erneut

Auf der M4 Pro wurden weitere Gemma-4-Varianten nachgeladen — Gelegenheit
für einen kompletten Architektur-Vergleich auf der kleineren Maschine
(24 GB RAM statt 32 GB):

| Modell | Format | Zeit | Ergebnis |
|---|---|---:|---|
| **`gemma-4-26b-a4b-it-qat`** (`mlx-community`) | MLX, 4-bit QAT | **51 s** | ✅ **6/6, 4 Schritte, 0 Fehler — schnellster Lauf der gesamten M4-Pro-Session** |
| `gemma-4-26b-a4b-it@4bit` (`mlx-community`) | MLX, 4-bit | 84 s | ✅ 6/6, 0 Fehler |
| `gemma-4-26b-a4b-it@mxfp4` (`mlx-community`) | MLX, MXFP4 | 92 s | ✅ 6/6, 1 Fehler |
| `google/gemma-4-26b-a4b-qat` | GGUF, 4-bit QAT | 148 s | ✅ 6/6, 1 Fehler |
| `google/gemma-4-26b-a4b` | GGUF, Q4_K_M | 184 s | ✅ 6/6 |
| `mlx-community/gemma-4-e4b-it` | MLX, MXFP4 | 332 s | ❌ 5/6, 8 Fehler bei 16 Schritten |
| `google/gemma-4-e4b` | GGUF, Q4_K_M | 170 s | ❌ 0/6 |

**Zwei Muster bestätigen sich hier ein letztes Mal auf einer zweiten
Maschine:** Erstens schlägt MLX GGUF durchgehend bei Geschwindigkeit
(51–92 s vs. 148–184 s für praktisch dasselbe Modell). Zweitens ist
**QAT innerhalb desselben Formats immer die schnellste Variante** —
die MLX-QAT-Version ist fast doppelt so schnell wie die reguläre MLX-
4-bit-Version (51 s vs. 84 s), und die GGUF-QAT-Version schlägt die
reguläre GGUF-Version ebenfalls deutlich (148 s vs. 184 s). Und drittens:
**`e4b` bleibt über jede getestete Kombination aus Maschine, Format und
Quantisierung hinweg das schwächste Mitglied der Gemma-4-Familie** — hier
zum wiederholten Mal mit einem Totalausfall bzw. einem unvollständigen,
fehlerreichen Lauf.

Mit `gemma-4-26b-a4b-it-qat` auf der M4 Pro (51 s) und
`gemma-4-26b-a4b-it@4bit` auf der M1 Max (125–141 s über drei Läufe) ist
die Bilanz eindeutig: **Gemma-4-26B-A4B ist über beide Maschinen, alle
getesteten Quantisierungsstufen und beide Serving-Formate hinweg die
zuverlässigste und schnellste Modellfamilie des gesamten Tages.**

### 9.19 Auch auf der M4 Pro: Code-Tiefenprüfung, und ein spektakulärer Fund

Dieselbe Tiefenprüfung wie in 9.15 — Code lesen, Backend/Frontend live
starten, Edge Cases testen — für die sechs M4-Pro-Läufe unter 300 s
(Abschnitt 9.18 plus `qwen3.6-27b`):

| Modell | 404-Behandlung | Sonstige Befunde |
|---|---|---|
| `gemma-4-26b-a4b-it-qat` (MLX) | ⚠️ stiller Erfolg bei PUT/DELETE | sonst sauber |
| **`gemma-4-26b-a4b-it@4bit`** (MLX) | ✅ korrekt bei PUT und DELETE | halluzinierter Browserslist-Wert `"#DA0000"` (ein Hex-Farbcode statt einer Browser-Query) in `package.json` |
| **`gemma-4-26b-a4b-it@mxfp4`** (MLX) | ✅ korrekt via `get_or_404()` | nutzt SQLAlchemy statt rohem sqlite3 — elegantester Code des ganzen Tages, keine Bugs |
| `google/gemma-4-26b-a4b-qat` (GGUF) | ⚠️ stiller Erfolg bei PUT/DELETE | weiterer halluzinierter Browserslist-Wert: `"not firefox"` / `"not safari"` ohne Versionsangabe sind ungültige Syntax |
| `google/gemma-4-26b-a4b` (GGUF) | — | **siehe unten — kompiliert gar nicht** |
| `qwen/qwen3.6-27b` | ⚠️ korrekt bei PUT, fehlt bei DELETE | sonst sauber |

**Der bemerkenswerteste Fund des gesamten Tages** steckte in
`google/gemma-4-26b-a4b` (GGUF-Standardversion, 184 s, von `mc.py` als
„6/6 Dateien" gemeldet): Die Datei `App.jsx` bricht nach 58 Zeilen mit
einer einzelnen schließenden Klammer ab — kein JSX-Return, kein
`export default`. Der Grund war im Text selbst sichtbar: Das Modell hatte
sein **eigenes Gedankenprotokoll direkt als Code-Kommentare in die Datei
geschrieben**, komplett mit einer sichtbaren Selbstkorrektur mitten im
Fließtext:

```js
const url = editingId ? `${API_URL}/${editinglyId}` : API_URL;
// Note: fixed variable name typo in logic below to be consistent with prompt
const targetUrl = editingId ? `${API_URL}/${editingId}` : API_URL;
...
// Re-implementing handleSubmit correctly without the typo I just introduced in thought
const savePerson = async (e) => {
  ...
  const url = editingId ? `${API_URL}/${editingId}` : API_URL;
  // Wait, I need to be careful with my own code generation.
  // Let's rewrite the App.jsx content clearly.
} catch(e) {}
};
// Corrected version for the action block:
}
```

`mc.py`s Validierung prüft nur py/json/yaml/php-Syntax, nicht JS/JSX —
dieser komplett unbrauchbare Code passierte die Prüfung unbemerkt. Erst
`npm start` deckte es auf: **„Attempted import error: './App' does not
contain a default export"** — die Anwendung kann nicht einmal geladen
werden, trotz gemeldeten Erfolgs.

**Zwischenbilanz aller Tiefenprüfungen (M1 Max + M4 Pro, 14 Modelle
insgesamt):** Browserslist-Halluzinationen in `package.json` traten jetzt
**drei Mal** unabhängig voneinander auf (M1-Max-`mxfp4`, M4-`4bit`,
M4-`qat`) — offenbar eine systematische Schwachstelle über die ganze
Gemma-4-Familie hinweg, nicht ein Einzelfall. Und der „Gedankenprotokoll-
im-Code"-Fund zeigt: Selbst ein von `mc.py` als vollständig gemeldeter
Lauf kann eine Datei enthalten, die syntaktisch kein gültiges JavaScript
ist — die aktuelle Validierung deckt das nicht ab. **`mxfp4` bleibt nach
allen Tiefenprüfungen des Tages der einzige Kandidat ganz ohne jeden
gefundenen Bug.**

### 9.20 Der Material-Design-Stresstest

Ein letztes Experiment: Dieselbe Aufgabe an die fünf besten Modelle des
Tages, aber mit einer geänderten Anforderung — das Frontend soll die
[Material Web Components](https://github.com/material-components/material-web)
(`@material/web`) nutzen statt einfacher HTML-Elemente. Eine völlig neue,
unbekannte UI-Bibliothek mit eigener Custom-Element-API — ein deutlich
härterer Test für die tatsächlichen Web-Kenntnisse der Modelle als das
gewohnte CRUD-Grundgerüst.

| Modell | Zeit | Dateien | Schritte | Fehler |
|---|---:|---:|---:|---:|
| `qwen/qwen3.6-27b` | 656 s | 6 | 7 | 1 |
| `qwopus3.6-27b-v2-mlx` | 1327 s (22 Min!) | 8 | 13 | 1 |
| `gemma-4-26b-a4b-it@4bit` | 247 s | 6 | 10 | 3 |
| `google/gemma-4-26b-a4b-qat` | 386 s | 7 | 13 | 5 |
| `gemma-4-26b-a4b-it@mxfp4` | 171 s | 6 | 7 | 1 |

Alle fünf brauchten spürbar länger als beim gewohnten HTML-CRUD (die
schnellste Zeit, 171 s, liegt bereits über dem, was `mxfp4` normalerweise
für die einfache Version braucht) — die ungewohnte Bibliothek kostet
sichtbar Overhead. Aber die eigentliche Geschichte steckt nicht in der
Zeit, sondern im Code selbst.

**Fund 1 — alle fünf Modelle halluzinieren dieselbe, nicht existierende
Komponente.** `@material/web` hat schlicht **keine Card-Komponente** (echte
Kategorien: `button`, `checkbox`, `chips`, `dialog`, `divider`, `fab`,
`icon`, `list`, `menu`, `progress`, `radio`, `select`, `slider`, `switch`,
`tabs`, `textfield`). Trotzdem importierten und verwendeten **alle fünf**
Modelle unabhängig voneinander ein `<md-elevated-card>` bzw.
`elevated-card.js` — ein Konzept, das Material Design als *Designsprache*
zwar kennt (und andere Implementierungen wie Flutter oder MUI auch anbieten),
das aber in Googles offizieller Web-Components-Bibliothek nie umgesetzt
wurde. Fünf verschiedene Modelle, fünf unabhängige Testläufe, derselbe
spezifische Fehlschluss — ein starkes Indiz, dass dieses Wissen aus den
Trainingsdaten (Material Design allgemein) nicht sauber von der konkreten
Implementierung (`@material/web` speziell) getrennt gespeichert ist.

**Fund 2 — Browserslist-Halluzinationen jetzt in fünf von fünf Läufen.**
Jedes einzelne Modell produzierte eine andere Variante desselben
Fehlertyps: ungültige Werte in der `browserslist`-Konfiguration
(`"not firefox"` ohne Version, `">0.2"` ohne %-Zeichen, ein komplett falsches
Objekt-Format im Babel-Zielsyntax-Stil statt eines Arrays). Zusammen mit den
drei Fällen aus 9.15/9.19 sind das jetzt **acht unabhängige
Browserslist-Bugs an einem einzigen Tag** — mit Abstand die häufigste
Einzel-Fehlerart der gesamten Session.

**Fund 3 — drei verschiedene, unterschiedlich erfolgreiche Ansätze, Werte
an Custom Elements zu binden:**

| Ansatz | Modelle | Funktioniert? |
|---|---|---|
| Verschachteltes `<input slot="input">` im Custom Element | `qwen3.6-27b`, `qwopus3.6-27b-v2` | ❌ React aktualisiert den State nie — Formular komplett funktionsunfähig |
| `ref`-basiertes Auslesen von `.value` bei Submit | `google/gemma-4-26b-a4b-qat` | ⚠️ funktioniert technisch, aber Adresse/Telefon durch `address`/`phone`-statt-`adresse`/`telefon`-Verwechslung immer leer |
| Direktes `value={}` + Event-Handler auf dem Custom Element | `gemma-4-26b-a4b-it@4bit` (korrekt: `onInput`), `gemma-4-26b-a4b-it@mxfp4` (falsch: `oninput` klein geschrieben) | ✅ bei korrekter Groß-/Kleinschreibung — ❌ bei `oninput` reagiert React gar nicht |

**Nur `gemma-4-26b-a4b-it@4bit` lieferte eine tatsächlich vollständig
funktionierende Material-Design-Anwendung** — live im Browser verifiziert:
Anlegen und Bearbeiten funktionieren, neue Personen erscheinen korrekt in
der Liste. Alle anderen vier scheiterten an mindestens einem Punkt so
grundlegend, dass die zentrale Funktion (eine Person anlegen) nicht
funktionierte, obwohl `mc.py` bei allen "6/6 Dateien" meldete. `qwopus3.6-
27b-v2` war dabei am gravierendsten betroffen: Es referenzierte sechs
eigene "Stub"-Dateien für die Material-Komponenten, schrieb aber nur zwei
davon tatsächlich — die App kompilierte ohne manuellen Fix gar nicht erst.

**Fazit:** Sobald die Aufgabe eine Bibliothek verlangt, die seltener in
Trainingsdaten vorkommt als React+HTML, bricht die Trefferquote massiv ein
— selbst bei den fünf zuverlässigsten Modellen des gesamten Tages. Die
Fehler liegen dabei nicht im gewohnten Bereich (JSON-Formatierung,
fehlende 404-Prüfung), sondern in einer neuen Kategorie: **plausibel
aussehende, aber falsche Annahmen über die tatsächliche API einer
UI-Bibliothek** — Fehler, die weder `mc.py`s Syntax-Validierung noch ein
einfacher Dateicheck aufdecken, sondern nur ein echter Blick in den
laufenden Browser.

### 9.21 Die Konsequenz: mc.py lernt, sich selbst zu überprüfen

Die Bilanz der Abschnitte 9.15–9.20 lässt sich in einem Satz zusammenfassen:
**Fast alle wirklich schlimmen Bugs des Tages hätte nur echte Ausführung
gefunden — keine noch so gute statische Prüfung.** Die Card-Halluzination
wäre bei einem `ls node_modules/@material/web/` sofort aufgeflogen. Der
Browserslist-Fehler crasht beim ersten `npm start` mit einer klaren Meldung.
Der `address`/`adresse`-Mismatch wäre in der ersten curl-Antwort als leeres
Feld sichtbar gewesen. Und ein reines „Selbst-Review" (den Code nochmal
lesen lassen) hätte all das NICHT gefunden — die Wissenslücke, die den
Fehler verursachte, bleibt beim erneuten Lesen dieselbe.

Daraus wurde die nächste Ausbaustufe von `mc.py`: ein **Check-Modus**
(`--check` bzw. `MC_CHECK=1`), bewusst schlank gehalten (~90 Zeilen):

- **`run` kann jetzt Dauerläufer:** `{"action":"run","command":"…",
  "background":true}` startet z.B. einen Flask-Server, liefert die ersten
  Sekunden Ausgabe zurück und lässt ihn weiterlaufen; alle Hintergrund-
  prozesse werden am Ende automatisch beendet (Prozessgruppen-Kill via
  `atexit`). Dazu ein optionales `"timeout"` (max. 300 s) für langsame
  Builds.
- **finish wird verweigert, solange nicht real geprüft wurde:** Im
  Check-Modus akzeptiert `mc.py` ein finish erst, wenn nach der letzten
  Dateiänderung mindestens ein Vordergrund-`run` mit `exit=0` durchlief.
  Ein gestarteter Server allein zählt dabei nicht — erst der curl-Test
  dagegen ist der Beweis. Jede neue Dateiänderung setzt die Uhr zurück:
  wer nach dem Fix nicht erneut testet, bekommt das finish wieder
  abgelehnt.
- **Der System-Prompt gibt die Prüf-Rezeptur vor:** Dependencies
  installieren, Build/Syntax prüfen, Dienst im Hintergrund starten, per
  curl testen — ausdrücklich auch die Fehlerfälle („unbekannte ID sollte
  404 liefern, nicht Erfolg", die Lektion aus dem stillen-Erfolg-Befund).
- **Nachschlagen statt raten — jetzt als generelle Regel:** Unabhängig vom
  Check-Modus ermuntert der Prompt das Modell, bei API-Unsicherheit real
  nachzusehen (`ls node_modules/<paket>/`, `pip show`, curl gegen den
  eigenen Endpunkt). Was nachgeschlagen wurde, kann nicht halluziniert
  sein.
- **Eine kleine Notbremse für `--yes`:** Offensichtlich destruktive
  Kommandos (sudo, rm auf Wurzelpfade, dd auf Devices, mkfs …) werden
  abgelehnt, bevor sie den Bestätigungs-Mechanismus überhaupt erreichen —
  relevant, weil im Batch-Betrieb jede run-Anfrage automatisch genehmigt
  wird.

Bewusst weggelassen: echtes Sandboxing (Container, chroot), automatische
Browser-Tests, Framework-spezifische Testrunner — das würde den Charakter
des Mini-Tools sprengen. Die Wette ist: allein die Rückkopplung „führe aus,
lies die echte Fehlermeldung, reagiere" hebt die Qualität spürbar, weil sie
genau die Fehlerklasse angreift, die heute dominierte.

**Der nächste Schritt ist damit vorgezeichnet:** Die beiden besten Modelle
des Tages bekommen dieselben Aufgaben erneut — diesmal mit `--check` und
großzügigerem Schrittbudget. Die spannende Frage: Findet und behebt ein
lokales 26B-Modell seine eigene Card-Halluzination, wenn ihm das Tool die
echte `npm start`-Fehlermeldung vor die Nase hält? Nach allem, was dieser
Tag gezeigt hat, wäre das der Unterschied zwischen „Code-Generator" und
„Coding-Agent".

### 9.22 Vorab dokumentiert: der erste echte Selbsttest-Lauf

Bevor der Lauf startet, noch eine winzige Lücke geschlossen: `--plan` und
`--yes` schlossen sich bisher gegenseitig aus (`plan_mode = args.plan and
not AUTO_YES`) — sinnvoll für interaktive Nutzung, aber unbrauchbar fuer
einen unbeaufsichtigten Batch-Lauf, der trotzdem mit einem Plan beginnen
soll. Da `plan_phase()` sein `input()` bei EOF ohnehin schon als „Plan
akzeptiert, weiter" behandelt, reichte es, die Bedingung auf `plan_mode =
args.plan` zu vereinfachen — im nicht-interaktiven Kontext (kein `stdin`,
z.B. unter `nohup`) läuft der Plan dann automatisch durch, statt komplett
zu entfallen. Sanity-Check bestanden: Plan wird angezeigt, `EOFError`
greift, Umsetzung startet automatisch.

**Der Testlauf, der jetzt folgt** (Ergebnis in 9.23): dasselbe Modell wie
in 9.20 (`gemma-4-26b-a4b-it@mxfp4`, dort an der Card-Halluzination und dem
kleingeschriebenen `oninput` gescheitert), diesmal mit Plan-Phase UND dem
neuen Check-Modus — UND mit Vite (https://github.com/vitejs/vite) statt
Create React App, um gleich das gesamte Browserslist/`react-scripts`-Bug-
Nest aus 9.15–9.20 zu umgehen. Anders als bei allen bisherigen Läufen
dieses Tages darf das Modell diesmal selbst installieren und ausführen —
das war den ganzen Tag über per Prompt-Anweisung ausdrücklich verboten,
weil `mc.py` es bis eben nicht konnte.

Exakter Aufruf:

```bash
python3 mc.py --base-url http://192.168.178.79:1234/v1 \
  --model "gemma-4-26b-a4b-it@mxfp4" \
  --yes --plan --check --max-steps 60 \
  "$(cat beispiel-prompts/prompt_vite_material_check.txt)"
```

Der Prompt (`beispiel-prompts/prompt_vite_material_check.txt`):

> Erstelle eine CRUD-Webanwendung 'Personenverwaltung'. BACKEND in backend/:
> Flask + SQLite (Datei personen.db), Tabelle person mit Spalten id
> (autoincrement), name, adresse, telefon. REST-API mit flask-cors: GET
> /api/persons (alle), POST /api/persons (anlegen), PUT /api/persons/<id>
> (bearbeiten), DELETE /api/persons/<id> (loeschen). Tabelle beim Start
> automatisch anlegen. FRONTEND in frontend/: React-App, erstellt mit Vite
> (https://github.com/vitejs/vite, z.B. via 'npm create vite@latest
> frontend -- --template react'), die fuer die UI-Komponenten die Material
> Web Components Bibliothek (@material/web,
> https://github.com/material-components/material-web/tree/main/docs)
> verwendet statt einfacher HTML-Elemente (z.B. md-outlined-text-field,
> md-filled-button, md-outlined-button, md-list/md-list-item). Die App
> zeigt alle Personen in einer Liste und erlaubt Anlegen, Bearbeiten und
> Loeschen ueber ein Formular; spricht das Backend per fetch auf
> http://localhost:5000 an. Installiere alle noetigen Abhaengigkeiten
> (npm install im Frontend, pip install -r requirements.txt im Backend in
> einer venv) und PRUEFE deine Arbeit wirklich: starte Backend und den
> Vite-Dev-Server im Hintergrund und teste alle vier REST-Endpunkte per
> curl, inklusive Fehlerfaellen (PUT/DELETE auf eine nicht existierende ID
> sollte 404 liefern, nicht stillschweigend Erfolg). Behebe alle Fehler,
> bevor du finish aufrufst.

Bewusste Unterschiede zu allen bisherigen Läufen des Tages: kein Verbot von
npm/pip-Installation mehr (das war nötig, weil `mc.py` bislang nichts
ausführen konnte); explizite Nennung des stillen-Erfolg-Bugs aus 9.15/9.19
als Testfall; Vite statt Create React App. Ansonsten identische
Anforderung wie in 9.20, für einen fairen Vorher-Nachher-Vergleich.

Geplantes Vorgehen für den Lauf selbst: `mc.py` bekommt die Aufgabe, baut
seinen Plan, setzt ihn um und prüft sich mit `--check` so lange selbst,
bis es `finish` meldet oder das Schrittlimit erreicht — ohne Eingriffe
währenddessen. Danach folgt eine externe Prüfung nach demselben Muster wie
in 9.15–9.19 (Code lesen, Backend/Frontend selbst nochmal starten,
Browser-Screenshot), um zu sehen, ob der Selbsttest hält, was er
verspricht.

### 9.23 Das Ergebnis: echte Fortschritte, ein entscheidender blinder Fleck

Der Lauf war nach 23 von 60 möglichen Schritten und 330 Sekunden fertig
(`finish` akzeptiert). Das Protokoll zeigt zunächst genau das erhoffte
Verhalten:

**Was tatsächlich funktionierte:**
- Ein waschechtes Vite-Projekt wurde per `npm create vite@latest` erzeugt
  (`main.jsx`, `.oxlintrc.json`, `package-lock.json` — keine
  handgeschriebene Fake-Struktur) und `@material/web` in einer echten,
  existierenden Version installiert (`2.4.1` — anders als die frei
  erfundene `^0.1.0` aus 9.20).
- Ein Tippfehler (`Flask(____name__)` statt `Flask(__name__)`) wurde noch
  vor dem ersten Ausführungsversuch selbst korrigiert.
- **Das Modell stieß auf denselben AirPlay-Port-5000-Konflikt, den wir
  heute früh manuell entdeckt hatten** — und reagierte genau richtig: den
  echten Fehler „Address already in use" gelesen, `app.py` angepasst,
  Port 5001 probiert, wieder blockiert, Port 5002 probiert — dort lief es.
  Kein geraten, sondern eine echte Fehler→Reaktion→Fehler→Reaktion-Kette.
- Backend-404-Verhalten bei PUT/DELETE auf unbekannte IDs: korrekt
  implementiert (unabhängig geprüft).

**Der entscheidende blinde Fleck:** Das Modell testete ausschließlich GET
und POST gegen das Backend per curl — PUT, DELETE und die im Prompt
*ausdrücklich* geforderten Fehlerfälle (404 bei unbekannter ID) wurden
**nie** ausgeführt. Die finale `finish`-Zusammenfassung behauptet trotzdem:
„wurde erfolgreich implementiert […] mit allen CRUD-Operationen sowie
Fehlerfällen getestet" — eine nachweislich falsche Aussage, die dem Log
widerspricht.

**Das Frontend war komplett kaputt, und das Modell hat es nie bemerkt.**
`App.jsx` importiert `{ Button, Textfield, List, ListItem, Divider } from
'@material/web/button'` — ein Verzeichnis-Import mit erfundenen benannten
Exporten, kommentiert als „notwendig für Typisierung, aber wir nutzen die
Custom Elements direkt" (der Import wird nirgends im Code verwendet).
Sowohl `npm run dev` (Vite-Fehler-Overlay: „Failed to resolve import
'@material/web/button'") als auch — entscheidend — **`npm run build`**
schlagen dadurch mit klarem `exit≠0` fehl. Die App lädt in keinem Browser.

Gefunden wurde das mit genau den Mitteln, die auch das Modell selbst zur
Verfügung hatte — keine Spezialwerkzeuge, kein Quellcode-Verständnis
nötig, nur Ausführung:

```bash
cd frontend && npm install                       # installiert @material/web@2.4.1 real
npm run build
# ✗ Build failed in 536ms
# Error: [vite]: Rolldown failed to resolve import "@material/web/button"
#   from ".../src/App.jsx". This is most likely unintended because it
#   can break your application at runtime.
```

Vite/Rolldown lösen beim Build (und beim Start des Dev-Servers) JEDEN
Import-Pfad gegen echte Dateien auf der Platte auf — ein nicht existierender
Export wird dabei zwangsläufig sichtbar, ganz ohne Browser oder
Codeverständnis. Das ist derselbe Mechanismus, der auch die zusätzliche
404-Lücke beim Backend offenlegte:

```bash
curl -X PUT http://localhost:5003/api/persons/999 -d '{"name":"X"}'   # -> 404, korrekt
curl -X DELETE http://localhost:5003/api/persons/999                  # -> 404, korrekt
```

Beide Endpunkte waren tatsächlich korrekt implementiert — nur eben nie vom
Modell selbst aufgerufen, obwohl es exakt dieselbe `curl`-Aktion schon für
GET/POST genutzt hatte. Der Unterschied zwischen „gefunden" und „nicht
gefunden" war in beiden Fällen nicht Wissen, sondern schlicht: ausführen
oder nicht.

**Warum das besonders ärgerlich ist:** Der Check-Modus-Systemprompt nennt
`npm run build` wörtlich als Beispiel für einen browserlosen Build-Check
(„Syntax/Build pruefen (z.B. […], npm run build, […])"). Das Modell hatte
das Werkzeug UND die Anleitung, den Fehler ganz ohne Browser zu finden —
und hat es nicht genutzt. Es begnügte sich mit der (unvollständigen)
Backend-Prüfung und erklärte die Aufgabe für erledigt, mit der
ausdrücklichen Begründung „Da ich das Frontend nicht in einem Browser
testen kann".

**Die Lücke liegt im Gate selbst:** `--check` verlangt aktuell nur „irgend
ein Vordergrund-`run` mit `exit=0` seit der letzten Änderung" — nicht, dass
die zuletzt geänderte Komponente auch tatsächlich geprüft wurde. Ein Modell
kann diese Bedingung technisch korrekt erfüllen (Backend curl-testen) und
das Frontend dabei komplett ignorieren. Das Gate zu verschärfen (z.B.:
wurden `.jsx`-Dateien angefasst, muss ein `npm run build` seit der letzten
Aenderung durchgelaufen sein) wäre der naheliegende nächste Schritt —
bewusst hier nur benannt, nicht mehr umgesetzt, um das Werkzeug nicht
programmspezifisch aufzublähen.

**Nebenbefund zur Prozessführung:** Das Modell startete den Backend-Server
nicht über die neue `"background":true`-Aktion, sondern per klassischem
Shell-`&` innerhalb eines normalen `run`-Kommandos
(`cd backend && python3 app.py & sleep 3 && curl …`). Das funktioniert,
entzieht den Hintergrundprozess aber meinem `BG_PROCS`-Tracking — der
`atexit`-Aufräumer konnte ihn nicht erfassen, ein verwaister Python-Prozess
blieb nach Laufende auf dem Rechner aktiv (manuell nachträglich beendet).
Auch das eine Lücke für eine spätere Version.

**Einschränkung der Fairness:** Diesmal tauchte keine Card-Komponenten-
Halluzination auf wie bei allen fünf Modellen in 9.20 — aber das liegt
schlicht daran, dass der heutige Prompt `md-elevated-card` gar nicht mehr
als Beispiel nannte (ein Versehen beim Formulieren, keine Verbesserung
durch `--check`). Der `oninput`-Bug (klein statt `onInput`) dagegen trat
**identisch erneut** auf — derselbe Fehler wie beim exakt selben Modell in
9.20, diesmal wie damals nie durch echten Test aufgedeckt, weil das
Frontend gar nicht erst lief.

**Fazit:** Der Check-Modus hat in diesem ersten echten Testlauf bewiesen,
dass er funktioniert — das Modell reagierte nachweislich auf reale
Fehlermeldungen (Port-Konflikt) statt zu raten. Er hat aber auch gezeigt,
dass „kann sich selbst prüfen" nicht automatisch „prüft sich vollständig"
bedeutet: Ein zu locker formuliertes Prüfkriterium lässt sich durch
Teilprüfung erfüllen, während der eigentlich kritische Teil (das Frontend,
genau der Teil mit der neuen, unbekannten Bibliothek) unangetastet bleibt
— und die abschließende Zusammenfassung das sogar aktiv verschleiert.

### 9.24 Die Konsequenz: die Plan-Phase fragt jetzt nach Prüfschritten

Direkt aus dem 9.23-Fund abgeleitet: Der generische Hinweis „prüfe deine
Arbeit" reichte nicht, weil das Modell selbst entscheiden konnte, was als
„geprüft" zählt — und entschied sich für die bequeme Teilprüfung (nur
Backend). Die Lösung greift eine Zeile früher an: die Plan-Phase
(`--plan`) fragt jetzt, wenn `--check` aktiv ist, explizit nach einem
eigenen Abschnitt „Pruefschritte:" mit den **konkreten Kommandos** für
jeden Aufgabenteil — Backend UND Frontend/Build getrennt, inklusive
Fehlerfällen. Weist das Check-Gate ein verfrühtes `finish` zurück, zitiert
es nicht mehr eine generische Checkliste, sondern **das selbst genannte
Prüfprogramm des Modells wörtlich zurück** — es wird an seinem eigenen
Versprechen gemessen, nicht an einer abstrakten Regel. Fallback ohne
erkannten Abschnitt: der komplette Plan wird als Kontext genutzt; ohne
`--plan` bleibt die alte generische Meldung aus 9.21 bestehen.

**Zwischenstand (Lauf läuft während dieser Zeilen, ohne Eingriff):**
identischer Aufruf wie in 9.22/9.23, für einen sauberen Vorher-Nachher-
Vergleich mit derselben Aufgabe und demselben Modell:

```bash
python3 mc.py --base-url http://192.168.178.79:1234/v1 \
  --model "gemma-4-26b-a4b-it@mxfp4" \
  --yes --plan --check --max-steps 60 \
  "$(cat beispiel-prompts/prompt_vite_material_check.txt)"
```

Nach 35 Schritten (deutlich mehr als die 23 aus dem ersten Lauf) steckt
das Modell erneut in einer Port-Konflikt-Kette — diesmal offenbar mit
mehreren gleichzeitig laufenden Backend-Instanzen aus vorherigen
Versuchen, die selbst erzeugte Kollisionen verursachen. Das eigentliche
Ergebnis (Dauer, ob das Frontend diesmal wirklich per `npm run build`
geprüft wird, ob derselbe `oninput`-Bug erneut auftritt) folgt in 9.25,
sobald der Lauf — ohne weitere Eingriffe — von selbst zu `finish` oder ans
Schrittlimit kommt.

**Dieser Lauf wurde abgebrochen, bevor er ein Ergebnis lieferte** — nicht
wegen des Schrittlimits, sondern wegen eines neuen, ernsteren Fundes
mitten im Protokoll:

```
» write_files 2 Datei(en):
   backend/requirements.txt (24 Zeichen)
   frontend/package.json (84 Zeichen)
(auto-yes)
✓ 2 Datei(en) geschrieben:

── Schritt 2 ─────────────────────────────
Ich habe versehentlich write_files verwendet, anstatt die Dateien zu
lesen. Ich korrigiere dies nun und lese die tatsaechlichen Inhalte...
```

Das Modell wollte zwei bereits bestehende Konfigurationsdateien nur
ANSEHEN (um z.B. den richtigen Start-Befehl zu ermitteln), griff aber zur
Schreib- statt zur Lese-Aktion — und überschrieb beide dabei mit fast
nichts. Es bemerkte den eigenen Fehler sofort im nächsten Satz, aber der
Schaden war angerichtet: Der echte Inhalt beider Dateien war weg. Mit
`--yes` gibt es keine interaktive Rückfrage, die das hätte abfangen
können — genau die Voraussetzung, die einen unbeaufsichtigten Lauf erst
möglich macht, nimmt hier auch die letzte Bremse weg.

**Die Abhilfe liegt nicht im Prompt, sondern im Tool selbst:**
`_shrink_warning()` vergleicht vor jedem Schreiben die bisherige
Dateigröße mit der neuen. Schrumpft eine bereits substantielle Datei
(>40 Zeichen) auf unter 40 % ihrer Größe, wird eine deutliche
ACHTUNG-Meldung in die Ergebnis-Antwort geschrieben — kein Blocker (der
Schreibvorgang gelingt weiterhin, denn manchmal ist drastisches Kürzen
gewollt), sondern eine Rückmeldung, die das Modell im nächsten Zug sieht
und auf die es reagieren kann. Fügt sich damit in dasselbe Muster wie die
bestehende Syntax-Validierung ein, statt eine neue Bestätigungsschicht
einzuführen, die mit `--yes` ohnehin wirkungslos wäre. Isoliert an fünf
Szenarien getestet (Erstanlage, drastisches Schrumpfen, legitimes leichtes
Kürzen, `write_files`-Variante, brandneue Datei) — korrekt erkannt, keine
Fehlalarme.

**Zweite Anpassung für den nächsten Versuch:** Die wiederkehrende
Port-Konflikt-Kette (AirPlay auf 5000, dann Kollisionen mit eigenen
vorherigen Backend-Instanzen über mehrere Läufe hinweg) kostete in beiden
bisherigen Läufen unnötig viele Schritte. Der Prompt bekommt deshalb feste
Ports vorgeschrieben statt freier Wahl: Backend **5010**, Vite-Dev-Server
**8095** — als eigene Datei
[`beispiel-prompts/prompt_vite_material_check.txt`](beispiel-prompts/prompt_vite_material_check.txt) jetzt
mit im Repository statt nur im Scratchpad.

Der dritte Versuch mit beiden Korrekturen folgt in 9.25.

**Nachtrag — würde Git das verhindert haben?** Die naheliegende Frage:
`mc.py` hat doch schon einen Git-Mechanismus. Die ehrliche Antwort: nein,
nicht in diesem Fall. Der bestehende `GIT_ROLLBACK` lief nur interaktiv
(`if not AUTO_YES`) und bot selbst dann nur EINEN Rollback ganz am Ende
des Laufs an — keine Zwischen-Sicherungspunkte. Die gute Version von
`requirements.txt` wurde innerhalb desselben Laufs erzeugt UND zerstört,
ohne dass je ein Commit dazwischenlag; ein `git diff` gegen den
Stand-vor-dem-Lauf hätte nur "Datei komplett neu" gezeigt, nicht "Inhalt
zwischen Schritt 1 und 2 verloren". Und bei `--yes` war die gesamte
Git-Absicherung ohnehin komplett abgeschaltet — genau der Lauf-Typ, der
sie am nötigsten hätte.

Behoben mit einer bewusst schlanken Lösung statt Auto-Commit nach jedem
Schritt (das hätte die Historie mit vielen Zwischenständen vollgemüllt):
Ein neuer Merker `CLEAN_FINISH` ist nur dann `True`, wenn der Lauf über ein
echtes `finish` OHNE offene Probleme endet (nicht bei Schrittlimit oder
stillem Prosa-Ende). Nur dann wird automatisch committet — auch
unbeaufsichtigt bei `--yes`, mit der `finish`-Zusammenfassung als
Commit-Message. Endet ein Lauf unsauber, bleibt es bei der bisherigen
Logik (Rollback-Angebot bei interaktiver Nutzung); automatisches
*Verwerfen* ohne Rückfrage bleibt bewusst aus — das Risiko, ungefragt
etwas zu löschen, wiegt schwerer als das Risiko, einen unfertigen Stand
unangetastet liegen zu lassen. Isoliert und über die echte CLI getestet:
sauberer finish → Commit, Baum danach sauber; unsauberer Abschluss → kein
Auto-Commit, Datei bleibt unangetastet uncommitted.

**Zwei weitere Funde aus einem eigenen, parallelen Testlauf des Nutzers**
(anderes Arbeitsverzeichnis `test3/`, mit einem älteren, unvollständigen
Prompt-Entwurf ohne Material-Web-Anforderung — daher unten kein
`oninput`/Card-Bug, weil die Bibliothek nie angefragt wurde):

Backend und Frontend selbst waren einwandfrei (live nachgeprüft: korrekte
404-Behandlung, Anlegen über die echte UI funktioniert) — aber die vom
Modell mitgelieferten `run_all.sh`/`stop_all.sh`-Skripte verhinderten
genau das. Der Bug:

```bash
cd backend && python3 app.py &     # laeuft in einer SUBSHELL (wegen &)
cd ../frontend && npm run dev &    # bezieht sich weiterhin aufs ALTE cwd!
```

Das erste `cd backend` ändert das Arbeitsverzeichnis des Skripts selbst
nicht (nur das der Subshell). `cd ../frontend` scheitert deshalb mit
„No such file or directory" — das Frontend startet nie. Ein klassischer
Shell-Fallstrick, den auch `--check` nicht automatisch gefunden hätte,
denn: Hätte das Modell `./run_all.sh` selbst ausgeführt und den Frontend-
Port getestet, wäre der Fehler sofort sichtbar gewesen — aber die
Skripte waren offenbar das *Ergebnis* der Prüfung, nicht Teil davon.
Zweiter, kleinerer Fund: `stop_all.sh` killt Port 5173 (Vites Standard),
obwohl `vite.config.js` korrekt `5091` fest eingestellt hatte
(`strictPort: true`) — das Skript hätte den Server nie sauber beendet.

**Die praktische Rückfrage, die daraus folgte:** `mc.py` wird oft aus
einem separaten, frischen Projektverzeichnis heraus genutzt, das noch gar
kein Git-Repo ist — genau wie `test3/`. Bisher blieb die gesamte
Git-Absicherung dort wirkungslos, weil `git_usable()` nur „kein
Git-Repository" meldete und aufgab. Neue Funktion `git_auto_init()`:
Findet `mc.py` beim Start kein Repo vor, legt es automatisch eines an,
ergänzt eine `.gitignore` (`node_modules/`, `venv/`, `__pycache__/`,
`*.db`, `dist/`, `build/`, `.DS_Store` — nur falls noch keine vorhanden
ist) und committet den Ausgangszustand als Baseline. Risikoarm und
jederzeit rückgängig zu machen (nur ein lokales `.git`-Verzeichnis, kein
Remote, kein Push). Wichtig: nur bei ECHT fehlendem Repo — ein bereits
vorhandenes, aber unsauberes Repo (offene Änderungen) bleibt unangetastet,
kein automatischer Eingriff in bestehende Arbeit.

Vier Szenarien über die echte CLI verifiziert: frisches leeres
Verzeichnis (Auto-Init + Baseline-Commit), bestehendes Verzeichnis mit
`node_modules` (korrekt durch `.gitignore` ausgeschlossen, nur
Quelldateien landen im Baseline-Commit), bereits sauberes Repo
(unangetastet, normale Funktion), bereits unsauberes Repo (keine
Absicherung, aber auch kein Eingriff).

### 9.26 Der Härtetest der Tag-Erkenntnisse: Tailwind statt Material Web

Eine abschließende, aufschlussreiche Frage: Wie baut man mit einem lokalen
Modell eigentlich eine Anwendung, die auch optisch etwas hermacht? Die
Antwort liegt direkt in den Lektionen des Tages — Material Web Components
(Abschnitte 9.20–9.25) ist eine relativ neue, wenig verbreitete
Custom-Element-Bibliothek und produzierte praktisch in jedem Lauf
Halluzinationen (nicht existierende Card-Komponente, falsche Import-Pfade,
ein kleingeschriebenes `oninput` statt `onInput`). Die naheliegende
Gegenprobe: Ein neuer Prompt (`beispiel-prompts/prompt_cool_tailwind.txt`) verlangt
**Tailwind CSS statt Material Web Components** — extrem gut dokumentiert,
massenhaft in Trainingsdaten vertreten — bewusst **per CDN-Skript-Tag**
(`<script src="https://cdn.tailwindcss.com">`) statt als npm-Paket, um
zusätzlich jedes Modul-Auflösungsrisiko zu umgehen, das den Tag über für
Ärger sorgte. Dazu eine **konkrete Design-Vorgabe** statt „mach es
hübsch": zentrierte Karte, Weißraum, ein einheitlicher Akzentton, farblich
unterscheidbare Aktions-Buttons.

Getestet mit `gemma-4-26b-a4b-it@mxfp4` gegen die M4 Pro
(`http://192.168.178.191:1234/v1`), mit `--plan --check`, ohne Eingriff
während des Laufs:

```bash
python3 mc.py --base-url http://192.168.178.191:1234/v1 \
  --model "gemma-4-26b-a4b-it@mxfp4" \
  --yes --plan --check --max-steps 60 \
  "$(cat beispiel-prompts/prompt_cool_tailwind.txt)"
```

**Das Ergebnis übertraf die Erwartungen deutlich:**

| | Material Web (9.20–9.25) | Tailwind CSS (dieser Lauf) |
|---|---|---|
| Zeit | 160–1327 s über mehrere Versuche | **160 s** — schnellster Lauf des ganzen Experiments |
| Prüfschritte aus dem eigenen Plan | Nur Backend getestet, Frontend nie | **GET/POST/PUT/DELETE + der 404-Fehlerfall** vollständig abgearbeitet |
| `"background":true` genutzt | Nein, riskantes Shell-`&` | **Ja**, korrekt über die neue Aktion |
| Visuell | Kaputt oder gar nicht ladbar | **Sieht tatsächlich gut aus** — zentrierte Karte, Indigo-Akzent, Schatten, sauberer Weißraum |
| Live funktionsfähig? | Nie vollständig | **Ja** — Anlegen, Bearbeiten, 404-Fehlerfälle unabhängig verifiziert |

Das Backend (SQLAlchemy, korrekte 404-Behandlung bei PUT/DELETE) war
tadellos; das Frontend nutzt ausschließlich Standard-`<input>`/`<button>`-
Elemente mit Tailwind-Utility-Klassen — die ganze Fehlerklasse „falsche
Annahme über eine Custom-Element-API" ist damit strukturell gar nicht
mehr möglich. Das Ergebnis erfüllt die Design-Vorgabe fast exakt: weißer
Karten-Hintergrund mit Schatten und abgerundeten Ecken auf hellgrauem
Seitenhintergrund, `bg-indigo-600` als Akzent, klare Typografie-Hierarchie,
farblich unterschiedene Bearbeiten-/Löschen-Aktionen — sogar ein
Lade-Zustand für den Button, der nirgends explizit verlangt war.

**Fazit, das den ganzen Tag zusammenfasst:** Die Wahl der Bibliothek ist
der wirksamste Hebel für Qualität bei einem lokalen Modell — wichtiger als
Prompt-Formulierungen, die auf „Sorgfalt" abzielen. Gleichzeitig zeigen
sich hier beide `mc.py`-Erweiterungen von heute (Prüfschritte aus der
Plan-Phase, `"background":true`) zum ersten Mal gemeinsam wirksam: Das
Modell hielt sich an sein eigenes, vorher genanntes Testprogramm, statt
sich mit einer Teilprüfung zufriedenzugeben.

## 10. Vibelove — ein Lovable-artiger App-Builder auf Basis von `mc.py`

Der Tag endet mit einem größeren, ehrgeizigeren Experiment: Kann `mc.py`
nicht nur einzelne Aufgaben abarbeiten, sondern als **Motor für einen
eigenen App-Builder** dienen — im Stil von [Lovable](https://lovable.dev)
(chatbasiert, Live-Vorschau, iterative Verfeinerung)? Das neue
Unterprojekt heißt **Vibelove** und lebt in `vibelove/` im selben Repo.

**Rollenverteilung, bewusst so festgelegt:** `mc.py` (mit
`gemma-4-26b-a4b-it@mxfp4` auf der M4 Pro) soll den Code **selbst bauen,
in Etappen** — nicht ich. Meine Rolle ist die eines Copiloten: Ich
entwerfe die **Architektur-Entscheidungen im Voraus** (damit sich das
Modell nicht in Systemdesign-Fragen verliert, die es aus eigenem Antrieb
kaum treffen könnte — Gemma kennt Lovable nicht), schreibe die Prompts,
und begleite/prüfe die Läufe. Der eigentliche Code entsteht durch `mc.py`.

**Die zentrale Architektur-Weichenstellung**, die ich vorab treffen
musste, weil sie sonst mit hoher Wahrscheinlichkeit schiefgegangen wäre:
`mc.py` darf beim Bauen **keinen dauerhaften Dev-Server hinterlassen** —
ein von `mc.py` selbst per `"background":true` gestarteter Prozess wird
beim Programmende automatisch beendet (`atexit`/`kill_bg_procs()`, siehe
weiter oben). Für eine echte Live-Vorschau, die auch **nach** einem
`mc.py`-Lauf noch läuft, muss **Vibelove selbst** den Vite-Dev-Server
unabhängig verwalten — einmalig gestartet, unabhängig vom Lebenszyklus
jedes einzelnen `mc.py`-Aufrufs. Diese Trennung steht explizit im Prompt.

### Etappe 1: das Grundgerüst

Bewusst klein geschnitten (Chat-Verlauf/iteratives Verfeinern kommt erst
in Etappe 2), um dem Modell eine realistische Chance zu geben:

```bash
cd vibelove
python3 ../mc.py --base-url http://192.168.178.191:1234/v1 \
  --model "gemma-4-26b-a4b-it@mxfp4" \
  --yes --plan --check --max-steps 60 \
  "$(cat ../prompt_vibelove_stage1.txt)"
```

Der volle Prompt-Text steht in
[`beispiel-prompts/prompt_vibelove_stage1.txt`](beispiel-prompts/prompt_vibelove_stage1.txt) im Repo-Root.
Kernpunkte der Vorgabe:

- **Struktur:** `server.py` (Flask, fest Port 5050), `templates/index.html`
  (Formular + Log links, `<iframe>` auf Port 5173 rechts, Tailwind per
  CDN), `workspace/` als Zielverzeichnis für die eigentliche, vom Nutzer
  gewünschte Anwendung — `mc.py` arbeitet nie in Vibelove selbst, immer
  nur in `workspace/`.
- **Der `/build`-Endpunkt** ruft `mc.py` per `subprocess.run` mit
  `--dir workspace --yes --check` auf und hängt automatisch einen fest
  vorgeschriebenen Zusatzsatz an jede Anweisung an, der das
  Hintergrund-Server-Verbot durchsetzt.
- **Die Vorschau-Verwaltung** ist explizit **von `mc.py` entkoppelt**:
  `server.py` startet den Vite-Server für `workspace/frontend/` selbst
  (`subprocess.Popen`, `start_new_session=True`), prüft vorher ob Port
  5173 schon belegt ist, und hält ihn über mehrere Bau-Anfragen hinweg am
  Leben.
- **Sofort ein lauffähiger Startzustand**: ein Platzhalter-Vite+React-
  Projekt in `workspace/frontend/` (wieder Tailwind per CDN statt
  Material Web — konsequent aus 9.26 übernommen), damit die Vorschau beim
  ersten Start nicht leer ist.
- **Eigene Backend-Ports** für spätere gebaute Anwendungen fest auf 5090
  (5000 ist durch macOS AirPlay belegt — dieselbe Lektion wie den ganzen
  Tag über).

Der Lauf ist gestartet; Ergebnis und Bewertung folgen im nächsten
Abschnitt.

### 10.1 Etappe 1, Ergebnis: eine Wiederholungsschleife, zwei echte
`mc.py`-Lücken, drei kleine Nacharbeiten — am Ende funktioniert der volle
Kreislauf

**Wichtige Leitplanke vorab, vom Nutzer während des Laufs bekräftigt:**
`mc.py` bleibt ein eigenständiges, allgemeines Werkzeug — Vibelove ist nur
ein Anwendungsfall davon. Ein Fund führt nur dann zu einer Änderung an
`mc.py` selbst, wenn er ein **grundsätzliches** Problem ist, das JEDE
Aufgabe treffen könnte. Anwendungsspezifische Fehler im von Gemma gebauten
Vibelove-Code gehören dagegen in einen neuen Prompt, nicht in `mc.py`.
Diese Trennung hat sich im Verlauf als genau richtig erwiesen — zwei Funde
waren grundsätzlich (→ `mc.py` geändert), einer war rein
anwendungsspezifisch (→ nur per Prompt behoben).

**1) Der erste Versuch hängt 36 Minuten in einer Wiederholungsschleife.**
Ein Validierungsfehler in `server.py` (Tippfehler `atesit` statt `atexit`,
verschmolzene Zeilen am Dateiende) löste bei Gemma keine echte Korrektur
aus, sondern einen Monolog: Die komplette Datei wurde immer wieder fast
identisch neu geschrieben, **mit demselben Fehler wieder drin**, ohne
jemals zu einem gültigen Zustand zu kommen. Nach 36 Minuten ohne
Fortschritt manuell abgebrochen.

**Grundsätzlicher Fund #1 → `mc.py` geändert:** `_check_repetition()`
vergleicht bei jedem `write_file`/`write_files` den neuen Inhalt mit der
letzten Version desselben Pfads (`difflib.SequenceMatcher.quick_ratio`).
Ab der dritten fast identischen Version in Folge (>90 % Ähnlichkeit) wird
das Modell explizit zum Strategiewechsel gedrängt: `edit_file` für die
konkrete Stelle nutzen statt die ganze Datei neu zu schreiben. Das ist ein
**allgemeines** Problem — jedes Modell kann bei jeder Aufgabe in so eine
Schleife geraten, deshalb gehört die Erkennung ins Tool, nicht in einen
Prompt.

**2) Retry mit gezieltem Prompt** (nur `server.py` reparieren,
`templates/index.html` und `workspace/frontend/` waren schon fertig, plus
expliziter Hinweis auf den vorherigen Fehlschlag) lief in **73 Sekunden,
10 Schritten**, glatt durch — keine Wiederholungswarnung nötig, keine
Validierungsfehler. Vollständiger Prompt:
[`beispiel-prompts/prompt_vibelove_stage1_retry.txt`](beispiel-prompts/prompt_vibelove_stage1_retry.txt).

**3) Etappe 1b** (187s): Der Retry-Lauf hatte selbst bemerkt, dass
`server.py` den `mc.py`-Aufruf ohne `--model`/`--base-url` absetzt und
damit auf `mc.py`s Standardwerte zurückfällt. Kleiner Folgeauftrag:
zwei Umgebungsvariablen (`VIBELOVE_BASE_URL`, `VIBELOVE_MODEL`) mit
Fallback ergänzen — von Gemma selbst per `curl` verifiziert (Prompt:
[`beispiel-prompts/prompt_vibelove_stage1b.txt`](beispiel-prompts/prompt_vibelove_stage1b.txt)).

**4) Live-Test in der echten Weboberfläche** (nicht nur Code-Review):
Formular ausgefüllt, „Bauen" geklickt, echter `mc.py`-Lauf gegen Gemma
ausgelöst — der komplette Kreislauf funktionierte sichtbar (Button zeigt
„Baue...", Log zeigt „Starte Bauprozess..."). Dabei zwei weitere Funde:

**Grundsätzlicher Fund #2 → `mc.py` geändert:** Nach dem Lauf blieb ein
Vite-Prozess auf einem verschobenen Port (5178, weil 5173 schon belegt
war) übrig — **obwohl der `mc.py`-Subprozess längst beendet war.** Ursache:
ein per `command &` (Shell-Hintergrundstart) gestarteter Prozess wird von
`mc.py`s eigenem `BG_PROCS`/`kill_bg_procs()`-Tracking nicht erfasst, weil
nicht die vorgesehene `"background":true`-Aktion genutzt wurde — exakt die
Lücke, die schon in 9.23 dokumentiert, aber nie behoben wurde. Auch das
ist ein **allgemeines** Problem (jedes `run`-Kommando mit `&` kann das
auslösen), deshalb: `SHELL_BG`-Regex erkennt ein trailendes einzelnes `&`
und hängt bei Erfolg eine deutliche Warnung an („wird NICHT verfolgt und
NICHT automatisch beendet, nutze `\"background\":true`").

**Anwendungsspezifischer Fund → NICHT `mc.py` geändert, nur Prompt:** Eine
Bauanweisung über die UI („ändere den Text zu ...") wurde korrekt
ausgeführt — `App.jsx` enthielt danach nachweislich den neuen Text (per
`curl` auf den Vite-Quelltext bestätigt) — aber im Browser blieb der ALTE
Platzhalter sichtbar. Ursache: `workspace/frontend/index.html` enthielt
seit dem allerersten Lauf nur **statisches HTML** im `body` (der
Platzhaltertext direkt reingeschrieben), aber **kein**
`<div id="root"></div>` und **kein** `<script src="/src/main.jsx">` —
React wurde nie gemountet, `App.jsx` hatte dadurch strukturell **keine
Wirkung**, egal was drin stand. Ein Fehler, den reine „Port antwortet mit
200"-Prüfungen nicht erkennen, weil der Server ja durchaus antwortet — nur
mit dem falschen (statischen) Inhalt statt dem gerenderten React-Baum.
Klar anwendungsspezifisch (ein Fehler im generierten Vibelove-Code, kein
Werkzeug-Problem) → behoben per gezieltem Prompt
([`beispiel-prompts/prompt_vibelove_stage1c.txt`](beispiel-prompts/prompt_vibelove_stage1c.txt)), **nicht**
an `mc.py` selbst. Etappe 1c lief in 56 Sekunden durch.

**Ergebnis nach allen vier Runden, live verifiziert:** Formular links,
Live-Vorschau rechts, eine echte Bauanweisung über die UI ändert
tatsächlich sichtbar die laufende Vorschau — der volle Lovable-artige
Kreislauf funktioniert, mit `mc.py`/Gemma als alleinigem Motor und mir nur
als Prompt-Autor/Prüfer, nicht als Code-Autor der eigentlichen
Vibelove-Logik.

**Gesamtbilanz Etappe 1:** ~40 Minuten Fehlschlag + Diagnose, danach
73 + 187 + 56 = **316 Sekunden reine Bauzeit** über drei gezielte
Nachbesserungsrunden. Zwei `mc.py`-Erweiterungen (Wiederholungserkennung,
`&`-Warnung) sind jetzt dauerhaft im Werkzeug — beide unabhängig von
Vibelove nützlich, für jede zukünftige Aufgabe.

### 10.2 Etappe 2: Mehrfach-Chat-Verlauf

Bisher startete jeder Klick auf „Bauen" `mc.py` komplett neu, ohne
Erinnerung an vorherige Anweisungen — kein echtes iteratives Verfeinern,
eher eine Aneinanderreihung unabhängiger Einzelaufträge. Etappe 2 soll das
beheben. Architektur-Vorgabe (wieder vorab festgelegt, damit Gemma sich
nicht im Session-Design verliert):

- `server.py` sammelt bisherige Bauschritte **im Speicher** (`BUILD_HISTORY`,
  keine Datei/DB) und hängt die **letzten maximal 5** davon als Kontext-Text
  vor jede neue Anweisung an `mc.py` — bewusst gedeckelt, damit der Kontext
  nicht unbegrenzt waechst (dieselbe Sorge wie bei `mc.py`s eigener
  Kontext-Kürzung, nur hier über mehrere Subprozess-Aufrufe hinweg).
- Als „Ergebnis"-Merker pro Runde dienen die letzten ~500 Zeichen der
  `mc.py`-Ausgabe — pragmatisch statt einer fragilen Regex-Extraktion der
  exakten `finish`-Zusammenfassung.
- Neue Route `/reset` leert nur den Chat-Verlauf, **rührt `workspace/`
  nicht an** — der bisherige Baustand bleibt erhalten, nur das Gedächtnis
  wird geleert.
- Frontend: Log-Bereich wird ab jetzt **angehängt** statt ersetzt (mit
  Trennlinie pro Runde, Auto-Scroll nach unten), ein Button „Verlauf
  zurücksetzen", und das Formular leert sich nach einem Build automatisch
  für die nächste Eingabe.

Aufruf (unverändert gegen die M4 Pro, `--check` diesmal ohne `--plan`, da
die Aenderung klein genug ist, um ohne separate Planungsrunde direkt
loszulegen):

```bash
cd vibelove
python3 ../mc.py --base-url http://192.168.178.191:1234/v1 \
  --model "gemma-4-26b-a4b-it@mxfp4" \
  --yes --check --max-steps 30 \
  "$(cat ../prompt_vibelove_stage2_chat.txt)"
```

Vollständiger Prompt:
[`beispiel-prompts/prompt_vibelove_stage2_chat.txt`](beispiel-prompts/prompt_vibelove_stage2_chat.txt).

### 10.3 Etappe 2, Ergebnis: eine zweite, andere Art von Wiederholungsschleife
— und wieder zwei grundsätzliche `mc.py`-Fixes

**1) Der erste Versuch hängt erneut fest — diesmal auf einer tieferen
Ebene.** Nicht wie in Etappe 1 eine wiederholte komplette Dateineuschrift
über mehrere Schritte, sondern eine **Token-Wiederholung innerhalb EINER
einzigen, noch unfertigen Antwort**: Beim Versuch, per `edit_file` einen
`<h1>`-Tag zu ändern, produzierte Gemma wiederholt denselben ungültigen
JSON-Escape (`</h1\>` — ein überflüssiger Backslash vor `>`). Bemerkenswert:
das Modell **erkannte den Fehler im eigenen Fließtext** ("*Ah, ich sehe
es: `</h1\>` war in meiner Antwort. Das ist falsch.*"), reproduzierte ihn
danach aber **identisch erneut** — mehrfach, wortgleich, über mehrere
„Korrekturversuche" hinweg, alles noch bevor überhaupt eine gültige Aktion
zustande kam. Das konnte die in Etappe 1 gebaute `_check_repetition()`
nicht abfangen, weil sie erst NACH einem erfolgreich geparsten
`write_file`/`write_files` greift — hier kam nie eine gültige Aktion
zustande.

**Grundsätzlicher Fund #3 → `mc.py` geändert:** `frequency_penalty: 0.3`
im Request-Payload (Standard-OpenAI-Feld, von inkompatiblen Endpoints
einfach ignoriert) — eine Bremse auf Sampling-Ebene statt auf
Anwendungs-/Protokoll-Ebene, weil das Problem dort entsteht.

**2) Retry mit dem Fix lief durch (121s), aber mit einer Lehre:** Ohne
`--plan` gab es keine selbst genannten Prüfschritte, an denen das
Check-Gate das Modell hätte festhalten können — es reichte ein einziger
`ast.parse`-Syntaxcheck, um `finish` zu akzeptieren, obwohl die im Prompt
verlangten funktionalen `curl`-Tests (zwei aufeinanderfolgende
`/build`-Aufrufe, `/reset`) nie liefen. Genau die aus 9.23 bekannte
Kernschwäche des Check-Gates — diesmal selbst verursacht, weil `--check`
ohne `--plan` gestartet wurde. **Lehre für zukünftige Läufe:** `--check`
entfaltet seine volle Durchsetzungskraft nur zusammen mit `--plan`.

**3) Eigene Live-Verifikation deckte einen echten, aber
anwendungsspezifischen Bug auf:** `templates/index.html` referenzierte im
JavaScript einen Button mit `id="resetButton"`, der im HTML-Markup nie
angelegt wurde — ein `TypeError` beim Laden der Seite, die
„Verlauf-zurücksetzen"-Funktion komplett unbenutzbar. Klar
anwendungsspezifisch → **nicht** an `mc.py` geändert, nur per gezieltem
Prompt behoben (25 Sekunden Laufzeit,
[`beispiel-prompts/prompt_vibelove_stage2_fix.txt`](beispiel-prompts/prompt_vibelove_stage2_fix.txt)).

**4) Beim erneuten direkten Testen desselben `h1`-Änderungsauftrags trat
dieselbe Escape-Wiederholung nochmal auf** — `frequency_penalty` allein
reichte nicht, um sie zu verhindern, nur die Erfolgsquote zu verbessern.

**Grundsätzlicher Fund #4 → `mc.py` geändert:** Statt nur zu bremsen, jetzt
zusätzlich eine **eskalierte Rückmeldung ab dem 2. aufeinanderfolgenden
JSON-Parse-Fehler**: konkreter Hinweis auf das wahrscheinliche
Escaping-Problem, explizite Anweisung den Text NICHT zu wiederholen,
sondern einen kürzeren Ausschnitt zu wählen oder auf `write_file`
auszuweichen. Ergebnis im direkten Retest: Nach dem 2. Fehler wechselte
Gemma **genau wie vorgeschlagen** auf `write_file` mit dem kompletten
Dateiinhalt — Auftrag danach in nur 6 Schritten mit echtem
`npm run build`-Check abgeschlossen.

**Finale End-to-End-Verifikation, mit beiden Fixes, ohne weitere
Störungen:** Zwei aufeinanderfolgende Bauanweisungen über die echte
Weboberfläche — 1. „Ändere die Hauptüberschrift zu 'Vibelove Demo'" (30s),
2. „Ändere die Textfarbe dieser Überschrift zu Rot" (10s, per neuer
CSS-Regel `h1 { color: red; }` sauber umgesetzt) — beide sichtbar in der
Live-Vorschau bestätigt. `/reset` liefert korrekt `OK`. Auffällig: **beide
Läufe waren mit den neuen Fixes drastisch schneller** (30s/10s) als die
vorherigen, von Wiederholungsschleifen geplagten Versuche (mehrere
Minuten bis zum Abbruch) — ein Nebeneffekt, der zeigt, wie teuer
Wiederholungsschleifen in Tempo sind, nicht nur in Zuverlässigkeit.

**Nebenbefund, keine Code-Änderung wert:** Bei den Tests sammelten sich
mehrfach verwaiste `vite`-Prozesse an — überwiegend, weil *ich selbst*
`server.py` beim Testen wiederholt mit `kill -9` statt `kill -TERM`
beendet habe. `SIGKILL` umgeht `atexit`-Handler grundsätzlich (Unix-
Semantik, kein behebbarer Bug) — der von `server.py` selbst verwaltete
Vite-Kindprozess überlebt das dann zwangsläufig. Eigene Lektion: beim
manuellen Testen `kill -TERM` verwenden, nicht `kill -9`.

**Gesamtbilanz Etappe 2:** Zwei weitere `mc.py`-Erweiterungen
(`frequency_penalty`, eskalierte Parse-Fehler-Rückmeldung) sind jetzt
dauerhaft im Werkzeug — beide unabhängig von Vibelove nützlich. Der
Mehrfach-Chat-Verlauf funktioniert vollständig: Formular, Kontext über
mehrere Bauschritte, Verlauf-Reset, alles live über die echte UI
verifiziert.

### 10.4 Etappe 3: Live-Streaming der Build-Ausgabe

Feedback aus der Praxis: Beim Ausprobieren war im Log-Bereich nicht zu
erkennen, was während eines laufenden Builds passiert — nur „Baue..." bis
zum Schluss, dann alles auf einmal. Ursache: `subprocess.run()` im
`/build`-Endpunkt blockiert komplett, bis `mc.py` fertig ist.

**Architektur-Vorgabe** (wieder vorab festgelegt): `subprocess.Popen` statt
`subprocess.run`, ein Flask-Generator mit `stream_with_context`/`Response`,
der jede Zeile sofort weiterreicht, sobald sie ankommt; im Frontend
`response.body.getReader()` statt `response.text()`, damit der Log-Bereich
während des Builds wächst statt am Ende zu erscheinen.

**Der erste Versuch sah im Code richtig aus, funktionierte aber nicht.**
Live-Test per `curl --no-buffer`: nach 3 Sekunden **0 Zeilen** angekommen,
alles erst nach Prozessende auf einmal — trotz korrektem Generator-Code.

**Ursache, durch direkten Vergleich verifiziert:** Ein klassisches
Python-I/O-Detail — `stdout` wird **blockweise statt zeilenweise
gepuffert**, sobald es in eine Pipe statt ein Terminal umgeleitet wird.
Das betrifft `mc.py`s eigenes `print()`, nicht den Streaming-Code von
`server.py`. Test: derselbe Aufruf mit `python3 -u` (unbuffered) lieferte
nach 3 Sekunden bereits **21 Zeilen**, ohne `-u` über `curl` nach 3
Sekunden **0**. Bewusst **nicht** in `mc.py` selbst gelöst — das ist keine
grundsätzliche Eigenschaft des Werkzeugs, sondern eine Anforderung des
*Aufrufers*, der Live-Streaming will. Der Fix gehört an die
Popen-Aufrufstelle in `server.py`: `"python3"` → `"python3", "-u"`.
Zusätzlich behoben: toter Code nach einem `return`, und ein robusterer
Timeout-Mechanismus (`select.select()` statt eines blockierenden
`readline()`, das den 900s-Timeout bei einem kompletten Hänger nie hätte
greifen lassen).

**Ergebnis, verifiziert über drei Messpunkte per `curl --no-buffer`:**
8 Zeilen nach 3s, 22 nach 6s, 47 final — echtes, kontinuierliches
Wachstum statt Alles-oder-Nichts. Über die echte UI bestätigt: Log-Bereich
füllt sich sichtbar während `mc.py` noch arbeitet.

**Nebenbefund beim Code-Review — Scope-Creep trotz expliziter Vorgabe:**
Der Prompt verlangte ausdrücklich „ändere NUR diese drei Stellen in
server.py, sonst nichts" — das Modell hat stattdessen zusätzlich
`templates/index.html` **komplett neu geschrieben**, obwohl das nicht
verlangt war. Funktional korrekt (Streaming-Logik korrekt integriert),
aber die Tailwind-Gestaltung aus Etappe 1/2 ist dabei verlorengegangen
(jetzt einfaches Inline-CSS statt der vorherigen Karten-/Formular-Optik).
Ein Beleg dafür, dass auch explizite Scope-Einschränkungen im Prompt
("NUR diese Stellen") nicht immer eingehalten werden — Code-Review nach
jedem Lauf bleibt notwendig, unabhängig davon, wie genau der Prompt
formuliert ist.

### 10.5 Bekannte Grenze: `curl`-basierte Seitenanalyse sieht kein JavaScript

Nach der `summarize_large_fetch()`-Erweiterung (10. Kapitel oben) wurde
Vibelove gebeten, `https://herr.tech/ki-webinar/` erneut nachzubauen. Das
Ergebnis sah auf den ersten Blick gut aus (passendes Farbschema, generische
Hero/Features/Formular/Footer-Struktur) — bei genauerem Nachfragen zeigte
ein direkter Strukturvergleich mit der echten Seite aber, dass die
**Struktur nicht wirklich getroffen wurde**:

- Die Originalseite hat **null** `<h1>`- und **null** `<h2>`-Tags (nur 5
  `<h3>`) — sie ist mit Elementor (WordPress-Baukasten) gebaut, Überschriften
  laufen dort über custom-gestylte Widgets statt semantischer HTML-Tags.
  Der Nachbau hat dagegen ein klassisches `<h1>` gesetzt.
- Die Originalseite hat **kein einziges** natives `<form>`-Element. Die
  „Anmeldung" läuft über zwei eingebettete Drittanbieter-Widgets
  (`heyflow.com`, ein interaktiver Multi-Step-Formular-Builder, und
  `webinarjam.com`, eine externe Webinar-Registrierungsplattform) — beide
  werden per JavaScript nachgeladen, im rohen HTML steht dafür nur ein
  leerer Container plus ein `<script>`-Tag.

**Ursache:** `mc.py` sieht bei `curl` nur den **rohen, unausgeführten
HTML-Quelltext** — bei modernen Seiten mit JS-nachgeladenen Widgets (wie
hier Heyflow/WebinarJam) ist der eigentliche interaktive Inhalt im
Rohtext schlicht nicht vorhanden, unabhängig davon, wie gut die
Analyse-Zusammenfassung sonst ist. Das ist eine **grundsätzliche Grenze**
von `curl`-basiertem Seitenabruf, keine, die sich durch eine bessere
Kürzung/Analyse beheben liesse — dafür wäre ein echtes Browser-Rendering
(z.B. headless Chrome) nötig, um zu sehen, was tatsächlich im DOM landet.

**Entscheidung:** Bewusst nicht weiterverfolgt — der Aufwand für
Browser-basiertes Rendering steht in keinem Verhältnis zum Nutzen für
dieses Experiment. Als bekannte Grenze dokumentiert statt behoben.

## 11. Neues Projekt: Bilderkennung (Vision-Test mit `gemma-4-26b-a4b-it@mxfp4`)

Ein separates, neues Testprojekt (`bilderkennung/`, unabhängig von
Vibelove): eine kleine App, die ein hochgeladenes Bild an ein
**Vision-Sprachmodell** schickt und die Beschreibung darunter anzeigt. Lädt
man ein neues Bild hoch, beginnt der Ablauf komplett von vorne. Praktischer
Zufallsfund vorab: `gemma-4-26b-a4b-it@mxfp4` ist laut LM Studios
`/api/v0/models` tatsächlich als `"type": "vlm"` gelistet — also ein
echtes Vision-Language-Model, keine reine Text-Coding-Annahme.

**Architektur-Vorgabe** (wieder vorab festgelegt, diesmal mit besonderem
Fokus auf das exakte Multimodal-Request-Format, da ein falsch aufgebautes
JSON hier nicht nur einen Fehler, sondern eine sinnlose, aber technisch
„erfolgreiche" Antwort produzieren könnte):

- `backend/app.py` (Flask, fest Port 5060): kodiert das hochgeladene Bild
  als Base64, schickt eine Chat-Completions-Anfrage im
  **OpenAI-Vision-Standardformat** (`content` als Array mit `{"type":
  "text", ...}` und `{"type": "image_url", "image_url": {"url":
  "data:<mime>;base64,<daten>"}}`) an einen konfigurierbaren Endpunkt
  (`BILDERKENNUNG_BASE_URL`/`BILDERKENNUNG_MODEL`, Fallback M4 Pro +
  `gemma-4-26b-a4b-it@mxfp4`), mit großzügigem Timeout (Bildanalyse dauert
  länger als reine Textantworten).
- `frontend/` (Vite+React, Tailwind per CDN, fest Port 5175): Datei-Upload
  löst **sofort** automatisch die Analyse aus (kein separater
  „Los"-Button), zeigt einen Ladezustand, und ein **neues** Bild setzt den
  alten Beschreibungstext zurück, bevor die neue Analyse startet.
- Wichtigster Prüfschritt im Prompt: **ein echtes Testbild erzeugen und
  hochladen**, nicht nur prüfen, ob der Server antwortet — bei einer
  Vision-Anwendung ist „Server antwortet mit HTTP 200" bedeutungslos, wenn
  die Antwort keine echte Bildbeschreibung enthält.

Aufruf:

```bash
cd bilderkennung
python3 ../mc.py --base-url http://192.168.178.191:1234/v1 \
  --model "gemma-4-26b-a4b-it@mxfp4" \
  --yes --plan --check --max-steps 40 \
  "$(cat ../prompt_bilderkennung.txt)"
```

Vollständiger Prompt: [`beispiel-prompts/prompt_bilderkennung.txt`](beispiel-prompts/prompt_bilderkennung.txt).

### 11.1 Ergebnis: Vision-App gebaut — und ein Chrome-Sicherheitsdetail entdeckt

**Bauverlauf** (417 s, `--plan --check`): löste unterwegs selbstständig
einen echten Port-Konflikt (verwaister Prozess von einem vorherigen
Testversuch gefunden per `lsof`, beendet, neu gestartet) — keine
Wiederholungsschleifen, keine Parse-Fehler.

**Code-Review bestanden:** `backend/app.py` nutzt exakt das vorgegebene
OpenAI-Vision-Multimodalformat (`content`-Array mit `text`- und
`image_url`-Eintrag, Base64-Bild mit korrekt ermitteltem MIME-Typ,
großzügiger 120s-Timeout). `frontend/App.jsx` löst die Analyse automatisch
beim Datei-Upload aus und setzt bei einem neuen Bild Beschreibung/Fehler
sauber zurück, bevor die neue Anfrage startet.

**Live verifiziert (per `curl`):** Ein selbst erstelltes Testbild (Himmel,
Sonne, Wiese, Haus-Silhouette) lieferte eine echte, detaillierte und
korrekte Beschreibung zurück — Farben, Formen, Anordnung, sogar der Stil
("Flat Design") wurden richtig erkannt. Zu diesem Zeitpunkt: Läuft
scheinbar einwandfrei.

**Am nächsten Tag meldete der Nutzer im echten Browser einen Fehler:**
„Failed to fetch", später präzisiert zu `net::ERR_UNSAFE_PORT`. Der
Grund: **Backend-Port 5060 ist der SIP-Standardport** (Telefonie-Protokoll)
und steht auf einer fest einprogrammierten Sperrliste „unsicherer" Ports,
die Chrome aus Sicherheitsgründen (Schutz vor Cross-Protocol-Angriffen)
grundsätzlich nie anfasst — unabhängig davon, ob der Server dort real
lauscht. **`curl` kennt diese Beschränkung nicht**, weshalb meine eigene
Verifikation während des Baus fälschlich als vollständig erfolgreich galt
— ein blinder Fleck rein browserbasierter Fehlerklassen, den reines
Kommandozeilen-Testen strukturell nicht aufdecken kann.

**Fix:** Port 5060 → **5065** (nicht auf der Sperrliste), sowohl im
Backend als auch in der Frontend-Fetch-URL. Vite übernahm die Änderung
automatisch per Hot-Reload, kein Neustart nötig. Live erneut bestätigt:
CORS-Preflight und Requests funktionieren auf dem neuen Port einwandfrei.

**Lektion für künftige Portwahl bei browserbasierten Tools:** Neben Port
5000 (macOS AirPlay, schon oft heute aufgetaucht) jetzt auch Chromes feste
Sperrliste im Hinterkopf behalten — u. a. 5060/5061 (SIP), 6000 (X11),
6665–6669 (IRC). Ein `curl`-Test allein reicht bei browserbasierten Apps
nicht aus, um das zu erkennen.

## 12. Die Ernte: sechs `mc.py`-Verbesserungen aus den gesammelten Lektionen

Zum Abschluss die Frage: Was lässt sich aus allen Fehlschlägen der
Testtage noch ins Werkzeug zurückspielen? Sechs Verbesserungen, jede
direkt auf einen real beobachteten Vorfall zurückführbar — keine
theoretischen Features:

**1. `truncate()` zeigt Kopf UND Ende** (60/40 statt nur die ersten 8000
Zeichen). Auslöser: Bei `npm run build`-Fehlern steht die eigentliche
Fehlermeldung fast immer am **Ende** der Ausgabe — genau dem Teil, den
die bisherige Kürzung abschnitt. Das Modell sah 8000 Zeichen erfolgreicher
Zwischenmeldungen, aber nie den Fehler.

**2. Warnung bei blindem Überschreiben.** Ein neues `READ_FILES`-Set
merkt sich, welche Dateien im Lauf gelesen wurden. Überschreibt
`write_file` eine existierende Datei, die weder gelesen noch im Lauf
selbst angelegt wurde, gibt es eine deutliche Warnung. Deckt **zwei**
beobachtete Fehlerklassen mit einem Mechanismus ab: den Datenverlust aus
Vibelove-Etappe 1 (`write_files` versehentlich statt `read_file`) und den
Scope-Creep aus Etappe 3 (`index.html` ungefragt komplett neu geschrieben,
Vorschau-iframe dabei zerstört).

**3. Fence-Format wird bei Parse-Fehler-Eskalation konkret vorgeführt.**
Der Parser versteht das ```content-Blockformat schon immer — aber das
Modell kannte es nur, wenn `--fence` gesetzt war. Ab dem 2. Parse-Fehler
in Folge zeigt die Fehlermeldung jetzt das komplette Format als Beispiel
(action-JSON ohne `content`-Feld, Inhalt roh im Block dahinter). Hätte
den 28-Schritte-Hänger beim Neurawork-Nachbau (große `App.jsx`, immer
derselbe JSON-Escaping-Fehler) vermutlich im 3. Schritt aufgelöst.

**4. Geladenes Kontextfenster wird abgefragt statt geraten.**
`loaded_context_chars()` holt einmal pro Lauf LM Studios
`loaded_context_length` (via `/api/v0/models`) und leitet daraus das
Limit für die isolierte Fetch-Analyse ab. Kalibriert am beobachteten
Fall: 8192 Token geladen → Formel liefert 11685 Zeichen (damals
scheiterten 20000 still, 10000 gingen). Live gegen die M4 Pro bestätigt.
Bei Servern ohne den Endpunkt (Ollama) greift der bisherige Fallback.

**5. Portwahl-Wissen im System-Prompt.** Ein Absatz warnt jetzt vor Port
5000 (macOS AirPlay) und Browser-Sperrlisten-Ports (5060/5061, 6000,
6665–6669 → `ERR_UNSAFE_PORT` trotz funktionierendem `curl`) und nennt
sichere Bereiche. Beide Fälle kosteten real Zeit — der AirPlay-Konflikt
mehrfach, der SIP-Port produzierte einen erst am Folgetag entdeckten Bug.

**6. Check-Probe, wenn `--check` ohne `--plan` läuft.** Die beobachtete
Schwäche: ohne Plan-Phase genügte dem Finish-Gate ein einziger
erfolgreicher `run` — real war das einmal nur ein `ast.parse`-Syntaxcheck,
während die verlangten funktionalen Tests nie liefen. Jetzt stellt das
Gate beim ersten `finish` genau eine Nachfrage: pro Aufgabenteil benennen,
welches Kommando real lief („ein reiner Syntax-Check zählt nicht als
Funktionstest"), Fehlendes nachholen. Kostet maximal einen Umlauf und
entfällt, wenn Prüfschritte aus der Plan-Phase existieren.

Alle sechs isoliert getestet (u. a. vier Blind-Overwrite-Szenarien,
Check-Probe mit und ohne Plan, Kontextfenster-Abfrage live inkl. Cache)
plus ein End-to-End-Smoke-Test über die echte CLI. Damit fließt jede
größere Lektion der Testtage dauerhaft ins Werkzeug zurück — unabhängig
von Vibelove, Bilderkennung oder dem konkreten Modell.

---

## 13. Der Weiterentwicklungs-Testtag: vom Neubau-Reflex zum echten Iterieren

Bisher drehte sich fast alles um den **ersten Wurf**: leeres Verzeichnis,
Prompt rein, App raus. Der Alltag sieht anders aus — man führt denselben
oder einen neuen Prompt **im selben Projektordner** noch einmal aus. Und
genau da zeigte `mc` zwei hässliche Verhaltensweisen: Bestehende Dateien
wurden **teilweise blind überschrieben**, ohne den alten Inhalt je gesehen
zu haben. Oder der Lauf **hing endlos**, weil `npm create vite` beim
zweiten Mal interaktiv „Overwrite?" fragte — eine Frage, die im
`capture_output`-Betrieb niemand je sieht und niemand je beantwortet.
Die Diagnose in einem Satz: **Das Tool ging immer davon aus, dass alles
„neu" ist.**

### Runde 1: Drei Abfangnetze vor dem Schaden

Die bestehende `_blind_overwrite_warning` kam erst **nach** dem
Überschreiben — eine Beileidsbekundung, kein Schutz. Drei neue Mechanismen
setzen **davor** an:

**1. Ist-Zustand-Hinweise an der Aufgabe.** Vor dem ersten Modell-Aufruf
prüft das Tool selbst (reiner Dateisystem-Check, kein LLM-Aufruf), ob
Projekt-Marker wie `package.json` oder `requirements.txt` existieren und ob
in der Aufgabe genannte Dateien schon da sind. Wenn ja, wird der Aufgabe
ein Hinweis angehängt: *„Das ist eine WEITERENTWICKLUNG, kein Neubau —
erst lesen, dann gezielt ändern, keinen Generator erneut ausführen."*
Der Projektüberblick im System-Prompt existierte zwar längst, aber kleine
Modelle ignorieren passive Listen zuverlässig — konkrete Anweisungen
direkt in der User-Message wirken.

**2. Overwrite-Gate.** `write_file` auf eine existierende, im Lauf nie
gelesene Datei wird **abgelehnt** statt beklagt — mit Anleitung (erst
`read_file`, dann `edit_file`; bewusster Neuschrieb per `"overwrite":true`)
und Notausgang nach 2 Ablehnungen pro Pfad.

**3. Generator-Konflikt-Check + geschlossenes stdin.** Scaffolder auf ein
nicht-leeres Zielverzeichnis werden vorab abgefangen. Und `run` läuft
jetzt mit `stdin=DEVNULL`: Wer interaktiv fragt, bekommt sofort EOF und
scheitert **lesbar** — statt 120 Sekunden still auf eine Antwort zu warten,
die nie kommt.

Das Ergebnis war eindeutig. Erstlauf des CRUD-Prompts: alle drei Netze
feuerten schon mittendrin (das Modell wollte tatsächlich mitten im Lauf
`npm create vite` **erneut** ausführen — es hatte sein eigenes Scaffolding
vergessen). **Zweitlauf desselben Prompts im selben Ordner:** das Modell
las nur noch (list_dir, read_file), verifizierte per curl, sauberes finish
— **null Überschreibungen, 17 statt 32 Schritte, 84k statt 175k Tokens.**
Das Ausgangsproblem war damit gelöst.

### Runde 2: Die Erweiterungsstufen finden die nächste Schicht

Dann der eigentliche Härtetest: die fertige App in Stufen erweitern, mit
kurzen Prompts („Hilfe-Button", „E-Mail + Geburtstag", „Sortierung",
„Suchfeld", „Geburtstags-Ansicht"). Stufe 1 lief sauber — die Stufen 2, 4
und 5 liefen ins Schrittlimit, Stufe 3 endete **stillschweigend ohne
finish**. Die Logs zeigten vier klare Muster:

1. **edit_file war vom Fence-Modus ausgeschlossen.** Für Änderungen an
   bestehenden Dateien nutzt das Modell (richtigerweise!) `edit_file` —
   aber `old`/`new` mussten als JSON-Strings escaped werden. Ergebnis in
   Stufe 4: **neun** „Unterminated string"/„Invalid control
   character"-Fehler in Serie. Genau die Fehlerklasse, die der Fence-Modus
   bei `write_file` längst gelöst hatte.
2. **„old nicht gefunden" ohne jede Hilfe.** Das Modell riet, scheiterte,
   riet identisch erneut — dreimal in Folge.
3. **Leere Antwort = stilles Ende.** Stufe 3 starb daran: Kontextfenster
   des geladenen Modells überschritten → leere Antwort → das Tool wertete
   das als „Textantwort, fertig". Lauf beendet, Aufgabe halb erledigt,
   keine Fehlermeldung.
4. **Lese-Schleifen.** Vorher hatte das Modell dieselbe 7-KB-Datei dreimal
   hintereinander gelesen und so den Kontext selbst vollgepumpt.

Vier Fixes: ```old/```new-**Fence-Blöcke für edit_file** (Parser versteht
sie immer, der Fence-System-Prompt lehrt sie, die Parse-Fehler-Eskalation
führt sie vor); **Whitespace-Toleranz** am Zeilenende plus — bei echtem
Fehltreffer — die **ähnlichste Stelle wörtlich aus der Datei** in der
Fehlermeldung (kopieren statt raten); **leere Antworten** → Kontext hart
beschneiden, begrenzt neu anfragen, sonst sauberer Abbruch mit Diagnose;
**identische Lese-Aktionen** direkt hintereinander abfangen.

Die Retries sprachen für sich: Stufe 2 fiel von *50 Schritten ohne finish,
251k Tokens* auf **22 Schritte, sauberes finish, 118k Tokens, 0
JSON-Fehler**. Stufe 3 von *stillem Abbruch* auf **15 Schritte, sauber**.
Stufe 4 von *50 Schritten, 9 JSON-Fehlern* auf **20 Schritte, 0 Fehler**.
Und der Ähnlichkeits-Hinweis wirkte wörtlich nachweisbar — das Modell
schrieb: *„nutze ich den exakten Block, der vom Tool als Übereinstimmung
vorgeschlagen wurde"*.

### Runde 3: Umbenennen, die Zwei-Datenbanken-Falle und das Schrittbudget

Ein Wartungs-Task deckte die nächste Schicht auf: Das Modell hatte beim
Erweitern einen konsistenten Tippfehler eingebaut (`geburstag` statt
`geburtstag` — konsistent falsch, die App lief trotzdem). Der Auftrag
„benenne das überall um" scheiterte zunächst: ~15 Vorkommen über zwei
Dateien, und das Modell versuchte, **jede Stelle einzeln** mit großen
edit_file-Blöcken zu treffen — 14 Fehltreffer bis zum Schrittlimit. Für
Umbenennungen ist das Werkzeug so schlicht falsch gehalten. Der Fix:
System-Prompt-Regel plus Fehlermeldungs-Hinweis — *pro Datei EIN
`edit_file` mit dem kurzen Namen und `"replace_all":true`*. Im Retest
stieg das Modell nach dem ersten Mehrdeutigkeits-Fehler sofort um: 5
Stellen in App.jsx, 11 in app.py, in **je einem Schritt**.

Dass der Retest trotzdem das Limit riss, lag an einer Falle, die der
allererste Lauf gelegt hatte: `DB_PATH = 'personen.db'` — ein **relativer
Pfad**. Je nach Startverzeichnis des Backends entstanden **zwei
verschiedene Datenbanken**, und die Verifikation traf mal die eine, mal
die andere: widersprüchliche Ergebnisse, 35 Schritte Prüf-Kreisverkehr.
Ein App-Bug, kein Tool-Bug — aber er offenbarte ein Tool-Defizit: Die
Arbeit war nach 15 Schritten fertig, doch **das Modell wusste nicht, dass
ihm die Schritte ausgehen.** Daher der **Schrittbudget-Hinweis**: Bei ≤ 5
verbleibenden Schritten wird der letzten user-Nachricht angehängt, dass
der Lauf gleich hart endet — Aufgabe jetzt abschließen, nichts Neues
anfangen, finish mit ehrlicher Zusammenfassung. Im Test (DB_PATH-Fix als
echte Aufgabe, bewusst knappes Budget von 15): Hinweis bei Schritt 11,
sauberes finish bei Schritt 12, DB-Pfad absolut, überflüssige DB gelöscht.

Nebenbei fiel noch ein Klassiker: Ein einzelner **Read-Timeout beim
allerersten Request** (der Endpoint lud gerade ein Modell) beendete den
kompletten Lauf. Jetzt gilt: Fehler **vor** den ersten Antwort-Bytes werden
bis zu 3× mit Backoff wiederholt; reißt der Stream **mittendrin** ab, wird
das Teilstück behalten und über die Auto-Continuation vervollständigt.

### Die Bilanz des Tages

Elf Verbesserungen, jede auf einen real beobachteten Vorfall
zurückführbar: Ist-Zustand-Hinweise, Overwrite-Gate,
Generator-Konflikt-Check, stdin=DEVNULL mit erklärender Timeout-Meldung,
```old/```new-Fences, Whitespace-Toleranz + Ähnlichkeits-Vorschlag,
Leere-Antwort-Behandlung, Lese-Schleifen-Erkennung, replace_all-Regel,
Netzwerk-Retry, Schrittbudget-Hinweis. Der Muster-Wechsel dahinter: von
*„das Modell möge sich bitte richtig verhalten"* zu *„das Tool macht
falsches Verhalten mechanisch unmöglich oder teuer und richtiges billig"*.
Kleine Modelle folgen keinem Regelwerk — aber sie folgen sehr zuverlässig
einer konkreten Fehlermeldung, die ihnen den nächsten Schritt vorschreibt.

## 14. Ein großes Modell im Weiterentwicklungs-Szenario — und der unbewachte Ausgang

Nach all den kleinen lokalen Modellen die Gegenprobe: **Kann ein großes
Cloud-Modell die Weiterentwicklungs-Leitplanken einfach so bedienen?**
Aufgabe: die bestehende Personenverwaltung (`test/`) um ein Feld
`koerpergroesse` (cm) erweitern plus Sortierung danach — mit
`deepseek/deepseek-v4-flash` über OpenRouter ($0.09/$0.18 pro Mio Token),
gegen die **laufenden** Server (Backend 5010 mit Flask-Autoreload, Vite
8095), mit der harten Anforderung, dass die bestehenden Daten in
`personen.db` erhalten bleiben.

**Lauf 1 (155 s, $0.0151, 23 Schritte) — Bilderbuch-Anfang:** Der
Ist-Zustand-Hinweis feuerte, das Modell las **zuerst** `app.py` und
`App.jsx`, prüfte das DB-Schema per `PRAGMA table_info` und arbeitete
dann ausschließlich mit gezielten `edit_file`-Schritten — kein einziger
blinder Neuschrieb, das Overwrite-Gate musste nie eingreifen. Backend
komplett korrekt: idempotente `ALTER TABLE`-Migration, Feld in
POST/PUT/GET und CSV-Export, Bestandsdaten intakt (live verifiziert).
Das Frontend bekam sogar eine **verallgemeinerte Sortierung über alle
Spalten** — mehr als verlangt.

**Dann der eigentliche Fund.** In Schritt 23 kündigte das Modell den
nächsten Edit an und schrieb dann wörtlich
`(edit_file ausgefuehrt: frontend/src/App.jsx (0 Z) — Inhalt gekuerzt)`
— **als Prosa**. Es imitierte exakt das Format, mit dem `mc` ältere
Schritte in der gekürzten Kontext-Historie zusammenfasst, das es in
seinen eigenen Nachrichten sah. Kein Action-Block, kein Edit — und die
Regel „Antwort ohne Aktion = Textantwort = fertig" beendete den Lauf
sofort. **Mitten in der Arbeit, unter Umgehung des kompletten
Check-/Finish-Gates.** Prosa-Ende war ein unbewachter Ausgang: alle
Gates dieser Testreihe (Check-Modus, Finish-Verifikation, Notizen-
Nachfrage) hängen am `finish` — eine Antwort, die einfach keinen
Action-Block enthält, lief an allen vorbei. Zurück blieben drei Lücken:
keine Größen-Spalte in der Tabelle (Kernanforderung per UI unbenutzbar),
keine Null-Behandlung beim Sortieren, unvollständige Formular-Resets —
exakt die Punkte, die das Modell selbst noch als offen benannt hatte.

**Der `mc.py`-Fix:** Wurde im Lauf bereits geschrieben, bekommt eine
aktionslose Antwort jetzt genau **eine** Rückfrage („Text wie
`(edit_file ausgefuehrt: ...)` führt KEINE Aktion aus — fertig heißt
finish, sonst nächste echte Aktion"). Ein `finish` läuft dann durch alle
Gates; eine zweite aktionslose Antwort gilt als bewusstes Prosa-Ende
(keine Schleife); reine Frage-Antwort-Läufe ohne Schreibaktionen enden
unverändert sofort. Drei Szenarien isoliert getestet, Suite 66/66.

**Lauf 2 (149 s, $0.0116) — Nachbesserung der drei Lücken:** sauberes
`finish`, alle drei Punkte korrekt (sortierbare Spalte „Groesse" mit
„X cm"-Anzeige, Personen ohne Wert in **beiden** Sortierrichtungen am
Ende, numerischer Vergleich, vollständige Resets) — live im Browser
per Klick auf den Spaltenkopf verifiziert. Nebenbei pflegte das Modell
unaufgefordert `MC-NOTIZEN.md` (Feld-Liste ergänzt) — die
Notizen-Nachfrage aus dem Weiterentwicklungs-Paket griff.

**Einordnung:** Das große Modell bediente die neuen Leitplanken auf
Anhieb so, wie sie gedacht sind (lesen → prüfen → gezielt editieren) —
und lieferte trotzdem die Entdeckung des Tages: Der Ausstieg über
imitierte Beobachtungs-Prosa ist kein Klein-Modell-Problem, sondern ein
Protokoll-Loch, das erst ein Modell fand, das die Historien-Kürzung
aufmerksam genug „gelesen" hat, um sie nachzuahmen. Gesamtkosten beider
Läufe: **$0.0267**.

## 15. Dasselbe Experiment mit `xiaomi/mimo-v2.5` — und Variante 2 des Prosa-Lochs

Gleiche Aufgabenform, gleiches Projekt, nächstes Modell: `xiaomi/mimo-v2.5`
($0.10/$0.28 pro Mio Token) sollte das Feld `gewicht` (kg, Dezimalzahl)
plus Sortierung ergänzen — mit dem Vorteil, dass inzwischen die
`koerpergroesse`-Migration als Vorbild im Code steht und `MC-NOTIZEN.md`
die Feldliste dokumentiert.

**Lauf 1 (161 s, $0.0187): ins Schrittlimit gestolpert.** Statt
`read_file` blätterte mimo per `sed`/`cat` durch die Dateien (die
Lese-Schleifen-Erkennung feuerte beim **9. Zugriff** auf dieselbe Datei)
und produzierte **9+ `edit_file`-Fehltreffer** („old nicht gefunden") —
370k Token für ein zur Hälfte fertiges Feature. Immerhin: Migration
korrekt im Vorbild-Muster, Sortier-Logik/Resets/Vorbefüllung sauber,
Bestandsdaten unangetastet.

**Der gezielte Fix-Lauf endete nach 5 Sekunden — Variante 2 des
Prosa-Lochs.** mimo antwortete auf den Fix-Prompt in Schritt 1 nur mit
der Ankündigung *„Ich lese zuerst die relevanten Dateien"* — ohne
Action-Block. Der frisch eingebaute Prosa-Wächter griff nicht, denn seine
Bedingung war „bereits geschrieben" (`TOUCHED` nicht leer) — in Schritt 1
war naturgemäß nichts geschrieben. Ein Request, $0.0002, Lauf beendet.
Keine 30 Minuten nach dem ersten Fix fand ein anderes Modell die zweite
Lücke desselben Lochs — besserer Härtetest geht kaum.

**Der nachgezogene `mc.py`-Fix:** Ein Lauf gilt jetzt als Arbeits-Lauf
(und bekommt die Rückfrage), wenn **eines** zutrifft: bereits geschrieben,
Check-Modus aktiv, oder die Aufgabe nennt Dateien. Reine
Frage-Antwort-Läufe enden unverändert sofort. Drei Szenarien getestet,
Suite 66/66.

**Fix-Lauf 2 (235 s, $0.0090):** mimo lieferte diesmal von Schritt 1 an
echte Aktionen (der Wächter musste nie feuern), setzte alle fünf
benannten Lücken korrekt um und lief erst bei der Verifikations-Politur
erneut ins Schrittlimit — den letzten Kosmetik-Punkt (POST-Rückgabe ohne
die neuen Felder) hatte es selbst noch gefunden, aber nicht mehr
umgesetzt (eine Zeile, von Hand nachgezogen). Live verifiziert: POST mit
72.5 kg, GET korrekt, Bestandsdaten samt `koerpergroesse` intakt, Spalte
„Gewicht" sortiert mit Personen-ohne-Wert am Ende in beiden Richtungen.

**Modellvergleich im selben Szenario:**

| | deepseek-v4-flash | xiaomi/mimo-v2.5 |
|---|---|---|
| Vorgehen | erst lesen, `PRAGMA`-Schemaprüfung | sofort editieren, `sed`-Blättern |
| `edit_file`-Fehltreffer | wenige | 9+ |
| Läufe bis fertig | 2 | 3 (einer davon 5-Sekunden-Fehlstart) |
| Gesamtkosten | $0.0267 | $0.0279 |
| Gefundenes `mc.py`-Loch | Prosa-Imitat der Historien-Kürzung | Ankündigungs-Prosa in Schritt 1 |

Am Ende kosteten beide fast dasselbe und lieferten dasselbe Feature —
aber auf sehr verschiedenen Wegen. Und beide haben je eine Variante
desselben Protokoll-Lochs aufgedeckt, das drei Wochen Klein-Modell-Tests
nie getroffen hatten: der stille Ausstieg über aktionslose Prosa.

---

## 16. Schneller mit lokalem gemma: das Pruning sabotierte den Prompt-Cache

Die Frage klang harmlos: Wie arbeitet man mit dem lokalen
`gemma-4-26b-a4b-it@mxfp4` noch **schneller**? Die halbe Antwort stand
längst als Kommentar in `mc.py`: *„Auf lokalen Maschinen ist
Prompt-Processing der Flaschenhals"* — genau deshalb gab es ja die
Kontext-Beschneidung. Was fehlte, war die zweite Hälfte: LM Studio
(llama.cpp) hat einen **Prefix-/KV-Cache**. Enthält ein Request den
vorigen als Präfix, kostet er nur die *neuen* Tokens — und ein
Agenten-Loop ist dafür der Idealfall, denn Schritt N ist Schritt N−1
plus Antwort plus Tool-Ergebnis.

Nur: `prune_messages()` lief **vor jedem Schritt** und kürzte dabei
jedes Mal genau die Nachricht, die gerade aus dem Vollständig-Fenster
fiel — **mitten in der Historie**. Ab der Änderungsstelle ist der Cache
wertlos; der Server musste die letzten `KEEP_CONTEXT` vollen Schritte —
ausgerechnet die größten Brocken, volle Tool-Ausgaben und write-Blöcke —
bei *jedem* Schritt neu vorverarbeiten. Die Optimierung gegen das
langsame Prompt-Processing erzwang das langsame Prompt-Processing.

**Der Umbau (Lazy Pruning):**

- Die Historie wächst **unangetastet**, solange sie unter ~70 % des
  **geladenen** Kontextfensters bleibt (abgefragt über LM Studios
  `/api/v0/models` — dieselbe Quelle, die schon die Fetch-Analyse
  nutzt). Jeder Schritt ist dann eine reine Präfix-Verlängerung:
  Cache-Hit, Prefill nur für die neuen Tokens.
- Erst beim Reißen der Schwelle wird **einmal im Batch** gekürzt —
  danach ist das Präfix wieder stabil und der Cache baut sich einmalig
  neu auf. Reicht das nicht, greift die Notfall-Stufe (nur der letzte
  Schritt bleibt voll), wie beim Leere-Antwort-Fall.
- Ist das Fenster **nicht abfragbar** (Ollama, Cloud-Endpoints), bleibt
  alles beim Alten — dort zählt Überlauf-Schutz bzw. Token-Preis mehr
  als der Cache.

**Und eine Falle, die erst der Test gegen den echten Server zeigte:**
LM Studio meldet `loaded_context_length: null`, solange das Modell nicht
geladen ist — geladen wird aber erst JIT beim ersten Chat-Request. Ein
naiver Cache hätte den Wert beim Start als „unbekannt" eingefroren und
das Lazy Pruning für den ganzen Lauf still deaktiviert. Deshalb wird der
transiente Fall nicht gecacht; der Wert zieht ab Schritt 2 nach.
Dieselbe Lektion, die `mc` seinen Modellen predigt („nachschauen statt
raten"), gilt offenbar auch für den, der an `mc` baut. Suite: 72/72.

## 17. Gegentest über OpenRouter: eine komplette CRUD-App für 0,7 Cent

Zur Abwechslung mal kein Weiterentwicklungs-Szenario, sondern der
Klassiker from scratch — und zwar mit `deepseek/deepseek-v4-flash`, dem
Modell aus Kapitel 14: Flask + SQLite, CRUD-API für Personendaten,
HTML-Oberfläche, fester Port, `--yes --check`, unbeaufsichtigt.

**Der Lauf: 18 Schritte, 85.181 Tokens, $0.0068.** Null Parse-Fehler
(der Fence-Default bewährt sich weiter), keine Truncation, der
Prosa-Wächter musste nie eingreifen. Das Check-Gate holte vor dem
`finish` eine Verifikationstabelle ein — alle Endpunkte per `curl`
getestet, inklusive `DELETE /999 → 404`. `MC-NOTIZEN.md` wurde
unaufgefordert angelegt, der Abschluss sauber committet, die
Hintergrundserver aufgeräumt. Das Modell, das in Kapitel 14 durch den
unbewachten Ausgang verschwand, lief unter den inzwischen eingebauten
Leitplanken glatt durch.

**Die Token-Bilanz beantwortet nebenbei die Speed-Frage aus
Kapitel 16 quantitativ:** 77.516 Prompt- gegen 7.665
Completion-Tokens — **91 %** des Aufwands ist das Wiederkäuen der
Historie, nicht das Erzeugen der Antworten. Lokal frisst das Zeit (die
jetzt der KV-Cache spart), in der Cloud frisst es Geld (das dort das
Pro-Schritt-Pruning drückt). Beide Modi drehen an derselben Schraube,
nur in entgegengesetzter Richtung.

**Der unabhängige Abnahme-Test** (API per `curl` inklusive Randfälle,
Oberfläche im echten Browser): Kern-CRUD komplett in Ordnung — 404 bei
unbekannten IDs, partielles PUT erhält die übrigen Felder, Umlaute
sauber, das Frontend escaped gespeicherte XSS-Payloads. Aber vier
Lücken: ein **leerer** Name (`""`) wird angelegt (geprüft wird nur, ob
das Feld *vorhanden* ist), POST ohne JSON-Content-Type liefert eine
HTML-415-Seite statt JSON, `app.run(debug=True)` steht in der fertigen
App, und der DB-Pfad ist relativ zum Arbeitsverzeichnis — exakt die
Lektion, die im Personenverwaltungs-Projekt schon einmal als
`BASE_DIR`-Regel in den Projekt-Notizen stand, dort aber nur *pro
Projekt* wirkt.

**Das Muster dahinter ist die eigentliche Erkenntnis:** Das Modell
testete exakt die Fehlerfälle, die der Check-Prompt **wörtlich nennt**
(„unbekannte ID sollte 404 liefern" — brav und gründlich geprüft), und
exakt keinen darüber hinaus. Beispiellisten in Prompts sind für kleine
Modelle keine Illustration, sie sind die Spezifikation. Konsequenz,
direkt eingebaut:

1. Der **Check-Prompt** verlangt jetzt ausdrücklich auch ungültige
   Eingaben: ein fehlendes UND ein leeres Pflichtfeld müssen beide
   mit 400 abgelehnt werden.
2. Zwei neue **System-Prompt-Regeln**: Daten-/DB-Pfade absolut zur
   Skript-Datei auflösen (`BASE_DIR`-Muster) statt relativ zum
   Arbeitsverzeichnis, und fertiger Code läuft ohne Debug-Modus (kein
   `app.run(debug=True)` — der Werkzeug-Debugger erlaubt
   Code-Ausführung im Browser).

Damit ist die `BASE_DIR`-Lektion von einer Projekt-Notiz zur globalen
Regel befördert. Suite: 74/74.

## 18. Dieselbe Aufgabe mit GPT-5.6 Luna — und der Beweis, dass die Prompt-Schärfung wirkt

Direkt im Anschluss dieselbe Aufgabe, wortgleich, gleiche Flags — nur das
Modell getauscht: `openai/gpt-5.6-luna` ($0.10/$0.60 pro Mio Token,
completion-seitig gut doppelt so teuer wie deepseek-v4-flash).
Fairerweise vorweg: das ist **kein sauberer A/B-Vergleich der Modelle**,
denn Luna lief bereits mit den in Kapitel 17 nachgeschärften Prompts.
Aber genau das war der Zweck des Laufs — prüfen, ob die Schärfung wirkt.

**Der Lauf: 15 Schritte, 150.619 Tokens, $0.0143.** Luna arbeitet
erkennbar anders: erst alle Dateien in wenigen großen Schritten, dann ein
eigenes venv, dann die CRUD-Logik per lokalem Python-Testskript — und
erst danach Serverstart und ein einziges, durchkomponiertes
`curl`-Testskript mit einem Dutzend Prüfungen. Weniger Schritte, aber
längere Antworten und größere Tool-Blöcke: trotz drei Schritten weniger
verbrauchte Luna fast doppelt so viele Tokens wie deepseek. Zwei
`mc`-Wächter kamen zum Einsatz und funktionierten: der
Port-belegt-Hinweis (Luna wollte den Server starten, der eigene
Hintergrundprozess lief schon — Port wurde behalten statt gewechselt)
und die Notizen-Nachfrage vor dem `finish` (worauf `MC-NOTIZEN.md`
sauber nachgeliefert wurde).

**Der Kausalitäts-Beleg steht in Lunas eigenem Testskript:** Dort finden
sich wörtlich die Zeilen `POST fehlendes name` und `POST leeres name` —
exakt die zwei Prüffälle, die der Check-Prompt seit Kapitel 17 verlangt.
Und der Code hält, was das Skript prüft: `BASE_DIR`-Muster beim
DB-Pfad, kein `debug=True`, Whitespace-Namen werden gestrippt und
abgelehnt, Nicht-JSON-Requests bekommen einen 400er **mit
JSON-Fehlermeldung**. Der unabhängige Abnahme-Test (dieselbe
`curl`-Batterie plus Browser-Test von Anlegen/Bearbeiten und
XSS-Escaping): **null Befunde**. Alle vier Lücken des deepseek-Laufs
sind zu.

| | deepseek-v4-flash (Kap. 17) | GPT-5.6 Luna |
|---|---|---|
| Schritte / Requests | 18 | 15 |
| Tokens (prompt + completion) | 85.181 (77.5k + 7.7k) | 150.619 (136.6k + 14.1k) |
| Kosten | $0.0068 | $0.0143 |
| Vorgehen | kleinteilig, Einzel-`curl`s | venv + gebündelte Testskripte |
| Abnahme-Befunde | 4 | 0 |
| PUT-Semantik | partiell (Felder bleiben erhalten) | strikt (volles Objekt, sonst 400) |

Die PUT-Zeile ist keine Wertung — beides ist vertretbar (strikt ist
näher am REST-Lehrbuch, partiell ist praktischer), und beide Frontends
passen jeweils zu ihrer API. Interessant ist sie trotzdem: dieselbe
unterspezifizierte Aufgabe, zwei legitime Interpretationen.

**Eine Luna-Eigenheit zum Beobachten:** Das Modell schreibt nach dem
Action-Block gern weiter — und behauptet dort schon Ergebnisse („Der
Server ist gestartet", „erfolgreich geprüft"), bevor das Tool-Ergebnis
überhaupt zurück ist. `mc` führt ohnehin nur den einen Action-Block aus,
die vorauseilende Prosa bleibt also folgenlos — aber es ist derselbe
Geist wie das Prosa-Loch aus Kapitel 14/15: Modelle erzählen gern von
Taten statt sie abzuwarten. Die Wächter dagegen bleiben also zu Recht.

Unterm Strich: Für 0,7 Cent mehr gab es die fehlerfreie App im ersten
Anlauf. Und die eigentliche Erkenntnis ist modellunabhängig — was im
Check-Prompt wörtlich steht, wird geprüft; was nicht dasteht, bleibt
Glückssache. Die Prompts sind jetzt die Spezifikation.

## 19. Neun Modelle, eine Aufgabe: Dauer, Zuverlässigkeit, Kosten

Nach den Einzeltests aus Kapitel 17/18 der Rundumschlag: die
meistgenutzten OpenRouter-Modelle, alle mit **derselben wortgleichen
CRUD-Aufgabe**, `--yes --check`, unbeaufsichtigt, 20-Minuten-Limit pro
Lauf, sequenziell (fester Port). Danach lief gegen jede fertige App die
**identische Abnahme-Batterie**: 8 Kern-Fälle (CRUD inkl. 404er), 3
Validierungs-Fälle (fehlendes/leeres Pflichtfeld, Nicht-JSON), dazu
PUT-Semantik und statische Checks (`debug=True`, absoluter DB-Pfad).

| Modell | Dauer | Kosten | Abnahme (Kern·Valid) | Sauberes finish? |
|---|---|---|---|---|
| deepseek-v4-flash *(Kap. 17)* | n. gem. | $0.007 | 8/8 · 1/3 | ✓ |
| gpt-5.6-luna *(Kap. 18)* | n. gem. | $0.014 | 8/8 · 3/3 | ✓ |
| deepseek-v4-flash-0731 | 192 s | $0.008 | 8/8 · 3/3 | ✓ |
| deepseek-v4-pro | 167 s | $0.028 | 8/8 · 3/3 | ✓ |
| nemotron-3-ultra (free) | 269 s | $0.00 | 8/8 · 2/3 | ✓ |
| tencent/hy3 | 877 s | $0.025 | 8/8 · 3/3 | ✓ |
| z-ai/glm-5.2 | 1200 s (Limit) | unbek.¹ | 8/8 · 3/3 | ✗ Timeout |
| xiaomi/mimo-v2.5 | 33 s / 1724 s² | $0.095² | 8/8 · 3/3² | ✗ Schrittlimit |
| minimax/minimax-m3 | 494 s | $0.109 | **keine App** | ✗ Prosa-Ende |

¹ hart abgebrochen, keine Abrechnungszeile mehr. ² Erstlauf endete nach
33 s in einem Harness-Crash (dazu gleich); Nachtest mit gefixtem `mc.py`.

**Was die Tabelle lehrt:**

**Qualität ist kaum noch das Unterscheidungsmerkmal — Zuverlässigkeit
schon.** Acht von neun Apps, die überhaupt entstanden, bestehen alle
8 Kern-Fälle. Aber nur sechs von neun Läufen endeten mit einem sauberen
finish. glm-5.2 baute eine **fehlerfreie** App und verlor sich dann bis
zum Timeout in Verifikations-Schleifen (langsames Thinking-Modell, das
pro Schritt Minuten braucht); mimo-v2.5 lieferte im Nachtest ebenfalls
eine perfekte App, brauchte dafür aber 58 Requests voller
Selbstreparatur (14 Schreib-Anläufe, 34 Wächter-Warnungen) bis ans
Schrittlimit. Das Werk kann stimmen, während der Weg desaströs ist —
gemessen werden muss beides.

**Der Preis sagt nichts über das Ergebnis.** Das teuerste Modell des
Feldes (minimax-m3, $0.109) lieferte als einziges **gar keine App**:
eine Schreib-Abbruch-Spirale, in der es sein eigenes Werk löschte und
den Lauf schliesslich mit einer Ankündigungs-Prosa ohne Aktion beendete.
Der Preis-Leistungs-Sieger heisst deepseek-v4-flash-0731: fehlerfreie
App, 3/3 Validierung, 192 Sekunden, **0,8 Cent**. Und das Gratis-Modell
(nemotron) liefert eine brauchbare App mit nur einer Validierungs-Lücke
— fuer Benchmarks und Experimente voellig ausreichend.

**Die Prompt-Schärfung aus Kapitel 17 wirkt über Modellgrenzen hinweg.**
Sieben der acht entstandenen Apps lehnen das leere Pflichtfeld korrekt
ab — vor der Schärfung tat das nicht einmal der Klassenprimus. Nur
nemotron patzte hier.

**Und der Benchmark war zugleich der härteste `mc.py`-Test seit
Wochen — zwei echte Funde:** Erstens brachte mimo den Harness zum
Absturz, indem es `write_files` mit blanken Strings statt Objekten
aufrief (`AttributeError`, Lauf nach 33 s tot — jetzt wird normalisiert
statt abgestürzt; der Nachtest bestätigt den Fix). Zweitens zeigte
minimax eine Lücke im Prosa-Wächter: Der verbrauchte seine einmalige
Rückfrage früh im Lauf, und Dutzende Aktionen später ging der stille
Prosa-Ausstieg doch wieder durch — der Wächter schaltet sich jetzt nach
jeder echten Aktion wieder scharf.

**Ausblick:** Als nächste Disziplin neben Neubau und Weiterentwicklung
bietet sich ein **Design-Vergleich** an — dieselbe Aufgabe mit
Vite/React-Frontend, alle Apps mit identischen Beispieldaten befüllt,
Screenshots bei gleicher Fenstergröße, und die Optik anonymisiert als
Blind-Galerie bewertet (vom Menschen, optional zusätzlich von einer
Vision-Modell-Jury). Notiert, aber bewusst noch nicht gebaut.

## 20. Blick in fremde Werkstätten — und das Weiterentwicklungs-Paket

Die offene Wunde von `mc` war seit dem Weiterentwicklungs-Testtag
(Kapitel 13) bekannt: Bei **bestehendem** Code haben kleine Modelle einen
Neubau-Reflex — statt die betroffene Stelle zu suchen und gezielt zu
ändern, schreiben sie Dateien lieber komplett neu. Bevor ich dagegen
etwas baute, habe ich zwei große quelloffene Coding-Agenten seziert
(einen Rust-Klon eines bekannten CLI-Agenten mit ~120k Zeilen, und ein
TypeScript-Schwergewicht mit ~600k Zeilen über 34 Pakete) — mit einer
Leitfrage: *Wie machen die das mit dem Verstehen vor dem Ändern?*

**Der überraschendste Befund: Eine Repo-Map hat keiner.** Kein
AST-Index, keine Symbol-Übersicht, nichts dergleichen. Beide setzen auf
andere Strategien: der eine auf **deterministische Steckbriefe**
(Stack-Erkennung per Marker-Dateien, Git-Status und -Historie in den
System-Prompt — alles ohne Modell-Aufruf) und darauf, Lese- und
Schreib-Phasen über die **Tool-Liste selbst** zu trennen: im
Analyse-Modus stehen Schreibwerkzeuge gar nicht erst im Protokoll. Der
andere auf einen formalen **Plan-Modus** (erst erkunden, dann Plan als
Datei schreiben, erst nach Freigabe umsetzen) und auf die raffinierteste
**Edit-Fehlertoleranz**, die ich bisher gesehen habe: eine Kaskade aus
neun Matching-Strategien, die fast-richtige `old`-Blöcke doch noch
eindeutig zuordnet. Und ein Fund zum Schmunzeln: *Read-before-Write*
predigen beide nur im Prompt — bei einem behauptet die Tool-Doku sogar
eine Erzwingung, die im Code gar nicht (mehr) existiert. Die Gegenprobe
fiel ohnehin freundlich aus: deterministisches Finish-Gate,
Truncation-Fortsetzung, Runaway-Erkennung, Prosa-Wächter,
Git-Absicherung — all das hat von beiden keiner.

Aus beidem — den fremden Ideen und den eigenen Narben — ist das
**Weiterentwicklungs-Paket** geworden, vier ineinandergreifende
Mechanismen:

1. **Bestands-Kontext ohne Modell-Aufruf**: Projekt-Steckbrief (Stack,
   real vorhandene Kommandos, letzte Commits) plus **Code-Struktur** je
   Quelldatei — Funktionen, Klassen, Routen mit Zeilennummern (Python
   über die eingebaute `ast`-Bibliothek, JS/TS per Regex-Näherung). Ein
   Modell, das Struktur statt nur Dateinamen sieht, startet mit der
   Grundannahme *es gibt schon Code*.
2. **`--analyse` — erst verstehen, dann planen, dann ändern**: Die
   Analyse-Phase bekommt ein Protokoll **ohne Schreibaktionen**
   (Weglassen ist bei kleinen Modellen zuverlässiger als Verbieten) und
   endet mit einer `plan`-Aktion: nummerierte, konkrete Änderungen mit
   Dateipfad. Der Plan wird erst akzeptiert, wenn mindestens eine Datei
   gelesen wurde; beim `finish` wird das Modell einmalig an seinem
   **eigenen Plan** gemessen — dasselbe Prinzip wie beim Check-Modus.
3. **Neubau-Bremse**: Rein neue Dateien entstehen in Projekten mit
   Bestandscode erst, nachdem mindestens einmal in den Bestand geschaut
   wurde (`find`/`grep`/`read_file`/`list_dir` — auch ein leeres
   Ergebnis schaltet frei). Dazu die Prompt-Regel: ein leeres
   Suchergebnis heißt *Muster verbreitern und erneut suchen*, nicht
   *gibt es nicht*.
4. **Edit-Toleranz-Kaskade**: Nach dem exakten Match versucht
   `edit_file` zeilenweise getrimmtes Matching (mit automatischer
   Einrückungs-Anpassung von `new`), entfernt eine doppelte
   Escape-Ebene, und matcht zuletzt per Block-Anker (erste und letzte
   Zeile exakt, Mitte ≥ 75 % ähnlich). Jede Stufe verlangt
   Eindeutigkeit, ein Größen-Wächter verhindert zu große Treffer. Denn
   der harte Fehlschlag war bisher genau der Moment, in dem Modelle
   aufgaben und die ganze Datei neu schrieben.

Suite: 86/86. Der erste Praxistest des Pakets steht noch aus — das wird
der nächste Weiterentwicklungs-Testtag.

## 21. `/skills` — die Prompt-Dateien werden erwachsen

Beim Blick auf die Terminal-Bedienung anderer Agenten fiel auf, dass
dieses Repo längst mit „Skills" arbeitet, nur zu Fuß: die vielen
`prompt_*.txt`-Dateien sind wiederverwendbare Aufgaben-Vorlagen, die
bisher per Copy-Paste oder `"$(cat …)"` in `mc` gefüttert wurden. Das
neue, **optionale** Modul `mc_terminal.py` formalisiert genau das —
und ist zugleich der erste Schritt weg vom Ein-Datei-Zwang (die
Einzeldatei war praktisch, wird aber langsam groß; fehlt das Modul,
läuft `mc.py` unverändert allein weiter).

Skills sind Textdateien in `~/.mc/skills/` bzw. `mc_skills/` (Projekt
gewinnt), mit `$ARGUMENTS`-Platzhalter und optionalen Kopfzeilen, die
Flags **nur für diese eine Aufgabe** setzen — der
Weiterentwicklungs-Skill bringt sich so selbst den `--analyse`- und
`--check`-Modus mit:

```text
du> /weiterentwickeln Gewicht-Feld ergaenzen
```

Dazu kommen die kleinen Dinge, die eine Terminal-Bedienung angenehm
machen: persistente Eingabe-History (Pfeil-hoch/Ctrl-R), Tab
vervollständigt `/`-Kommandos (die Kandidaten werden bei jedem Tab
frisch aus den Skill-Verzeichnissen gelesen — neue Skills wirken ohne
Neustart), „Meintest du …?"-Vorschläge per Editierdistanz bei
Vertippern, `/model` zum Sitzungs-Modellwechsel, und Bare-Word-Dispatch
(das erste Wort einer Eingabe zählt auch ohne Slash als Skill, wenn es
exakt passt). Suite: 95/95.

## 22. Die Nachzügler: fünf kleine Learnings aus den fremden Werkstätten

Beim Sezieren der beiden großen Agenten (Kapitel 20) blieben ein paar
kleine, feine Mechanismen im Notizbuch liegen — „klein" heißt hier: je
unter 50 Zeilen Stdlib. Jetzt sind sie drin:

1. **Kontextfenster aus der Fehlermeldung lernen.** Meldet der Endpoint
   einen Kontext-Überlauf per HTTP-Fehler, parst `mc` die tatsächliche
   Fenstergröße aus dem Fehlertext („maximum context length is 32768
   tokens …"), kalibriert die Kürzungs-Schwelle damit neu, kürzt hart
   und versucht es erneut. Selbstkalibrierung für alle Endpoints, deren
   geladenes Fenster nicht abfragbar ist — die Abfrage-Variante gibt es
   ja nur für einen Servertyp.
2. **Spill-Datei statt Datenverlust.** Wird eine Tool-Ausgabe für das
   Modell gekürzt, landet die *vollständige* Ausgabe jetzt in einer
   Temp-Datei, und der gekürzte Text endet mit dem Hinweis, wo sie
   liegt („dort mit read_file/grep nachsehen"). Aus Trunkierung wird
   ein Nachschlagewerk — gerade bei langen Build-Logs Gold wert.
3. **Ablehnung als Steuerkanal.** Der Bestätigungs-Prompt versteht
   jetzt Freitext: Wer statt `j`/`n` etwas wie „nimm Port 5030" tippt,
   lehnt damit ab UND gibt dem Modell eine Anweisung mit — die als
   Aktions-Ergebnis zurückfließt. Aus einem binären Nein wird eine
   Kurskorrektur, statt dass das Modell dieselbe Aktion nochmal rät.
4. **„Meintest du …?" bei Dateipfaden.** Ein fehlgeschlagenes
   `read_file` schlägt jetzt die ähnlichsten real existierenden Pfade
   vor (Editierdistanz, auch über den Dateinamen allein). Kleine
   Modelle vertippen Pfade ständig — und die nackte Fehlermeldung war
   bisher eine Einladung zum Neuanlegen.
5. **Erzwungene Übergabe am Schrittlimit.** Läuft ein Lauf ins Limit,
   bekommt das Modell einen letzten Request ausdrücklich OHNE
   Aktionsmöglichkeit: Was ist fertig, was fehlt, womit weitermachen?
   Der Benchmark aus Kapitel 19 hat gezeigt, wie wertvoll das ist —
   zwei der drei gescheiterten Läufe endeten mitten in einer Aktion,
   ohne dass der Verlauf verriet, wo man steht. Jetzt endet auch ein
   gescheiterter Lauf mit einem Zustandsbericht.

Suite: 98/98. Bewusst weiter im Notizbuch: der Cache-Bruch-Detektiv,
das Folgeschäden-Radar über Import-Beziehungen und der Plan als Datei.

## 23. Der Praxistest: Neubau, dann Weiterentwicklung — alles auf einmal

Alle neuen Mechanismen zusammen im Ernstfall, mit dem Preis-Leistungs-Sieger
`deepseek-v4-flash-0731`: erst die CRUD-App **neu bauen** (per
`/crud-personen`-Skill, der sich sein `check: true` selbst mitbringt),
dann zwei Personen als Bestandsdaten anlegen, dann per
`/weiterentwickeln` (bringt `analyse` + `check` mit) das Feld `beruf`
ergänzen — Migration, API, Formular, Tabelle.

**Lauf 1 (Neubau): 31 Schritte, $0.0173, sauberes finish.** Die
Skill-Mechanik griff im Einmal-Modus wie geplant (Vorlage expandiert,
Flags aus der Kopfzeile aktiv).

**Lauf 2 (Weiterentwicklung) begann mit einem Lehrstück.** Nach zwei
Requests und $0.0005 war der Lauf tot: Das Modell hatte seine Aktion als
```` ```json ````-Block statt ```` ```action ````-Block ausgegeben, der
Parser erkannte nichts, und nach der einen Prosa-Rückfrage war Schluss.
Die Ursache war hausgemacht — der neue Analyse-Prompt **beschrieb** das
Format nur, statt es zu **zeigen**, und verstieß damit exakt gegen die
Lektion aus Kapitel 17 (das Beispiel ist die Spezifikation). Doppel-Fix:
Beispiel-Antwort in den Analyse-Prompt, und der Parser akzeptiert
gefencte JSON-Objekte mit `action`-Feld jetzt auch ohne korrektes Label.

**Lauf 2, zweiter Versuch: das Weiterentwicklungs-Paket liefert.** Die
Analyse-Phase produzierte einen **8-Punkte-Änderungsplan**, der sich wie
eine Edit-Anleitung liest („`app.py init_db()`: nach CREATE TABLE ein
PRAGMA-Check, falls Spalte `beruf` fehlt → ALTER TABLE …"). Danach:
**19 gezielte `edit_file`-Änderungen, kein einziger Neuschrieb** einer
Bestandsdatei. Ergebnis der unabhängigen Abnahme: Migration korrekt,
Bestandsdaten überlebt (inklusive leerem `beruf` als Default), das neue
Feld funktioniert in POST/PUT/GET, Formular und Tabelle, die Validierung
blieb intakt — für $0.0169. Ehrliche Fußnoten: Die Edit-Toleranz-Kaskade
musste kein einziges Mal eingreifen (alle 19 `old`-Blöcke saßen exakt —
das Netz ist für schwächere Modelle gespannt), und der Lauf rannte bei
der Verifikation ins 40-Schritte-Limit. Dort griff dafür erstmals die
neue **Übergabe**: statt eines Abbruchs mitten in einer Aktion steht am
Ende ein Zustandsbericht im Verlauf.

Damit der Benchmark-Apparat nicht im Temp-Verzeichnis verdunstet, liegt
er jetzt im Repo: `mc_benchmark/runner.py` (sequenzielle Modell-Läufe
mit Zeitmessung) und `mc_benchmark/abnahme.py` (die Abnahme-Batterie:
8 Kern-Fälle, 3 Validierungs-Fälle, PUT-Semantik, statische Checks).

## 24. Nachtrag zum Benchmark: qwen3.7-flash, und der Cloud-Schock beim Hausmodell

Drei Nachzügler durch denselben CRUD-Benchmark (Vorsicht beim Vergleich
mit der Kapitel-19-Tabelle: diese Läufe nutzten bereits den neuesten
Harness-Stand):

| Modell | Dauer | Kosten | Abnahme (Kern·Valid) | Ausgang |
|---|---|---|---|---|
| qwen3.7-flash ($0.03/$0.13!) | 641 s | n. erfasst | 8/8 · 1/3, **PUT → 500** | unsauber¹ |
| gemma-4-26b-a4b-it (Cloud) | 1200 s | $0.031 | **keine App** | Timeout |
| gemma-4-31b-it | 604 s | $0.026 | 8/8 · 3/3 | ✓ sauber |

¹ endete mit einem im Vordergrund gestarteten Dev-Server statt eines
finish.

**Der billigste Kandidat enttäuscht:** qwen3.7-flash kostet nur ein
Drittel des bisherigen Preis-Leistungs-Siegers, liefert aber eine App
mit echtem Bug (PUT stürzt mit 500 ab), akzeptiert leere Pflichtfelder —
und beendete den Lauf, indem es den Server im Vordergrund startete und
hängen blieb. Billig gekauft ist zweimal gelaufen; der Thron bleibt bei
deepseek-v4-flash-0731.

**Die eigentliche Geschichte ist gemma-4-26b.** Das ist exakt das
Modell, das lokal (als mxfp4-Quantisierung in LM Studio) seit Wochen der
zuverlässigste Arbeiter dieses Blogs ist — und in der Cloud-Variante
**degenerierte es bis zum Timeout in Markdown-escapten Code**
(`os\.path\.dirname\(...\)`, `\=\=` statt `==`), aus dem nie eine
lauffähige Datei wurde. 20 Minuten, 3 Cent, keine App. Gleiche
Gewichte-Familie, anderes Serving, gegenteiliges Ergebnis: Quantisierung,
Chat-Template und Serving-Stack gehören offenbar genauso zum „Modell"
wie die Gewichte selbst. Wer Modelle nur nach Namen bucht, vergleicht
Äpfel mit Birnen-Infrastruktur. Der größere Bruder gemma-4-31b lief
dagegen tadellos durch — sauberes finish, volle Abnahme, 2,6 Cent.

## 25. Der Schüchternen-Test: fünf wortkarge Eingaben, eine fertige App

Menschen tippen ungern. Also eine interaktive Sitzung wie von jemandem,
der genau das tut — keine Skills, keine langen Aufträge, `--yes`, das
günstige Standard-Modell:

```text
du> todo liste
du> mit kategorien
du> mach schoener
du> warum flask?
du> sortierung fehlt
du> exit
```

**Das Ergebnis nach 50 Schritten und 2,5 Cent:** eine richtig brauchbare
CLI-Todo-App (`todo.py`, JSON-Persistenz, keine Dependencies) mit
Kategorien in Rahmen-Boxen, Farben und ✔/◻-Symbolen („mach schoener"
wurde wörtlich geliefert), alphabetischer Sortierung mit Erledigten am
Ende — jede der fünf Mini-Eingaben kam an.

**Der Knappheits-Stupser lief bilderbuchmäßig:** Auf „todo liste"
stellte das Modell genau EINE ask-Frage (CLI oder Web-App?), bekam im
`--yes`-Betrieb die Auto-Antwort „triff sinnvolle Annahmen", benannte
seine Annahme in einem Satz und legte los. Genau der choreographierte
Ablauf: erst eine gezielte Frage, dann Annahmen, dann Arbeit — statt
zehn Schritte in die falsche Richtung. Die Frage-Weiche fing „warum
flask?" als Frage ab (beantwortet, nichts geändert), und der neue
Diff-Selbstreview meldete sich vor jedem finish.

**Der Bonus-Fund:** Mitten in der Sitzung degenerierte ausgerechnet das
zuverlässigste Modell des Feldes in **13.500 Tokens multilingualen
Wortsalat** („조 Dach trailsphen alternate fa J cons row양 …"). Der
Runaway-Wächter kappte die Antwort, der Parser rettete die brauchbare
Aktion aus dem intakten Teil davor, die Sitzung lief weiter — aus einem
potenziellen Totalschaden wurde eine Fußnote. Kein Modell ist davor
gefeit; Leitplanken sind keine Option, sondern die halbe Miete.

Zwei Beobachtbarkeits-Lücken hat der Test nebenbei aufgedeckt (und
behoben): Im Pipe-Betrieb wurden die Eingaben nicht ins Log geechot, und
Knappheits-Stupser/Frage-Weiche arbeiteten unsichtbar — beide melden
sich jetzt mit einer Info-Zeile.

## 26. Das Kimi-Duell: „das soll ja mega sein"

Zwei Kandidaten aus dem Premium-Regal gegen den amtierenden
Preis-Leistungs-Sieger, gleiche Aufgabe, gleiche Abnahme (Läufe über den
frisch parametrisierten `mc_benchmark/runner.py`):

| | kimi-k2.7-code | kimi-k3 | deepseek-v4-flash-0731 |
|---|---|---|---|
| Preis pro Mio | $0.73/$3.50 | $3.00/$15.00 | $0.09/$0.18 |
| Dauer | **111 s** | 433 s | 192 s |
| Schritte | 16 | 40 (Limit!) | 20 |
| Lauf-Kosten | $0.084 | **$0.909** | $0.008 |
| Abnahme | 8/8 · 3/3 | 8/8 · 3/3 | 8/8 · 3/3 |
| Sauberes finish | ✓ | ✗ nie eines ausgegeben | ✓ |

**kimi-k3 bestätigt das Perfektionisten-Muster** (bekannt von glm-5.2
und dem Weiterentwicklungs-E2E): eine tadellose App, aber in 40
Schritten **kein einziges finish** — es verifizierte und polierte bis
ans harte Limit, die Übergabe rettete den Abschluss. Fast ein Dollar für
eine Abnahme-Note, die es anderswo für 0,8 Cent gibt: als Denker
beeindruckend, als mc-Arbeiter Perlen vor die Säue.

**kimi-k2.7-code ist die eigentliche Überraschung:** Der schnellste
Lauf aller bisherigen Tests — 111 Sekunden, 16 Schritte, sauberes
finish, volle Abnahme. „Mega" stimmt hier also tatsächlich, zumindest
beim Tempo. Nur: Es kostet das Elffache von deepseek bei identischem
Ergebnis. Wer auf die Uhr schaut, nimmt kimi-k2.7-code; wer auf die
Rechnung schaut, bleibt beim Champion.

Und die Meta-Erkenntnis nach nunmehr **14 getesteten Modellen**: Die
Abnahme-Note trennt die Spreu kaum noch — fast jedes Modell, das
überhaupt ankommt, baut inzwischen eine korrekte CRUD-App durch die
`mc`-Leitplanken. Die Unterschiede leben in Zuverlässigkeit (kommt ein
finish?), Tempo und Preis — genau den drei Spalten, die dieser Blog
inzwischen bei jedem Lauf misst.

## 27. Vier aus der Oberklasse — und ein 52-Sekunden-Paukenschlag

Auf Zuruf noch vier Premium-Modelle durch denselben Benchmark (das
extreme claude-opus-4.7-**fast** mit $30/$150 pro Mio blieb nach kurzem
Blick auf den Taschenrechner bewusst im Regal — ein Lauf hätte so viel
gekostet wie alle bisherigen zusammen):

| | gpt-5.6-terra | claude-sonnet-5 | gpt-5.6-sol | claude-opus-4.7 |
|---|---|---|---|---|
| Preis/Mio | $1/$6 | $2/$10 | $5/$30 | $5/$25 |
| Dauer | **52 s** | 128 s | 106 s | 145 s |
| Schritte | **6** | 16 | 11 | 16 |
| Lauf-Kosten | $0.054 | $0.333 | $0.541 | $0.926 |
| Abnahme | 8/8 · 3/3 | 8/8 · 3/3 | 7/8 · 3/3 | 7/8 · 3/3 |
| finish | ✓ | ✓ | ✓ | ✓ |

**gpt-5.6-terra ist der Paukenschlag:** 52 Sekunden, **sechs Schritte**
— App in einem Wurf geschrieben, geprüft, fertig. Halbiert den
Tempo-Rekord von kimi-k2.7-code und drittelt Lunas Effizienz-Bestmarke.
Für 5,4 Cent ist das der neue Referenzpunkt für „schnell UND sauber".

**Die Strenge-Schule der Oberklasse:** sol und opus-4.7 — die beiden
teuersten des Quartetts — bauten unabhängig voneinander dieselbe
maximal strenge API: Feld-Validierung VOR der Existenz-Prüfung (PUT auf
unbekannte ID → 400 statt 404), POST nur mit vollständigen Feldern.
Vertretbare Design-Schule, aber nach unserer Abnahme je ein
Kern-Fehler — und wieder einmal: **mehr Geld heißt nicht bessere
Benchmark-Note.** Das günstigere Schwestermodell terra schlägt sol in
jeder Spalte.

## 28. Die Newcomer-Runde: ein neuer König für drei Zehntelcent

Sechs 2026er-Neuzugänge, ausgesucht nach Katalog-Recherche (`created`-
Feld der Modell-API): der Code-Spezialist zum Kampfpreis, die Google-
und xAI-Lücke, das frischeste Anthropic-Flaggschiff, ein Budget-Coder
und die 1-Cent-Wildcard.

**poolside/laguna-s-2.1 ist der neue Preis-Leistungs-König.** 57
Sekunden, 13 Schritte, volle Abnahme, sauberes finish — für
**$0.0029**. Das ist 2,6× billiger als der bisherige Champion
deepseek-v4-flash-0731 bei mehr als dreifachem Tempo, und nur fünf
Sekunden hinter dem absoluten Tempo-Rekord von terra. Ein
Code-Spezialanbieter zum deepseek-Tarif — genau das, wonach die
Katalog-Suche gesucht hatte.

**claude-opus-5 lieferte den teuersten Erfolgslauf der Geschichte:**
$2.16, 484 Sekunden, 34 Schritte gründlichster Arbeit — aber volle
Abnahme, sauberes finish, und im Gegensatz zum Vorgänger opus-4.7 ohne
den Strenge-Fehler (PUT wieder partiell, alle 8 Kern-Fälle bestanden).
Der Nachfolger hat die Schwäche also tatsächlich abgelegt — für das
270-fache des laguna-Preises. **grok-4.5** dagegen reiht sich in die
Strenge-Schule ein (PUT 400 statt 404, wie sol und opus-4.7), und
**gemini-3.6-flash** liefert solide volle Abnahme im Mittelfeld.

Zwei Ausfälle: **kat-coder-air-v2.5** kämpfte sich durch 40 Schritte
und hinterließ eine **syntaktisch ungültige app.py** (das Tool warnte
beim Abschluss ausdrücklich). Und **ling-2.6-flash** kam gar nicht erst
an — der Upstream-Anbieter war rate-limited (HTTP 429 auf den ersten
Request): ein Verfügbarkeits-, kein Fähigkeits-Urteil.

## 29. Die Konsole wird erwachsen: /settings, /models und Profile

Nach 24 Modell-Läufen war das Muster im Terminal immer dasselbe: Env-
Variablen setzen, Flags tippen, Modell-IDs aus dem Katalog kopieren.
Jetzt kann die Konsole das selbst:

- **`/settings`** zeigt alle zehn Laufzeit-Einstellungen als
  Label-Wert-Block; `/settings check true` ändert sofort. Ändert sich
  etwas, das im System-Prompt steckt (fence/check), wird der Prompt
  automatisch neu aufgebaut — und ein `base_url`-Wechsel leert die
  Kontextfenster-Caches, sodass man mitten in der Sitzung von LM Studio
  auf OpenRouter umschwenken kann.
- **`/models`** listet die Modelle des Endpoints samt Preisen (die
  Funktion dafür gab es seit dem OpenRouter-Kapitel — sie hatte nur nie
  ein Kommando).
- **`/profil speichern <name>`** sichert den kompletten
  Einstellungs-Satz unter `~/.mc/profile/`, `/profil laden <name>`
  stellt ihn **sitzungsübergreifend** wieder her. Der Praxisfall, für
  den das gebaut ist: ein Profil „lokal" (LM Studio, gemma, geduldige
  Limits) und eins „cloud" (OpenRouter, laguna, `--check`) — Umschalten
  ist ein Kommando statt einer Zeile Env-Variablen.

Suite: 114/114. Zusammen mit `/skills`, History und den Stupsern ist
aus dem einstigen Ein-Zeilen-`input()`-Loop damit eine kleine, aber
vollwertige Konsole geworden — ohne eine einzige Abhängigkeit.

## 30. mc baut an seinem eigenen Wrapper — und der Fehler, den kein Text-Check sieht

Zum Abschluss die schönste Schleife des Projekts: Der Lovable-Clone
(Kapitel 10) sollte lernen, in Bau-Anweisungen erwähnte **URLs per curl
abzurufen**. Eingebaut hat das nicht ich — sondern **mc selbst**:
`laguna-s-2.1` bekam per `--analyse` den Auftrag, `server.py` zu
erweitern. Es lieferte einen 3-Punkte-Plan (import, Hilfsfunktion,
Einbindung in build()), setzte ihn mit gezielten Edits um, bestand den
Diff-Selbstreview — **$0.0063**. Der Diff: mustergültig minimal.

Der End-to-End-Test saß auf Anhieb: Bauauftrag mit „orientiere dich an
https://example.com" — erste Aktion des Modells war `curl -sL
https://example.com`, und die neue Sektion enthielt den echten
iana.org-Link der Seite statt halluziniertem Inhalt.

**Und dann der Lehrbuch-Fund:** Die Sektion stand im Code, der Build
lief mit exit 0 durch, der Diff-Review war zufrieden — aber im Browser
fehlte sie. Das Modell hatte die Komponente **definiert, aber nie in
App() eingebunden**. Eine unbenutzte Komponente ist valides JavaScript;
`npm run build`, Syntax-Validierung, Diff-Review — **alle Text-Checks
sind dafür blind.** Nur der Blick auf die gerenderte Seite entlarvt es.
Die Reparatur lief dann wieder durch den Lovable-Flow selbst („die
Sektion wird nicht angezeigt — binde sie ein", $0.0010, ein Edit) und
die Sektion erschien.

Zwei Lehren: Erstens funktioniert die ganze neue Maschinerie auch im
Wrapper-Kontext — Analyse-Phase, Edit-Disziplin, Selbstreview,
Git-Sicherungspunkte (die „mc:"-Commits im Repo-Verlauf stammen
woertlich vom Agenten). Zweitens hat der Ausblick aus Kapitel 19 jetzt
seinen Beweis: Die naechste Leitplanken-Generation schaut nicht auf
Text, sondern auf das **gerenderte Ergebnis** — „ist der neue Inhalt
auch sichtbar?" ist eine Frage, die nur ein Screenshot oder
DOM-Vergleich beantworten kann.

## 31. Der tippfaule Mensch und der Verlust-Wächter

Beim Ausbau des Lovable-Clones passierte der lehrreichste Unfall der
Woche: Ein Modell sollte eine Werkzeugleiste **ergänzen** — und ersetzte
dabei das komplette Preview-iframe. Der eigene Diff-Selbstreview nickte
die Löschung ab. Beim nächsten Auftrag stand deshalb im Prompt: „Nur
HINZUFÜGEN, nichts entfernen, iframe und Buttons müssen erhalten
bleiben, prüfe das per grep" — und es klappte.

Worauf der Projektbetreiber trocken anmerkte: **„‚Nur hinzufügen,
nichts entfernen' ist aber schwierig, wenn der Mensch tippfaul ist."**
Touché. Der sorgfältige Schutz-Prompt ist selbst Prompt-Fleiß — genau
das, worauf man bei echten Menschen nicht bauen kann. Schutz gehört ins
Werkzeug, nicht in die Aufgabenstellung. Also:

- **Der Verlust-Wächter**: Jeder Schreibvorgang an einer bestehenden
  Datei vergleicht benannte Elemente vorher/nachher — HTML-IDs,
  Funktions- und Klassennamen, Struktur-Tags wie `<iframe>`.
  Verschwindet etwas davon, obwohl die Aufgabe kein Entfernen verlangt
  (Stichwort-Prüfung: löschen/entfernen/ersetzen/refactor …), bekommt
  das Modell die Warnung direkt ins Aktions-Ergebnis: „Diese Elemente
  wurden ENTFERNT — unbeabsichtigt Entferntes jetzt wiederherstellen,
  Beabsichtigtes kurz begründen."
- **Der Diff-Selbstreview** verlangt jetzt ausdrücklich Rechtfertigung
  für jede gelöschte Zeile — „im Zweifel wiederherstellen". Ein
  gefälliges Modell nickt sonst auch Löschungen ab.

Der tippfaule Mensch darf jetzt wieder „bau nen zip download ein"
tippen — die Leitplanke weiß auch ohne seinen Prompt, dass das iframe
dabei nicht sterben sollte. Suite: 118/118.

## 32. Dritter Werkstatt-Besuch: das Toleranz-Paket

Noch ein grosses Agent-Harness seziert (ein TypeScript-Monorepo mit
138k Zeilen, Frontier-Modelle, natives Tool-Calling) — diesmal mit hoch
gelegter Messlatte: Der Recherche-Auftrag enthielt die komplette
aktuelle mc-Merkmalsliste, gemeldet wurde nur, was uns wirklich fehlt.
Die Gegenprobe fiel deutlich aus (keinerlei finish-Verifikation, kein
Check-Modus, null Wächter, Cache-feindliche Kompaktierung — unsere
4000 Stdlib-Zeilen stehen erstaunlich gut da), aber an den Rändern
lagen fünf Fundstücke, jetzt alle eingebaut:

1. **Argument-Koerzierung**: `"5"` wird `5`, `"true"` wird `True`,
   Einzelwert wird Liste — je Aktions-Feldschema, bevor die Aktion
   läuft. Und scheitert es doch, spiegelt der Fehler dem Modell **seine
   eigenen Argumente wörtlich zurück** statt nur „ungültig" zu sagen.
2. **Form-Reparatur** eine Schicht davor: Aktions-Aliase (`bash`→`run`,
   `write`→`write_file`), `write_files` als Dict, doppelt
   JSON-kodierte Felder — deklarierte Toleranz statt Einzelfall-Flicken.
3. **Die Trunkierungs-Wache** schliesst ein echtes Datenverlust-Loch:
   Blieb eine Antwort trotz aller automatischen Fortsetzungen
   unvollständig, sah ein abgeschnittenes `write_file` bisher oft wie
   valides JSON aus — und schrieb eine halbe Datei. Jetzt werden
   schreibende Aktionen aus solchen Antworten verweigert und kleiner
   neu angefordert.
4. **Das Datei-Kontobuch**: Nach jeder Kontext-Kürzung wird
   deterministisch wieder eingespielt, welche Dateien der Lauf gelesen
   und geschrieben hat — null Tokens Modellarbeit, null
   Halluzinationsrisiko. Es adressiert die Wurzel des doppelten
   Buttons aus Kapitel 31: das Modell, das nach der Kürzung die
   eigenen Edits vergisst.
5. **Feinschliff für die Edit-Kaskade**: Unicode-Drift (Smart Quotes,
   Gedankenstriche, geschützte Leerzeichen) wird gefaltet, und
   JSON-Strings mit rohen Steuerzeichen parst der Aktions-Parser jetzt
   klaglos.

Suite: 133/133. Der dritte Werkstatt-Besuch bestätigt das Muster der
ersten beiden: Gelernt wird an den Rändern — der Kern der Leitplanken-
Philosophie musste noch nirgends nachgebessert werden.

## 33. Zwei Bugs von aussen: der ruckelnde Cursor und der vergessene Kontext

Zwei Fehler diesmal, die nicht in `mc.py`s Logik selbst lagen — einer
im Terminal, einer beim lokalen Server.

**Der ruckelnde Cursor.** Nach dem Umstieg auf `mc_terminal.py` (mit
History, Pfeil-hoch/-runter, Skill-Vervollständigung) blieben beim
Einfuegen laengerer Eingaben und bei der History-Navigation Reste der
vorherigen Zeile im Terminal stehen — einmal so sichtbar, dass aus
`/settings base_url http://...` und einem nachfolgenden `hallo` im
Terminal `/settings basehallo` wurde. Der eingegebene Wert selbst kam
aber unversehrt bei `mc.py` an (die Task-Hinweise direkt danach liefen
normal an) — es war reines Redraw-Chaos, kein Parsing-Fehler. Ursache:
Sobald `mc_terminal.py` `readline` importiert, uebernimmt GNU readline
die Zeilenbearbeitung fuer JEDEN `input()`-Aufruf im Prozess — auch
fuer die mit Farbcodes verzierten Prompts wie `du>`. Farbcodes
(`\033[32m` & Co.) sind aber sichtbare Escape-Bytes ohne Breite auf dem
Schirm; readline zaehlt sie trotzdem mit, verrechnet sich bei der
Cursorposition, und der Redraw nach Zeilenumbruch, Paste oder
History-Wechsel geht daneben. Fix: neue Hilfsfunktion `rl_prompt()`
klammert alle ANSI-Codes in einem Prompt mit `\001`/`\002` ein — den
Markern, mit denen man readline explizit sagt „das hier ist unsichtbar,
nicht mitzaehlen". Vier `input()`-Aufrufe umgestellt, 133 Tests weiter
gruen.

Nachtrag ein paar Tage spaeter: Pfeil-hoch/-runter blieb trotzdem
fehlerhaft — der Cursor kam beim History-Wechsel nicht mehr ganz nach
links, Reste blieben stehen. Zweite Ursache, unabhaengig von den
ANSI-Codes: Zwei der vier Prompts (`du>` und die Plan-Bestaetigung)
enthielten ein eingebettetes `\n` IM Prompt-String selbst, um vor der
Eingabe eine Leerzeile zu erzeugen. Ein rohes Newline im Prompt ist fuer
readline aber kein unsichtbares Zeichen wie ein Farbcode, sondern ein
echter Zeilenumbruch, den es fuer die Zeilen-/Spaltenrechnung beim
Redraw mitfuehren muss — genau dabei verzaehlt es sich zusaetzlich. Fix:
den Zeilenumbruch per `print()` VOR `input()` ausgeben statt im Prompt
selbst unterzubringen, damit der eigentliche Prompt einzeilig bleibt.

**Die Phantom-Kontextgrenze.** Direkt danach meldete ein lokal
geladenes Modell (ueber LM Studio, 125873 Token geladenes Fenster) bei
jedem Auftrag sofort einen Fehler: *"The number of tokens to keep from
the initial prompt is greater than the context length."* Verdaechtig,
denn dieselbe Kombination lief frueher anstandslos. Nachgebaut mit
`curl` direkt gegen den Endpoint, unter Umgehung von `mc.py` komplett:
Ein nacktes `hallo` ohne System-Prompt laeuft. Der volle `mc.py`-System-
Prompt (Werkzeugbeschreibung + Projekt-Steckbrief + Code-Outline dieses
Repos, ca. 4200 Token) allein am Stueck geschickt: Fehler. Beide Haelften
einzeln getestet — Werkzeugbeschreibung (~2100 Token) laeuft, Steckbrief
+ Outline (~2500 Token) laeuft ebenfalls. Erst zusammen, ab einer
Schwelle knapp ueber 4300 Token, kippt es. Per Bisektion (Prompt in
Char-Schritten kuerzen, an jeder Stufe neu anfragen) auf ein enges
Fenster eingegrenzt: bei 4348 Token noch okay, ab rund 4350 Token
immer der Fehler. Kein `n_keep`-Feld existiert im OpenAI-kompatiblen
Request, den `mc.py` schickt (`model`, `messages`, `stream`,
`stream_options`, `usage`, `frequency_penalty` — das ist alles); an der
eigenen Anfrage gibt es also nichts zu reparieren.

Die Aufloesung kam erst beim Quervergleich mit anderen Modellen auf
demselben Server: `gemma-4-12b` verarbeitet denselben Prompt plus fast
6000 Token Puffer klaglos, `qwen3.6-27b` kippt mit identischer
Fehlermeldung. Der Live-Check via `/api/v0/models` direkt nach einem
frischen Request verriet den eigentlichen Grund: Ein Modell, das
zwischenzeitlich entladen wurde (Leerlauf, Modellwechsel, Neustart der
Werkstatt-Maschine) und dann automatisch per Request neu geladen wird
(JIT), bekommt dabei NICHT das zuvor manuell in der Oberflaeche gesetzte
Kontextfenster (125873 Token) — sondern einen viel kleineren
Default (hier: 8192 Token). Bei 8192 Token geladenem Fenster und einem
Prompt von ~4350 Token reicht die interne Keep-/Fortsetzungs-Reserve
der Engine (grob die Haelfte des Fensters plus Chat-Template-Overhead)
nicht mehr — kein Phantom-Bug, sondern ein echtes, nur unerwartet
kleines Kontextfenster. Und damit ist auch geklaert, warum es "frueher"
ging: damals steckte das Modell noch im manuell geladenen Zustand mit
125873 Token im Speicher; nach dem naechsten Entladen griff beim
naechsten mc-Lauf der kleine Default.

Kein Code-Fix in `mc.py` an dieser Stelle — vermutet wird ein zu klein
geladenes Kontextfenster nach automatischem Neuladen. Mitgenommen
bleibt in jedem Fall die Methode: Prompt haelften- und dann char-weise
per Bisektion kuerzen, `curl` direkt gegen den Endpoint, `/api/v0/models`
fuer den tatsaechlich geladenen Zustand pruefen — damit laesst sich
"liegt es am Modell, am Server oder an uns" in Minuten klaeren statt zu
raten. Ob die Theorie stimmt, klaert der naechste Kapitel-Selbsttest —
Spoiler: nur zur Haelfte.

## 34. `/model` und `/model-reset` — und der Selbsttest, der die eigene These widerlegt

Aus der Vermutung von Kapitel 33 (JIT-Reload zieht einen kleineren
Default-Kontext als der manuell gesetzte) folgte ein naheliegender
Bau-Auftrag: ein Kommando, das ein Modell am lokalen Endpunkt explizit
mit einem gewuenschten Kontextfenster neu laedt, statt sich auf den
Default zu verlassen.

**Was gebaut wurde**, alles deterministischer Python-Code in
`mc_terminal.py`/`mc.py`, bewusst NICHT als Skill (ein Skill verlaesst
sich darauf, dass das Modell selbst korrekte curl-Aufrufe mit
Endpunkt-Erkennung formuliert — gerade wenn das lokale Modell wegen
eines Kontextproblems schon schwaechelt, ist das die unzuverlässigere
Variante):
- **`/model`** ohne Argument zeigt jetzt eine nummerierte Auswahlliste
  aller Modelle am Endpunkt (funktioniert bei jedem OpenAI-kompatiblen
  Endpunkt, auch OpenRouter) statt nur den aktuellen Namen zu drucken.
  `/model <id>` wechselt weiterhin direkt.
- **`/model-reset`** erkennt per Sonde (`GET /api/v0/models` vs.
  `GET /api/tags`), ob LM Studio oder Ollama dahintersteckt, und laedt
  das aktuelle Modell explizit neu — bei LM Studio ueber die native
  `POST /api/v1/models/load`-Route mit `context_length`, bei Ollama per
  `num_ctx` im `options`-Feld von `/api/generate`. Bei einem Endpunkt
  ohne eigenen Lade-Mechanismus (OpenRouter & Co.) meldet es sauber
  "hier nicht anwendbar" statt etwas Falsches zu versuchen.
- Neue Einstellung `context_length` (Default 32768, `/settings
  context_length <n>` aenderbar), damit der Wert nicht hart codiert ist.

7 neue Tests (Endpunkt-Root-Kuerzung, Erkennung beider Engines, beide
Reset-Pfade, unbekannter Endpunkt), 140/140 gruen.

**Der Selbsttest.** Live gegen den Werkstatt-Server: `/model-reset`
funktioniert exakt wie gebaut — `/api/v0/models` bestaetigt danach
`loaded_context_length: 32768` fuer `gemma-4-26b-a4b-it@mxfp4`, vorher
8192. Der Prompt, der in Kapitel 33 bei ~4350 Token kippte, erneut
geschickt: **derselbe Fehler, an derselben Stelle.** Zur Kontrolle die
Bisektion mit dem neuen Kontextfenster wiederholt — identisches
Ergebnis, Token fuer Token: bei 4309 okay, ab 4310 immer der Fehler.
Exakt dieselbe Schwelle wie mit 8192 Token Fenster.

Das widerlegt die These aus Kapitel 33 vollstaendig: Waere ein zu
kleiner JIT-Default die Ursache gewesen, haette ein vierfach
groesseres Fenster (32768 statt 8192) die Schwelle deutlich verschieben
muessen. Sie blieb exakt gleich. Die tatsaechliche Ursache ist also
NICHT die Fenstergroesse, sondern ein fixer, offenbar modellspezifischer
Bug in LM Studios Engine fuer genau diese MoE-Quantisierung
(`...-a4b-it@mxfp4`) — unabhaengig davon, wieviel Kontext geladen ist.
Zum Vergleich verarbeitet `gemma-4-12b` denselben Prompt plus fast 6000
Token Puffer weiterhin klaglos.

Bleibt: `/model-reset` ist als generisches Werkzeug weiterhin korrekt
und nuetzlich (fuer Modelle, bei denen tatsaechlich die Fenstergroesse
das Problem ist, tut es exakt das Richtige — nachgewiesen per
`/api/v0/models`). Fuer DIESEN spezifischen Fehler bei DIESEM Modell ist
es aber kein Fix, sondern hoechstens ein Diagnose-Werkzeug, das die
falsche Theorie in Minuten widerlegt hat, statt sie ungeprueft im Blog
stehen zu lassen.

**Nachtrag, noch am selben Tag: die Ursache doch gefunden.** Drei
verschiedene `context_length`-Werte angefordert (8192, 16384, 65536) und
jedesmal per `/api/v0/models` nachgeschaut, was WIRKLICH ankommt:
egal was angefordert wird, geladen wird immer exakt **4352** Token —
Token fuer Token identisch mit der per Bisektion gefundenen
Fehlerschwelle aus Kapitel 33. Kein Zufall mehr, sondern der Beweis:
Dieses Modell (diese MoE/MXFP4-Kombination, auf dieser Hardware) laesst
sich schlicht nicht groesser als 4352 Token laden — eine harte
Kapazitaetsgrenze, vermutlich Speicher-/Allokationslimit dieser
Quantisierung, die LM Studio beim Laden still herunterkappt, OHNE das
zu melden. Die `load_config.context_length` in der Load-Antwort ist nur
ein Echo der Anfrage, keine Bestaetigung — genau das hatte die eigene
Erfolgsmeldung von `/model-reset` vorhin in die Irre gefuehrt (`32768`
angezeigt, obwohl real 4352 geladen war).

Konsequenz: `reset_model()` verlaesst sich jetzt nicht mehr auf die
Load-Antwort, sondern fragt danach per `/api/v0/models` explizit nach,
was tatsaechlich geladen wurde, und meldet eine ACHTUNG-Warnung, wenn
das deutlich unter dem Angeforderten liegt:

```
gemma-4-26b-a4b-it@mxfp4 neu geladen (LM Studio) — ACHTUNG:
32768 angefordert, aber nur 4352 tatsaechlich geladen
(Modell-/Hardware-Grenze).
```

Zwei neue Tests fuer genau diesen Fall (Bestaetigung ohne Abweichung,
Warnung bei Abweichung), 141/141 gruen. Fuer dieses eine Modell bleibt
nur, es fuer Aufgaben mit groesserem Kontext zu meiden — `gemma-4-12b`
auf demselben Server verarbeitet den identischen Prompt weiterhin ohne
Probleme.

## 35. `/mode dev|chat` — nicht jedes „hallo" braucht den vollen Werkzeugkasten

Aus der 4352-Token-Geschichte blieb eine berechtigte Nebenfrage: Selbst
wenn das Kontextfenster gross genug waere — wieso schickt mc.py bei
einem blossen „hallo" ueberhaupt einen ~4000 Token grossen System-Prompt
mit Werkzeugbeschreibung, Projekt-Steckbrief und Code-Outline? Naheliegende
Idee: erst das Modell fragen, ob die Eingabe ueberhaupt eine
Programmieraufgabe ist. Dagegen sprechen zwei Dinge, die im Projekt schon
gelernt wurden: ein zusaetzlicher Roundtrip kostet Zeit/Tokens und ist bei
genau den schwachen/lokalen Modellen unzuverlaessig, die am ehesten
Kontextprobleme haben — und er wuerde den Cache-Praefix zwischen Anfragen
veraendern, also genau das Muster zerstoeren, das die fruehe Lazy-Pruning-
Arbeit bewusst vermeidet.

Stattdessen die einfachere, deterministische Loesung: ein expliziter
Modus-Schalter. `/mode dev` (Standard, unveraendertes Verhalten) haengt
weiterhin den vollen Werkzeug-/Aktions-Prompt an; `/mode chat` schaltet
auf einen kurzen, werkzeugfreien System-Prompt um — keine Aktionen, kein
JSON-Protokoll, nur Unterhaltung. Bittet man das Modell im Chat-Modus um
eine Programmieraufgabe, weist der Prompt selbst darauf hin, `/mode dev`
zu aktivieren. Der Eingabe-Prompt zeigt den aktiven Modus direkt an
(`du [dev]>` / `du [chat]>`), Chat-Antworten laufen direkt durch
`chat_stream()` ohne Aktions-Parsing, Finish-Check oder Git-Logik — das
volle Agenten-Protokoll bleibt exklusiv `dev` vorbehalten. Die
Entscheidung, WANN welcher Modus sinnvoll ist, bleibt bewusst beim
Menschen statt bei einer weiteren Modell-Anfrage.

4 neue Tests, 143/143 gruen, live gegen den Werkstatt-Server verifiziert.

## 36. Prompt-Caching fuer Cloud-Endpunkte: 90% Rabatt auf den stabilen System-Prompt

Die 35er-Frage hatte einen zweiten Teil: Selbst in `dev` bleibt der
System-Prompt (Werkzeugbeschreibung + Steckbrief + Outline) eine ganze
Sitzung lang stabil — bei lokalen Servern (LM Studio/Ollama) ist das
dank server-eigenem Prompt-Cache nach dem ersten Request praktisch
kostenlos. Bei Cloud-Endpunkten (OpenRouter) gibt es diesen Vorteil
nicht automatisch: jede Chat-Completions-Anfrage ist zustandslos, die
komplette Historie inkl. System-Prompt wird bei JEDEM Schritt neu
abgerechnet.

Nachgemessen mit `curl` direkt gegen OpenRouter, zwei identische
Anfragen an Claude hintereinander: mit reinem String-`content` beide
Male voller Preis, `cached_tokens: 0`. Mit dem Inhalt als Anthropic-
kompatibles Content-Array plus `cache_control: {"type": "ephemeral"}`
markiert: erster Call `$0.0261` (Cache-Write, leichter Aufschlag),
zweiter Call (identischer Praefix) nur `$0.0022` — rund 90% guenstiger.
Vertraeglichkeit bei anderen Anbietern gegengeprueft (Kimi/Moonshot,
GPT-5.6-luna, Gemini-3-flash, DeepSeek): keine Fehler nirgends, manche
honorieren es sogar automatisch mit.

Umgesetzt als schlanker Eingriff GENAU an der Netzwerk-Grenze, nicht im
restlichen Code: `_payload_messages()` baut kurz vor dem Request eine
KOPIE der Nachrichtenliste, in der `messages[0]` (falls System-Rolle)
ins Array-Format mit Cache-Breakpoint gewandelt wird — der interne
String-Zustand von `messages[0]` bleibt fuer Kuerzung, Kontobuch und
`--resume` ueberall sonst unveraendert, nur der tatsaechlich gesendete
Request-Body unterscheidet sich. Lokale Engines werden dabei bewusst
ausgenommen — erkannt ueber dieselbe Sonde wie `/model-reset`
(`/api/v0/models` / `/api/tags`), diesmal aber gecacht je `BASE_URL`,
damit nicht jeder Chat-Schritt eine zusaetzliche Netzwerk-Anfrage kostet.

5 neue Tests (Cache der Erkennung, Unveraendert-Fall lokal, Umformung
bei Cloud, Sonderfall ohne System-Prompt), 147/147 gruen, end-to-end
gegen den echten `_chat_once()`-Pfad bei OpenRouter verifiziert (nicht
nur isoliert an der Hilfsfunktion). Live-Gegenprobe an einem lokalen
LM-Studio-Server blieb offen — der Werkstatt-Server haengte just in dem
Moment an `/v1/chat/completions` fest (vermutlich Nachwirkung der vielen
Kontext-Reload-Tests aus Kapitel 34), unabhaengig von dieser Aenderung.

**Nachtrag: die 4352 endgueltig erklaert.** Ein Neustart des Werkstatt-
Mac-mini behob den haengenden Server — aber `/model-reset` mit 32768
angefordert landete sofort wieder bei den bekannten 4.352 Token, ACHTUNG-
Meldung inklusive. Kein Server-Haenger-Artefakt also, sondern reproduzierbar
ueber einen Neustart hinweg. Diesmal aber mit der vollstaendigen Erklaerung
direkt aus LM Studios eigenem Log:

```
[context_fit][INFO]: Model context auto-fit: family=gemma4 max=262,144
fitted=4,352 working_set=17.76GiB reserve=3.00GiB safe_ceiling=14.76GiB
baseline=13.82GiB full_kv=20480B/token prompt_inputs=5632B/token
attention=65536B/token rotating_peak=0.59GiB fixed_ssm=0.00GiB
estimated_peak=14.78GiB
[cache_store][INFO]: VLM prompt cache context target: configured=15,953
fitted=4,352 effective=15,953
```

Kein Bug, sondern LM Studios eigener Speicher-Schutzmechanismus:
Die Modellgewichte selbst (`baseline`) belegen bereits 13.82GiB von
17.76GiB verfuegbarem Speicher; nach der 3GiB-Reserve bleiben nur rund
0.94GiB fuer den KV-Cache. Bei ~91KB Speicherbedarf pro Token
(20480+5632+65536 Byte, ueberwiegend Attention) passen da schlicht nur
4.352 Token hinein — voellig unabhaengig davon, was `configured` war
(15.953 im Log, 32768 in unserer Anfrage: beides wird gleichermassen auf
`fitted` gekappt). Die "n_keep greater than context length"-Meldung war
also die ganze Zeit nur ein irrefuehrender Folgefehler eines stillen,
speicherbedingten Auto-Fit-Downgrades — und der einzige echte Hebel liegt
ausserhalb von mc.py: mehr freier Speicher auf der Maschine, eine
speicherguenstigere Quantisierung dieses Modells, oder ein Modell mit
kleinerem `baseline`-Fussabdruck wie `gemma-4-12b`.

## 37. vMLX auf demselben Mac mini: mehr als doppelt so viel Kontext, und mc.py lernt es kennen

Der Werkstatt-Mac-mini bekam einen dritten Mitspieler: **vMLX**, ein
weiterer lokaler Server (Port 8000, `owned_by: "vmlx-engine"` als
eindeutiges Erkennungsmerkmal in `/v1/models`), der Apples MLX-Framework
nutzt statt der bisherigen llama.cpp/GGUF-Quantisierungen. Direkter
Vergleich, GLEICHE Hardware, GLEICHES Modell (`gemma-4-26b-a4b-it-mxfp4`):
Wo LM Studio bei 4.352 Token hart kappte (Kapitel 33/34/36), meldet vMLX
ein konfiguriertes Limit von **10.326 Token** — mehr als doppelt so viel
—, und unser realer mc.py-System-Prompt (5.851 Token) lief anstandslos in
~19 Sekunden durch. Bonus: Bei Ueberschreitung liefert vMLX eine
Fehlermeldung, die sofort sagt, WAS los ist (`"This would need ~18.8GB
of KV cache memory, exceeding the configured prompt/context limit of
~10,326 tokens"`) und sogar den Fix nennt (`--max-prompt-tokens`) — der
komplette Gegenentwurf zu LM Studios kryptischer "n_keep"-Meldung, die
diese ganze Ermittlungsreihe erst ausgeloest hatte.

vMLX bildet als Bonus GLEICH ZWEI APIs nach: die Ollama-API (`/api/tags`,
`/api/generate`, `/api/ps` ...) UND die OpenAI-kompatible (`/v1/chat/
completions`, `/v1/models` ...) — inklusive eigener Erweiterungen wie
`/v1/cache/*` und sogar `/v1/messages` (Anthropics Format). Das hatte
eine direkte Konsequenz fuer mc.py: Die bestehende Endpunkt-Erkennung
(`_detect_local_engine()`, gebaut fuer `/model-reset` und das
Prompt-Caching) haette vMLX ueber dessen nachgebildetes `/api/tags`
faelschlich als "ollama" erkannt. Fix: die spezifischere
`owned_by: "vmlx-engine"`-Pruefung ueber `/v1/models` laeuft jetzt ZUERST,
bevor der generische Ollama-Check greift.

Drei Ergaenzungen dank der neuen Erkennung:
1. **`_detect_local_engine()`** kennt jetzt drei Sorten: `lmstudio`,
   `ollama`, `vmlx` (bzw. `None` fuer Cloud-Endpunkte).
2. **`_loaded_ctx_tokens()`** (die bestehende Grundlage der Kontext-
   bewussten Kuerzung) fragt bei vMLX zusaetzlich `/v1/models/{model}/
   capabilities` ab und nutzt `max_prompt_tokens` — also automatisch das
   TATSAECHLICH nutzbare Fenster (10.326), nicht das theoretische Maximum
   (262.144). Kein neuer Mechanismus noetig: das bestehende Lazy-Pruning
   greift jetzt einfach auch bei vMLX korrekt.
3. **`/model-reset`** meldet bei vMLX ehrlich, dass es dort (anders als
   bei LM Studio) keinen Lade-Endpunkt gibt — das Kontextfenster sitzt
   fest im Server-Start-Flag `--max-prompt-tokens` und laesst sich zur
   Laufzeit nicht aendern. Statt ein Neuladen vorzutaeuschen, zeigt die
   Meldung den aktuellen Wert und den Flag zum Aendern.

6 neue Tests (Erkennungs-Prioritaet vMLX vor Ollama, Kontextfenster-
Fallback, ehrliche Reset-Meldung), 150/150 gruen, alles live gegen den
echten vMLX-Server verifiziert.

## 38. Reasoning erkennen: die leere Antwort war gar kein Kontext-Problem

Kaum auf vMLX umgestellt, meldete `mc.py` bei einem simplen "hallo"
sofort dreimal in Folge "Leere Antwort (vermutlich Kontextfenster
ueberschritten)" und brach ab — obwohl der Prompt mit 6.126 von 10.326
Token reichlich Luft hatte. Die 4352er-Ermittlung von Kapitel 33/34/36/37
hatte also NICHT das letzte Wort.

Der eigentliche Grund, per direktem `curl` gegen den Streaming-Endpoint
nachgestellt: Dieses Gemma-Modell ist eine "Thinking"-Variante und
sendet seine Ausgabe zunaechst als `reasoning_content` (Denk-Trace),
ERST danach als normales `content`. `mc.py` liest beim Streamen aber
ausschliesslich `delta.content` aus. Bei einem Testlauf produzierte das
Modell **700 Reasoning-Chunks und 0 Content-Chunks** — das (recht knapp
bemessene) Antwort-Token-Budget von vMLX (`max_output_tokens: 2147`)
war komplett beim Nachdenken aufgebraucht, bevor je ein sichtbares
Zeichen entstand. mc.py sah nichts, folgerte "leere Antwort" und
diagnostizierte reflexhaft auf das bekannte Kontext-Muster — diesmal
falsch. Nicht reproduzierbar bei jedem Versuch, weil die Reasoning-Laenge
pro Anfrage schwankt (Sampling): mal reicht das Budget, mal nicht.

Zufallsfund nebenbei: Bei den fruehen LM-Studio-Tests hatte dieselbe
Antwortstruktur bereits ein `reasoning_content`-Feld im Schema — es war
dort aber immer leer. Gleiche Modellgewichte vermutlich, nur zeigt vMLX
das Nachdenken aktiv (`"supports_thinking": true` in `/v1/capabilities`),
wodurch uns die ganze Kategorie bisher nie aufgefallen war.

Zwei Ergaenzungen:
1. **Erkennung**: `_chat_once()` liest jetzt auch `delta.reasoning_content`
   im Streaming aus, zaehlt die Zeichen (`LAST_REASONING_CHARS`, live im
   Warte-Spinner angezeigt: "Modell denkt (Reasoning: N Zeichen)") und
   setzt sie bei jedem Aufruf zurueck. Die "Leere Antwort"-Diagnose in
   `run_task()` unterscheidet jetzt zwei Ursachen mit unterschiedlichem
   Gegenmittel: Kontext-Ueberlauf (wie bisher, Kuerzen hilft) vs.
   Reasoning-Budget aufgebraucht (Kuerzen hilft NICHTS — Prompt- und
   Ausgabe-Budget sind getrennte Toepfe — sondern nur weniger/kein
   Nachdenken).
2. **Abschaltbar per `/settings think false`** (oder `--no-think`): haengt
   dem Request `reasoning_effort: "none"`, `enable_thinking: false` und
   `chat_template_kwargs: {"enable_thinking": false}` an — drei
   unterschiedliche Namenskonventionen verschiedener Backends fuer
   dieselbe Absicht, in der Erwartung, dass ein Endpunkt, der keinen davon
   kennt, sie einfach ignoriert (getestet: ein Nicht-Reasoning-Modell via
   OpenRouter nahm alle drei Felder klaglos entgegen). Mit `think=false`
   lief derselbe "hallo"-Auftrag sofort und ohne einen einzigen
   Reasoning-Chunk durch.

7 neue Tests (Reasoning-Zaehlung, Rueckstellung pro Aufruf, beide
Payload-Varianten, die neue Einstellung selbst), 155/155 gruen, live
gegen den echten vMLX-Server verifiziert — mit UND ohne Reasoning.

## 39. Zwei Nachtraege: `/tmp` ist kein Projektverzeichnis, und der haengende Verlauf

Direkt danach ein Praxistest, der zwei getrennte Probleme gleichzeitig
zeigte. Erstens meldete `mc.py` bei "hallo" wieder dreimal leere Antwort
— aber diesmal MIT der neuen, korrekten Diagnose (kein Reasoning gezaehlt,
also keine Fehlmeldung mehr). Der Grund war simpel und selbstverschuldet:
mc.py lief in `/private/tmp`, dem System-weiten Temp-Verzeichnis des Mac
— voller fremder, teils riesiger Dateien (einige über 100MB, manche root
gehoerend) und Resten eines alten "fe-test"-Projekts. Der automatische
Projekt-Steckbrief/Code-Outline versuchte, daraus einen Kontext zu bauen,
und sprengte damit tatsaechlich das Kontextfenster — diesmal kein Bug,
sondern schlicht der falsche Ort fuer einen autonomen Coding-Agenten.

Zweitens, und das war der eigentliche Fund: Nach dem Abbruch (3x leere
Antwort) blieb die unbeantwortete Aufgaben-Nachricht (samt den
automatisch angehaengten "Ist-Zustand erkannt"-Hinweisen, die "fe-test/"
erwaehnten) im Gespraechsverlauf haengen. Ein Wechsel zu `/mode chat`
danach aendert zwar den System-Prompt, aber NICHT den Rest des Verlaufs
— das Modell sah die alte, nie beantwortete Nachricht weiterhin und
antwortete prompt mit Bezug auf "fe-test/", was komplett verwirrend war,
weil der Chat-Modus ja explizit KEINE Projektinfos haben sollte.

Fix: An beiden Abbruch-Stellen in `run_task()` (Kontext-Ueberlauf-Serie
UND Leere-Antwort-Serie) wird die letzte, unbeantwortete user-Nachricht
jetzt vor der Rueckkehr aus dem Verlauf entfernt — der Zustand nach einem
gescheiterten Lauf ist damit wieder sauber: System-Prompt plus alle
tatsaechlich abgeschlossenen Zuege, nichts Halbes haengt mehr herum, auch
nicht ueber einen Modus-Wechsel hinweg. 1 neuer Test (Abbruch entfernt die
unbeantwortete Nachricht, Rest des Verlaufs bleibt unberuehrt), 156/156
gruen.

## 40. Warum der System-Prompt so gross geworden ist — und was das ueber kleine Modelle sagt

Eine Nebenfrage aus der Werkstatt, ausgeloest vom Blick auf einen
kompletten, real gesendeten Prompt: Der System-Anteil (Werkzeugbeschreibung
+ Projekt-Steckbrief + Code-Outline) ist inzwischen um ein Vielfaches
groesser als jede einzelne Aufgabe — selbst ein bewusst knapper Zwei-Zeiler
wie "fuege ein /profil-loeschen-Kommando hinzu" bekommt ~16.800 Zeichen
System-Kontext dazu. Die Frage dahinter: Arbeitet ein LLM tatsaechlich
besser, wenn man ihm ALLE Leitplanken explizit vorgibt, statt auf sein
eigenes Urteilsvermoegen zu vertrauen?

Kurze Antwort: ja, aber mit einem wichtigen Zusatz. Die lange Antwort
steht in der eigenen Projekt-Geschichte:

- Der **Fence-Modus** wurde Standard, NACHDEM Messungen zeigten, dass die
  JSON-Fehlerrate bei Datei-Inhalten auf 0 fiel, sobald das Format nicht
  mehr "escape das sauber selbst" verlangte, sondern explizit rohe
  ```-Bloecke vorschrieb. Nicht weniger Anleitung war die Loesung, sondern
  praeziser vorgegebene Struktur.
- Der **json-Fence-Parser-Bug** (Kapitel um den Analyse-Modus) entstand,
  weil `ANALYSE_PROMPT` das Format zwar beschrieb, aber nie ein konkretes
  Beispiel zeigte — "die Formatbeschreibung ist die Spezifikation" reichte
  nicht, "das BEISPIEL ist die Spezifikation" schon. Ein Modell, das ein
  Beispiel sieht, kopiert die Form zuverlaessiger als eines, das sie aus
  Prosa ableiten muss.
- Die **Argument-Koerzierung und Form-Reparatur** (Toleranz-Paket) wurden
  noetig, WEIL selbst detaillierte Formatvorgaben nicht immer exakt
  befolgt werden — kleine Modelle "verstehen" die Regel oft, wenden sie
  aber inkonsequent an.
- Der **komplette Waechter-Familie** (Verlust-, Duplikat-, Referenz-,
  Prosa-Waechter) ist die direkte Konsequenz einer noch schaerferen
  Erkenntnis: Selbst ein Prompt, der explizit "loesche nichts ohne Grund"
  sagt, reicht nicht — Menschen tippen faul, Modelle generalisieren
  seine Anweisungen unzuverlaessig weiter, und ein gelegentlicher
  Fehlgriff bleibt trotz bester Anleitung moeglich.

Das Muster durchzieht praktisch die ganze Projekt-Historie: **explizite,
vollstaendige Anleitung schlaegt vage Prinzipien** — ein kleines Modell,
dem man genau sagt, WELCHE Aktionen es gibt, WIE ihr Format aussieht und
WELCHES Beispiel als Vorlage dient, arbeitet spuerbar zuverlaessiger als
eines, dem man nur die Absicht beschreibt und den Rest ueberlaesst. Das
ist der Grund, warum der System-Prompt so gewachsen ist, und bewusst so
bleibt, statt ihn aus Kostengruenden zu kuerzen (siehe auch Kapitel 35/36:
lieber `/mode chat` fuer Aufgaben OHNE Werkzeugbedarf einfuehren, als am
Dev-Prompt selbst zu sparen).

Der wichtige Zusatz, der diese Lektion von einem simplen "mehr Prompt
ist besser" unterscheidet: **Anleitung allein hat eine Decke.** Selbst
die praeziseste Formatbeschreibung verhindert nicht jeden Fehlgriff — die
Waechter-Familie existiert genau deshalb als zweite, unabhaengige Schicht
UNTER dem Prompt: deterministischer Code, der nicht hofft, dass die
Anleitung befolgt wird, sondern das Ergebnis nachtraeglich prueft. Gelernt
haben wir also nicht nur "gib genaue Anleitungen", sondern die
Kombination: so viel Praezision im Prompt wie moeglich, UND so viel
Absicherung im Code wie noetig — keins von beidem ersetzt das andere.

## 41. Drei Funde aus einem einzigen "warum geht 'hallo' immer noch nicht"

Derselbe Praxistest wie in Kapitel 39, aber diesmal genauer seziert —
und diesmal fast dreifach ergiebig.

**Fund 1, ein echter Bug in der eigenen Reasoning-Diagnose (Kapitel 38).**
`chat_stream()` ruft bei einer abgeschnittenen Antwort intern mehrfach
`_chat_once()` auf (die automatische Fortsetzungs-Logik) — und JEDER
interne Aufruf setzte `LAST_REASONING_CHARS` auf 0 zurueck. Ausgerechnet
im Fall, den die Diagnose eigentlich erkennen sollte (Reasoning fuehrt zu
`finish_reason=length`, was eine Fortsetzung ausloest), ueberlebte nur der
LETZTE interne Versuch in der Zaehlung — fruehere, ggf. sehr lange
Reasoning-Phasen gingen verloren, und die Meldung fiel faelschlich auf
"Kontextfenster ueberschritten" zurueck. Fix: `chat_stream()` summiert die
Reasoning-Laenge jetzt ueber ALLE internen Versuche auf, bevor es
zurueckkehrt.

**Fund 2, isolierter Vergleichstest 3-von-3 erfolgreich, echte Sitzung
weiterhin 3-von-3 leer.** Trotz identischem Prompt, gleichem Modell,
gleichem Endpunkt lief der eigene Nachbau anstandslos durch (Reasoning
jeweils 500-1700 Zeichen, weit unterm Budget) — ein Hinweis auf reine
Sampling-Varianz (Temperatur 1.0) als Erklaerung fuer einzelne
Fehlschlaege, aber kein vollstaendiger Beweis. Bleibt vorerst offen.

**Fund 3, der eigentliche Wiederholungstaeter: `/private/tmp` als
Arbeitsverzeichnis** (siehe schon Kapitel 39 — es wurde einfach nochmal
dort gestartet). Diesmal genau vermessen: 7.236 Token Prompt, klar unter
dem 10.326er-Limit von vMLX — also diesmal wirklich KEIN Kontext-Problem,
sondern (vermutlich) wieder Zufall beim Reasoning. Aber die Vermessung
brachte einen ganz eigenen Fund zutage: `code_outline()` hatte eine
Python-Virtualenv namens `whisper-env` (aus einem voelligen fremden
Scratchpad-Unterordner, Rest einer anderen Sitzung) durchsucht und
Hunderte Funktionen aus deren `site-packages` (u.a. `typing_extensions`,
`anyio`) ins Code-Outline aufgenommen — die `IGNORE_DIRS`-Liste kennt nur
uebliche Namen wie `venv`/`.venv`, nicht jeden beliebig benannten
virtuellen-Environment-Ordner.

Zwei Nachbesserungen fuer den dritten Fund:
1. **`_is_venv_dir()`**: erkennt eine Virtualenv jetzt NAMENSUNABHAENGIG
   ueber die Markerdatei `pyvenv.cfg`, die `python -m venv` in jeder
   Virtualenv anlegt — an allen 8 Stellen im Code, die Verzeichnisse
   durchsuchen, per Ein-Zeilen-Ergaenzung nachgezogen.
2. **Eine neue Start-Warnung** (`_suspicious_cwd_warning()`): mc.py
   erkennt jetzt, wenn das Arbeitsverzeichnis ein System-/Temp-Verzeichnis
   (`/tmp`, `/private/tmp`, `/var/tmp`, `$TMPDIR`, auch Unterordner davon)
   oder direkt das Home-Verzeichnis ist, und warnt VOR dem ersten
   Dateizugriff statt stillschweigend fremden Kram einzulesen — die
   direkte Antwort auf "dann sollte man das aber irgendwie merken".

7 neue Tests (Reasoning-Summierung indirekt ueber den bestehenden
Regressionstest, `_is_venv_dir`, Venv-Ausschluss im Outline, vier Faelle
der Start-Warnung), 162/162 gruen.

## 42. "Kannst du mal das lokale Modell aergern?" — ein Drei.js-Jump'n'Run als Stresstest

Direkter Auftrag diesmal: das lokale Setup bewusst reizen, weil es
"nicht mehr so klappt wie es mal geklappt hat". Reizvoll dafuer: eine
komplette `index.html` mit Three.js (CDN), Spieler, Kamera, Steuerung,
Physik und Animation-Loop — deutlich groesser als ein einzelner
Antwort-Zyklus liefern kann.

**Erster Fund unterwegs**: vMLX hat einen echten Hardware-Sicherheitsdeckel
fuer die Ausgabelaenge. Ein Request mit `max_tokens: 8000` wurde nicht
etwa gekappt, sondern klar ABGELEHNT: *"Requested max output tokens
exceed projected safe Metal headroom: requested=8000, safe_cap=1838...
disabling it accepts Metal OOM / kernel-panic risk."* — vMLX schuetzt die
Apple-GPU aktiv vor einem Speicher-Overflow, der Kernel-Panics ausloesen
koennte. `safe_cap` schwankt mit dem freien Speicher (vorhin 2147, jetzt
1838) und liegt damit bei GROESSEREN Dateien praktisch immer unter dem,
was in EINEM Zug fertig wird — die automatische Fortsetzungs-Logik ist
hier also keine Kuer, sondern die einzige Moeglichkeit, ueberhaupt grosse
Dateien zu schreiben.

**Zweiter, eigentlicher Fund**: Ein realer Nachbau des Drei.js-Spiels
brauchte tatsaechlich mehrere Fortsetzungsrunden — und `chat_stream()`
klebt Fortsetzungen bislang per simplem `text += more` zusammen, OHNE auf
einen Zeilenumbruch an der Naht zu achten. Das waere harmlos, wenn nicht
ausgerechnet die Fence-Erkennung eine CommonMark-Regel durchsetzt: eine
schliessende ``` -Fence MUSS am Anfang einer Zeile stehen. Landet die
Fortsetzung so, dass die schliessende Fence direkt (ohne \n) an
vorherigen Code anschliesst (z.B. `canJump = false;```"`), erkennt der
Parser den kompletten Content-Block nicht mehr — trotz vollstaendigem,
korrektem Inhalt. Exakt der beobachtete Fehler: "write_file ohne Inhalt"
nach vier Fortsetzungsrunden, obwohl die Datei laengst fertig war.

Fix: `_fix_fence_seams()` normalisiert den zusammengefuegten Text nach
jeder Fortsetzung — fuegt fehlende Zeilenumbrueche VOR jede
` ``` `-Sequenz ein, die nicht schon am Zeilenanfang steht. Wichtig dabei:
Der bereits an das Modell gesendete Konversationsverlauf bleibt
UNVERAENDERT (das Modell sieht weiterhin exakt seine eigene rohe
Ausgabe) — repariert wird nur die LOKALE Kopie, die mc.py selbst zur
Fence-Erkennung verwendet. Ein Seiteneffekt beim Nachbau, den ich
mitgenommen aber nicht "repariert" habe: das Modell schrieb einmal
`<script typeer="importmap">` statt `type="importmap"` — ein echter
Modell-Tippfehler, kein mc.py-Thema, aber ein guter Reminder, dass nicht
jeder Fehler an der Werkzeug-Seite liegt.

4 neue Tests (Naht-Reparatur isoliert, unveraenderter Fall, End-to-End
durch `chat_stream()` mit simulierter Fortsetzung), 166/166 gruen.

## 43. "Kann man vMLX nicht einfach mehr Kontext geben?" — nein, und das ist auch gut so

Naheliegende Nachfrage zum `safe_cap`-Deckel aus Kapitel 42: Koennte man
vMLX nicht per API anweisen, mehr Kontext/Ausgabebudget bereitzustellen?
Nachgemessen statt vermutet: `num_ctx: 32768` ueber die Ollama-kompatible
`/api/generate`-Route geschickt, direkt danach `max_prompt_tokens` erneut
abgefragt — unveraendert bei 10.326. vMLX hat fuer ein bereits geladenes
Modell schlicht KEINEN API-Hebel dafuer; anders als LM Studio (`/v1/models/
load`) fehlt vMLX ein Lade-Endpunkt komplett (siehe Kapitel 37/38, wo
`/model-reset` das schon ehrlich meldet).

Der eigentlich interessante Teil war der Denkfehler in der Frage selbst:
Mehr Kontext wuerde das Problem nicht LOESEN, sondern VERSCHAERFEN. Ein
groesseres geladenes Kontextfenster braucht mehr KV-Cache-Speicher — und
KV-Cache und Ausgabe-Budget (`safe_cap`) konkurrieren um denselben knappen
Speicher-Topf (siehe die `context_fit`-Rechnung aus Kapitel 34:
`baseline` + `full_kv` + `estimated_peak` gegen `safe_ceiling`). Mehr
Kontext anfordern heisst weniger Spielraum fuer die Antwort, nicht mehr.

Der einzige echte Hebel liegt konsequent ausserhalb von mc.py, direkt am
Mac mini: vMLX mit groesserem `--max-prompt-tokens` neu starten (nur wenn
genug Speicher frei ist), mehr Speicher freimachen (der `safe_cap`
schwankte zwischen unseren eigenen Tests bereits von 2147 auf 1838), oder
eine speicherguenstigere Quantisierung laden. Kein Code-Fix diesmal — nur
eine Vermutung durch eine Messung ersetzt, bevor sie sich falsch
festgesetzt haette.

## 44. Falscher Flag-Name, ein drittes Tool, und zwei echte Nachbesserungen

Der Versuch, vMLX mit `--max-prompt-tokens` neu zu starten, deckte gleich
mehrere Schichten auf. Erstens: der Flag existiert gar nicht — ein
Fehler, den ich selbst gemacht hatte (aus dem Wortlaut einer
Fehlermeldung abgeleitet statt aus der echten Doku). Das offizielle
Repo (github.com/jjang-ai/vmlx) nennt den echten Namen: `--max-model-len`
(dieselbe Konvention wie vLLM). Zweitens: selbst mit korrektem Flag
haette `open -a vMLX.app --args ...` nichts bewirkt — laut
Architektur-Diagramm im Repo spawnt die Electron-Desktop-App den
eigentlichen Server-Prozess ueber einen eigenen Session-Manager, der
macOS-Start-Argumente gar nicht durchreicht. Zwei Lektionen fuer den
Preis eines fehlgeschlagenen Neustarts: Doku statt Fehlermeldungs-
Wortlaut als Quelle nehmen, und bei Electron-Wrapper-Apps nicht von
`open --args` ausgehen.

Dann der Szenenwechsel: Auf demselben Mac mini lief plötzlich **oMLX**
statt vMLX — ein drittes, eigenstaendiges Tool, API-Key-geschuetzt.
Direkt am API erkundet statt geraten: `/v1/models/status` zeigt pro
geladenem Modell `max_context_window` UND `max_tokens` (Ausgabebudget)
— bei diesem Modell **262.144 bzw. 32.768**, beides drastisch groesser
als vMLX' ~10.326 bzw. ~1.838-2.147. Der Drei.js-Jump'n'Run-Stresstest
aus Kapitel 42 lief bei oMLX in **einem einzigen Aufruf, 38 Sekunden,
keine Fortsetzung noetig** — kein Fence-Nahtstellen-Risiko, weil das
grosszuegige Budget gar nicht erst zum Abschneiden zwingt.

Beim Durchsehen des generierten Codes aber ein echter Modell-Fehler:
`new THREE.BoxGeometry(50, , 50)` — ein fehlendes Argument, ein
handfester JavaScript-`SyntaxError`, der das ganze `<script
type="module">` beim Parsen zum Absturz gebracht haette. mc.py validiert
bisher nur `py/json/yaml/php` (plus JSX/TSX in npm-Projekten) — HTML-
Dateien mit eingebettetem `<script>` liefen nie durch eine Pruefung.

Drei Nachbesserungen aus diesem einen Werkstatt-Ausflug:

1. **HTML-Validierung**: `_extract_inline_scripts()` zieht `<script>`-
   Bloecke ohne `src` und ohne JSON-artigen `type` (importmap etc.) aus
   HTML-Text; `_check_js_syntax()` prueft sie zuerst projektlokal per
   esbuild/oxlint (wie JSX/TSX), sonst per system-weitem `node --check`
   als Fallback — funktioniert AUCH ohne npm-Projekt/node_modules, genau
   der Fall bei einer einzelnen `index.html` mit CDN-Importen. Wichtiges
   Detail: die temporaere Pruef-Datei bekommt die Endung `.mjs`, sonst
   faellt `node` auf CommonJS zurueck und meldet bei jedem `import`-
   Statement einen falschen Fehler.
2. **oMLX-Unterstuetzung**: `_detect_local_engine()` erkennt jetzt vier
   Sorten (`lmstudio`, `ollama`, `vmlx`, `omlx`) ueber `owned_by` in
   `/v1/models`. `_loaded_ctx_tokens()` fragt bei oMLX zusaetzlich
   `/v1/models/status` ab (mit Bearer-Auth, anders als LM Studio/vMLX).
   `/model-reset` nutzt oMLX' ECHTEN Lade-/Entlade-Endpunkt (anders als
   vMLX), kann das Kontextfenster selbst aber ebenfalls nicht setzen —
   das sitzt hinter einer separaten Admin-Anmeldung im Dashboard, die
   mc.py nicht hat. Auch hier: ehrlich melden statt vortaeuschen.
3. Der Flag-Name-Fehler selbst wurde in den fruehren Blog-Eintraegen
   NICHT nachtraeglich korrigiert (Kapitel 42/43 nennen noch
   `--max-prompt-tokens`) — bewusst so belassen, weil der Fehler und
   seine Aufklaerung selbst Teil der chronologischen Geschichte sind.

9 neue Tests (HTML-Extraktion, esbuild-Pfad, node-Fallback fuer Fehler
UND gueltiges ESM, kein-Script-Fall, oMLX-Erkennung, Kontextfenster-
Fallback, beide Reset-Faelle), 175/175 gruen, alles live gegen den
echten Server verifiziert.

## 45. oMLX' max_context_window luegt nicht, aber es taeuscht

Direkt beim ersten echten End-to-End-Test mit oMLX (derselbe Drei.js-
Jump'n'Run-Auftrag wie in Kapitel 42, diesmal ueber `/settings api_key`)
kippte der Lauf nach dem dritten Schritt in genau das Muster, das schon
LM Studio (Kapitel 33/34) gezeigt hatte: Ein echter Kontext-Ueberlauf vom
Endpoint, `"Endpoint meldet Kontextfenster: 13081 Token"` — obwohl
`/v1/models/status` fuer dasselbe Modell **262.144** als
`max_context_window` gemeldet hatte, dieselbe Zahl, die Kapitel 44 gerade
erst als vertrauenswuerdig eingebaut hatte.

Direkter Vergleich der beiden Zahlen zeigt das Problem: `max_context_window`
ist das THEORETISCHE Konfigurationsmaximum des Modells, nicht das
tatsaechlich allozierte Fenster — exakt dieselbe Verwechslung wie
LM Studios `max_context_length` vs. `loaded_context_length`. Der
Unterschied: LM Studio liefert BEIDE Werte getrennt, oMLX' `/v1/models/
status` nur den theoretischen. Mit dieser (falschen) 262.144 als
Kuerzungs-Schwelle liess `maybe_prune()` die Historie frei wachsen — bis
zum echten Ueberlauf bei ~22.000 Token, viel zu spaet.

Fix: `_loaded_ctx_tokens()` gibt fuer oMLX jetzt bewusst 0 zurueck (die
262.144 werden schlicht ignoriert) — `maybe_prune()` faellt damit auf sein
BEREITS VORHANDENES sicheres Verhalten fuer 'Fenster unbekannt' zurueck:
vor JEDEM Schritt kuerzen statt zu warten, bis es reisst. Kostet etwas
Prompt-Cache-Vorteil (der eigentliche Sinn der lazy Kuerzung), gewinnt
aber Zuverlaessigkeit — und die reaktive Selbstkalibrierung ueber
`CtxOverflowError` bleibt unveraendert als zweites Sicherheitsnetz
bestehen, falls doch mal etwas durchrutscht.

End-to-End nachgemessen, derselbe Spiel-Auftrag zweimal im direkten
Vergleich: VOR dem Fix wuchs die gesendete Historie ungebremst auf
~40.000 Zeichen, drei leere Antworten in Folge, Abbruch. NACH dem Fix:
eine einzelne voruebergehende leere Antwort bei ~17.200 Zeichen, ein
Wiederholungsversuch, dann sauberer Abschluss mit `finish` — alle drei
Dateien geschrieben, keine einzige Kontext-Ueberlauf-Meldung mehr. 1
Test angepasst (das alte, jetzt bewusst falsche Verhalten durch das neue
ersetzt), 178/178 gruen.

## 46. oMLX cached automatisch — kein Marker noetig, kein Code-Fix noetig

Kurze Nachfrage: cached oMLX den Prompt wie OpenRouter/Anthropic (Kapitel
36)? Direkt gemessen statt vermutet: derselbe grosse System-Prompt zweimal
hintereinander geschickt, PLAIN als String-Content (kein Array, kein
`cache_control`-Marker) — erster Call `cached_tokens: 0`, `total_time:
10.0s`; zweiter Call mit identischem Praefix `cached_tokens: 4096`,
`total_time: 1.64s`. Automatisch, ohne jede Sonderbehandlung im Request.

Kein Code-Fix noetig, im Gegenteil: Die `cache_control`-Umformung aus
Kapitel 36 ist fuer Anthropic-artige Cloud-APIs gedacht, die es explizit
verlangen — bei oMLX waere sie ueberfluessig und (da `_is_local_engine()`
oMLX korrekt erkennt) wird sie ohnehin schon uebersprungen. Der eigentliche
Grund, warum das automatisch funktioniert, ist derselbe, der die ganze
Lazy-Pruning-Arbeit von Anfang an begruendet hat: mc.py haelt den
System-Prompt in einer Sitzung stabil und veraendert den Praefix nie
unnoetig — das kommt jedem lokalen Server mit Prefix-Cache zugute, ganz
gleich ob LM Studio, vMLX oder oMLX, ohne dass mc.py wissen muss, WELCHER
davon es gerade ist.

## 47. Wenn der Wächter selbst zum Problem wird: der Bürokratie-Loop

Ein DeepSeek-V4-Flash-Lauf (billig, klein) sollte ein Drei.js-Spiel
"mit richtig viel details" aufwerten — und geriet in eine Endlosschleife.
Auslöser: der Verlust-Wächter (Kapitel 31) meldete wiederholt entfernte
Namen (`CONFIG`, `createGain`, `COLORS`...), und das Modell verstand die
Meldung nicht als Hinweis, sondern als Fehler, den es beheben muss —
mit Code. Ergebnis, wörtlich im generierten File: ein Kommentar
`// Wiederherstellung von CONFIG_BINDING für den VERLUST-WAECHTER`, gefolgt
von einem toten `console.log("Config applied:", COLORS, CONFIG_BINDING)`
— das Modell hatte den eigenen Schutzmechanismus AUSGETRICKST, statt die
eigentliche Aufgabe (mehr Spieldetails) zu erledigen.

Zwei Ursachen, beide im Code bestaetigt:
1. Die Formulierung erlaubte zwar schon "kurz begruenden und
   weiterarbeiten", war fuer ein schwaches Modell aber offenbar nicht
   eindeutig genug, dass ein SATZ reicht -- kein Code noetig.
2. Der Wächter vergleicht bei jedem Schreibvorgang nur den UNMITTELBAR
   vorherigen Zustand mit dem neuen. Ein Modell, das dieselbe Datei
   wiederholt komplett neu schreibt (statt gezielt zu editieren, siehe
   die "3x fast identisch neu geschrieben"-Bremse aus einem frueheren
   Kapitel), variiert von Versuch zu Versuch, was drin ist -- der
   Wächter feuerte dadurch bei praktisch jedem Versuch erneut fuer
   denselben, laengst kommentierten Verlust. Kein Gedaechtnis, keine
   Ruhe, kein Fortschritt.

Zwei kleine, unabhaengige Aenderungen an `_loss_warning()`:
1. **Schaerfere Formulierung**: explizit "kein Code-Fix noetig, kein
   kuenstliches Wiedereinfuegen (z.B. per totem console.log) nur um die
   Namen 'benutzt' aussehen zu lassen" -- UND einen Satz Begruendung
   in der Antwort reicht.
2. **Einmal gemeldet, dann Ruhe**: `LOSS_WARNED_NAMES` merkt sich (Pfad,
   Name)-Paare pro Aufgabe (zurueckgesetzt zu Beginn von `run_task()`,
   wie READ_FILES & Co.) -- ein bereits gemeldeter Verlust wird fuer den
   Rest DIESER Aufgabe nicht nochmal angemahnt. Neue Verluste an neuen
   Namen werden weiterhin sofort gemeldet; die Schutzfunktion bleibt
   fuer alles Neue voll erhalten, nur das wiederholte Nachtreten beim
   selben, schon kommentierten Fall entfaellt.

Die tiefere Frage aus diesem Fund bleibt eine echte Design-Spannung, die
sich nicht vollstaendig aufloesen laesst: Ein rein textbasierter,
deterministischer Wächter kann strukturell nicht wissen, ob ein
Komplett-Neuschrieb im Einzelfall Sinn ergibt oder nicht -- er kann nur
auf das WAS reagieren (etwas ist weg), nie auf das WARUM. Die jetzige
Loesung verschiebt die Entscheidung dahin, wo sie hingehoert: einmal
melden, dann dem Modell (und im Diff-Review dem Menschen) ueberlassen,
ob die Begruendung traegt -- statt denselben Fall in einer Schleife
wieder und wieder zur Abstimmung zu stellen.

4 neue Tests (einmalige Meldung, neuer Verlust trotz bereits gewarnter
Namen, neue Formulierung), 181/181 gruen.

## 48. Ctrl-C soll den Auftrag abbrechen, nicht das ganze Terminal

Kleiner, aber spuerbarer Komfort-Wunsch: ein einzelnes Ctrl-C waehrend
eines laufenden Auftrags beendete bisher gleich die komplette Sitzung —
kein Weg, nur den aktuellen (z.B. feststeckenden oder unerwuenschten)
Lauf abzubrechen und im Terminal weiterzuarbeiten.

Die Loesung kommt ohne Zaehler oder Timer aus: `KeyboardInterrupt`
waehrend der Plan-Phase oder der Agenten-Schleife (`run_task()`) wird
jetzt in der interaktiven Schleife selbst abgefangen — druckt eine
Abbruch-Meldung samt Hinweis ("Nochmal Ctrl-C druecken, um mc.py zu
beenden") und kehrt zur naechsten `du>`-Eingabe zurueck. Ein ZWEITES
Ctrl-C direkt an dieser naechsten Eingabe lauft in das ohnehin schon
bestehende `except (EOFError, KeyboardInterrupt)` rund um `input()` und
beendet mc.py ganz reguleaer — "einmal abbrechen, nochmal wirklich
beenden" ergibt sich also aus der bestehenden Struktur, ohne eigene
Zustandsverwaltung. `after_run()` wird bewusst auch nach einem Abbruch
aufgerufen: bei `result=None` bietet es (wie beim Erreichen des
Schrittlimits schon immer) einen Rollback der bis dahin gemachten
Aenderungen an, statt sie kommentarlos liegen zu lassen.

## 49. "Das Tool verrennt sich total" — ein Live-Sezieren via vibelove, und was dabei zutage kam

Direkter Auftrag: ein 3D-Jump'n'Run mit Three.js in einem neuen vibelove-
Projekt bauen lassen (`google/gemma-4-26b-a4b-it:free` via OpenRouter),
und GENAU beobachten, wo es hakt. Ergebnis: ein veritabler Modell-
Zusammenbruch, live mitverfolgt.

**Der Zusammenbruch selbst.** Nach zwei sauberen Schritten (Vite-Geruest,
Abhaengigkeiten installieren) geriet das Modell beim eigentlichen
Schreiben der Spiel-Logik in eine Spirale aus Selbstkorrektur-Meta-Text:
`<Wait - looking at my previous attempt...>`, erfundene "System Alert"-
Nachrichten, geleakte Chat-Template-Sondertoken (`<channel|>`), kaputte
JSON-Pfade (`"main most}_wait_"`, `"main://NO_"`), zunehmend hysterische
Grossschreibung (`GO!!!!!!!!!!!!!!!!!!!!!!!!!!!!!`) und einen einzelnen
Lauf ueber 65.000 Tokens fuer eine einzige Datei. mc.pys bestehende
Absicherung griff dabei zuverlaessig: `_looks_runaway()` schnitt die
degenerierte Antwort zweimal ab ("Antwort ausser Kontrolle... gekappt"),
und der fehlende ` ```content `-Block danach loeste den bekannten
"write_file ohne Inhalt"-Fehler aus — die Datei blieb in BEIDEN Faellen
unveraendert (der urspruengliche Vite-Demo-Code), kein Byte Muell
gelangte auf die Platte. Nach zwei Nudges fing sich das Modell tatsaechlich
selbst und schrieb sauberen Code — bis es in einem ZWEITEN Content-Block
mitten im selben Schreibversuch erneut kippte. Nach ueber sieben Minuten
in derselben Schleife wurde der Lauf abgebrochen.

**Die Erklaerung: "gleiches Modell" ist bei OpenRouter kein Versprechen.**
Waehrend der Untersuchung fiel auf: derselbe HTTP-429-Fehler nannte
`"provider_name":"Google AI Studio"`, ein direkter Test Sekunden spaeter
lief ueber `"provider":"Darkbloom"` — OpenRouter routet ein Gratis-
Modell-Label ueber MEHRERE Backend-Anbieter, je nach Verfuegbarkeit.
Dieselbe gemma-4-26b, mit der frueher gute Erfahrungen gemacht wurden,
kann also technisch eine ganz andere Serving-Instanz sein (andere Last,
andere Quantisierung/Konfiguration) — "gleiches Modell" ist bei
Gratis-Routing kein Versprechen auf gleiche Infrastruktur.

**Der Vergleich, der alles einordnete.** Derselbe Auftrag, dasselbe
Projekt, mit `deepseek/deepseek-v4-flash-0731`: 7 Schritte, sauberer
Batch-Read aller relevanten Dateien zu Beginn, strukturierter Code mit
Nebel-Effekt, geklonten Baumobjekten, Delta-Zeit-Physik und korrekter
AABB-Kollision, zweimal `npm run build` zur Selbstpruefung, ein
begruendetes `finish` — fuer $0.0088. Kein einziger Rueckfall.

**Ein echter vibelove-Bug, gefunden waehrend der Aufraeumarbeiten danach.**
`server.py` nahm fuer die BUILD_HISTORY (den Kontext, der dem NAECHSTEN
Bauauftrag mitgegeben wird) bisher ungefiltert die letzten 500 Zeichen
der kompletten Prozessausgabe. Bei einem entgleisten Lauf bestehen genau
diese letzten 500 Zeichen aus Zusammenbruch-Muell — der dann UNGEFILTERT
als "Ergebnis des vorherigen Schritts" in den naechsten Prompt
uebernommen wurde. `_extract_run_summary()` zieht jetzt stattdessen
gezielt mc.pys eigene finish-Zusammenfassung (die Zeile `✓ <text>`
unmittelbar vor der Token-/Kosten-Zeile) heraus; ohne sauberes finish
(oder bei degeneriert wirkendem Fund, per Mini-Version von mc.pys
DEGEN_CHAR_RE/DEGEN_WORD_RE) gibt es einen neutralen Platzhalter statt
Rohtext.

## 50. Vibelove-Redesign: Chat statt Terminal-Formular, kompakte Toolbar, Git-natives Rollback

Direkt im Anschluss ein UI-Wunsch fuer vibelove selbst: kleinere Buttons
statt der breiten Formularleiste, eine Chat-Oberflaeche (Lovable-/
WhatsApp-Stil) statt reinem Terminal-Auswurf fuer die Ergebnisse, und
IMMER die Moeglichkeit, auf den Stand vor der letzten Anweisung
zurueckzuspringen.

**Die Design-Entscheidung**: keine zweite, parallele Chat-Historie im
Server pflegen. Jeder sauber abgeschlossene mc-Lauf erzeugt bereits einen
eigenen Git-Commit (mc.pys eigene `git_commit_run()`) — die Git-Historie
des Projekts IST die Chat-Historie, nur anders dargestellt. Ein neuer
Endpunkt `GET /projects/git-log` liefert Hash/Parent/Nachricht jedes
Commits; die Oberflaeche baut daraus die Chat-Bubbles (Nutzer-Anweisung
rechts, Ergebnis-Zusammenfassung links mit einklappbaren Terminal-
Details). Ergebnis: der Chat-Verlauf ueberlebt Seiten-Reloads und
Projekt-Wechsel von selbst, ganz ohne eigene Persistenz-Schicht.

**Rollback als direkte Konsequenz**: jede Ergebnis-Bubble bekommt einen
"↩ Rueckgaengig"-Knopf, der den ELTERN-Commit dieser Bubble uebergibt.
Ein neuer Endpunkt `POST /projects/rollback` prueft zuerst, dass der
angegebene Commit-Hash ueberhaupt in DIESEM Projekt-Repo existiert
(schuetzt vor einem veralteten Hash aus einem Browser-Tab, der z.B. nach
Projektwechsel auf ein FALSCHES Projekt zeigen wuerde), dann `git reset
--hard` + `git clean -fd` (entfernt nur echte, nie committete
Neuzugaenge — .gitignore-Eintraege wie `node_modules/` bleiben
unangetastet, kein `-x`).

**Der Realitaetscheck, der einen zweiten echten Bug freilegte**: Beim
Live-Test des neuen Features (im selben `jumprun3d`-Projekt aus Kapitel
48) zeigte `/projects/git-log` nur EINEN Commit, obwohl der erfolgreiche
DeepSeek-Lauf sichtbar Dateien geschrieben hatte. Grund: der zuvor per
SIGTERM abgebrochene gemma-4-Lauf hatte neue, nie committete Dateien
hinterlassen (`frontend/`, `MC-NOTIZEN.md` als "untracked"). mc.pys
eigene Git-Absicherung verlangt fuer JEDEN Lauf einen SAUBEREN
Arbeitsbaum beim Start — fand sie stattdessen "offene Aenderungen" vor,
blieb ihre Commit-Logik fuer den GESAMTEN naechsten Lauf deaktiviert,
obwohl dieser selbst sauber durchlief. Genau das stand sogar sichtbar in
der Lauf-Ausgabe ("Git-Absicherung nicht verfuegbar (Arbeitsbaum nicht
sauber)"), war zuvor aber nicht als Problem erkannt worden.

Das untergraebt das "immer moeglich"-Versprechen des neuen Rollback-
Features direkt an der Wurzel — also behoben an der Wurzel:
`stelle_sauberen_arbeitsbaum_sicher()` committet liegen gebliebene
Aenderungen VOR jedem neuen Bauauftrag (nur falls tatsaechlich etwas
unsauber ist, kein leerer Commit im Normalfall), damit mc.pys eigene
Git-Absicherung bei JEDEM Lauf funktionsfaehig bleibt — unabhaengig
davon, ob ein vorheriger Lauf sauber durchlief, scheiterte oder
abgebrochen wurde. Zweifacher Live-Test (zwei Bauauftraege + ein
Rollback dazwischen) bestaetigte die durchgehende Commit-Kette:
`Erst-Commit → vibelove: Zwischenstand → mc: HUD-Titel angepasst`, danach
sauberer Rollback auf den mittleren Commit, danach ein weiterer sauberer
mc-Commit OHNE erneuten Zwischenstand (Baum war ja schon sauber) — die
Kette haelt.

Kein automatisierter Test-Unterbau fuer `vibelove/server.py` vorhanden
(anders als `mc.py`) — die Verifikation lief vollstaendig live gegen den
echten Flask-Server per curl (Endpunkt-Antworten, Git-Log-Inhalt,
Rollback-Effekt auf die tatsaechliche Datei, Ablehnung ungueltiger/
fremder Commit-Hashes) sowie eine statische HTML/JS-Konsistenzpruefung
(Tag-Balance, referenzierte IDs). Kein Zugriff auf einen echten Browser
in dieser Umgebung verfuegbar — die visuelle/interaktive Pruefung der
neuen Oberflaeche bleibt beim Nutzer offen.

## 51. Zehn verwaiste Vite-Prozesse: kill -TERM loest kein atexit aus

Nachtrag noch am selben Tag: "laeuft da noch was zum jump and run, und
was ist das Problem?" Antwort auf den ersten Teil: ja, das Spiel lief
weiterhin einwandfrei. Der zweite Teil deckte aber einen echten
Ressourcen-Leck auf -- zehn Vite-Kindprozesse liefen gleichzeitig fuer
dasselbe Projekt, Reste jedes einzelnen manuellen Server-Neustarts
waehrend der Redesign-Arbeit aus Kapitel 50.

Ursache: `vite_process = subprocess.Popen(..., start_new_session=True)`
entkoppelt den Vite-Kindprozess bewusst vom Server-Prozess (er soll die
Konsolen-Session ueberleben) -- aber genau das bedeutet auch, dass ein
externes `kill <pid>` gegen den Flask-Server NUR den Server selbst
beendet, nicht sein Kind. `atexit.register(cleanup)` haette den Vite-
Prozess sauber mitbeendet, ABER: Pythons `atexit`-Handler laufen nur bei
einem NORMALEN Prozessende (`sys.exit()`, Rueckkehr aus main(),
unbehandelte Exception) -- ein von aussen gesendetes SIGTERM ueberspringt
sie komplett, sofern kein expliziter Signal-Handler registriert ist. Jeder
`kill -TERM` gegen den Server liess den Vite-Kindprozess also als Zombie
zurueck; nur einer davon (der aelteste, der den Port zuerst bekam) diente
tatsaechlich die Vorschau aus, die anderen neun hingen nutzlos herum.

Fix: ein expliziter `signal.signal(signal.SIGTERM, _beende_sauber)` (und
SIGINT dazu), der `cleanup()` VOR dem Prozessende aufruft -- ab jetzt
raeumt auch ein externes `kill` den Vite-Kindprozess korrekt auf. Die
zehn bestehenden Leichen manuell per PID beendet, ein sauberer Neustart
bestaetigte danach: genau ein Vite-Prozess, HTTP 200, richtiges Projekt.

Der zweite Teil des Fundes (kurzzeitig war ploetzlich das falsche,
unabhaengige Alt-Projekt "jumprun" statt "jumprun3d" aktiv) hatte gar
keinen Code-Grund: der Nutzer hatte selbst im geoeffneten Browser-Tab am
Projekt-Dropdown geklickt. Guter Reminder, bei ueberraschendem Zustand
zuerst den einfachsten Erklaerungsweg zu pruefen, bevor man einen Bug
vermutet.

## 52. Der Protokoll-Kern spricht jetzt Englisch — 4352 Tokens sind 4352 Tokens

Ausloeser war ein Rueckschritt: gemma-4-26b-a4b-it-mxfp4 lief frueher
zuverlaessig ueber LM Studio auf dem Mac mini unter .191 (siehe die
fruehen Kapitel), inzwischen aber nicht mehr. Der Grund war kein Bug,
sondern schlichtes Wachstum: jedes neue Feature dieses Projekts hat den
System-Prompt um ein paar Zeilen erweitert, und die Maschine hat via LM
Studios eigenem `context_fit`-Log eine reale, hardware-bedingte
Speichergrenze von exakt 4352 Tokens fuer genau dieses Modell -- kein
Software-Limit, das sich durch eine groessere `context_length`-Anfrage
umgehen liesse (dreimal probiert, dreimal identisch stillschweigend auf
4352 gekappt).

Drei Stellschrauben standen zur Wahl: Prompts knapper formulieren,
Prompts auf Englisch umstellen, oder die Prompt-Groesse adaptiv an das
erkannte Kontextfenster anpassen. Gegen das blinde Kuerzen sprach Kapitel
40: explizite, ausformulierte Praezision ist genau das, was kleine
Modelle zuverlaessig macht -- vage machen waere ein Rueckschritt an
anderer Stelle. Ein kontrollierter Versuch (identischer Inhalt, einmal
deutsch, einmal englisch, echte Tokenizer-Anfrage an das tatsaechlich
betroffene Modell) ergab 2142 vs. 1758 Tokens -- rund 18% Ersparnis, ohne
ein Wort an Bedeutung zu verlieren. Adaptive Groessenanpassung bleibt ein
sinnvoller struktureller Folgeschritt, ist aber groessere Chirurgie; die
Uebersetzung war der schnelle, risikoarme Gewinn und wurde zuerst
umgesetzt.

Uebersetzt wurde bewusst nur der FESTE Protokoll-Kern -- der Teil, der
bei praktisch jedem Lauf mitgeschickt wird: `SYSTEM_PROMPT_TEMPLATE`
samt den `WRITE_SPEC`/`EDIT_SPEC`/`CONTENT_RULE`/`EXAMPLE`-Bausteinen,
`CHECK_PROMPT`, `ANALYSE_PROMPT`, `EXPLORE_PROMPT`, `CHAT_SYSTEM_PROMPT`
und die deterministischen Task-Hint-Funktionen (`task_hints`,
`terse_task_hint`, `qa_task_hint`). Die ~30 situativen Waechter- und
Fehlermeldungen (Verlust-Waechter, Duplikat-Waechter, JSON-Koerzierungs-
fehler und aehnliche, die nur bei bestimmten Ereignissen ueberhaupt im
Kontext landen) bleiben bewusst ein eigener, spaeterer Uebersetzungsblock
-- kleinere Hebelwirkung pro Aufwand, und jede Uebersetzung ist eine
Gelegenheit, eine Nuance zu verlieren.

Zwei Faktoren mussten mitgezogen werden, sonst waere die Uebersetzung
unbemerkt kaputtgegangen:

Erstens die Sprache der Modell-Antworten selbst. Der Nutzer liest
Deutsch; ein rein englischer Protokoll-Prompt haette das Modell ohne
Not auf Englisch antworten lassen koennen. Jeder uebersetzte
System-/Analyse-/Explore-/Chat-Prompt bekam deshalb einen expliziten
Satz: "Always reply in German (the user is German-speaking)."

Zweitens eine interne Kopplung, die erst der Grep vor der eigentlichen
Aenderung offenlegte: `terse_task_hint()` und `qa_task_hint()` erzeugen
Text, der NICHT nur an das Modell geht, sondern von mc.py's eigener
Interaktions-Schleife per Substring geprueft wird, um zu entscheiden, ob
eine Info-Zeile im Terminal erscheint ("Knappheits-Stupser angehaengt",
"Frage-Weiche aktiv") -- an vier Stellen im Code (einmal fuer den
Single-Task-Modus, einmal fuer den interaktiven REPL-Modus, je zwei
Pruefungen). Die deutschen Marker "knapp gehalten" und "eine FRAGE"
wurden durch die im uebersetzten Hint-Text tatsaechlich vorkommenden
englischen Phrasen "kept brief" und "a QUESTION" ersetzt -- an ALLEN
vier Stellen gleichzeitig, sonst haette die Terminal-Anzeige lautlos
aufgehoert zu funktionieren, obwohl das Modell weiterhin korrekt
reagiert.

Ein einziger Test hing an konkretem Prompt-Text (`"LEERES Pflichtfeld"
in mc.CHECK_PROMPT`), sieben weitere an den Marker-Substrings der
Hint-Funktionen -- alle mit auf die neuen englischen Formulierungen
umgestellt. Danach: 181 Tests gruen. Ergebnis in Zeichen: der reale
System-Prompt (Fence-Modus) schrumpfte von etwas mehr als 8000 auf
6729 Zeichen -- die Groessenordnung passt zur fruehen Messung, auch
wenn Zeichen kein Tokens sind. Das eigentliche Kriterium bleibt die
naechste Live-Session mit gemma-4-26b-a4b-it-mxfp4 auf .191: passt der
Prompt jetzt wieder unter 4352 Tokens.

## 53. Die Live-Session: oMLX luegt nicht, es verhungert -- und LM Studio war die ganze Zeit der bessere Nachbar

Der Test kam schneller als gedacht: neues vibelove-Projekt "pacman",
oMLX auf .191 mit gemma-4-26b-a4b-it-mxfp4, Auftrag "baue ein
Pac-Man-aehnliches Spiel". Die ersten vier Schritte liefen sauber --
`index.html`, `style.css` und ein 6450 Zeichen grosses `script.js` mit
Labyrinth, Punkten, zwei Geistern (einfacher Greedy-Algorithmus) und
Score/Game-Over, alles auf Deutsch kommentiert, das uebersetzte
Protokoll aus Kapitel 52 wurde korrekt verstanden und beantwortet. Dann,
Schritt 5: drei leere Antworten in Folge, sauberer Abbruch durch mc.py's
eigene Kontext-Kalibrierung -- kein Crash, keine kaputte Datei, aber
auch kein `finish`. oMLX hatte sein reales nutzbares Fenster inzwischen
auf 6861 Token herunterkalibriert.

Die eigentliche Frage danach war nicht "wie schreibt man kompaktere
Prompts" (das war Kapitel 52, und half hier kaum -- das feste
Protokoll ist nur ein kleiner Bruchteil von 6861 Token), sondern: WARUM
ist das Fenster so klein, und laesst sich das vergroessern? Eine Reihe
echter Messungen statt Vermutungen:

**oMLX' eigener Speicherstatus** (`/v1/models/status`) zeigte den
Grund direkt: `final_ceiling` (oMLX' Sicherheitsbudget) lag bei ~19.07
GB, das geladene 26B-Modell allein verbrauchte ~15.28 GB davon -- nur
~3.79 GB blieben fuer den KV-Cache/Kontext. Ein `/model-reset` (dieselbe
Funktion, die mc.py fuer den ehrlichen Neuladen-Bericht nutzt) aenderte
daran nichts -- keine Fragmentierung, sondern eine feste Rechnung.

**Ein Umstieg auf ein kleineres Modell** (`gemma-4-e4b-it-4bit`, ~5.4 GB
Gewichte) zeigte sofort ~13.67 GB frei -- rund 3.6x mehr Kopfraum.
Interessanterweise galt das NICHT linear fuer jedes kleinere Modell:
`gemma-4-12b-coder-fable5-composer2.5-8bit` hatte zwar mehr freien
Speicher als das 26B-Modell (~5.39 GB), aber ein per Bisektion
gemessenes REALES Fenster von nur ~4300-4900 Token -- kleiner als beim
26B-Modell trotz mehr freiem Speicher. Vermutlich ein Architektur-
Unterschied (`vlm`/`gemma4_unified` vs. schlichtes `gemma4`) mit
hoeherem KV-Cache-Verbrauch pro Token. Freier Speicher in GB sagt also
nichts Verlaessliches ueber die nutzbare Token-Zahl aus, ohne Modell
fuer Modell echt zu messen.

**Ein Dashboard-Etikettierungsfehler**: der Nutzer setzte im oMLX-
Dashboard ein Feld, das dort "context window" hiess, auf 132768 --
`/v1/models/status` zeigte danach aber `max_tokens: 132768` (die
Obergrenze fuer generierte AUSGABE-Token), waehrend `max_context_window`
unveraendert bei 262144 blieb. Ein identischer Bisektions-Test vorher/
nachher zeigte exakt denselben Fehlschlag bei derselben Prompt-Groesse
-- die Einstellung hatte keinerlei Wirkung auf den tatsaechlichen
Prefill-Speicher-Wächter. Erst ein zweiter Dashboard-Versuch traf die
richtige Einstellung: `max_context_window` sprang auf 132000 (26B) bzw.
123000 (12B-Coder) -- aber auch das aenderte am realen Fenster nichts,
weil die eigentliche Grenze gar nicht durch diesen Wert, sondern durch
den echten Speicherplatz bestimmt wird.

**Der Kernel-Hebel**: oMLX' eigene Fehlermeldung nannte diesmal den
wahren Namen: *"Raise kernel iogpu.wired_limit_mb in Terminal (currently
caps Metal at 17.76 GB)"* -- ein macOS-Kernelparameter, der begrenzt,
wieviel GPU-Speicher ueberhaupt fest zugewiesen (gewired) werden darf.
Der Nutzer fuehrte direkt auf .191 aus:

```bash
sudo sysctl iogpu.wired_limit_mb=19456
```

(vorher testweise schon `=18432`, danach weiter auf `=19456` erhoeht).
Nach einem oMLX-Neustart uebernahm `/v1/models/status` den neuen Wert
exakt (`final_ceiling` sprang von 19069665280 auf 19327352832 Bytes --
byteweise passend zu 18432 MB). Der Effekt blieb trotzdem winzig (+240
MB) und wurde von etwas viel Groesserem ueberdeckt: identische
Bisektions-Anfragen derselben Groesse fielen im Sekundentakt zwischen
Erfolg und Fehlschlag um -- eine kleinere Anfrage scheiterte, wo eine
groessere Sekunden zuvor noch durchging.

**Was die Schwankungen NICHT erklaert**: naheliegend war der Verdacht,
oMLX und LM Studio haetten gleichzeitig dasselbe Modell im Speicher
gehalten und sich den Metal-Speicherpool streitig gemacht -- das haette
zu den wilden, nicht-monotonen Fehlschlaegen gepasst. Der Nutzer stellte
klar: beide liefen nie gleichzeitig, er hat zwischen den beiden Engines
umgeschaltet (jeweils die eine beendet, bevor die andere startete). Die
Ursache der Schwankungen WAEHREND der oMLX-Phase bleibt damit offen --
vermutlich eigene Admin-Dashboard-Aktionen (das Neuladen "mit einer
extra Einstellung fuer Context") liefen zeitlich parallel zu den
Bisektions-Anfragen aus diesem Gespraech, oder oMLX' eigene
Speicher-Schaetzung ist unter schnellen Wiederholanfragen selbst
ungenau. Festhalten laesst sich nur, was tatsaechlich gemessen wurde,
nicht die spekulative Erklaerung dafuer.

Klar und mehrfach reproduziert ist dagegen: sobald NUR LM Studio das
Modell hielt (oMLX-Modell explizit entladen), meldete es fuer
gemma-4-26b-a4b-it@mxfp4 ein `loaded_context_length` von 18688 -- und
anders als oMLX' theoretisches `max_context_window` ist dieser Wert bei
LM Studio bereits an anderer Stelle (Kapitel 33/`_loaded_ctx_tokens()`)
als vertrauenswuerdig bekannt. Eine echte Bisektion bestaetigte es
sauber und STABIL: 17833 Token OK, 18350 Token OK, 18867 Token FEHLER
-- derselbe Fehlertyp wie beim urspruenglichen 4352-Token-Fall, nur
diesmal bei einer mehr als vierfach groesseren Grenze, und ohne das
geringste Hin-und-Her bei Wiederholung.

Mit LM Studio als Halter des Modells (vibelove auf
`http://192.168.178.191:1234/v1` umgestellt) lief der
Pac-Man-Bauauftrag zu Ende: 7 Anfragen, 46924 Token in Summe (die
Kontext-Beschneidung von mc.py haelt jede EINZELNE Anfrage unter der
realen Grenze, auch wenn die Summe darueber liegt), sauberer
Git-Commit, `node --check` fehlerfrei. Das Spiel selbst bekam dabei noch
eine echte kleine Verbesserung spendiert: die Geister liefen anfangs
bei JEDEM Update-Schritt (alle 200ms), das Modell erkannte das selbst
als zu hektisch und bremste sie per Zaehler auf jeden zweiten Schritt.

Die Lehre: der gemeldete "Kontextfenster"-Wert einer lokalen Engine ist
im besten Fall eine Konfigurationsabsicht, kein Versprechen -- die reale
Grenze steckt im tatsaechlich freien GPU-Speicher in genau diesem
Moment, und muss per echter Bisektion gemessen werden, nicht aus einer
Status-Antwort abgelesen. Und: eine plausible Erklaerung fuer
beobachtete Flakiness ist nicht dasselbe wie eine bestaetigte -- die
Dual-Engine-Theorie klang stimmig, war aber schlicht falsch, bis der
Nutzer sie korrigiert hat.

## 54. po.py: ein Product Owner vor mc.py, weil der Coder keine Ideen haben soll

Die Frage kam beim Bauen, nicht davor: "gemma-4 macht immer nur genau
das was man sagt, es ist nie kreativ -- an was liegt das? Keine Ideen,
kein Wissen, oder was?" Statt zu raten, ein Blick in den tatsaechlichen
Request-Code (`_chat_once()`): mc.py setzt gar KEINE `temperature` --
nur einen milden `frequency_penalty` von 0.3 gegen Wiederholungsschleifen.
Der eigentliche Grund liegt also nicht in einem hart heruntergedrehten
Zufallsregler, sondern im System-Prompt selbst: der ist bewusst eng und
woertlich gehalten ("genau EIN action-Block pro Antwort",
`terse_task_hint()` sagt dem Modell explizit "leite die wahrscheinlichste
Absicht ab und beginne direkt" statt Alternativen zu erwaegen) --
eine Design-Entscheidung aus Kapitel 40: explizite Praezision macht
kleine Modelle zuverlaessig, und genau dieselbe Praezision unterdrueckt
jede Abschweifung. Dazu kommt die `-it`-Instruction-Tuning-Neigung, eng
zu befolgen statt auszuschmuecken. Kurz: dem Modell fehlen keine Ideen --
das Harness sagt ihm explizit und wiederholt, keine zu haben.

Die Konsequenz daraus war ein Architektur-Vorschlag: einen Product-Owner-
Schritt VOR mc.py einziehen, der genau das Gegenteil darf -- frei,
kreativ, ohne Aktions-Protokoll -- und einen knappen Wunsch in eine
ausformulierte, durchdachte Aufgabe verwandelt, bevor der Coder sie
woertlich umsetzt. Die Alternative waere gewesen, mc.py selbst
"kreativer" zu machen -- genau das haette die ganze Praezisionsarbeit
dieser Session (Kapitel 40, 52, die Kontext-Disziplin) wieder aufgeweicht.
Sauberer: Verantwortung trennen. po.py denkt sich Dinge aus, mc.py bleibt
klein, woertlich, zuverlaessig.

**po.py** ist bewusst kein zweiter mc.py: keine Datei-/Shell-Aktionen,
kein Fence-Protokoll -- nur EIN einziger, klar strukturierter
` ```decision ` -JSON-Block pro Antwort, mit genau zwei moeglichen Formen:
entweder GENAU EINE Rueckfrage (nur bei echter Mehrdeutigkeit, nie mehr
als eine im ganzen Dialog) oder eine fertige, kreative Aufgabenbeschreibung
fuer mc.py. `gather_project_context()` liefert dabei denselben Zweck wie
mc.py's eigene `task_hints()`: vorhandene Dateien und `MC-NOTIZEN.md`
werden mitgegeben, damit Vorschlaege am Bestehenden ansetzen statt generisch
ins Blaue zu gehen.

Zwei Anbindungen, bewusst getrennt von mc.py selbst:

1. **vibelove** bekommt `/refine` (haelt `PO_HISTORY` serverseitig, wird bei
   Projektwechsel und sobald ein Auftrag tatsaechlich an mc.py geht
   automatisch geleert) und eine eigene Bubble-Optik im Chat ("🧭 Product
   Owner", farblich abgesetzt vom Coder). Eine Rueckfrage wird angezeigt,
   die naechste Eingabe geht wieder an `/refine`; eine fertige Spezifikation
   zeigt Zusammenfassung + vollstaendigen Auftragstext mit zwei Knoepfen
   ("Jetzt bauen" -> direkt an `/build`, "Text anpassen" -> in das
   Eingabefeld zum Bearbeiten, geht danach wieder durch `/refine`).

2. **Eine eigenstaendige Kommandozeile** (`python3 po.py "wunsch" --dir
   projekt/`) fuer die Nutzung ganz ohne vibelove: derselbe Frage-/
   Abschluss-Dialog per `input()`, danach entweder `--no-run` (nur die
   fertige Aufgabe ausgeben) oder automatischer Aufruf von mc.py als
   Subprozess mit der fertigen Aufgabe. mc.py selbst wurde dafuer NICHT
   angefasst -- po.py haengt sich als eigenstaendiges Werkzeug davor, genau
   wie vibelove es auch nur importiert, nie mc.py's eigenen Code aendert.

Live erprobt, nicht nur gelesen: ein vager Wunsch ("mach das Tool besser")
loeste zuverlaessig GENAU EINE Rueckfrage aus (welcher Aspekt -- Design,
Analyse-Tiefe, Funktionen); die Antwort ("das Design soll moderner wirken")
ergab eine detaillierte, aus eigenem Antrieb ausgeschmueckte Spezifikation
(Farbpalette, Card-Layout, Lade-Animationen -- keins davon vorgegeben),
die dabei korrekt auf den echten bestehenden Backend-Port und die
tatsaechlichen API-Endpunkte des Projekts Bezug nahm. Ein konkreter Wunsch
("Statistikseite je Domain") ging direkt durch, ganz ohne Rueckfrage, und
brachte von sich aus eine Idee mit, die nie verlangt wurde: eine
Balkendarstellung nach Haeufigkeit.

## 55. po.py bekommt eine eigene Kommandozeile, faengt sich noch einen JSON-Bug -- und besteht den echten Praxistest

Drei Nacharbeiten an po.py, alle aus echtem Gebrauch entstanden, nicht
aus Theorie.

**Eine eigene Kommandozeile statt eines Kommandos in mc.py.** Die Frage
war: soll `/po` ein Slash-Kommando IN mc.py's eigener REPL werden? Die
bessere Antwort kam vom Nutzer selbst: nein, po.py bekommt eine eigene
CLI (`python3 po.py "wunsch" --dir projekt/`), mc.py bleibt komplett
unangetastet. Das passt zur mc.py-Philosophie dieser ganzen Session
(klein, stdlib-only, moeglichst wenig Angriffsflaeche fuer neue Bugs im
am staerksten gehaerteten Teil des Projekts) -- po.py haengt sich als
eigenstaendiges Werkzeug davor, importiert von vibelove, aufrufbar per
Terminal, und stoesst mc.py am Ende nur als ganz normalen Subprozess an.
`gather_project_context()` wanderte dabei aus vibelove/server.py in
po.py selbst, damit beide Nutzungswege dieselbe Logik teilen statt sie
zu duplizieren.

**Derselbe JSON-Escaping-Bug, den mc.py schon einmal hatte.** Live in
vibelove gemeldet: "Ungueltiges JSON in der Entscheidung." Die erste
Reproduktion gelang zufaellig NICHT (zweimal in Folge erfolgreich) --
ein Hinweis darauf, dass es sich um genau die Art von Fehler handelt,
die mc.py schon in Kapitel [Fence-Modus] geloest hat: ein kleines Modell
soll einen LANGEN, mehrzeiligen Fliesstext als JSON-String-Wert
einbetten (`"instruction": "..."`), und verhaspelt sich dabei
gelegentlich an Anfuehrungszeichen/Zeilenumbruechen -- nicht
deterministisch falsch, nur manchmal. po.py hatte genau dieses Muster
selbst eingebaut, obwohl die Lektion im selben Projekt schon bekannt
war. Fix: derselbe Fence-Ansatz wie bei mc.py -- der `decision`-JSON-Block
enthaelt nur noch `{"type": "..."}`, der eigentliche Freitext (Frage,
Zusammenfassung, Auftrag) steht in eigenen rohen ` ```question ` /
` ```summary ` / ` ```instruction ` -Bloecken. Verifiziert am tatsaechlich gescheiterten
Nutzer-Wunsch: die neue, korrekte Antwort enthaelt mehrere woertliche
Anfuehrungszeichen im Fliesstext, die im alten JSON-String-Format den
Parser zuverlaessig zerschossen haetten.

**Eine neue Regel: Dateien von Anfang an nach Zustaendigkeit trennen.**
Direkt aus den Bugs der letzten Kapitel abgeleitet -- die geloeschten
CSS-Regeln, die vergessene Ghost-Tempo-Drossel -- beides Faelle, in
denen ein grosser Datei-Umbau Inhalt ausserhalb des gerade bearbeiteten
Bereichs verloren hat. Neue System-Prompt-Regel: HTML/CSS/JS nie
vermischt, JS/Backend-Code nach Zustaendigkeit in eigene Dateien
(Routen/DB-Zugriff/Datenmodelle getrennt, ein React-Component pro
Datei), UND das JETZT beim Planen entscheiden statt erst wenn eine
Datei unhandlich geworden ist -- `plan_phase()`'s Prompt fragt das jetzt
explizit ab. Live getestet: eine kleine Notizen-App-Aufgabe ergab von
sich aus `app.py` + `database/{db_manager.py,schema.sql}` +
`routes/note_routes.py` + `templates/index.html` +
`static/{css/style.css,js/{api.js,ui.js}}` -- schon im Plan festgelegt,
bevor eine einzige Datei geschrieben wurde.

**Der eigentliche Praxistest: du -> po -> mc, mit einem echten Spiel.**
"Baue einen Tetris-Klon wie auf dem Game Boy" ergab bei po.py eine
detaillierte, unaufgefordert kreative Spezifikation (7 Tetrominoes,
Wall Kicks, Level-System, Game-Boy-Graupalette, vier unterschiedliche
Soundeffekte) -- und mc.py baute daraus in 15 Schritten ein
funktionierendes, sauber nach der neuen Regel aufgeteiltes Spiel
(`engine.js`/`renderer.js`/`input.js`/`audio.js`/`main.js`). Genauer
Code-Review deckte danach aber echte Luecken auf, die der eigene
Diff-Selbstreview NICHT gefunden hatte: eine tote, absichtlich als
fehlerhaft kommentierte `rotate()`-Methode neben der korrekten
`tryRotate()` (der Fehler wurde erkannt, aber nach vorne statt rueckwaerts
korrigiert), und eine Sound-Anforderung, die zwar im Auftrag stand aber
in der Umsetzung verwaesserte: kein eigener "Landing"-Sound, dafuer
spielte `playTick()` bei jedem einzelnen Fallschritt.

Der eigentliche Test folgte danach: eine praezise Bugliste (6 konkrete
Punkte) durch genau dieselbe Kette geschickt -- po.py uebersetzte sie
1:1 ohne Rueckfrage und ohne zusaetzlichen Umfang, mc.py setzte alle
sechs Punkte in 20 Schritten korrekt um, ohne eine einzige unbeteiligte
Datei anzufassen. Unabhaengig gegengeprueft (nicht nur die Abschluss-
meldung geglaubt): `git diff` bestaetigte alle sechs Aenderungen exakt,
`playTick()` blieb fuer seinen einen verbleibenden legitimen Einsatzzweck
(Rotation) unangetastet.

Die Lehre daraus: die Pipeline selbst funktioniert zuverlaessig in
beide Richtungen -- po.py dichtet weder dazu noch laesst es Vorgaben
fallen, egal ob es einen vagen Wunsch ausschmueckt oder eine praezise
Bugliste weiterreicht. Was NICHT zuverlaessig ist: ein einzelner langer
Bau-Durchlauf fuer eine grosse, vage Aufgabe -- der bleibt anfaellig
fuer genau die Art von Luecken, die die eigene Abschlusspruefung nicht
findet. Der bewaehrte Ablauf bleibt deshalb zweiphasig: grosser Wunsch
-> po elaboriert -> mc baut -> jemand liest den Diff wirklich ->
praezise Bugliste -> po reicht weiter -> mc fixt praezise. Die
Ueberpruefung in der Mitte ist keine optionale Zutat, sondern genau der
Schritt, der in dieser ganzen Session jeden echten Bug gefunden hat.

## 56. Kann mc.py sich selbst pruefen? Ein echter Test -- mit einem ueberraschenden Ergebnis

Die logische Weiterentwicklung von Kapitel 55: wenn ein grosser Bau-
Durchlauf unzuverlaessig ist, ein gezielter Bugfix-Durchlauf aber
zuverlaessig, waere dann nicht ein automatischer Verifikations-Schritt
ZWISCHEN Bau-Phasen der richtige Weg, um MVP-Stufen (erst Grundspiel,
dann Highscore, dann Multiplayer) sicher nacheinander zu bauen? Die
Idee: po.py formuliert produktseitige Akzeptanzkriterien statt
technischer Vorgaben, ein SEPARATER mc.py-Lauf bekommt genau diese
Kriterien und den Auftrag, sie am echten Code zu VERIFIZIEREN (nicht
nur zu lesen, sondern per `node`-Testskript wirklich auszufuehren) --
erst bei Erfolg geht es zur naechsten Stufe.

Ob das traegt, wurde nicht angenommen, sondern getestet: acht konkrete,
produktseitige Kriterien fuer den bereits gebauten Tetris-Klon (7
Tetromino-Typen, Zeilen-Loeschung, exakte Punktzahlen, der Landing-Sound
aus Kapitel 55, genau eine Rotationsmethode, Game-Over bei Spawn-
Kollision, Wall-Kicks an BEIDEN Raendern, Level-/Tempo-Skalierung), als
reiner Lese-/Test-Auftrag an mc.py -- mit der ausdruecklichen Anweisung,
Behauptungen wirklich auszufuehren statt nur zu lesen.

Das Ergebnis: mc.py schrieb tatsaechlich ein echtes Testskript, das
`engine.js` laedt und Szenarien simuliert -- keine reine Code-Lektuere.
Ausgabe: 5/8 PASS, 2/8 FAIL. Beide FAILs wurden unabhaengig nachgeprueft,
nicht einfach geglaubt:

- **Kriterium 6 (Game Over) -- ein ECHTER Bug, der sogar der eigenen
  manuellen Review aus Kapitel 55 entgangen war.** `spawnPiece()` ruft
  `collides(this.currentPiece, this.currentPiece.x, this.currentPiece.y)`
  auf -- aber `collides(piece, ox, oy)` erwartet OFFSETS (kleine Deltas
  wie 0/±1, so wie ueberall sonst im Code, z.B. in `move()`), keine
  absoluten Koordinaten. Das ergibt `2×piece.x + x` statt `piece.x + x`
  -- die Kollisionspruefung beim Spawnen ist dadurch verlaesslich falsch.
  mc.py's Verifikation erkannte korrekt, dass hier etwas nicht stimmt --
  diagnostizierte die Ursache aber falsch (schob es auf einen Fehler im
  eigenen Testskript statt auf den echten Bug in `spawnPiece()`). Waere
  das automatisch in einen Fix-Schritt gelaufen, waere vermutlich das
  Testskript "repariert" worden, nicht der eigentliche Bug.
- **Kriterium 7 (Wall Kicks beidseitig) -- vermutlich ein Fehlalarm.**
  Ein eigener, sauberer Test (I- und T-Stein ganz links und ganz rechts,
  unabhaengig vom Testskript der Verifikation) zeigte: die Rotation
  gelingt in beiden Faellen ohne Verschiebung -- weil sie an dieser
  Position gar keinen Wall-Kick BRAUCHT, nicht weil der Mechanismus
  kaputt ist. Die Verifikation sah "x blieb bei 0" und schloss daraus
  faelschlich auf einen Fehler.

Nebenbefund, nicht Kern der Sache: die eigene Auftragsformulierung
("kein engine.js/audio.js/input.js/...") nutzte '/' als Trennzeichen
zwischen Dateinamen -- mc.py's eigene `expected_files_from_task()`-
Erkennung las das als verschachtelten Pfad und blockierte `finish`
mehrere Runden lang. Kein mc.py-Bug im engeren Sinn, aber eine echte
Lektion: Auftragstexte, die eine AUSSCHLUSSLISTE von Dateinamen nennen,
sollten Kommas oder "und" nutzen, niemals '/'.

**Das ehrliche Fazit**: delegierte Verifikation liefert echtes Signal --
sie fand einen Bug, den nicht nur der Bau-Lauf, sondern auch die eigene
manuelle Review aus Kapitel 55 uebersehen hatte. Aber ihre DIAGNOSE ist
nicht vertrauenswuerdig genug, um blind einen Fix-Schritt daraus
abzuleiten: von zwei gemeldeten Fehlern war einer real (mit falscher
Ursachenzuschreibung), der andere vermutlich ein Fehlalarm durch
Fehlinterpretation von korrektem Verhalten. Fuer eine automatische
MVP-Stufen-Pipeline heisst das konkret: ein "FAIL" von mc.py's eigener
Verifikation darf nicht automatisch einen Fix-Auftrag ausloesen, ohne
dass jemand (Mensch oder ein weiterer, unabhaengiger Pruef-Schritt) den
gemeldeten Fehler erst nachvollzieht. Genau das musste in dieser Sitzung
noch von Hand passieren, um den echten Bug vom Fehlalarm zu trennen --
ein automatisierter "Richter"-Schritt waere der naechste logische Test,
aber noch nicht gebaut.

## 57. Die MVP-Stufen-Pipeline: was das Verifikations-Experiment fuer den Entwurf bedeutet

Nach Kapitel 56 blieb die naheliegende Anschlussfrage: heisst das
Ergebnis, dass die urspruenglich angedachte MVP-Stufen-Pipeline (Wunsch
-> po zerlegt in Stufen wie "Grundspiel", "+ Highscore", "+
Multiplayer" -> jede Stufe wird gebaut, verifiziert, erst dann geht es
weiter -- alles unbeaufsichtigt) verworfen werden muss? Nein, aber der
Entwurf muss die Messung ernst nehmen statt sie zu ignorieren.

Was Kapitel 56 tatsaechlich zeigt: das "irgendetwas stimmt hier nicht"-
Signal der Verifikation ist real -- sie fand einen Bug, den weder der
Bau-Lauf noch die eigene manuelle Review zuvor gefunden hatten. Ihr
"und zwar deswegen" ist es nicht -- falsche Ursachenzuschreibung beim
echten Fund, vermutlich ein Fehlalarm beim zweiten. Und: von den 6
gemeldeten PASS-Ergebnissen wurde KEINS unabhaengig nachgeprueft --
es gibt schlicht keine Evidenz, ob "PASS" zuverlaessiger ist als "FAIL"
mit falscher Begruendung. Ein stiller Fehl-PASS waere fuer eine
automatische Stufen-Pipeline das gefaehrlichere Versagen: eine kaputte
Stufe wuerde unbemerkt in die naechste durchgereicht.

Der angepasste Entwurf zieht daraus eine einzige, aber wichtige
Konsequenz: der Verifikations-Schritt bleibt drin, entscheidet aber
nicht mehr allein. Konkret vier Teile:

1. **Stufen-Zerlegung durch po.py** -- reine Planungsarbeit, kein
   Ausfuehrungsrisiko, bleibt wie angedacht.
2. **Sequentieller Bau je Stufe durch mc.py** -- mit vorherigen Stufen
   als Kontext, genau wie vibelove das heute schon zwischen einzelnen
   Bauauftraegen macht (BUILD_HISTORY), nur automatisch statt auf
   erneute Nutzereingabe wartend.
3. **Die Verifikation laeuft weiter automatisch nach jeder Stufe** --
   sie liefert echtes Signal und soll nicht wegfallen, nur weil sie
   nicht perfekt ist.
4. **Aber: das Ergebnis wird angezeigt statt automatisch zu entscheiden.**
   Dasselbe Muster, das diese ganze Session ueber tatsaechlich
   funktioniert hat -- Plan vor dem Bauen zeigen, Bugliste vor dem
   Fix-Auftrag zeigen -- wandert an die naechste Risikostelle: das
   Verifikationsergebnis vor dem naechsten Schritt (Fix-Schleife ODER
   naechste Stufe) zeigen, nicht stillschweigend danach handeln.

Die Automatisierung bleibt damit real (Stufen werden vorgeschlagen, der
Reihe nach gebaut, automatisch geprueft) -- nur der EINE Schritt, fuer
den in Kapitel 56 direkt belegt wurde, dass er nicht unbeaufsichtigt
laufen darf, bleibt ein Kontrollpunkt statt eine Blackbox-Entscheidung.
Noch nicht gebaut, aber jetzt mit einem Entwurf, der auf einer echten
Messung beruht statt auf der Annahme, dass ein zweiter LLM-Durchlauf
automatisch vertrauenswuerdiger ist als der erste.

## 58. po.py baut jetzt Ketten -- und eine Luecke in der eigenen Git-Absicherung, zweimal gefunden

Aus Kapitel 57 direkt weitergebaut: po.py kann jetzt statt eines
einzelnen Auftrags auch einen mehrteiligen PLAN erzeugen. Jeder Schritt
bekommt einen EIGENEN, frischen mc.py-Lauf statt eines gemeinsamen,
wachsenden Kontexts ueber mehrere Schritte -- genau das Problem, das
diese Session wiederholt zeigte: Details aus der Mitte einer langen
Aufgabe gehen verloren. Der Plan wird als `mc_plan.md` abgelegt, im
GLEICHEN Format wie mc.py's eigene interne Plan-Datei, damit auch ein
einzelner, nicht ueber die Kette laufender mc.py-Aufruf einen offenen
Plan ueber seine eigene Logik erkennt.

Der Weg dahin ging durch drei echte, live gefundene Bugs, nicht durch
Theorie:

1. **Crash bei leerem Schritt-Block.** Ein `IndexError`, wenn ein
   Schritt-Text leer war -- durch eine Validierung ersetzt, die einen
   klaren, wiederholbaren Fehler statt eines Absturzes liefert.
2. **Derselbe Schritt doppelt, einmal leer.** Manche Modelle emittieren
   einen leeren `step-N`-Block und direkt danach denselben `step-N`
   nochmal mit echtem Inhalt -- eine Selbstkorrektur mitten in der
   Antwort. `findall()` behielt beide Treffer als separate Eintraege.
   Fix: bei doppelter Nummer gewinnt der LETZTE nicht-leere Treffer.
3. **Nicht-deterministisches Format-Versagen.** Derselbe Fehler trat bei
   verschiedenen Zufalls-Generierungen unterschiedlich auf -- die
   richtige Antwort war nicht, jedes moegliche Muster einzeln
   abzufangen, sondern zu wiederholen (genau wie mc.py es fuer seine
   eigenen Chat-Aufrufe laengst tut). Eine `retryable`-Markierung an
   Protokollfehlern plus ein 3-Versuche-Wrapper loesten es.

Der vierte Fund war der interessanteste: ein voller Testlauf (Tetris,
zwei Schritte: Highscore-Speicher + Einstellungsdialog) lief fachlich
einwandfrei durch, beide Punkte wurden in `mc_plan.md` korrekt
abgehakt -- aber `git status` zeigte danach ALLES als unkommittet.
Ursache: der Arbeitsbaum war schon zu Beginn der Kette dreckig (Rest
aus einem frueheren Test), und mc.py's eigene Git-Absicherung verlangt
einen SAUBEREN Baum bei JEDEM Lauf -- fand sie stattdessen offene
Aenderungen, blieb sie fuer den GESAMTEN Lauf still deaktiviert, obwohl
dieser selbst sauber durchlief. Exakt dasselbe Problem, das vibelove
schon lange per `stelle_sauberen_arbeitsbaum_sicher()` loest -- diese
Funktion wurde 1:1 nach po.py portiert, als `ensure_clean_worktree()`.

Erster Fix: nur am Anfang der Kette aufgerufen. Beim naechsten Live-Test
(diesmal Pause + Restart fuer Tetris, ein einzelner, nicht in Stufen
zerlegter Auftrag) zeigte sich: po.py entschied sich fuer den
Einzel-Auftrag-Pfad (`spec`), nicht fuer `plan` -- und dieser Pfad rief
`ensure_clean_worktree()` nie auf. Der Fix deckte nur den selteneren
Fall ab, nicht den haeufigeren. Die richtige Loesung: die Absicherung
in `_run_mc()` selbst verlegen, den EINEN gemeinsamen Punkt, durch den
sowohl der Einzel-Auftrag als auch jeder Kettenschritt laufen -- statt
sie an jeder Aufrufstelle einzeln zu wiederholen.

Verifiziert nicht durch Lesen, sondern durch einen echten Stresstest:
fuenf aufeinanderfolgende Laeufe gegen ein echtes Projekt (kleine,
harmlose Aenderungen an einer Testdatei), jeder gegen einen Endpoint
ueber Umgebungsvariablen erreicht. Alle fuenf endeten mit "Git
verfuegbar" statt der fruehen Fehlermeldung, alle fuenf committeten
sauber. Erst danach als stabil betrachtet.

## 59. Drei Spiele, ein Endpoint-Wechsel, drei echte Bugs: vibelove komplett durchgespielt

Der bisher groesste Praxistest der ganzen Kette: du -> po.py -> mc.py,
diesmal nicht per Kommandozeile, sondern durch vibelove selbst -- neues
Projekt anlegen, `/refine` fuer den Produktdialog, `/build` fuer den
eigentlichen Bau, alles gegen einen oeffentlichen Cloud-Endpoint
(OpenRouter, Modell `openai/gpt-5.6-terra-pro`) statt der lokalen
Modelle dieser Session. Basis-URL, Modellname und API-Key wanderten
dabei NUR in `vibelove/mc_settings.json` -- eine Datei, die von Anfang
an in `.gitignore` steht und nie in ein Repository gelangt.

Drei Spiele in Folge, jedes ein eigenes Projekt:

- **"Flatterflug"** (Flappy Bird): 17 Anfragen, $0.44. po.py lieferte
  eine detaillierte, unaufgefordert durchdachte Spezifikation (Zustaende,
  Kollisionslogik, Barrierefreiheit), mc.py baute sie in einem Rutsch
  und verifizierte sich selbst per Node-VM-Sandbox mit simuliertem
  DOM/Canvas (kein echter Browser installiert). Zwei Nachbesserungen
  live nachgereicht -- Soundeffekte (13 Anfragen, $0.25) und ein zu
  enger Rohrabstand (9 Anfragen, $0.15) --, jede als eigener, praezise
  formulierter `/refine`-Durchlauf.
- **"Wiesen-Sprung"** (Crossy-Road-Prinzip, bewusst umbenannt und ohne
  Original-Grafiken/-Markenmaterial): 20 Anfragen, $0.70. Diesmal stand
  tatsaechlich Playwright zur Verfuegung -- mc.py testete sich selbst
  per echtem Headless-Chromium: Eingaben, Punktestand, Ton-Stummschaltung
  samt `localStorage`-Persistenz nach Reload, Game-Over-Overlay,
  Neustart. Kein simuliertes DOM diesmal, ein echter Browser.
- **"Neon Breakout"**: Basisspiel 10 Anfragen, $0.40 -- aber OHNE
  Commit (dazu gleich mehr). Die Nachbesserung um Spezialsteine
  (Power-Ups fuer breiteres Paddle, Zeitlupe, Multiball) brauchte zwei
  Anlaeufe: der erste haengte nach einer fehlgeschlagenen Pruefung
  regungslos fest, bis vibelove's eigenes 900-Sekunden-Timeout den
  Prozess hart beendete.

Aus den drei Laeufen kamen drei echte, unabhaengig verifizierte mc.py-
und vibelove-Bugs, keiner davon geraten:

**1. Die Vorschau blieb fuer statische Spiele leer.** vibelove
entscheidet "Vite starten oder Static-Server?" bisher allein an der
BLOSSEN EXISTENZ eines `package.json` -- fand es eins, versuchte es
blind `npm run dev`. Aber mc.py legt fuer reine Canvas-Spiele OHNE
Build-Tool selbst ein `package.json` mit eigenen Skripten (`start`,
`build`) an, ohne `dev`-Skript. Ergebnis: `npm run dev` schlug fehl,
die Vorschau blieb leer, ohne jede Fehlermeldung. Fix: die Pruefung
schaut jetzt tatsaechlich ins `scripts`-Feld statt nur auf die
Dateiexistenz -- live an "Flatterflug" verifiziert (Vorschau lief
danach sofort).

**2. Ein sauberer Bau-Lauf, aber kein Commit.** Der Breakout-Basis-Lauf
endete erfolgreich, alle Dateien korrekt auf der Platte -- aber
`git status` zeigte sie als unversioniert, mc.py meldete "keine
Aenderungen". Ursache: `git_commit_run()` warf den Rueckgabewert von
`git add` einfach weg. Schlug `add` aus irgendeinem Grund fehl (die
genaue Ursache blieb offen, vermutlich eine kurze Lock-Kontention),
sah der folgende `git commit` nichts Gestagtes und meldete irrefuehrend
"nichts zu committen" -- der eigentliche Fehler wurde nie sichtbar.
Fix: der Rueckgabewert von `add` wird jetzt geprueft, ein Fehlschlag
dort bricht mit der ECHTEN Fehlermeldung ab, statt sich hinter einem
harmlos klingenden "keine Aenderungen" zu verstecken.

**3. Ein 800-Sekunden-Stillstand ohne jede Meldung.** Der haengende
Power-Up-Lauf war kein Zufall: manche Endpoints (OpenRouter gehoert
dazu) schicken bei langen Anfragen periodische SSE-Keep-Alive-
Kommentarzeilen, extra damit der Client NICHT wegen Inaktivitaet
abbricht. Genau das setzte mc.py's Socket-Timeout (300s) bei JEDER
Kommentarzeile zurueck -- auch wenn die eigentliche Generierung laengst
haengt. Ohne vibelove's eigenen 900-Sekunden-Watchdog waere der Lauf
schlicht ewig still stehen geblieben. Fix: ein eigener, unabhaengig
von Keep-Alives gemessener Stillstands-Timeout (150s, nur echte
SSE-Datenereignisse zaehlen als Fortschritt) -- nutzt danach dieselbe
Retry-Logik, die fuer Netzwerkfehler laengst existiert.

Alle drei Fixes liefen anschliessend gegen die bestehende Testsuite
(185 Tests bleiben gruen) und wurden live nachgewiesen, nicht nur
gelesen: die Vorschau tatsaechlich neu geladen, ein manueller Commit
der verlorenen Breakout-Dateien, ein weiterer erfolgreicher Bau-Lauf
nach dem Timeout-Fix.

## 60. requirements.txt als Pflicht, dann der Container -- echt gebaut, echt gestartet

Eine Nutzer-Idee direkt aus dem Spiele-Marathon: wenn vibelove schon
ein ZIP generiert, koennte es nicht auch ein Container-Setup
generieren? Zwei Teile, bewusst in dieser Reihenfolge umgesetzt.

**Erstens die Grundlage.** mc.py las `requirements.txt` schon immer
beim Start eines Laufs, um Abhaengigkeiten zu installieren -- aber der
System-Prompt verlangte nirgends, dass die Datei bei einem neuen
externen Python-Import auch tatsaechlich angelegt/aktualisiert wird.
Neue Regel: bei jedem Import ausserhalb der Standardbibliothek wird
`requirements.txt` gepflegt, die Version per `pip show` NACHGESCHAUT
statt geraten. Ohne das waere jedes generierte Python-Backend
ausserhalb von mc.py's eigenem, schon vorbereitetem Environment schlicht
nicht lauffaehig gewesen.

**Zweitens der Container selbst.** Ein neuer Knopf in vibelove
(🐳) plus `POST /generate-container`: erkennt die Projekt-Form nach
GENAU demselben Muster wie die Live-Vorschau selbst (dieselben
Funktionen wie `start_vite_server()` nutzt) -- Container und lokale
Vorschau sollen nie auseinanderlaufen. Drei Faelle: reines
Static-Frontend -> ein `Dockerfile` mit nginx. Vite-Frontend -> ein
Mehrstufen-Build (node baut, nginx liefert aus). Frontend UND Backend
zusammen -> `docker-compose.yml` mit GETRENNTEN Containern statt
einem gemeinsamen (ein Container fuer zwei Prozesse waere ohne eigenes
Init-System fragil -- und `static_preview_server.py` macht die
Trennung beim lokalen Testen laengst per Proxy genauso vor). Die
generierte `nginx.conf` spiegelt exakt dessen `/api/`-Proxy-Konvention.

Nicht nur gelesen, sondern echt gebaut und gestartet: Docker Desktop
extra dafuer hochgefahren, dann `docker build` + `docker run` fuer den
Static-Fall (das echte "Flatterflug"-Projekt, HTTP 200, `game.js`
korrekt ausgeliefert), dann `docker compose up --build` fuer den
kombinierten Fall gegen ein eigens angelegtes Test-Projekt mit
echtem Flask-Backend. Der Frontend-Container erreichte den
Backend-Container tatsaechlich ueber den nginx-Proxy per HTTP --
`curl http://localhost:8080/api/hello` durch den Container hindurch
lieferte die echte Flask-Antwort. Beide Test-Container und -Images
danach wieder entfernt, nur das Ergebnis (die generierten Dateien in
den echten Projekten) blieb.

## 61. Ein Projektname kollidiert, und /refine bekommt seine eigene Haertung nachgereicht

Ein viertes Spiel ("Neon Invaders", Space-Invaders-Prinzip) sollte als
neues Projekt entstehen -- und legte sofort zwei echte, unabhaengige
Luecken offen.

**Erstens: `/projects` (POST) legt ein Projekt NIE wirklich neu an.**
`os.makedirs(..., exist_ok=True)` schluckt eine bereits bestehende
gleichnamige Verzeichnisstruktur stillschweigend -- es gab bereits ein
"spaceinvaders" aus einer fruehreren Session mit echter Historie (ein
Basis-Build UND ein spaeterer Bugfix-Commit). po.py's eigener
Projekt-Steckbrief las diese bestehenden Dateien korrekt und
formulierte den Auftrag entsprechend als "erweitere das Bestehende" um
-- fachlich richtig, aber nicht das, was hier gewollt war. Kein Datenverlust
(git init auf einem bestehenden Repo ist ein sicherer No-Op), aber eine
echte Ueberraschung. Geloest durch einen simplen Namenswechsel
("spaceinvaders2") statt eines Codefixes -- der Nutzer entscheidet hier
bewusst per Nachfrage, nicht automatisch.

**Zweitens, wichtiger: `/refine` nutzte die eigene Haertung von po.py
nicht.** Ein Format-Aussetzer des Modells (decision "spec" ohne
zugehoerigen summary-Block) liess die Web-Route hart fehlschlagen --
obwohl genau dieser Fall laengst durch `refine_retrying()` (automatische
Wiederholung bei als "retryable" markierten Protokollfehlern, Kapitel
58) abgefangen wird. Der Grund: `/refine` rief bislang direkt `po.refine()`
auf, nicht den gehaerteten Wrapper -- nur der CLI-Pfad (`po.py`'s eigene
`_main()`) profitierte von der Haertung. Fix: eine Zeile, `refine()` durch
`refine_retrying()` ersetzt. Live verifiziert: derselbe Wunsch, erneut
gesendet, ergab sofort eine saubere Spezifikation.

Das fertige Spiel selbst: 47 Anfragen, $1,44 -- spuerbar teurer als die
vorherigen drei (Bunker-Abnutzung, Wellen-Skalierung, Alien-KI-Feuer sind
mehr Zustand als ein einzelnes Canvas-Spiel ohne solche Wechselwirkungen).
Live gespielt und verifiziert: Punktestand stieg korrekt bei Treffern,
Formation und Bunker rendern wie spezifiziert, das Ruecksende-Feuer der
Aliens ist sichtbar.

## 62. SEO Insight: ein Praxis-Realitaetscheck fuer "mc.py macht das schon" -- drei weitere echte Bugs

Der bisher ambitionierteste Auftrag: "ein deutlich besseres SEO-Tool,
das du selbst gestalten darfst" plus feste Anforderungen (Flask+SQLite-
Verlauf, Diagramme, alte Ergebnisse laden/loeschen). po.py's Antwort war
kein Minimal-Formular, sondern eine durchdachte Spezifikation inklusive
SSRF-Schutz, deterministischer Score-Berechnung, Zeitvergleich derselben
URL ueber mehrere Analysen und gemockten Tests (keine echten Netzwerk-/
LLM-Aufrufe in der Testsuite) -- ohne dass ich das im Detail vorgeben
musste.

Der Bau selbst (40 Anfragen, $1,38 fuer die grosse Version) war beeindruckend
sorgfaeltig: SSRF-Pruefung inklusive Redirect-Ketten, echte Browser-Tests
via Playwright wo verfuegbar. Aber "beobachte bitte ob mc.py auch alles
optimal macht" (explizit gebeten, nicht nur zu vertrauen) foerderte drei
echte, unabhaengig verifizierte Luecken zutage -- keine davon geraten:

**1. Ein DNS-Rebinding-Loch in der eigenen SSRF-Absicherung.**
`validate_public_url()` loeste den Hostnamen EINMAL auf, um die IP zu
pruefen -- aber `requests.get()` loest denselben Hostnamen beim
tatsaechlichen Verbindungsaufbau ERNEUT auf. Ein Angreifer mit Kontrolle
ueber die DNS-Antwort (kurze TTL) koennte beim Check eine oeffentliche IP
liefern und Sekunden spaeter beim echten Connect eine private -- eine
bekannte, reale SSRF-Umgehungstechnik. Fix (durch mc.py selbst, nach
praeziser Auftragsbeschreibung): ein eigener `HTTPAdapter`/Connection-Pool,
der die bereits gepruefte, NUMERISCHE IP direkt anwaehlt (kein Hostname,
keine erneute Aufloesung moeglich), TLS-SNI aber weiterhin gegen den
echten Hostnamen validiert. Jede Weiterleitung wird einzeln neu geprueft
und gepinnt. Ein Test prueft das explizit: `socket.getaddrinfo` darf beim
eigentlichen Verbindungsaufbau NIE aufgerufen werden (`pytest.fail`, falls
doch) -- verifiziert, nicht nur gelesen.

**2. Eine Luecke, die zwei Seiten nie voneinander wussten.** mc.py kannte
vibelove's Backend-Konvention (`backend/vibelove-backend.json`, fester
Port 5001) UEBERHAUPT NICHT -- nur vibelove's eigene Erkennung setzte sie
voraus. Ein generiertes Flask-Backend waehlte deshalb einen eigenen Port
und legte kein Manifest an. Nur EIN frueheres Projekt (von Hand
nachtraeglich ergaenzt) erfuellte die Konvention zufaellig. Fix: die
`/build`-Zusatzanweisung (dieselbe Stelle, die schon die frontend/-
Konvention lehrt) beschreibt jetzt auch diese. Aber selbst MIT Manifest
haette die Vorschau eine monolithische Anwendung (Flask mit
serverseitig gerenderten Templates, KEIN separates Frontend) nicht
angezeigt -- `start_vite_server()` kannte nur "Static-Frontend" und
"Frontend + separates Backend", nicht "nur Backend, das die gesamte App
ist". Ein neuer Fall startet den Vorschau-Server mit LEEREM static_dir --
in diesem Modus leitet er JEDE Anfrage ans Backend weiter statt nur
welche unter `/api/`. Live mit einer Test-Fixture verifiziert (eigener
Flask-Mini-Server auf Port 5001): die Vorschau zeigte den echten
Backend-Inhalt.

**3. Ein "erfolgreicher" Lauf, der zwei Drittel seiner eigenen Aenderungen
verlor.** Eine Backend-Migration (alle Dateien nach `backend/`
verschoben) nutzte dafuer ein simples Shell-`mv`-Kommando. mc.py's
`git_commit_run()` staged aber nur Pfade aus `TOUCHED` -- einer Liste, die
AUSSCHLIESSLICH von `write_file`/`edit_file`-Aktionen befuellt wird.
Dateioperationen ueber die `run`-Aktion (mv, cp, rm), die das Modell
genauso legitim nutzen darf, tauchen dort nie auf. Ergebnis: von 19
tatsaechlich veraenderten Dateien wurden nur 6 committet, der Rest blieb
unversioniert liegen -- ein "erfolgreicher, sauberer Lauf" mit
stillschweigend halb verlorener Arbeit. Fix: `git_commit_run()`/
`git_rollback()` operieren jetzt auf dem GESAMTEN Arbeitsbaum (`git add
-A` bzw. `git reset --hard` + `git clean -fd`) statt auf der kuratierten
Liste -- sicher, weil `git_usable()` einen sauberen Baum beim Start jedes
Laufs ohnehin erzwingt, jede Abweichung von HEAD also zwangslaeufig aus
GENAU diesem Lauf stammt. Direkt gegen den realen, unvollstaendig
committeten Zustand verifiziert: alle 19 fehlenden Aenderungen wurden
beim erneuten Aufruf korrekt erfasst.

Ein vierter, kleinerer Fund kam noch dazu, live beim ersten Aufruf im
Browser: `/static/css/app.css` und `/static/js/app.js` lieferten 404
durch die Vorschau, obwohl derselbe Pfad direkt am Backend (Port 5001)
200 lieferte. Ursache: Flask registriert per Default automatisch eine
EIGENE `/static/`-Route (auf ein Verzeichnis, das neben dem
Vorschau-Skript gar nicht existiert) -- die fing die Anfrage ab, bevor
sie den eigentlichen Proxy-Code je erreichte. `static_folder=None` bei
der Flask-Instanziierung schaltet das ab.

## 63. WordPress-Schreiber: Guthabenlimit, Modell-Wechsel, ein haengender Lauf -- und zwei echte mc.py-Verbesserungen daraus

Der aufwendigste Einzelauftrag dieser Session: eine URL eingeben, einen
Artikel per LLM deutlich kuerzer umschreiben lassen, das Ergebnis per
SMTP an eine feste Adresse senden -- oder per Freitext iterativ
ueberarbeiten. Alle Zugangsdaten (LLM-Endpoint, SMTP-Server, Passwort)
ausschliesslich ueber Umgebungsvariablen, nichts hartkodiert, nichts in
`.env.example` als echter Wert. Die Spezifikation von po.py war wieder
bemerkenswert vollstaendig: SSRF-Schutz fuer den Artikelabruf, atomare
Versand-Reservierung gegen Doppel-Klicks, Quellenangabe im Ergebnis statt
reinem Kopieren.

**Der Bau war eine Odyssee, nicht ein einzelner Lauf:**

1. Der erste Versuch (OpenRouter, das teurere Modell dieser Session)
   brach nach 34 Schritten mit HTTP 402 ab -- das Konto-/Schluessel-Limit
   war erreicht, nicht das Guthaben insgesamt leer. Der bereits
   geschriebene Code (Artikel-Service, LLM-Anbindung, E-Mail-Versand,
   SQLite-Sitzungsspeicher) wurde als klar benannter Zwischenstand
   gesichert, nicht verworfen.
2. Ein Modellwechsel auf ein guenstigeres Modell scheiterte mit HTTP 401
   ("User not found") -- dieses Modell war auf dem Konto/Schluessel
   schlicht nicht verfuegbar.
3. Fallback auf den lokalen Endpoint dieser Session, ausschliesslich per
   Umgebungsvariablen (nie in vibelove's Einstellungsdatei geschrieben,
   wie in den fruehen Kapiteln dieser Session festgelegt) -- direkter
   `mc.py`-CLI-Aufruf statt ueber vibelove's `/build`, weil genau diese
   Route den Endpoint als sichtbares CLI-Argument weitergereicht haette.
   Dieser Lauf wurde fertig: 34 Anfragen, Live-Test per curl erfolgreich.

**Aber "fertig" hielt einer echten Pruefung nicht stand.** Zwei Luecken,
keine geraten:

- **Ein echter Startfehler.** Die Backend-Dateien importierten sich
  gegenseitig mit einem "backend."-Praefix (`from backend.config import
  Settings`) -- aber das Manifest startet die App per `python3 app.py`
  MIT `backend/` als Arbeitsverzeichnis, nicht dem Wurzelverzeichnis. Kein
  Paket "backend" zu importieren, sofortiger Absturz. Reproduziert mit
  genau dem Befehl aus dem Manifest, nicht nur per Syntaxpruefung.
- **Deklarierte, aber nie geschriebene Tests.** `requirements.txt` listete
  bereits `pytest`, aber es existierte keine einzige Testdatei -- die
  urspruengliche Anforderung (SSRF-Tests, Konfigurationsfehler, gemockter
  Erfolgsablauf, Reset, Fehlerfaelle) war schlicht nicht umgesetzt worden.

**Der erste Reparaturversuch blieb haengen.** Das kleinere lokale Modell
wiederholte beim Schreiben der Tests denselben, leicht fehlerhaften
`old`-Text (eine fehlende schliessende Klammer) ueber zehn Schritte in
Folge -- obwohl mc.py's `_closest_snippet()`-Mechanismus (aus einer
FRUEHEREN Session bereits gegen genau dieses Problem gebaut) bei JEDEM
Fehlschlag den korrekten Dateitext zum Kopieren mitschickte. Das blosse
ANZEIGEN des richtigen Texts reichte nicht aus, um die Schleife zu
durchbrechen -- der Lauf musste manuell abgebrochen werden.

Daraus zwei echte, getestete Verbesserungen an mc.py selbst:

1. **Eskalation statt endloser Wiederholung.** Ein neuer Zaehler
   (`EDIT_FAIL_STREAK`) verfolgt aufeinanderfolgende "nicht gefunden"-
   Fehlschlaege pro Datei. Ab dem dritten Fehlschlag IN FOLGE auf
   derselben Datei bekommt die Fehlermeldung eine harte Zusatzanweisung:
   nicht weiter raten, sondern per `write_file` die Datei komplett neu
   schreiben. Die Serie wird zurueckgesetzt, sobald `old` fuer diese
   Datei wieder gefunden wird.
2. **Ein immer aktives Klartext-Protokoll.** Bisher gab es nur
   `mc_verlauf.json` (JSON, nur bei `--resume` aktiv) -- ohne manuelles
   Umleiten von stdout blieb nach einem Lauf keine lesbare Aufzeichnung
   uebrig; genau das war noetig, um die Schleife oben ueberhaupt zu
   diagnostizieren. Ein neuer `_Tee`-Mechanismus spiegelt stdout/stderr
   jetzt IMMER (ANSI-Farbcodes entfernt) in `mc_run.log` im Projekt --
   die `.log`-Endung bewusst gewaehlt, damit die in praktisch jedem
   Projekt schon vorhandene `*.log`-Regel sie automatisch erfasst; fuer
   den selteneren Fall eines Projekts ohne diese Regel ergaenzt ein
   kleiner Helfer sie automatisch im `.gitignore`.

Der zweite Reparaturversuch (mit beiden Verbesserungen aktiv) lief sauber
durch: 25 Anfragen, Tests geschrieben UND ausgefuehrt (exit 0), Live-Start
per curl verifiziert -- keine Eskalation noetig, aber die Absicherung war
da. Der einzige verbleibende Stolperstein war meiner: der direkte
CLI-Aufruf (fuer den lokalen Endpoint) hat, anders als vibelove's
`/build`, keinen automatischen Sicherungs-Commit vor dem Start -- der
Arbeitsbaum war durch den vorherigen abgebrochenen Versuch noch dreckig,
`git_usable()` erkannte das korrekt und deaktivierte die Git-Absicherung
fuer den GANZEN Lauf. Manuell nachgeholt, nachdem der Code selbst (8 echte
Tests, unabhaengig in einer frischen virtuellen Umgebung nachgepruefft)
verifiziert war.

**Der echte Endtest:** ein Artikel-URL eingegeben, eine echte, sachlich
korrekte Zusammenfassung mit Quellenangabe erhalten, bestaetigt -- und
tatsaechlich per SMTP verschickt. Der erste Versuch schlug fehl (`535
authentication failed`), aber nicht wegen eines App-Bugs: eine
Bash-`source`-Anweisung interpretierte das `$` im SMTP-Passwort als
(nicht existierende) Shell-Variable und schnitt es dadurch ab -- ein
eigener Fehler beim manuellen Starten der App, nicht in ihrem Code. Nach
korrektem, per Python statt Bash geladenem Environment lief der komplette
Ablauf durch: "Die Zusammenfassung wurde erfolgreich per E-Mail
versendet." Nebenbefund, noch offen: `requirements.txt` listet
`python-dotenv`, aber `app.py` ruft `load_dotenv()` nie auf -- die App
liest `.env` also nur, wenn irgendetwas ausserhalb sie explizit
einspeist, nicht von selbst.

## Gesamttabelle: alle 24 Modelle im CRUD-Benchmark

Alle Läufe der Kapitel 17–28, sortiert nach Ausgang und Lauf-Kosten.
Achtung: Die Läufe verteilen sich auf mehrere Harness-Stände —
Feinvergleiche mit Vorsicht.

| Modell | Preis/Mio (P/C) | Dauer | Requests | Lauf-Kosten | Abnahme | Ausgang |
|---|---|---|---|---|---|---|
| nemotron-3-ultra (free) | gratis | 269 s | 30 | **$0.00** | 8/8 · 2/3 | ✓ sauber |
| **laguna-s-2.1** | $0.09/$0.18 | 57 s | 13 | **$0.003** 👑 | 8/8 · 3/3 | ✓ sauber |
| deepseek-v4-flash ¹ | $0.09/$0.28 | n. gem. | 18 | $0.007 | 8/8 · 1/3 | ✓ sauber |
| deepseek-v4-flash-0731 | $0.09/$0.18 | 192 s | 20 | $0.008 | 8/8 · 3/3 | ✓ sauber |
| gpt-5.6-luna | $0.10/$0.60 | n. gem. | 15 | $0.014 | 8/8 · 3/3 | ✓ sauber |
| tencent/hy3 | $0.13/$0.53 | 877 s | 15 | $0.025 | 8/8 · 3/3 | ✓ sauber |
| gemma-4-31b-it | $0.10/$0.34 | 604 s | 22 | $0.026 | 8/8 · 3/3 | ✓ sauber |
| deepseek-v4-pro | $0.43/$0.87 | 167 s | 19 | $0.028 | 8/8 · 3/3 ² | ✓ sauber |
| gpt-5.6-terra | $1.00/$6.00 | **52 s** | **6** | $0.054 | 8/8 · 3/3 | ✓ sauber |
| kimi-k2.7-code | $0.73/$3.50 | 111 s | 16 | $0.084 | 8/8 · 3/3 | ✓ sauber |
| grok-4.5 | $2.00/$6.00 | 130 s | 12 | $0.130 | 7/8 · 3/3 ³ | ✓ sauber |
| gemini-3.6-flash | $1.50/$7.50 | 119 s | 18 | $0.297 | 8/8 · 3/3 | ✓ sauber |
| claude-sonnet-5 | $2.00/$10.00 | 128 s | 16 | $0.333 | 8/8 · 3/3 | ✓ sauber |
| gpt-5.6-sol | $5.00/$30.00 | 106 s | 11 | $0.541 | 7/8 · 3/3 ³ | ✓ sauber |
| claude-opus-4.7 | $5.00/$25.00 | 145 s | 16 | $0.926 | 7/8 · 3/3 ³ | ✓ sauber |
| claude-opus-5 | $5.00/$25.00 | 484 s | 34 | $2.160 | 8/8 · 3/3 | ✓ sauber |
| xiaomi/mimo-v2.5 ⁴ | $0.14/$0.28 | 1724 s | 58 | $0.095 | 8/8 · 3/3 | ✗ Schrittlimit |
| kimi-k3 | $3.00/$15.00 | 433 s | 41 | $0.909 | 8/8 · 3/3 | ✗ Limit, nie ein finish |
| z-ai/glm-5.2 | $0.28/$0.89 | 1200 s ⏱ | ~12 | unbek. | 8/8 · 3/3 | ✗ Timeout (App fertig!) |
| qwen3.7-flash | $0.03/$0.13 | 641 s | n. erf. | n. erf. | 8/8 · 1/3, PUT→500 | ✗ hängender Server |
| kat-coder-air-v2.5 | $0.15/$0.60 | 287 s | 42 | $0.032 | **App ungueltig** | ✗ kaputte app.py |
| gemma-4-26b-a4b-it (Cloud) | $0.07/$0.34 | 1200 s ⏱ | — | $0.031 | **keine App** | ✗ Escape-Degeneration |
| minimax-m3 | $0.30/$1.20 | 494 s | 42 | $0.109 | **keine App** | ✗ stilles Prosa-Ende ⁵ |
| ling-2.6-flash | $0.01/$0.03 | 1 s | 0 | $0.00 | — | ✗ Anbieter-Rate-Limit ⁶ |

¹ einziger Lauf vor der Prompt-Schärfung (daher Valid 1/3, PUT partiell).
² POST verlangt alle Felder. ³ Strenge-Schule: PUT auf unbekannte ID →
400 statt 404, POST verlangt alle Felder. ⁴ Nachtest nach dem
Harness-Crash-Fix. ⁵ deckte die Prosa-Wächter-Lücke auf, inzwischen
gefixt. ⁶ HTTP 429 upstream — Verfügbarkeits-, kein Fähigkeits-Urteil.

Die Kurzfassung: **Neuer Preis-Leistungs-König ist laguna-s-2.1**
($0.003, 57 s, volle Abnahme). Für die Uhr bleibt gpt-5.6-terra (52 s,
6 Schritte), als Effizienz-Allrounder gpt-5.6-luna, Gratis-Tipp
nemotron. Wer Geld verbrennen will, nimmt claude-opus-5 — bekommt dafür
aber immerhin Gründlichkeit ohne Strenge-Fehler. Und lokal bleibt
gemma-4-26b als mxfp4 der Referenz-Arbeiter — nur seiner Cloud-Variante
sollte man nicht begegnen.

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
