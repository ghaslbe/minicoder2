# Projekt-Notizen

## vibelove Landingpage (frontend/)
- Build: `npm run build` (exit 0 bestätigt)
- Basisserver-Port: siehe vite.config.js (Standard 5173)

## Conversion-Optimierungen (dieser Lauf)
- Hero: Nutzenversprechen + sozialer Beweis, Handlungsaufruf klarer, sekundärer Link auf "Für wen ist das?" repariert
- Maybe-/Foundation-Sektionen: unverändert übernommen
- Anmeldung: Formular mit Pflichtfeldern (Name, E-Mail), Vertrauenshinweis "Keine Kreditkarte/sofortiger Zugang"
- Tippfehlerkorrekturen (u.a. "mt/tiny", doppelte divs) behoben

## Offene Punkte
- Hintergrundbild im Hero wird per CSS eingeblendet; kein eigenes <img> nötig (Kommentar entfernt)
- Kein Server-Test per curl durchgeführt (nur Build); funktional unverändert ausser Formular-Handling (clientseitig preventDefault)
