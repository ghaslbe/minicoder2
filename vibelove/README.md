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
* Backend (falls vorhanden): 5090

## Bekannte Grenzen
* Nur ein Bauschritt pro Anfrage.
* Kein Chat-Verlauf.
* Kein automatisches Neuladen der Vorschau waehrend des Builds.