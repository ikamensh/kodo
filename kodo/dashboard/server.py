"""Dashboard HTTP server with REST API and SSE for live event streaming."""

from __future__ import annotations

import argparse
import atexit
import json
import signal
import threading
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

_DASHBOARD_DIR = Path(__file__).parent
_STATIC_FILES = {
    "/": ("dashboard.html", "text/html; charset=utf-8"),
    "/dashboard.css": ("dashboard.css", "text/css; charset=utf-8"),
    "/dashboard.js": ("dashboard.js", "application/javascript; charset=utf-8"),
}

# Global server reference for embedded mode
_server: HTTPServer | None = None
_server_thread: threading.Thread | None = None
_server_url: str | None = None


def _runs_root() -> Path:
    from kodo.log import _runs_root as _root
    return _root()


def _fast_run_summary(run_dir: Path) -> dict | None:
    """Build run summary by reading only the first ~10 lines and checking for run_end."""
    log_file = run_dir / "log.jsonl"
    if not log_file.exists():
        log_file = run_dir / "run.jsonl"
    if not log_file.exists():
        return None

    goal = ""
    project_dir = ""
    orchestrator = ""
    model = ""
    max_cycles = 0
    is_debug = False
    finished = False
    completed_cycles = 0

    try:
        with open(log_file, encoding="utf-8", errors="replace") as fh:
            for i, raw in enumerate(fh):
                if i > 20:
                    break
                try:
                    evt = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                event = evt.get("event")
                if event == "run_init":
                    project_dir = evt.get("project_dir", "")
                elif event == "cli_args":
                    goal = goal or evt.get("goal_text", "")
                    max_cycles = max_cycles or evt.get("max_cycles", 0)
                    is_debug = evt.get("debug", False)
                elif event == "run_start":
                    goal = evt.get("goal", goal)
                    orchestrator = evt.get("orchestrator", "")
                    model = evt.get("model", "")
                    max_cycles = max_cycles or evt.get("max_cycles", 0)
                elif event == "debug_run_start":
                    is_debug = True

        # Check last few lines for run_end and cycle_end count
        with open(log_file, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            # Read last 8KB
            read_size = min(size, 8192)
            fh.seek(size - read_size)
            tail = fh.read().decode("utf-8", errors="replace")

        for line in tail.strip().splitlines():
            try:
                evt = json.loads(line)
                if evt.get("event") == "run_end":
                    finished = True
                elif evt.get("event") == "cycle_end":
                    completed_cycles += 1
            except (json.JSONDecodeError, ValueError):
                pass

    except (OSError, PermissionError):
        return None

    if not goal and not project_dir:
        return None

    project_name = Path(project_dir).name if project_dir else "?"
    return {
        "run_id": run_dir.name,
        "goal": goal[:300],
        "project_dir": project_dir,
        "project_name": project_name,
        "orchestrator": orchestrator,
        "model": model,
        "finished": finished,
        "completed_cycles": completed_cycles,
        "max_cycles": max_cycles,
        "is_debug": is_debug,
    }


def _list_runs_metadata(limit: int = 50) -> list[dict]:
    """Build lightweight run index. Reads only headers, not full logs."""
    runs_dir = _runs_root()
    if not runs_dir.exists():
        return []

    try:
        entries = sorted(runs_dir.iterdir(), reverse=True)
    except PermissionError:
        return []

    index = []
    for d in entries:
        if len(index) >= limit:
            break
        if not d.is_dir():
            continue
        summary = _fast_run_summary(d)
        if summary:
            index.append(summary)
    return index


def _load_run_events(run_id: str) -> list[dict]:
    """Load all JSONL events for a run."""
    log_file = _runs_root() / run_id / "log.jsonl"
    if not log_file.exists():
        log_file = _runs_root() / run_id / "run.jsonl"
    if not log_file.exists():
        return []
    events = []
    with open(log_file, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                events.append(json.loads(raw))
            except (json.JSONDecodeError, ValueError):
                pass
    return events


def _load_run_config(run_id: str) -> dict[str, str | None]:
    """Load config files for a run (goal, plan, team, config)."""
    run_dir = _runs_root() / run_id
    config: dict[str, str | None] = {}
    for name in ("goal.md", "goal-refined.md", "goal-plan.json", "team.json", "config.json"):
        path = run_dir / name
        if path.exists():
            try:
                config[name] = path.read_text(encoding="utf-8")
            except OSError:
                config[name] = None
        else:
            config[name] = None
    return config


def _get_live_stats() -> dict | None:
    """Get in-process RunStats and RunProgress if a run is active."""
    try:
        from kodo.log import get_run_stats, get_run_progress, get_elapsed_s
        stats = get_run_stats()
        agents, orch_cost, orch_bucket = stats.snapshot()
        cycle, max_cycles, stage_label, active_agent = get_run_progress().snapshot()
        elapsed = get_elapsed_s()
        return {
            "agents": {
                name: {
                    "calls": s.calls,
                    "cost_usd": s.cost_usd,
                    "input_tokens": s.input_tokens,
                    "output_tokens": s.output_tokens,
                    "elapsed_s": s.elapsed_s,
                    "errors": s.errors,
                    "cost_bucket": s.cost_bucket,
                }
                for name, s in agents.items()
            },
            "orchestrator_cost_usd": orch_cost,
            "orchestrator_bucket": orch_bucket,
            "cycle": cycle,
            "max_cycles": max_cycles,
            "stage_label": stage_label,
            "active_agent": active_agent,
            "elapsed_s": elapsed,
        }
    except Exception:
        return None


def _validate_run_id(run_id: str) -> bool:
    """Prevent path traversal."""
    return "/" not in run_id and "\\" not in run_id and ".." not in run_id


class DashboardHandler(BaseHTTPRequestHandler):
    """Handle dashboard HTTP requests."""

    def log_message(self, format: str, *args: Any) -> None:
        pass  # silence request logging

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)



    def _send_error(self, status: int, msg: str) -> None:
        self._send_json({"error": msg}, status)

    def do_GET(self) -> None:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path in _STATIC_FILES:
            filename, content_type = _STATIC_FILES[path]
            body = (_DASHBOARD_DIR / filename).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/runs":
            limit = int(params.get("limit", [50])[0])
            self._send_json(_list_runs_metadata(limit=min(limit, 500)))
            return

        # /api/run/{id}/...
        if path.startswith("/api/run/"):
            parts = path[len("/api/run/"):].split("/")
            run_id = parts[0]
            if not _validate_run_id(run_id):
                self._send_error(400, "Invalid run_id")
                return

            sub = parts[1] if len(parts) > 1 else ""

            if sub == "":
                # Run metadata + config
                config = _load_run_config(run_id)
                self._send_json({"run_id": run_id, "config": config})
                return

            if sub == "events":
                events = _load_run_events(run_id)
                self._send_json(events)
                return

            if sub == "stats":
                stats = _get_live_stats()
                self._send_json(stats or {"error": "no live stats"})
                return

            if sub == "stream":
                self._handle_sse(run_id)
                return

            self._send_error(404, f"Unknown endpoint: {sub}")
            return

        self._send_error(404, "Not found")

    def _handle_sse(self, run_id: str) -> None:
        """Server-Sent Events: tail the JSONL log file."""
        log_file = _runs_root() / run_id / "log.jsonl"
        if not log_file.exists():
            self._send_error(404, f"Run not found: {run_id}")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        try:
            with open(log_file, encoding="utf-8", errors="replace") as fh:
                # Send all existing events first
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        json.loads(raw)  # validate
                        self.wfile.write(f"data: {raw}\n\n".encode())
                    except (json.JSONDecodeError, ValueError):
                        pass
                self.wfile.flush()

                # Then tail for new events
                while True:
                    line = fh.readline()
                    if line:
                        line = line.strip()
                        if line:
                            try:
                                json.loads(line)
                                self.wfile.write(f"data: {line}\n\n".encode())
                                self.wfile.flush()
                            except (json.JSONDecodeError, ValueError):
                                pass
                    else:
                        # Check if run has ended
                        time.sleep(1)
                        # Send keepalive
                        try:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            break
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_PUT(self) -> None:
        path = self.path.split("?")[0]
        if path.startswith("/api/run/"):
            parts = path[len("/api/run/"):].split("/")
            run_id = parts[0]
            if not _validate_run_id(run_id):
                self._send_error(400, "Invalid run_id")
                return
            sub = parts[1] if len(parts) > 1 else ""

            if sub == "stages":
                self._handle_save_stages(run_id)
                return

        self._send_error(404, "Not found")

    def _handle_save_stages(self, run_id: str) -> None:
        """Update stage definitions in goal-plan.json."""
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode() if length else ""
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._send_error(400, "Invalid JSON")
            return

        new_stages = data.get("stages")
        if not isinstance(new_stages, list):
            self._send_error(400, "stages must be an array")
            return

        plan_file = _runs_root() / run_id / "goal-plan.json"
        if not plan_file.exists():
            self._send_error(404, "No goal-plan.json for this run")
            return

        try:
            plan = json.loads(plan_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            self._send_error(500, f"Cannot read plan: {exc}")
            return

        # Merge: update name/description/acceptance_criteria for matching indices
        existing = {s["index"]: s for s in plan.get("stages", [])}
        for ns in new_stages:
            idx = ns.get("index")
            if idx in existing:
                for field in ("name", "description", "acceptance_criteria"):
                    if field in ns:
                        existing[idx][field] = ns[field]

        plan["stages"] = list(existing.values())
        try:
            plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        except OSError as exc:
            self._send_error(500, f"Cannot write plan: {exc}")
            return

        self._send_json({"status": "ok"})

    def do_POST(self) -> None:
        path = self.path.split("?")[0]

        if path.startswith("/api/run/"):
            parts = path[len("/api/run/"):].split("/")
            run_id = parts[0]
            if not _validate_run_id(run_id):
                self._send_error(400, "Invalid run_id")
                return

            sub = parts[1] if len(parts) > 1 else ""

            if sub == "feedback":
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode() if length else ""
                try:
                    data = json.loads(body) if body else {}
                except json.JSONDecodeError:
                    self._send_error(400, "Invalid JSON")
                    return
                # TODO: inject into advisory queue
                self._send_json({"status": "ok", "message": "feedback received"})
                return

            if sub == "stop":
                # TODO: signal graceful stop
                self._send_json({"status": "ok", "message": "stop requested"})
                return

        self._send_error(404, "Not found")


class _ThreadedHTTPServer(HTTPServer):
    """HTTPServer that handles each request in a new thread."""
    daemon_threads = True
    allow_reuse_address = True

    def process_request(self, request: Any, client_address: Any) -> None:
        t = threading.Thread(target=self._handle, args=(request, client_address), daemon=True)
        t.start()

    def _handle(self, request: Any, client_address: Any) -> None:
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


def start_dashboard(port: int = 0, open_browser: bool = False) -> str:
    """Start the dashboard server as a background daemon thread.

    Args:
        port: Port to bind (0 = auto-select free port). If the requested port
              is busy, automatically tries the next few ports before falling
              back to OS-assigned.
        open_browser: Open the dashboard in the default browser.

    Returns:
        The URL of the running dashboard.
    """
    global _server, _server_thread, _server_url

    if _server is not None:
        return _server_url or ""

    # Try the requested port, then a few neighbors, then let the OS pick
    attempts = [port] if port == 0 else [port, port + 1, port + 2, 0]
    for try_port in attempts:
        try:
            _server = _ThreadedHTTPServer(("127.0.0.1", try_port), DashboardHandler)
            break
        except OSError:
            continue
    else:
        raise OSError("Could not find a free port for the dashboard server")

    actual_port = _server.server_address[1]
    _server_url = f"http://127.0.0.1:{actual_port}"

    _server_thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _server_thread.start()
    atexit.register(stop_dashboard)

    if open_browser:
        webbrowser.open(_server_url)

    return _server_url


def stop_dashboard() -> None:
    """Stop the dashboard server if running."""
    global _server, _server_thread, _server_url
    if _server is not None:
        _server.shutdown()
        _server.server_close()
        _server = None
        _server_thread = None
        _server_url = None


def main() -> None:
    """CLI entry point: python -m kodo.dashboard"""
    parser = argparse.ArgumentParser(description="kodo dashboard")
    parser.add_argument("run_id", nargs="?", help="Open specific run")
    parser.add_argument("--port", type=int, default=8050, help="Port (default: 8050)")
    parser.add_argument("--no-open", action="store_true", help="Don't open browser")
    args = parser.parse_args()

    # Ensure clean shutdown on SIGTERM (e.g. kill <pid>)
    signal.signal(signal.SIGTERM, lambda *_: _shutdown_and_exit())

    url = start_dashboard(port=args.port)
    if args.run_id:
        url = f"{url}#run={args.run_id}"
    print(f"Dashboard: {url}", flush=True)

    if not args.no_open:
        webbrowser.open(url)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop_dashboard()


def _shutdown_and_exit() -> None:
    stop_dashboard()
    raise SystemExit(0)


if __name__ == "__main__":
    main()
