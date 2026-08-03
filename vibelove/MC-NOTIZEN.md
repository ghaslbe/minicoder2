# Projekt-Notizen (mc)

- Endpoint: GET /download-zip -> liefert workspace/ als ZIP (vibelove-projekt.zip)
  Ausschlüsse: node_modules, dist, .git, __pycache__, *.log
- Flask send_file mit as_attachment=True, download_name='vibelove-projekt.zip'
