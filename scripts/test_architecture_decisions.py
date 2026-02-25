#!/usr/bin/env python3
"""Test whether the orchestrator makes sound architecture decisions.

Runs goals with deliberate "trap" choices through the harness, then checks
the output files for signs of correct vs incorrect architecture picks.

Usage:
    uv run python scripts/test_architecture_decisions.py
    uv run python scripts/test_architecture_decisions.py offline-timer
    uv run python scripts/test_architecture_decisions.py csv-parser bar-chart

Requires: GOOGLE_API_KEY (or GEMINI_API_KEY), claude CLI on PATH.
"""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
os.environ.pop("CLAUDECODE", None)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from kodo import log, make_session
from kodo.agent import Agent
from kodo.log import RunDir
from kodo.orchestrators.api import ApiOrchestrator
from scripts.harness import (
    _c,
    extract_orchestrator_choice,
    instrument_team,
    print_banner,
    print_log_summary,
    print_separator,
)

# ── Orchestrator prompt ──────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an orchestrator. Your ONLY job is to get the user's desired outcome.

PROCESS — follow these steps using tool calls:

1. DESIGN PHASE: Call ask_worker and tell it to enter plan mode (using
   its built-in plan mode tool) and propose exactly 3 implementation
   approaches. Each approach should name the key technology choices and
   briefly explain trade-offs. The worker must NOT implement yet.

2. CHOOSE: The worker's result will contain [PROPOSED PLAN] with the
   options. Review them. Call ask_worker again telling it which approach
   you picked and why, and tell it to proceed with implementation.

3. DONE: After the worker implements, call the done tool.

