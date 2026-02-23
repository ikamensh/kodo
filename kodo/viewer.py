"""Open a JSONL log file in the chat-style HTML viewer.

Usage: python -m kodo.viewer <logfile.jsonl>
   or: python -m kodo.viewer  (opens drag-and-drop page)
   or: python -m kodo.viewer --serve [--port 8080] [logfile.jsonl]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

_VIEWER_HTML = Path(__file__).parent / "viewer.html"
_EMBED_MARKER = "/*__EMBED_MARKER__*/"


def _build_html(log_path: Path | None) -> str:
    template = _VIEWER_HTML.read_text()
    if log_path is not None:
        lines = log_path.read_text().strip().splitlines()
        events = []
        for line in lines:
            try:
                events.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                pass  # skip corrupt lines
        embed = f"EMBEDDED_DATA = {json.dumps(events)};"
        embed = embed.replace("</script>", "<\\/script>")
        return template.replace(_EMBED_MARKER, embed)
    return template.replace(_EMBED_MARKER, "")


def open_viewer(log_path: Path | None = None) -> None:
    if os.environ.get("KODO_NO_VIEWER"):
        return
    html = _build_html(log_path)
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(html)
        tmp = f.name
    url = f"file://{tmp}"
    webbrowser.open(url)
    print(f"Log viewer: {url}")


def _serve(port: int, log_path: Path | None) -> None:
    html = _build_html(log_path)
    tmpdir = tempfile.mkdtemp()
    (Path(tmpdir) / "index.html").write_text(html)

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=tmpdir, **k)

    server = HTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Log viewer: {url}")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="kodo log viewer")
    parser.add_argument("logfile", nargs="?", help="Path to .jsonl log file")
    parser.add_argument(
        "--serve", action="store_true", help="Serve on HTTP port instead of file://"
    )
    parser.add_argument(
        "--port", type=int, default=8080, help="Port for --serve (default: 8080)"
    )
    args = parser.parse_args()

    log_path = Path(args.logfile) if args.logfile else None
    if log_path is not None and not log_path.exists():
        print(f"File not found: {log_path}", file=sys.stderr)
        sys.exit(1)

    if args.serve:
        _serve(args.port, log_path)
    else:
        open_viewer(log_path)


if __name__ == "__main__":
    main()
