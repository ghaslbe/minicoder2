# Projekt-Notizen (mc)

- Endpoint: GET /download-zip -> liefert workspace/ als ZIP (vibelove-projekt.zip)
  Ausschlüsse: node_modules, dist, .git, __pycache__, *.log
- Flask send_file mit as_attachment=True, download_name='vibelove-projekt.zip'
- Breiten-Umschaltung in der Preview-Werkzeugleiste (templates/index.html): Buttons 375px/768px/Desktop,
  Funktion setPreviewWidth(width) setzt iframe-Breite; Desktop = Standard (100%).
