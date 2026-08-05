"""Eigenstaendiger Vorschau-Server fuer Projekte OHNE Build-Tool (kein
package.json/Vite): liefert die statischen Dateien direkt aus UND leitet
Anfragen unter einem API-Praefix an ein separates Backend weiter (Proxy) --
so kann ein Frontend ohne eigenes Build-Tool trotzdem relativ per fetch('/api/…')
mit einem echten Backend-Prozess sprechen, ganz ohne CORS-Konfiguration.

Aufruf: python3 static_preview_server.py <static_dir> <port> <backend_port> <api_prefix>
backend_port=0 bedeutet: kein Backend vorhanden, nur statische Dateien ausliefern.
"""
import os
import sys
import urllib.request
import urllib.error

from flask import Flask, request, Response, send_from_directory

STATIC_DIR = sys.argv[1]
PORT = int(sys.argv[2])
BACKEND_PORT = int(sys.argv[3]) if len(sys.argv) > 3 else 0
API_PREFIX = sys.argv[4] if len(sys.argv) > 4 else '/api/'

app = Flask(__name__)

# Header, die bei der Weiterleitung NICHT 1:1 uebernommen werden duerfen --
# Host/Content-Length muessen zum tatsaechlichen Ziel bzw. zur tatsaechlichen
# (evtl. veraenderten) Body-Laenge passen, sonst lehnt der Backend-Server
# die Anfrage ab oder haengt beim Lesen.
_HOP_BY_HOP = {'host', 'content-length', 'connection'}


def _proxy(path):
    url = f"http://127.0.0.1:{BACKEND_PORT}/{path}"
    if request.query_string:
        url += "?" + request.query_string.decode("utf-8", "replace")
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in _HOP_BY_HOP}
    req = urllib.request.Request(
        url, data=request.get_data() or None,
        headers=headers, method=request.method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            status = resp.status
            resp_headers = [(k, v) for k, v in resp.getheaders()
                             if k.lower() not in _HOP_BY_HOP]
    except urllib.error.HTTPError as e:
        body = e.read()
        status = e.code
        resp_headers = [(k, v) for k, v in e.headers.items()
                         if k.lower() not in _HOP_BY_HOP]
    except urllib.error.URLError as e:
        return Response(f"Backend nicht erreichbar (Port {BACKEND_PORT}): {e}",
                         status=502, mimetype='text/plain')
    return Response(body, status=status, headers=resp_headers)


@app.route('/', defaults={'path': ''}, methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])
@app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])
def catch_all(path):
    if BACKEND_PORT and ('/' + path).startswith(API_PREFIX):
        return _proxy(path)
    if path == '':
        path = 'index.html'
    full = os.path.join(STATIC_DIR, path)
    if os.path.isfile(full):
        return send_from_directory(STATIC_DIR, path)
    return Response('Nicht gefunden.', status=404, mimetype='text/plain')


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=PORT, debug=False)
