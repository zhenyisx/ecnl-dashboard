"""
ECNL Dashboard Proxy Server (zero-dependency)
Serves static files and proxies API requests to api.athleteone.com.

Usage:
    python proxy_server.py
    Then open http://localhost:5000/ecnl-dashboard.html
"""

import http.server
import json
import os
import sys
import urllib.request
import urllib.error
from urllib.parse import urlparse, parse_qs

PORT = 5000
API_BASE = "https://api.athleteone.com"
SERVE_DIR = os.path.dirname(os.path.abspath(__file__))


class ProxyHandler(http.server.SimpleHTTPRequestHandler):
    """Serves static files from the project directory and proxies /api/* requests."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=SERVE_DIR, **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)

        # Proxy API requests
        if parsed.path.startswith("/api/"):
            self._proxy_api(parsed.path)
            return

        # Serve static files normally
        super().do_GET()

    def _proxy_api(self, path):
        url = API_BASE + path
        try:
            req = urllib.request.Request(url, headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0",
                "Origin": "https://public.totalglobalsports.com",
                "Referer": "https://public.totalglobalsports.com/",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "public, max-age=300")
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def log_message(self, format, *args):
        # Colorize proxy vs static requests
        msg = format % args
        if "/api/" in msg:
            sys.stderr.write(f"\033[36m[PROXY]\033[0m {msg}\n")
        else:
            sys.stderr.write(f"[STATIC] {msg}\n")


def main():
    print(f"\n  ECNL Dashboard Proxy Server")
    print(f"  http://localhost:{PORT}/ecnl-dashboard.html")
    print(f"  Press Ctrl+C to stop\n")
    server = http.server.HTTPServer(("", PORT), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
