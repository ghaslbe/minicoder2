import os
import base64
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
import mimetypes

app = Flask(__name__)
CORS(app)

# Konfiguration über Umgebungsvariablen mit Fallbacks
BASE_URL = os.environ.get('BILDERKENNUNG_BASE_URL', 'http://192.168.178.191:1234/v1')
MODEL_NAME = os.environ.get('BILDERKENNUNG_MODEL', 'gemma-4-26b-a4b-it@mxfp4')
PORT = 5065  # 5060 ist SIP-Standardport, wird von Chrome als "unsafe port" blockiert (ERR_UNSAFE_PORT)

@app.route('/api/analyze', methods=['POST'])
def analyze_image():
    if 'image' not in request.files:
        return jsonify({'error': 'Kein Bild im Request gefunden'}), 400
    
    file = request.files['image']
    if not file:
        return jsonify({'error': 'Keine Datei hochgeladen'}), 400

    try:
        # MIME-Typ ermitteln
        mime_type, _ = mimetypes.guess_type(file.filename)
        if not mime_type:
            mime_type = 'image/jpeg'  # Fallback

        # Bild in Base64 kodieren
        image_content = file.read()
        base64_image = base64.b64encode(image_content).decode('utf-8')

        # Payload für das Vision-Modell vorbereiten
        payload = {
            "model": MODEL_NAME,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Beschreibe genau und ausfuehrlich, was auf diesem Bild zu sehen ist. Gehe auf Objekte, Personen, Farben, Anordnung und Kontext ein."},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}}
                    ]
                }
            ]
        }

        # Anfrage an das Modell senden mit Timeout
        response = requests.post(
            f"{BASE_URL}/chat/completions",
            json=payload,
            timeout=120
        )
        
        if response.status_code != 200:
            return jsonify({'error': f'Modell-Server Fehler: {response.status_code} - {response.text}'}), response.status_code

        result = response.json()
        description = result['choices'][0]['message']['content']
        
        return jsonify({'description': description})

    except requests.exceptions.Timeout:
        return jsonify({'error': 'Zeitüberschreitung bei der Bildanalyse'}), 504
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT)
