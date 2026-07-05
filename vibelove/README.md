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
* VIBELOVE_BASE_URL: URL zum Endpunkt (z.B. http://localhost:11434/v1)
* VIBELOVE_MODEL: Name des Modells (z.B. qwen3-coder:30b)

Ohne diese Variablen werden die Standardwerte von mc.py genutzt.

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