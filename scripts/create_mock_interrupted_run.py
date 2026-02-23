"""Create a mock interrupted run for testing kodo --resume.

Run from the kodo repo root:
    uv run python scripts/create_mock_interrupted_run.py

Then verify:
    cd /tmp/kodo_resume_test && kodo --resume --yes
"""

from __future__ import annotations

import json
from pathlib import Path

RUN_ID = "interrupted_run"
PROJECT_DIR = "/tmp/kodo_resume_test"


def main() -> None:
    project_path = Path(PROJECT_DIR).resolve()
    project_dir_str = str(project_path)

    runs_root = Path.home() / ".kodo" / "runs"
    run_dir = runs_root / RUN_ID
    run_dir.mkdir(parents=True, exist_ok=True)

    # Ensure project dir exists so resume can use it
    project_path.mkdir(parents=True, exist_ok=True)

    events = [
        {
            "ts": "2025-02-23T12:00:00Z",
            "t": 0,
            "event": "run_init",
            "project_dir": project_dir_str,
            "version": "0.4.38",
        },
        {
            "ts": "2025-02-23T12:00:01Z",
            "t": 1,
            "event": "cli_args",
            "mode": "saga",
            "goal_text": "Build a simple REST API for todo items",
            "has_plan": False,
            "max_exchanges": 30,
            "max_cycles": 5,
            "orchestrator": "api",
            "orchestrator_model": "gemini-flash",
        },
        {
            "ts": "2025-02-23T12:00:02Z",
            "t": 2,
            "event": "run_start",
            "goal": "Build a simple REST API for todo items",
            "orchestrator": "api",
            "model": "gemini-flash",
            "project_dir": project_dir_str,
            "max_exchanges": 30,
            "max_cycles": 5,
            "team": {
                "worker_fast": {"backend": "CursorSession", "model": "composer-1.5"},
                "worker_smart": {"backend": "ClaudeSession", "model": "opus"},
            },
            "resumed": False,
            "has_stages": False,
        },
        {
            "ts": "2025-02-23T12:05:00Z",
            "t": 300,
            "event": "cycle_end",
            "summary": "Started implementation. Worker created basic project structure and added initial endpoints. Tests not yet written.",
            "finished": False,
        },
    ]

    log_file = run_dir / "run.jsonl"
    with open(log_file, "w") as f:
        for evt in events:
            f.write(json.dumps(evt) + "\n")

    goal_file = run_dir / "goal.md"
    goal_file.write_text(
        "Build a simple REST API for todo items.\n\n"
        "Requirements:\n"
        "- CRUD operations for todos\n"
        "- In-memory storage (no database)\n"
        "- Run with pytest for tests\n"
    )

    print(f"Created mock interrupted run at {run_dir}")
    print(f"  - {log_file}")
    print(f"  - {goal_file}")
    print()
    print("To verify resume:")
    print(f"  cd {project_dir_str} && kodo --resume --yes")
    print()
    print("Or resume by run ID:")
    print(f"  kodo --resume {RUN_ID} {PROJECT_DIR} --yes")


if __name__ == "__main__":
    main()
