Erstelle eine README.md im aktuellen Arbeitsverzeichnis fuer die Anwendung 'Vibelove'. Lies dafuer zuerst server.py und templates/index.html, um die tatsaechliche Funktionsweise korrekt zu beschreiben (nicht raten). Die README soll folgende Abschnitte enthalten:

1. Kurze Beschreibung: Vibelove ist ein lokaler, Lovable-artiger App-Builder, der das Kommandozeilen-Tool mc.py (liegt eine Ebene hoeher im Projekt) als Motor nutzt, um per Texteingabe Anwendungen zu bauen - mit Chat-Formular links und Live-Vorschau rechts.
2. Voraussetzungen: Python 3 mit Flask installiert, Node/npm installiert, ein erreichbarer OpenAI-kompatibler Endpunkt (z.B. LM Studio oder Ollama) mit einem Modell.
3. Start (das WICHTIGSTE, ausfuehrlich): wie man Vibelove startet, inkl. der beiden Umgebungsvariablen VIBELOVE_BASE_URL und VIBELOVE_MODEL (mit Beispiel-Werten und Erklaerung, dass ohne sie mc.py's eingebaute Standardwerte genutzt werden), der Kommandozeile zum Starten von server.py, und dass die Anwendung danach unter http://localhost:5050 erreichbar ist.
4. Wie es funktioniert: Formular links sendet eine Bauanweisung an mc.py (im Verzeichnis workspace/), die Live-Vorschau rechts zeigt auf Port 5173 (der Vite-Dev-Server fuer workspace/frontend, von server.py selbst dauerhaft verwaltet, unabhaengig von einzelnen mc.py-Laeufen).
5. Ports-Uebersicht (Vibelove selbst: 5050, Vorschau/Vite: 5173, Backend der gebauten App falls vorhanden: 5090).
6. Bekannte Grenzen (kurz, ehrlich): aktuell nur ein einzelner Bauschritt pro Anfrage, kein Mehrfach-Chat-Verlauf, kein automatisches Neuladen der Vorschau bei laufenden Builds.

Danach: keine Code-Aenderungen, nur die README.md erstellen. Pruefe mit list_dir oder find, dass README.md tatsaechlich existiert, und gib den Dateinamen im finish-summary an.
