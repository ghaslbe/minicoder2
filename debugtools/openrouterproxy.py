#!/usr/bin/env python3
"""Universeller Logging-Proxy fuer OpenAI-kompatible Chat-Endpunkte.

Sitzt lokal zwischen einem Client (mc.py, opencode, curl, ...) und einem
beliebigen Upstream (OpenRouter, gemietete vLLM-Instanzen, LM Studio, ...)
und protokolliert JEDE Anfrage + Antwort vollstaendig (inkl. System-Prompt,
"tools"-Schema, Streaming-Antwort rekonstruiert) in eine Logdatei -- ohne
den eigentlichen Traffic zu veraendern. Streaming (SSE) wird live
durchgereicht, nicht gepuffert; das Logging passiert parallel im
Hintergrund.

Nutzung:
    python3 debugtools/openrouterproxy.py --port 8787 \\
        --upstream https://openrouter.ai/api/v1

Client dann auf den Proxy zeigen, z.B. mc.py:
    MC_BASE_URL=http://127.0.0.1:8787 python3 mc.py ...
oder opencode (opencode.json):
    "baseURL": "http://127.0.0.1:8787"

Jede Anfrage landet als eigene Datei in --log-dir (Default: ./proxy-logs),
Dateiname = Zeitstempel + Pfad, Inhalt = huebsch formatiertes JSON mit
Request (Headers ohne Klartext-Key, Body) und Response (Status, Headers,
rekonstruierter Body -- bei SSE alle "data:"-Zeilen zusammengefuegt).
"""

import argparse
import http.server
import json
import socketserver
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _redact(headers):
    """Kopie der Header mit maskiertem Authorization-Wert fuers Log --
    der echte Wert wird weiterhin unveraendert an den Upstream geschickt,
    nur die LOGDATEI zeigt ihn nicht im Klartext."""
    out = {}
    for k, v in headers.items():
        if k.lower() == "authorization" and len(v) > 14:
            out[k] = v[:10] + "…" + v[-4:]
        else:
            out[k] = v
    return out


def _reconstruct_sse_text(raw_bytes):
    """Baut aus rohen SSE-Chunks ("data: {...}\\n\\n"-Zeilen) den
    sichtbaren Assistant-Text + ggf. tool_calls zusammen -- fuers Log
    lesbarer als hunderte Einzel-Deltas."""
    text_parts = []
    tool_calls = {}
    events = []
    for line in raw_bytes.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        events.append(obj)
        for choice in obj.get("choices", []):
            delta = choice.get("delta", {})
            if delta.get("content"):
                text_parts.append(delta["content"])
            if delta.get("reasoning"):
                pass  # Reasoning-Tokens NICHT in den sichtbaren Text mischen
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = tool_calls.setdefault(idx, {"name": None, "arguments": ""})
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]
    return {
        "assembled_text": "".join(text_parts),
        "tool_calls": list(tool_calls.values()) if tool_calls else None,
        "event_count": len(events),
    }


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    upstream = ""       # per make_handler gesetzt
    log_dir = None
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[proxy] {self.address_string()} - {fmt % args}\n")

    def _handle(self, method):
        started = time.time()
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""

        req_headers = {k: v for k, v in self.headers.items()
                        if k.lower() not in ("host", "content-length")}
        url = self.upstream.rstrip("/") + self.path

        req = urllib.request.Request(url, data=body or None, method=method,
                                      headers=req_headers)
        try:
            # 360s statt vorher 300s: manche Qwen-Modelle (v.a. mit langem
            # Reasoning vor dem ersten sichtbaren Chunk) haben Antworten
            # ueber 5 Minuten produziert -- ein zu knapper Timeout wuerde
            # den Client mit einem 502 abschneiden, obwohl der Endpoint noch
            # arbeitet.
            resp = urllib.request.urlopen(req, timeout=360)
        except urllib.error.HTTPError as e:
            resp = e
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"Proxy-Fehler beim Weiterleiten an {url}: {e}".encode())
            self._write_log(method, url, req_headers, body, None, None, str(e), started)
            return

        self.send_response(resp.status if hasattr(resp, "status") else resp.code)
        skip = {"transfer-encoding", "connection"}
        for k, v in resp.headers.items():
            if k.lower() not in skip:
                self.send_header(k, v)
        self.end_headers()

        chunks = []
        while True:
            chunk = resp.read(4096)
            if not chunk:
                break
            chunks.append(chunk)
            try:
                self.wfile.write(chunk)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                break
        raw = b"".join(chunks)
        resp_headers = dict(resp.headers.items())
        self._write_log(method, url, req_headers, body, resp_headers, raw, None, started)

    def _write_log(self, method, url, req_headers, req_body, resp_headers,
                    resp_body, error, started):
        if self.log_dir is None:
            return
        duration = time.time() - started
        entry = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "duration_s": round(duration, 2),
            "method": method,
            "url": url,
            "request_headers": _redact(req_headers),
            "error": error,
        }
        try:
            entry["request_body"] = json.loads(req_body) if req_body else None
        except json.JSONDecodeError:
            entry["request_body_raw"] = req_body.decode("utf-8", "replace")[:4000]

        if resp_headers is not None:
            entry["response_status_headers"] = resp_headers
        if resp_body:
            ctype = (resp_headers or {}).get("Content-Type", "")
            if "text/event-stream" in ctype:
                entry["response_sse"] = _reconstruct_sse_text(resp_body)
            else:
                try:
                    entry["response_body"] = json.loads(resp_body)
                except json.JSONDecodeError:
                    entry["response_body_raw"] = resp_body.decode("utf-8", "replace")[:4000]

        ts = time.strftime("%Y%m%d-%H%M%S")
        safe_path = self.path.strip("/").replace("/", "_") or "root"
        fname = self.log_dir / f"{ts}_{safe_path}.json"
        fname.write_text(json.dumps(entry, indent=2, ensure_ascii=False))
        sys.stderr.write(f"[proxy] {method} {self.path} -> {fname.name} "
                          f"({duration:.1f}s)\n")

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--upstream", required=True,
                     help="Basis-URL des echten Endpunkts, z.B. "
                          "https://openrouter.ai/api/v1")
    ap.add_argument("--log-dir", default="./proxy-logs")
    args = ap.parse_args()

    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    ProxyHandler.upstream = args.upstream
    ProxyHandler.log_dir = log_dir

    srv = ThreadingHTTPServer((args.host, args.port), ProxyHandler)
    print(f"Logging-Proxy auf http://{args.host}:{args.port} "
          f"-> {args.upstream}")
    print(f"Logs in: {log_dir.resolve()}")
    print("Client-BaseURL entsprechend umbiegen, z.B.:")
    print(f"  MC_BASE_URL=http://{args.host}:{args.port} python3 mc.py ...")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
