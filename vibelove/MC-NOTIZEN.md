# Projekt-Notizen (mc)

- Endpoint: GET /download-zip -> liefert workspace/ als ZIP (vibelove-projekt.zip)
  Ausschlüsse: node_modules, dist, .git, __pycache__, *.log
- Flask send_file mit as_attachment=True, download_name='vibelove-projekt.zip'
- Breiten-Umschaltung in der Preview-Werkzeugleiste (templates/index.html): Buttons 375px/768px/Desktop,
  Funktion setPreviewWidth(width) setzt iframe-Breite; Desktop = Standard (100%).
- Git-Projektverwaltung: GET/POST /projects/remote, POST /projects/push; alle verlangen ein eigenes .git im aktiven Projekt, damit workspace (übergeordnetes Repo) nicht manipuliert wird.
- Einstellungen: max_steps ist persistent, Standard 100 und mindestens 1; POST /settings mit reset_api_key:true übernimmt MC_API_KEY erneut.
