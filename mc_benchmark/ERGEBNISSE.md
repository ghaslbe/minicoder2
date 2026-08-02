# CRUD-Benchmark: Gesamttabelle (24 Modelle)

Alle Läufe der Blog-Kapitel 17–28 (August 2026). Aufgabe: identische
CRUD-Personenverwaltung (Flask + SQLite + HTML-Oberfläche), `--yes --check`,
unbeaufsichtigt, 20-Minuten-Limit. Abnahme: 8 Kern-Fälle · 3
Validierungs-Fälle (`abnahme.py`). Sortiert nach Ausgang und Lauf-Kosten.
Die Läufe verteilen sich auf mehrere Harness-Stände — Feinvergleiche mit
Vorsicht.

| Modell | Preis/Mio (P/C) | Dauer | Requests | Lauf-Kosten | Abnahme | Ausgang |
|---|---|---|---|---|---|---|
| nemotron-3-ultra (free) | gratis | 269 s | 30 | **$0.00** | 8/8 · 2/3 | ✓ sauber |
| **laguna-s-2.1** 👑 | $0.09/$0.18 | 57 s | 13 | **$0.003** | 8/8 · 3/3 | ✓ sauber |
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
| kat-coder-air-v2.5 | $0.15/$0.60 | 287 s | 42 | $0.032 | **App ungültig** | ✗ kaputte app.py |
| gemma-4-26b-a4b-it (Cloud) | $0.07/$0.34 | 1200 s ⏱ | — | $0.031 | **keine App** | ✗ Escape-Degeneration |
| minimax-m3 | $0.30/$1.20 | 494 s | 42 | $0.109 | **keine App** | ✗ stilles Prosa-Ende ⁵ |
| ling-2.6-flash | $0.01/$0.03 | 1 s | 0 | $0.00 | — | ✗ Anbieter-Rate-Limit ⁶ |

¹ einziger Lauf vor der Prompt-Schärfung (daher Valid 1/3, PUT partiell).
² POST verlangt alle Felder.
³ Strenge-Schule: PUT auf unbekannte ID → 400 statt 404, POST verlangt alle Felder.
⁴ Nachtest nach dem Harness-Crash-Fix.
⁵ deckte die Prosa-Wächter-Lücke auf, inzwischen gefixt.
⁶ HTTP 429 upstream — Verfügbarkeits-, kein Fähigkeits-Urteil.

## Empfehlungen

- **Preis-Leistung:** poolside/laguna-s-2.1 ($0.003, 57 s, volle Abnahme)
- **Tempo:** gpt-5.6-terra (52 s, 6 Schritte)
- **Effizienz-Allrounder:** gpt-5.6-luna (15 Requests)
- **Gratis:** nemotron-3-ultra (free)
- **Gründlichkeit ohne Preisblick:** claude-opus-5 (einziges Oberklasse-Modell ohne Strenge-Fehler)
- **Lokal:** gemma-4-26b-a4b-it als mxfp4 in LM Studio (die Cloud-Variante meiden)

Reproduzieren: `MC_API_KEY=... python3 mc_benchmark/runner.py <anbieter/modell> ...`
dann `python3 mc_benchmark/abnahme.py mc_benchmark/laeufe/<slug>`.
