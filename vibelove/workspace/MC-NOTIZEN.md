# Projekt-Notizen

## vibelove Landingpage (frontend/)
- Build: `npm run build` (exit 0 bestätigt)
- Basisserver-Port: siehe vite.config.js (Standard 5173)

## Conversion-Optimierungen (dieser Lauf)
- Hero: Nutzenversprechen + sozialer Beweis, Handlungsaufruf klarer, sekundärer Link auf "Für wen ist das?" repariert
- Maybe-/Foundation-Sektionen: unverändert übernommen
- Anmeldung: Formular mit Pflichtfeldern (Name, E-Mail), Vertrauenshinweis "Keine Kreditkarte/sofortiger Zugang"
- Tippfehlerkorrekturen (u.a. "mt/tiny", doppelte divs) behoben

## Weitere Conversion-Optimierungen (dieser Lauf)
- Hero: Produkt-Mockup (Dashboard) rechts statt leerem div — sofortiger visueller Produktbezug
- Toter "Für wen"-Link repariert: ExampleDomain → TargetAudience (echte Zielgruppen-Sektion)
- FAQ-Sektion (#faq) ergänzt — Navbar-Link zeigt nicht mehr ins Leere
- Navbar-Button: "Gratis anmelden" (statt "Jetzt anmelden") und auf Mobilgeräten sichtbar — genau EIN Anmelde-Button, href="#anmeldung"
- CTA: Preisanker (regulärer Preis durchgestrichen, jetzt Gratis) + Deadline-Badge ("Plätze für das 90-Minuten-Programm begrenzt") + Formular-Feedback (Erfolgsmeldung nach Absenden)
- Footer: tote href="#"-Links ersetzt (Impressum→#, Datenschutz→#, Kontakt→mailto:)

## Offene Punkte
- Kein Server-Test per curl durchgeführt (nur Build); funktional unverändert ausser Formular-Handling (clientseitig preventDefault, setSubmitted-Status)
