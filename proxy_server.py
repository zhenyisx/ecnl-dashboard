"""
ECNL Dashboard Proxy Server (zero-dependency)

Serves static files and proxies API requests to api.athleteone.com, writing
every successful response into archive/api/ and falling back to that archive
whenever the live API is unreachable.

Usage:
    python proxy_server.py              live, with write-through archiving
    python proxy_server.py --offline    serve only from archive/, never hit the network
    python proxy_server.py --port 8000

    Then open http://localhost:5000/ecnl-dashboard.html

Responses carry X-ECNL-Source: live | archive so the dashboard can show where
the data came from.
"""

import argparse
import http.server
import json
import os
import sys
import urllib.error
from urllib.parse import urlparse, parse_qs

import ecnl_api as api

PORT = 5000
SERVE_DIR = os.path.dirname(os.path.abspath(__file__))

OFFLINE = False


class ProxyHandler(http.server.SimpleHTTPRequestHandler):
    """Serves static files from the project directory and proxies /api/* requests."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=SERVE_DIR, **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path.startswith("/api/"):
            api_path = parsed.path[len("/api/"):]
            query = parse_qs(parsed.query)
            refresh = query.get("refresh", ["0"])[0] == "1"
            self._handle_api(api_path, refresh)
            return

        super().do_GET()

    # ---------- API ----------

    def _handle_api(self, api_path, refresh):
        if not api.is_safe_api_path(api_path):
            self._send_json(400, {"error": "Invalid API path"})
            return

        if OFFLINE:
            if not self._send_from_archive(api_path):
                self._send_json(
                    504,
                    {"error": f"Offline mode and no archived copy of {api_path}. "
                              f"Run: python archive.py"},
                )
            return

        try:
            raw = api.fetch_api_raw(api_path, timeout=15, retries=1)
            json.loads(raw)  # only archive well-formed JSON
        except (api.ApiError, ValueError, urllib.error.URLError) as e:
            # Live call failed — this is exactly what the archive is for.
            if self._send_from_archive(api_path, note=str(e)):
                self.log_fallback(api_path, e)
                return
            self._send_json(502, {"error": str(e)})
            return

        api.write_archive(api_path, raw)
        self._send_bytes(
            raw,
            source="live",
            cache="no-store" if refresh else "public, max-age=300",
        )

    def _send_from_archive(self, api_path, note=None):
        raw, stamp = api.read_archive(api_path)
        if raw is None:
            return False
        self._send_bytes(raw, source="archive", archived_at=stamp,
                         cache="no-store", note=note)
        return True

    # ---------- response helpers ----------

    def _send_bytes(self, payload, source, archived_at=None, cache="no-store", note=None):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers",
                         "X-ECNL-Source, X-ECNL-Archived-At, X-ECNL-Note")
        self.send_header("X-ECNL-Source", source)
        if archived_at:
            self.send_header("X-ECNL-Archived-At", archived_at)
        if note:
            # Header values must stay on one line and ASCII-clean.
            self.send_header("X-ECNL-Note", "".join(
                c for c in note if c.isprintable() and ord(c) < 128)[:200])
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, code, obj):
        payload = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    # ---------- logging ----------

    def log_fallback(self, api_path, err):
        sys.stderr.write(f"\033[33m[ARCHIVE]\033[0m {api_path} (live failed: {err})\n")

    def log_message(self, format, *args):
        msg = format % args
        if "/api/" in msg:
            tag = "\033[35m[OFFLINE]\033[0m" if OFFLINE else "\033[36m[PROXY]\033[0m"
            sys.stderr.write(f"{tag} {msg}\n")
        else:
            sys.stderr.write(f"[STATIC] {msg}\n")


def main():
    global OFFLINE
    ap = argparse.ArgumentParser(description="ECNL dashboard static + API proxy server.")
    ap.add_argument("--offline", action="store_true",
                    help="Serve API responses only from archive/, never from the network.")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()
    OFFLINE = args.offline

    mode = "OFFLINE (archive only)" if OFFLINE else "live + write-through archive"
    print(f"\n  ECNL Dashboard Proxy Server — {mode}")
    print(f"  http://localhost:{args.port}/ecnl-dashboard.html")
    if not os.path.isdir(api.ARCHIVE_API_DIR):
        print(f"  note: no archive yet — run `python archive.py` to build one")
    print(f"  Press Ctrl+C to stop\n")

    server = http.server.HTTPServer(("", args.port), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