IMPORTANT:
- You MUST make a second ask_worker call after reviewing options.
- Keep implementation directives to 1-3 sentences of desired behavior.
- Do NOT include implementation details — the worker is an expert coder.
""".strip()

WORKER_DESC = (
    "A coding agent (Claude Code) that can read/write files, run commands, "
    "and execute the full development workflow.\n"
    "Give it a SHORT directive (1-3 sentences) describing desired BEHAVIOR.\n"
    "If the result contains [PROPOSED PLAN], the worker entered plan mode "
    "and is waiting for your review. Tell it which approach to take.\n"
    "If it seems stuck, set new_conversation=true with a fresh directive."
)

# ── Check infrastructure ────────────────────────────────────────────────


@dataclass
class Check:
    name: str
    fn: object  # Callable[[Path], bool]

    def run(self, project_dir: Path) -> bool:
        return self.fn(project_dir)  # type: ignore[operator]


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class ScenarioResult:
    name: str
    finished: bool = False
    success: bool = False
    checks: list[CheckResult] = field(default_factory=list)
    orchestrator_choice: str = ""
    error: str = ""


# ── Check helpers ────────────────────────────────────────────────────────


def _any_file_contains(project_dir: Path, pattern: str, glob: str = "*") -> bool:
    """True if any file matching glob contains pattern (case-insensitive)."""
    for f in project_dir.rglob(glob):
        if f.is_file():
            try:
                if pattern.lower() in f.read_text(errors="ignore").lower():
                    return True
            except Exception:
                pass
    return False


def _no_file_contains(project_dir: Path, pattern: str, glob: str = "*") -> bool:
    return not _any_file_contains(project_dir, pattern, glob)


def _any_file_under_bytes(
    project_dir: Path, max_bytes: int, glob: str = "*.html"
) -> bool:
    """True if at least one matching file is under max_bytes."""
    for f in project_dir.rglob(glob):
        if f.is_file() and f.stat().st_size < max_bytes:
            return True
    return False


def _has_file(project_dir: Path, glob: str) -> bool:
    return any(project_dir.rglob(glob))


# ── Scenarios ────────────────────────────────────────────────────────────

SCENARIOS: list[dict] = [
    {
        "name": "offline-timer",
        "goal": (
            "Create a single HTML file (no CDN, no npm, no external resources) "
            "with an animated countdown ring that counts down from 60 seconds. "
            "Must work offline when opened directly from the filesystem."
        ),
        "checks": [
            Check("has HTML file", lambda d: _has_file(d, "*.html")),
            Check(
                "no external URLs", lambda d: _no_file_contains(d, "https://", "*.html")
            ),
            Check("no http URLs", lambda d: _no_file_contains(d, "http://", "*.html")),
        ],
    },
    {
        "name": "tiny-bio-card",
        "goal": (
            "Create a single HTML file under 4KB with a styled bio card showing "
            "a name, title, and short bio paragraph. Smallest file size is the "
            "top priority — no frameworks, no external resources."
        ),
        "checks": [
            Check("has HTML file", lambda d: _has_file(d, "*.html")),
            Check("under 4KB", lambda d: _any_file_under_bytes(d, 4096, "*.html")),
            Check("no CDN links", lambda d: _no_file_contains(d, "https://", "*.html")),
        ],
    },
    {
        "name": "csv-parser",
        "goal": (
            "Create a Python script (no pip dependencies) that reads a CSV file "
            "from a path given as a CLI argument and prints each row as a JSON object. "
            "Must correctly handle quoted fields containing commas, newlines, and "
            "escaped quotes."
        ),
        "checks": [
            Check("has Python file", lambda d: _has_file(d, "*.py")),
            Check(
                "uses csv module", lambda d: _any_file_contains(d, "import csv", "*.py")
            ),
            Check("no pip deps", lambda d: _no_file_contains(d, "pandas", "*.py")),
        ],
    },
    {
        "name": "bar-chart",
        "goal": (
            "Create a single HTML file with 5 range sliders that each control "
            "the height of a corresponding bar in a bar chart. The bars must "
            "animate smoothly using CSS transitions. No JavaScript animation "
            "libraries, no CDN, no external resources."
        ),
        "checks": [
            Check("has HTML file", lambda d: _has_file(d, "*.html")),
            Check(
                "uses CSS transition",
                lambda d: _any_file_contains(d, "transition", "*.html"),
            ),
            Check(
                "no canvas element", lambda d: _no_file_contains(d, "<canvas", "*.html")
            ),
            Check("no CDN links", lambda d: _no_file_contains(d, "https://", "*.html")),
        ],
    },
]

SCENARIO_MAP = {s["name"]: s for s in SCENARIOS}


# ── Runner ───────────────────────────────────────────────────────────────


def run_scenario(scenario: dict) -> ScenarioResult:
    """Run a single scenario through the harness and check outputs."""
    name = scenario["name"]
    goal = scenario["goal"]
    checks = scenario["checks"]

    result = ScenarioResult(name=name)
    project_dir = Path(tempfile.mkdtemp(prefix=f"kodo-arch-test-{name}-"))

    print_banner(f"Scenario: {name}")
    print(f"  Goal:    {goal[:100]}{'...' if len(goal) > 100 else ''}")
    print(f"  Dir:     {project_dir}")

    # Init logging
    run_dir = RunDir.create(project_dir)
    log_path = log.init(run_dir)
    log.emit("harness_start", goal=goal, test=f"arch-decision-{name}")

    # Build worker + orchestrator
    worker_session = make_session("claude", "haiku", budget=None)
    worker = Agent(worker_session, WORKER_DESC, max_turns=5, timeout_s=60)
    team = instrument_team({"worker": worker})

    orchestrator = ApiOrchestrator(
        model="gemini-3-flash-preview",
        system_prompt=SYSTEM_PROMPT,
        max_context_tokens=100_000,
    )

    try:
        cycle_result = orchestrator.cycle(
            goal,
            project_dir,
            team,
            max_exchanges=15,
        )
        result.finished = cycle_result.finished
        result.success = cycle_result.success

        print_log_summary(log_path)

        # Extract what the orchestrator chose
        result.orchestrator_choice = extract_orchestrator_choice(log_path)
        if result.orchestrator_choice:
            print(f"\n  {_c('Orchestrator reasoning:', 'bold')}")
            print(textwrap.indent(result.orchestrator_choice[:500], "    "))

    except KeyboardInterrupt:
        result.error = "interrupted"
        raise
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        print(f"\n  ERROR: {result.error}")
    finally:
        try:
            worker.close()
        except Exception:
            pass

    # Run checks
    print(f"\n  {_c('Checks:', 'bold')}")
    for check in checks:
        try:
            passed = check.run(project_dir)
        except Exception as exc:
            passed = False
            detail = str(exc)
        else:
            detail = ""

        cr = CheckResult(name=check.name, passed=passed, detail=detail)
        result.checks.append(cr)
        icon = _c("PASS", "green") if passed else _c("FAIL", "yellow")
        print(f"    [{icon}] {check.name}" + (f" — {detail}" if detail else ""))

    print(f"\n  Output dir: {project_dir}")
    return result


# ── Scorecard ────────────────────────────────────────────────────────────


def print_scorecard(results: list[ScenarioResult]) -> None:
    print_banner("Scorecard")

    total_checks = 0
    total_passed = 0

    for r in results:
        if r.error:
            status = _c("ERROR", "yellow")
        elif all(c.passed for c in r.checks):
            status = _c("ALL PASS", "green", "bold")
        else:
            passed = sum(1 for c in r.checks if c.passed)
            status = _c(f"{passed}/{len(r.checks)} pass", "yellow")

        print(f"  {r.name:<20s}  {status}")
        for c in r.checks:
            icon = _c("✓", "green") if c.passed else _c("✗", "yellow")
            print(f"    {icon} {c.name}")
            total_checks += 1
            total_passed += int(c.passed)

        if r.error:
            print(f"    {_c(r.error, 'yellow')}")

    print_separator()
    print(f"  Total: {total_passed}/{total_checks} checks passed")
    print()


# ── Main ─────────────────────────────────────────────────────────────────


def main() -> None:
    # Check prerequisites
    has_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not has_key:
        print("ERROR: GOOGLE_API_KEY or GEMINI_API_KEY required")
        sys.exit(1)

    # Select scenarios
    if len(sys.argv) > 1:
        names = sys.argv[1:]
        scenarios = []
        for n in names:
            if n not in SCENARIO_MAP:
                print(f"ERROR: unknown scenario '{n}'")
                print(f"  Available: {', '.join(SCENARIO_MAP)}")
                sys.exit(1)
            scenarios.append(SCENARIO_MAP[n])
    else:
        scenarios = SCENARIOS

    print_banner("Architecture Decision Tests")
    print(f"  Scenarios: {', '.join(s['name'] for s in scenarios)}")

    results = []
    for scenario in scenarios:
        try:
            r = run_scenario(scenario)
            results.append(r)
        except KeyboardInterrupt:
            print("\n\nInterrupted. Printing partial scorecard.\n")
            break

    if results:
        print_scorecard(results)


if __name__ == "__main__":
    main()
