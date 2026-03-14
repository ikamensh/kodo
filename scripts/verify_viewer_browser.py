#!/usr/bin/env python3
"""Browser verification of the log viewer. Run: uv run python tests/test_viewer_browser.py"""

import json
import tempfile
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).parent.parent
# Use real log if available
LOG_PATH = Path.home() / ".kodo/runs/20260219_200346/log.jsonl"
MINIMAL_EVENTS = [
    {
        "ts": "2026-01-01T00:00:00Z",
        "t": 0,
        "event": "run_init",
        "project_dir": "/tmp/test",
    },
    {
        "ts": "2026-01-01T00:00:00Z",
        "t": 0.1,
        "event": "cli_args",
        "goal_text": "Test goal",
    },
    {
        "ts": "2026-01-01T00:00:00Z",
        "t": 0.2,
        "event": "run_start",
        "orchestrator": "api",
        "model": "gemini",
        "project_dir": "/tmp",
        "max_exchanges": 10,
        "max_cycles": 3,
    },
    {
        "ts": "2026-01-01T00:00:00Z",
        "t": 1,
        "event": "orchestrator_tool_call",
        "agent": "worker",
        "task": "Do something",
    },
    {
        "ts": "2026-01-01T00:00:00Z",
        "t": 5,
        "event": "agent_run_end",
        "agent": "worker",
        "response_text": "Done.",
        "elapsed_s": 4,
        "cost_usd": 0.01,
    },
    {
        "ts": "2026-01-01T00:00:00Z",
        "t": 6,
        "event": "orchestrator_done",
        "summary": "Complete",
        "success": True,
    },
]


def main():
    if LOG_PATH.exists():
        lines = LOG_PATH.read_text().strip().splitlines()
        # Use full log (</script> fix allows embedding agent output that contains viewer HTML)
        events = [json.loads(line) for line in lines]
    else:
        events = MINIMAL_EVENTS

    template = (PROJECT_ROOT / "kodo" / "viewer.html").read_text()
    embed = f"EMBEDDED_DATA = {json.dumps(events)};"
    embed = embed.replace(
        "</script>", "<\\/script>"
    )  # Prevent HTML parser from closing script early
    html = template.replace("/*__EMBED_MARKER__*/", embed)

    tmpdir = tempfile.mkdtemp()
    (Path(tmpdir) / "viewer.html").write_text(html)

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=tmpdir, **k)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/viewer.html"
    errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        console_err = []
        page.on(
            "console",
            lambda m: console_err.append(m.text) if m.type == "error" else None,
        )
        page.goto(url, wait_until="load")

        # Wait for JS to run and render (embedded data loads synchronously)
        try:
            page.wait_for_selector("#app", state="visible", timeout=30000)
        except Exception:
            if console_err:
                for e in console_err[:5]:
                    print(f"JS Error: {e}")
                    errors.append(f"Console: {e}")
            raise

        # 1. Drop zone should be hidden when data is embedded (auto-load)
        drop_zone = page.locator("#drop-zone")
        app = page.locator("#app")
        if drop_zone.is_visible():
            errors.append("Drop zone visible when data embedded (expected hidden)")
        if not app.is_visible():
            errors.append("App panel not visible when data embedded")

        # 2. Stats bar should render
        stats = page.locator("#stats-bar")
        if not stats.is_visible():
            errors.append("Stats bar not visible")
        stat_values = page.locator(".stat-value")
        if stat_values.count() < 3:
            errors.append(f"Expected multiple stat values, got {stat_values.count()}")

        # 3. Timeline should have content
        timeline = page.locator("#timeline")
        if not timeline.is_visible():
            errors.append("Timeline not visible")
        bubbles = page.locator(".bubble")
        if bubbles.count() < 2:
            errors.append(f"Expected timeline bubbles, got {bubbles.count()}")

        # 4. Expand button for long text (if any)
        expand_btn = page.locator(".expand-btn")
        if expand_btn.count() > 0:
            expand_btn.first.click()
            # Should toggle to "Show less"
            if "Show less" not in page.content():
                errors.append("Expand button did not toggle text")

        browser.close()

    if errors:
        print("BROWSER VERIFICATION FAILED:")
        for e in errors:
            print(f"  - {e}")
        raise SystemExit(1)
    print("Browser verification: OK")


if __name__ == "__main__":
    main()
