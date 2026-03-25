"""Kodo web dashboard — live monitoring and run replay.

Usage:
    python -m kodo.dashboard [--port PORT] [run_id]
    kodo dashboard [--port PORT] [run_id]

Or programmatically:
    from kodo.dashboard import start_dashboard, stop_dashboard
    url = start_dashboard(port=0)  # picks a free port
    ...
    stop_dashboard()
"""

from __future__ import annotations

from kodo.dashboard.server import start_dashboard, stop_dashboard

__all__ = ["start_dashboard", "stop_dashboard"]
