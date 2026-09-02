#!/usr/bin/env python3
"""mc_proxy.py -- transparenter Mess-Proxy fuer mc.py-Requests.

Sitzt zwischen mc.py und einem beliebigen OpenAI-kompatiblen Endpunkt
(LM Studio, oMLX, OpenRouter, ...), leitet jeden /chat/completions-Request
unveraendert durch und loggt dabei pro Request Dauer, Tokenzahlen (aus
dem SSE-"usage"-Feld) und Tok/s -- einheitlich, unabhaengig davon, ob
oder wie der jeweilige Endpunkt selbst Timing-Infos meldet.

Nutzung:
  MC_PROXY_UPSTREAM=https://openrouter.ai/api/v1 python3 mc_proxy.py --port 8123
  # in einem zweiten Terminal, mc.py gegen den Proxy zeigen:
  MC_BASE_URL=http://127.0.0.1:8123 MC_API_KEY=... python3 mc.py ...

Jede Zeile in mc_proxy_log.jsonl ist ein abgeschlossener Request:
  {"zeit": "...", "model": "...", "dauer_s": 12.3,
   "prompt_tokens": 5998, "completion_tokens": 486,
   "tok_s": 39.5, "kosten": 0.0012}
"""
import argparse
import http.server
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

_LOG_LOCK = threading.Lock()

UPSTREAM = os.environ.get("MC_PROXY_UPSTREAM", "").rstrip("/")
LOG_PATH = os.environ.get("MC_PROXY_LOG", "mc_proxy_log.jsonl")

# Header, die beim Weiterleiten nicht 1:1 uebernommen werden duerfen --
# Hop-by-Hop-Header bzw. von uns selbst neu gesetzte Framing-Header.
_SKIP_REQUEST_HEADERS = {"host", "content-length", "connection"}
_SKIP_RESPONSE_HEADERS = {"content-length", "transfer-encoding", "connection"}


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        self._forward("POST")

    def do_GET(self):
        self._forward("GET")

    def _forward(self, method):
        if not UPSTREAM:
            self.send_error(500, "MC_PROXY_UPSTREAM nicht gesetzt")
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        url = UPSTREAM + self.path
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in _SKIP_REQUEST_HEADERS}
        req = urllib.request.Request(url, data=body or None, headers=headers, method=method)

        model = "?"
        if body:
            try:
                model = json.loads(body).get("model", "?")
            except json.JSONDecodeError:
                pass

        t0 = time.perf_counter()
        usage = None
        try:
            with urllib.request.urlopen(req, timeout=900) as resp:
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() not in _SKIP_RESPONSE_HEADERS:
                        self.send_header(k, v)
                # Kein Content-Length/Chunked-Framing: Body wird bis zum
                # Verbindungsende durchgereicht (fuer diesen lokalen,
                # einmaligen Mess-Proxy voellig ausreichend).
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                for raw in resp:
                    self.wfile.write(raw)
                    line = raw.decode("utf-8", "replace").strip()
                    if line.startswith("data:"):
                        chunk = line[len("data:"):].strip()
                        if chunk and chunk != "[DONE]":
                            try:
                                obj = json.loads(chunk)
                            except json.JSONDecodeError:
                                continue
                            if obj.get("usage"):
                                usage = obj["usage"]
        except urllib.error.HTTPError as e:
            self.send_error(e.code, str(e))
            return
        except Exception as e:
            print(f"[mc_proxy] FEHLER: {e}", file=sys.stderr)
            try:
                self.send_error(502, str(e))
            except Exception:
                pass
            return

        dauer = time.perf_counter() - t0
        completion_tokens = usage.get("completion_tokens") if usage else None
        eintrag = {
            "zeit": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "model": model,
            "dauer_s": round(dauer, 2),
            "prompt_tokens": usage.get("prompt_tokens") if usage else None,
            "cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
                              if usage else None,
            "completion_tokens": completion_tokens,
            "tok_s": round(completion_tokens / dauer, 2)
                     if completion_tokens and dauer > 0 else None,
            "kosten": (usage.get("cost") or (usage.get("cost_details") or {}).get("upstream_inference_cost"))
                      if usage else None,
        }
        with _LOG_LOCK:
            with open(LOG_PATH, "a") as f:
                f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
            print(f"[mc_proxy] {model} · {dauer:.1f}s · "
                  f"{eintrag['completion_tokens']} Tok · {eintrag['tok_s']} Tok/s"
                  + (f" · ${eintrag['kosten']}" if eintrag['kosten'] else ""))

    def log_message(self, format, *args):
        pass  # eigenes Logging oben statt Standard-stderr-Zeilen pro Request


def main():
    global UPSTREAM
    ap = argparse.ArgumentParser(description="Mess-Proxy fuer mc.py-Requests")
    ap.add_argument("--port", type=int, default=int(os.environ.get("MC_PROXY_PORT", "8123")))
    ap.add_argument("--upstream", default=UPSTREAM,
                     help="Ziel-Basis-URL, z.B. https://openrouter.ai/api/v1")
    args = ap.parse_args()
    UPSTREAM = args.upstream.rstrip("/")
    if not UPSTREAM:
        print("Fehler: --upstream oder MC_PROXY_UPSTREAM setzen "
              "(z.B. https://openrouter.ai/api/v1).", file=sys.stderr)
        sys.exit(1)
    print(f"mc_proxy: leite 127.0.0.1:{args.port} -> {UPSTREAM} weiter, Log: {LOG_PATH}")
    # ThreadingHTTPServer statt HTTPServer: mc.py verbindet sich pro Retry-
    # Versuch neu -- ein einzelfaediger Server blockiert dabei komplett,
    # solange der ERSTE (evtl. haengende) Request noch auf die Upstream-
    # Antwort wartet, und macht so aus einem einzelnen langsamen Request
    # eine komplette Verbindungs-Sackgasse fuer alle folgenden Versuche.
    http.server.ThreadingHTTPServer(("127.0.0.1", args.port), ProxyHandler).serve_forever()


if __name__ == "__main__":
    main()
