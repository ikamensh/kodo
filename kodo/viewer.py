"""Open a JSONL log file in the chat-style HTML viewer.

Usage: python -m kodo.viewer <logfile.jsonl>
   or: python -m kodo.viewer  (opens log picker / drag-and-drop page)
   or: python -m kodo.viewer --serve [--port 8080] [logfile.jsonl]
"""

from __future__ import annotations

import argparse
import atexit
import glob
import json
import os
import shutil
import sys
import tempfile
import time
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

_VIEWER_HTML = Path(__file__).parent / "viewer.html"
_EMBED_MARKER = "/*__EMBED_MARKER__*/"
_INDEX_MARKER = "/*__INDEX_MARKER__*/"


def _build_run_index() -> list[dict]:
    """Build lightweight metadata for all runs (no log content)."""
    from kodo.log import list_runs

    index = []
    for r in list_runs():
        project_name = Path(r.project_dir).name if r.project_dir else "?"
        index.append({
            "run_id": r.run_id,
            "log_file": str(r.log_file),
            "goal": r.goal[:200],
            "project_dir": r.project_dir,
            "project_name": project_name,
            "orchestrator": r.orchestrator,
            "model": r.model,
            "finished": r.finished,
            "completed_cycles": r.completed_cycles,
            "max_cycles": r.max_cycles,
        })
    return index


def _build_html(log_path: Path | None, include_index: bool = False) -> str:
    template = _VIEWER_HTML.read_text()

    # Embed run index if requested
    if include_index:
        index = _build_run_index()
        idx_js = f"EMBEDDED_INDEX = {json.dumps(index)};"
        idx_js = idx_js.replace("</script>", "<\\/script>")
        template = template.replace(_INDEX_MARKER, idx_js)
    else:
        template = template.replace(_INDEX_MARKER, "")

    if log_path is not None:
        # Read JSONL lines, validate JSON, and embed directly as a JSON array
        # to avoid the memory cost of parse-then-reserialize.
        valid_lines: list[str] = []
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    json.loads(raw)  # validate only
                    valid_lines.append(raw)
                except (json.JSONDecodeError, ValueError):
                    pass  # skip corrupt lines
        embed = "EMBEDDED_DATA = [" + ",\n".join(valid_lines) + "];"
        embed = embed.replace("</script>", "<\\/script>")
        return template.replace(_EMBED_MARKER, embed)
    return template.replace(_EMBED_MARKER, "")


def _cleanup_stale_viewer_files() -> None:
    """Remove kodo_viewer_* temp files/dirs older than 1 hour."""
    tmpdir = tempfile.gettempdir()
    cutoff = time.time() - 3600
    for pattern in ("kodo_viewer_*.html", "kodo_viewer_*/"):
        for path in glob.glob(os.path.join(tmpdir, pattern)):
            try:
                if os.path.getmtime(path) < cutoff:
                    if os.path.isdir(path):
                        shutil.rmtree(path, ignore_errors=True)
                    else:
                        os.unlink(path)
            except OSError:
                pass


def open_viewer(log_path: Path | None = None) -> None:
    if os.environ.get("KODO_NO_VIEWER"):
        return
    _cleanup_stale_viewer_files()
    # Include run index when no specific log is given
    html = _build_html(log_path, include_index=(log_path is None))
    with tempfile.NamedTemporaryFile(
        "w", suffix=".html", prefix="kodo_viewer_", delete=False,
    ) as f:
        f.write(html)
        tmp = f.name
    atexit.register(os.unlink, tmp)
    url = f"file://{tmp}"
    webbrowser.open(url)
    print(f"Log viewer: {url}")


def _serve(port: int, log_path: Path | None) -> None:
    _cleanup_stale_viewer_files()
    html = _build_html(log_path, include_index=True)
    tmpdir = tempfile.mkdtemp(prefix="kodo_viewer_")
    atexit.register(shutil.rmtree, tmpdir, True)
    (Path(tmpdir) / "index.html").write_text(html)

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=tmpdir, **k)

        def do_GET(self):
            # API endpoint: serve individual log files by run_id
            if self.path.startswith("/api/log/"):
                run_id = self.path[len("/api/log/"):]
                self._serve_log(run_id)
                return
            super().do_GET()

        def _serve_log(self, run_id: str):
            from kodo.log import _runs_root
            if "/" in run_id or "\\" in run_id or ".." in run_id:
                self.send_error(400, "Invalid run_id")
                return
            log_file = _runs_root() / run_id / "run.jsonl"
            if not log_file.exists():
                self.send_error(404, f"Run not found: {run_id}")
                return
            try:
                data = log_file.read_bytes()
            except OSError as exc:
                self.send_error(500, str(exc))
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    try:
        server = HTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        print(f"Error: {exc}")
        print(f"Hint: try a different port with --port {port + 1}")
        sys.exit(1)
    url = f"http://127.0.0.1:{port}/"
    print(f"Log viewer: {url}", flush=True)
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="kodo log viewer")
    parser.add_argument("logfile", nargs="?", help="Path to .jsonl log file")
    parser.add_argument(
        "--serve", action="store_true", help="Serve on HTTP port instead of file://",
    )
    parser.add_argument(
        "--port", type=int, default=8080, help="Port for --serve (default: 8080)",
    )
    args = parser.parse_args()

    log_path = Path(args.logfile) if args.logfile else None
    if log_path is not None:
        if not log_path.exists():
            print(f"File not found: {log_path}", file=sys.stderr)
            sys.exit(1)
        if log_path.is_dir():
            print(f"Expected a log file, not a directory: {log_path}", file=sys.stderr)
            sys.exit(1)

    if args.serve:
        _serve(args.port, log_path)
    else:
        open_viewer(log_path)


if __name__ == "__main__":
    main()
