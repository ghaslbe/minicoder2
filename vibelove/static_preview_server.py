"""Eigenstaendiger Vorschau-Server fuer Projekte OHNE Build-Tool (kein
package.json/Vite): liefert die statischen Dateien direkt aus UND leitet
Anfragen unter einem API-Praefix an ein separates Backend weiter (Proxy) --
so kann ein Frontend ohne eigenes Build-Tool trotzdem relativ per fetch('/api/…')
mit einem echten Backend-Prozess sprechen, ganz ohne CORS-Konfiguration.

Aufruf: python3 static_preview_server.py <static_dir> <port> <backend_port> <api_prefix>
backend_port=0 bedeutet: kein Backend vorhanden, nur statische Dateien ausliefern.
static_dir='' (leer) bedeutet: KEIN Frontend vorhanden -- eine monolithische
Anwendung (z.B. Flask mit serverseitig gerenderten Templates) IST hier die
gesamte Anwendung; JEDE Anfrage wird an das Backend weitergeleitet, nicht nur
welche unter api_prefix.
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

# static_folder=None: Flask registriert sonst automatisch eine EIGENE
# /static/<path:filename>-Route (auf ein 'static/'-Verzeichnis NEBEN diesem
# Skript, das es hier gar nicht gibt) -- die faengt Anfragen wie
# /static/css/app.css ab, BEVOR sie catch_all() je erreichen, und liefert
# ihr eigenes 404 statt an das Projekt-static_dir oder das Backend
# weiterzuleiten. Real beobachtet: ein Flask-Backend, dessen Assets (wie
# ueblich) unter /static/ liegen, bekam dadurch 404 auf CSS/JS -- obwohl der
# Proxy fuer jeden anderen Pfad korrekt funktionierte.
app = Flask(__name__, static_folder=None)

# Header, die bei der Weiterleitung NICHT 1:1 uebernommen werden duerfen --
# Host/Content-Length muessen zum tatsaechlichen Ziel bzw. zur tatsaechlichen
# (evtl. veraenderten) Body-Laenge passen, sonst lehnt der Backend-Server
# die Anfrage ab oder haengt beim Lesen. Server/Date werden vom WSGI-Server
# dieses Proxys selbst schon gesetzt -- 1:1 durchgereicht gaeben sie doppelt.
_HOP_BY_HOP = {'host', 'content-length', 'connection', 'server', 'date'}


class _NoAutoRedirect(urllib.request.HTTPRedirectHandler):
    """Verhindert, dass urllib eine 301/302/303/307-Weiterleitung STILL
    SELBST verfolgt. Ein klassisches Flask-Muster (POST -> redirect(...) ->
    GET) braucht die Weiterleitung UNVERAENDERT beim eigentlichen Client
    (Browser/curl) -- folgt der Proxy ihr stattdessen selbst, bekommt der
    Client die FINALE Seite statt der Weiterleitung zurueck, OHNE dass
    sein eigener Cookie-Jar je die neue Session-Cookie aus der
    Zwischenantwort sieht. Real beobachtet: nach einem POST /generate kam
    ueber den Proxy eine leere Startseite statt der erwarteten
    Weiterleitung + Zusammenfassung -- der Flash-Hinweis und der neue
    Session-Cookie gingen dabei verloren."""

    def redirect_request(self, *args, **kwargs):
        return None  # None -> urllib wirft HTTPError mit der ORIGINAL-Antwort


_OPENER = urllib.request.build_opener(_NoAutoRedirect)


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
        with _OPENER.open(req, timeout=30) as resp:
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
    if not STATIC_DIR:
        # Kein Frontend -- das Backend IST die gesamte Anwendung.
        return _proxy(path)
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
