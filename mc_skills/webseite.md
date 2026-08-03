---
beschreibung: Webseite abrufen und strukturiert analysieren (auch JS-gerenderte Seiten)
---
Rufe die folgende Webseite ab und erstelle eine strukturierte Analyse:

$ARGUMENTS

Vorgehen:
1. Seite per run abrufen: curl -sL '<url>' (bei grossen Seiten uebernimmt das
   Tool automatisch eine isolierte Analyse — nutze deren Zusammenfassung).
2. WICHTIG bei JS-gerenderten Seiten (wenig sichtbarer Text im HTML, viel
   Script-Geruest): Der echte Inhalt steckt oft in eingebettetem
   Hydration-JSON. Suche im HTML nach eingebetteten Datenbloecken
   (z.B. __NEXT_DATA__, self.__next_f, "nodes"-Strukturen, application/ld+json),
   speichere sie per run in eine Temp-Datei und extrahiere die relevanten
   Daten mit python3 -c "import json; ...". Gib NICHT auf, nur weil das
   sichtbare HTML leer wirkt — und erfinde KEINE Inhalte: was nicht abrufbar
   ist, wird als 'nicht ermittelbar' benannt.
3. Auch Meta-Ebene auswerten: <title>, meta description, Open-Graph-Tags —
   die verraten Thema und Kernbotschaft auch bei JS-Seiten.

Schreibe das Ergebnis als webseiten-analyse.md in das Arbeitsverzeichnis:
- Titel, Zweck und Kernbotschaft der Seite
- Seitenstruktur (Sektionen/Navigation in Reihenfolge)
- Wichtige Inhalte/Daten (Listen, Produkte, Modelle, Preise — was die Seite hergibt)
- Auffaelligkeiten (Technik-Stack-Hinweise, Besonderheiten)
Danach finish mit einer knappen Zusammenfassung.
