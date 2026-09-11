# Vibelove

## Kurze Beschreibung
Vibelove ist ein lokaler App-Builder. Er nutzt das Tool mc.py als Motor, um per Texteingabe Anwendungen zu bauen. Die UI bietet links ein Formular und rechts eine Live-Vorschau.

## Voraussetzungen
* Python 3 mit Flask
* Node/npm
* OpenAI-kompatibler Endpunkt (z.B. LM Studio oder Ollama)

## Start
Führen Sie server.py aus:
python3 server.py

Die Anwendung ist unter http://localhost:5050 erreichbar.

Um den Bauprozess zu steuern, nutzen Sie diese Umgebungsvariablen:
* VIBELOVE_BASE_URL: URL zum Endpunkt (z.B. http://localhost:1234/v1 fuer LM Studio)
* VIBELOVE_MODEL: Name des Modells (z.B. gemma-4-26b-a4b-it@mxfp4)

Ohne diese Variablen nutzt server.py dieselben Standardwerte (LM Studio auf
Port 1234, Modell gemma-4-26b-a4b-it@mxfp4).

## Wie es funktioniert
Das Formular links sendet Anweisungen an mc.py im Verzeichnis workspace/. Die Live-Vorschau rechts zeigt die App auf Port 5173 (Vite-Server), welcher von server.py verwaltet wird.

## Ports-Uebersicht
* Vibelove: 5050
* Vorschau/Vite: 5173
* Backend (falls vorhanden): 5001

## Projekte und Chat
* Der Skills-Bereich zeigt auch die vorhandenen gemeinsamen Vorlagen aus `mc_skills` neben `mc.py` (Geltungsbereich Vibelove). Bei gleichem Namen haben globale und danach projektspezifische Vorlagen Vorrang.
* Links gibt es drei Ansichten: Build, Setup (Modellprofile) und Skills. Ein Ansichtswechsel unterbricht keinen Build.
* Setup verwaltet benannte Modellprofile mit Modellkennung, Endpunkt, API-Key, Schritten und Ausgabetokens. Bestehende Einstellungen werden als erstes Profil uebernommen. Das Projekt-Zahnrad waehlt nur noch das Profil fuer das aktive Projekt. Die Auswahl bleibt pro Projekt erhalten.
* Skills werden als `.md`/`.txt` im bestehenden mc-Format bearbeitet: global in `~/.mc/skills`, pro Projekt in `mc_skills`. Projektvorlagen haben bei gleichem Namen Vorrang. `Verwenden` setzt `/name` in die Chateingabe; Argumente ersetzen `$ARGUMENTS`. `analyse: true` wird an den Build weitergegeben, Builds verwenden weiterhin immer `--check`.
* Laufende Builds lassen sich direkt an der Chatnachricht mit Stoppen abbrechen. Der bisherige Dateistand bleibt erhalten und wird wie bei anderen abgebrochenen Builds gesichert.
* Im Dateien-Tab lassen sich UTF-8-Textdateien bis 2 MB direkt bearbeiten und per Speichern-Button oder Strg/Cmd+S speichern. Ungespeicherte Aenderungen werden beim Verlassen abgefragt; zwischenzeitlich geaenderte Dateien werden nicht ueberschrieben.
* Der Chatbereich laesst sich am Trenner in der Breite anpassen (auch mit den Pfeiltasten) und ueber den Pfeil neben der Vorschau einklappen.
* Breite und Einklappzustand werden pro Projekt in diesem Browser gespeichert und beim Projektwechsel oder Neuladen wiederhergestellt.
* Der Bauverlauf bleibt pro Projekt erhalten. Neue Bauschritte speichern ihren Git-Stand davor und danach, sodass Rueckgaengig auch nach einem Reload verfuegbar bleibt. Alte Eintraege ohne Git-Zuordnung werden weiterhin angezeigt.
* Waehrend eines Bauauftrags sind konkurrierende Aenderungen und Projektwechsel gesperrt, auch aus einem zweiten Browser-Tab.
* Die Vorschau wird nach dem Build neu geladen. Neben Vite werden statische Seiten und Backends mit `backend/vibelove-backend.json` unterstuetzt.
