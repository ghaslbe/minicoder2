---
beschreibung: SEO-Bewertung einer URL oder des lokalen Projekts, mit Punktzahl und konkreten Fixes
---
Fuehre eine SEO-Bewertung durch fuer:

$ARGUMENTS

(Ist eine URL angegeben, pruefe die live ausgelieferte Seite per curl -sL.
Ist keine URL angegeben, pruefe das lokale Projekt in diesem Verzeichnis —
bevorzugt die gebaute Ausgabe bzw. die zentrale HTML/JSX-Datei.)

Pruefe systematisch und BELEGE jeden Befund mit dem echten Fundstueck
(per run + curl/grep nachsehen, nicht raten):

1. Title-Tag: vorhanden? Laenge ~50-60 Zeichen? Enthaelt das Hauptkeyword?
2. Meta Description: vorhanden? ~150-160 Zeichen? Mit Handlungsaufruf?
3. Ueberschriften: genau EIN h1? Sinnvolle h2/h3-Hierarchie ohne Spruenge?
4. Bilder: alt-Attribute vorhanden und beschreibend?
5. Sprach-/Grundgeruest: lang-Attribut, viewport-Meta, charset?
6. Social/Struktur: Open-Graph-Tags, strukturierte Daten (ld+json), canonical?
7. Inhalt: Keyword-Fokus erkennbar? Interne Verlinkung/Anker? Textmenge
   ausreichend oder nur Bildwueste?
8. Technik-Signale: robots-Angaben, sitemap-Referenz, auffaellig grosse
   eingebundene Ressourcen?

Schreibe das Ergebnis als seo-bericht.md in das Arbeitsverzeichnis:
- Gesamtnote 0-100 mit Ein-Satz-Begruendung
- Tabelle: Kriterium | Befund (mit Beleg) | Bewertung (gut/mittel/schlecht)
- Die 5 wichtigsten konkreten Verbesserungen, priorisiert — bei einem
  lokalen Projekt jeweils mit der Datei/Stelle, wo die Aenderung hingehoert
Danach finish mit Gesamtnote und den Top-3-Empfehlungen.
