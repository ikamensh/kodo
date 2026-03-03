"""Generate a large run.jsonl and measure parse_run performance.

Creates 10,000 events and times kodo.log.parse_run. Useful for profiling
and regression testing of log parsing performance.

Usage:
    uv run python scripts/perf_log_parsing.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kodo.log import parse_run


def generate_events(n: int) -> list[dict]:
    """Generate n events in a realistic run structure."""
    events = [
        {
            "ts": "2026-02-24T12:00:00Z",
            "t": 0,
            "event": "run_init",
            "project_dir": "/tmp/project",
            "version": "0.4.0",
        },
        {
            "ts": "2026-02-24T12:00:00Z",
            "t": 0.1,
            "event": "cli_args",
            "team": "saga",
            "orchestrator": "api",
            "orchestrator_model": "gemini-flash",
            "max_exchanges": 30,
            "max_cycles": 5,
            "goal_text": "Build a web app",
            "has_plan": False,
        },
        {
            "ts": "2026-02-24T12:00:01Z",
            "t": 1.0,
            "event": "run_start",
            "orchestrator": "api",
            "model": "gemini-flash",
            "goal": "Build a web app",
            "project_dir": "/tmp/project",
            "max_exchanges": 30,
            "max_cycles": 5,
            "team": {
                "worker_fast": {"backend": "cursor", "model": "composer"},
                "worker_smart": {"backend": "claude", "model": "opus"},
            },
            "resumed": False,
            "resume_from_cycle": None,
            "has_stages": False,
            "num_stages": 0,
        },
    ]

    # Fill with cycle_end, session_query_end, agent_run_end, etc.
    for i in range(n - 4):  # reserve last slot for run_end
        r = i % 10
        if r == 0:
            events.append(
                {
                    "ts": "t",
                    "t": i,
                    "event": "cycle_end",
                    "summary": f"Cycle {i // 10} done",
                }
            )
        elif r == 1:
            events.append(
                {
                    "ts": "t",
                    "t": i,
                    "event": "session_query_end",
                    "session": "worker_fast",
                    "session_id": f"sess-{i}",
                }
            )
        elif r == 2:
            events.append(
                {"ts": "t", "t": i, "event": "agent_run_end", "agent": "worker_fast"}
            )
        elif r == 3:
            events.append(
                {
                    "ts": "t",
                    "t": i,
                    "event": "orchestrator_tool_call",
                    "tool": "ask_worker_fast",
                }
            )
        elif r == 4:
            events.append(
                {"ts": "t", "t": i, "event": "stage_start", "stage_index": (i % 3) + 1}
            )
        elif r == 5:
            events.append(
                {
                    "ts": "t",
                    "t": i,
                    "event": "stage_end",
                    "stage_index": 1,
                    "finished": True,
                    "summary": f"Stage done {i}",
                }
            )
        else:
            events.append(
                {"ts": "t", "t": i, "event": "cycle_end", "summary": f"Partial {i}"}
            )

    events.append({"ts": "t", "t": n, "event": "run_end"})
    return events


def main() -> int:
    n_events = 10_000
    project_dir = Path(__file__).resolve().parent.parent / "tmp_perf_log"
    project_dir.mkdir(exist_ok=True)
    log_file = project_dir / "run.jsonl"

    print(f"Generating {n_events} events...")
    events = generate_events(n_events)
    log_file.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    size_kb = log_file.stat().st_size / 1024
    print(f"Wrote {log_file} ({size_kb:.1f} KB)")

    print("Running parse_run...")
    start = time.perf_counter()
    state = parse_run(log_file)
    elapsed = time.perf_counter() - start

    if state is None:
        print("FAIL: parse_run returned None", file=sys.stderr)
        return 1

    print(f"Parsed in {elapsed:.3f}s ({n_events / elapsed:,.0f} events/s)")
    print(f"  completed_cycles={state.completed_cycles}, finished={state.finished}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
