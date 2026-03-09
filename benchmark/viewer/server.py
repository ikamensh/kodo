"""Tiny server: serves static viewer + proxies GCS data."""

import http.server
import json
import os
import urllib.request
from pathlib import Path

PORT = int(os.environ.get("PORT", 8080))
BUCKET = os.environ.get("GCS_BUCKET", "kodo-bench")
STATIC_DIR = Path(__file__).parent / "static"


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        # /data/* -> proxy from GCS
        if self.path.startswith("/data/"):
            self._proxy_gcs(self.path[1:])  # strip leading /
        else:
            self._serve_static()

    def _serve_static(self):
        path = self.path.rstrip("/")
        if path in ("", "/index.html"):
            fpath = STATIC_DIR / "index.html"
        else:
            fpath = STATIC_DIR / path.lstrip("/")

        if not fpath.is_file():
            self.send_error(404)
            return

        content = fpath.read_bytes()
        ctype = "text/html"
        if fpath.suffix == ".js":
            ctype = "application/javascript"
        elif fpath.suffix == ".css":
            ctype = "text/css"
        elif fpath.suffix == ".json":
            ctype = "application/json"

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _proxy_gcs(self, key: str):
        url = f"https://storage.googleapis.com/{BUCKET}/{key}"
        try:
            req = urllib.request.Request(url)
            # Use default credentials via metadata server on Cloud Run
            try:
                import google.auth.transport.requests
                import google.auth

                creds, _ = google.auth.default()
                creds.refresh(google.auth.transport.requests.Request())
                req.add_header("Authorization", f"Bearer {creds.token}")
            except Exception:
                pass  # Fall back to unauthenticated (for local dev)

            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
                self.send_response(200)
                self.send_header("Content-Type", resp.headers.get("Content-Type", "application/octet-stream"))
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
        except urllib.error.HTTPError as e:
            self.send_error(e.code, str(e.reason))
        except Exception as e:
            self.send_error(502, str(e))

    def log_message(self, fmt, *args):
        pass  # Silence request logs


if __name__ == "__main__":
    server = http.server.HTTPServer(("", PORT), Handler)
    print(f"Serving on :{PORT}")
    server.serve_forever()
