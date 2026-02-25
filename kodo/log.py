"""Structured JSONL logging for run reconstruction.

Every event is a single JSON line with at least: timestamp, event, and contextual fields.
Each run gets its own directory under ~/.kodo/runs/<run_id>/.
"""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log_file: Path | None = None
_run_id: str | None = None
_start_time: float | None = None
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Per-run directory
# ---------------------------------------------------------------------------


def _runs_root() -> Path:
    """Central run storage directory: ~/.kodo/runs/."""
    return Path.home() / ".kodo" / "runs"


@dataclass
class RunDir:
    """Path accessor for a single run's folder under ~/.kodo/runs/<run_id>/."""

    project_dir: Path
    run_id: str

    @staticmethod
    def create(project_dir: Path, run_id: str | None = None) -> "RunDir":
        rid = run_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        rd = RunDir(project_dir=project_dir, run_id=rid)
        rd.root.mkdir(parents=True, exist_ok=True)
        rd.root.chmod(0o700)
        return rd

    @staticmethod
    def from_log_file(log_file: Path, project_dir: Path) -> "RunDir":
        """Construct a RunDir from an existing log file path."""
        return RunDir(project_dir=project_dir, run_id=_extract_run_id(log_file))

    @property
    def root(self) -> Path:
        return _runs_root() / self.run_id

    @property
    def log_file(self) -> Path:
        return self.root / "run.jsonl"

    @property
    def goal_file(self) -> Path:
        return self.root / "goal.md"

    @property
    def goal_refined_file(self) -> Path:
        return self.root / "goal-refined.md"

    @property
    def goal_plan_file(self) -> Path:
        return self.root / "goal-plan.json"

    @property
    def config_file(self) -> Path:
        return self.root / "config.json"


def _extract_run_id(log_file: Path) -> str:
    """Extract run_id from a log file path (~/.kodo/runs/<run_id>/run.jsonl)."""
    return log_file.parent.name


# ---------------------------------------------------------------------------
# Live run statistics
# ---------------------------------------------------------------------------


@dataclass
class _AgentStats:
    calls: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_s: float = 0.0
    errors: int = 0
    cost_bucket: str = ""


@dataclass
class RunStats:
    """Accumulates per-agent and per-bucket cost stats during a run."""

    agents: dict[str, _AgentStats] = field(default_factory=dict)
    orchestrator_cost_usd: float = 0.0
    orchestrator_bucket: str = "api"

    def record_agent(
        self,
        agent: str,
        cost_usd: float,
        input_tokens: int,
        output_tokens: int,
        elapsed_s: float,
        is_error: bool,
        cost_bucket: str,
    ) -> None:
        if agent not in self.agents:
            self.agents[agent] = _AgentStats(cost_bucket=cost_bucket)
        s = self.agents[agent]
        s.calls += 1
        s.cost_usd += cost_usd
        s.input_tokens += input_tokens
        s.output_tokens += output_tokens
        s.elapsed_s += elapsed_s
        if is_error:
            s.errors += 1
        if cost_bucket:
            s.cost_bucket = cost_bucket

    def record_orchestrator(self, cost_usd: float, bucket: str = "api") -> None:
        self.orchestrator_cost_usd += cost_usd
        self.orchestrator_bucket = bucket

    @property
    def total_exchanges(self) -> int:
        return sum(s.calls for s in self.agents.values())

    def cost_by_bucket(self) -> dict[str, float]:
        buckets: dict[str, float] = defaultdict(float)
        for s in self.agents.values():
            buckets[s.cost_bucket] += s.cost_usd
        buckets[self.orchestrator_bucket] += self.orchestrator_cost_usd
        return dict(buckets)

    def total_cost(self) -> float:
        return (
            sum(s.cost_usd for s in self.agents.values()) + self.orchestrator_cost_usd
        )


_run_stats = RunStats()


# ---------------------------------------------------------------------------
# Test helpers — used by conftest.py to avoid coupling to private vars
# ---------------------------------------------------------------------------


def _test_snapshot():
    """Capture current module state for later restoration."""
    return (_log_file, _run_id, _start_time, _runs_root)


def _test_restore(snapshot):
    """Restore module state from a snapshot."""
    global _log_file, _run_id, _start_time, _runs_root
    _log_file, _run_id, _start_time, _runs_root = snapshot


def _test_redirect_runs(path: Path):
    """Redirect _runs_root() to a temporary directory for test isolation."""
    global _runs_root

    def _redirected() -> Path:
        return path

    _runs_root = _redirected  # type: ignore[assignment]


def init(run_dir: RunDir) -> Path:
    """Initialize logging for a run. Returns the log file path.

    Safe to call multiple times for the same run — only resets on the first
    call or when the run_id changes.
    """
    global _log_file, _run_id, _start_time, _run_stats, _virtual_cost_note_shown

    from kodo import __version__

    if _run_id == run_dir.run_id and _log_file is not None:
        return _log_file

    _run_id = run_dir.run_id
    _start_time = time.monotonic()
    _run_stats = RunStats()
    _virtual_cost_note_shown = False

    run_dir.root.mkdir(parents=True, exist_ok=True)
    run_dir.root.chmod(0o700)
    _log_file = run_dir.log_file

    emit("run_init", project_dir=str(run_dir.project_dir), version=__version__)
    return _log_file


def get_run_stats() -> RunStats:
    """Return the live run statistics accumulator."""
    return _run_stats


def get_log_file() -> Path | None:
    """Return the current log file path, or None if not initialized."""
    return _log_file


def emit(event: str, **data: Any) -> None:
    """Write a single log event."""
    if _log_file is None:
        return

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "t": round(time.monotonic() - (_start_time or 0), 3),
        "event": event,
        **data,
    }

    with _lock:
        try:
            with open(_log_file, "a") as f:
                f.write(json.dumps(record, default=_serialize) + "\n")
        except OSError:
            pass  # best-effort logging; don't crash on write failure


def tprint(msg: str) -> None:
    """Print with elapsed time prefix, e.g. '[  12.3s] ...'."""
    if _start_time is not None:
        elapsed = time.monotonic() - _start_time
        print(f"  [{elapsed:7.1f}s] {msg}", flush=True)
    else:
        print(f"  {msg}", flush=True)


def _fmt_time(s: float) -> str:
    if s < 60:
        return f"{s:.0f}s"
    m = int(s // 60)
    sec = s % 60
    if m < 60:
        return f"{m}m{sec:02.0f}s"
    return f"{m // 60}h{m % 60:02d}m"


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.0f}k"
    return str(n)


def _fmt_cost(c: float) -> str:
    if c < 0.005:
        return "  -"
    return f"${c:.2f}"


def _bucket_label(b: str) -> str:
    return {
        "api": "API",
        "claude_subscription": "CC Sub",
        "codex_subscription": "Codex",
        "cursor_subscription": "Cursor",
        "gemini_api": "Gemini API",
    }.get(b, b)


def _trunc(s: str, width: int) -> str:
    """Truncate *s* to *width*, replacing the last char with '~' if trimmed."""
    if len(s) <= width:
        return s
    return s[: width - 1] + "~"


_virtual_cost_note_shown = False


def print_stats_table(final: bool = False) -> None:
    """Print a compact stats table to the terminal.

    Called periodically during a run and once at termination.
    """
    global _virtual_cost_note_shown
    stats = _run_stats
    if not stats.agents:
        return

    elapsed = time.monotonic() - (_start_time or time.monotonic())

    # Header
    sep = "-" * 70
    label = "📊 FINAL STATS" if final else "📊 PROGRESS"
    print(f"\n  {sep}")
    print(f"  | {label:<66} |")
    print(
        f"  | {'Agent':<20} {'Bucket':<10} {'#':>3} {'Cost':>7}"
        f" {'In':>5} {'Out':>5} {'Time':>6} {'Err':>3} |"
    )
    print(f"  |{sep[1:-1]}|")

    for agent, s in sorted(stats.agents.items()):
        has_tokens = s.cost_bucket not in ("cursor_subscription",)
        in_tok = _fmt_tokens(s.input_tokens) if has_tokens else "-"
        out_tok = _fmt_tokens(s.output_tokens) if has_tokens else "-"
        print(
            f"  | {_trunc(agent, 20):<20} {_trunc(_bucket_label(s.cost_bucket), 10):<10}"
            f" {s.calls:>3} {_fmt_cost(s.cost_usd):>7}"
            f" {in_tok:>5} {out_tok:>5}"
            f" {_fmt_time(s.elapsed_s):>6} {s.errors:>3} |"
        )

    # Orchestrator row
    if stats.orchestrator_cost_usd > 0:
        print(
            f"  | {'orchestrator':<20} {_trunc(_bucket_label(stats.orchestrator_bucket), 10):<10}"
            f" {'':>3} {_fmt_cost(stats.orchestrator_cost_usd):>7}"
            f" {'':>5} {'':>5} {'':>6} {'':>3} |"
        )

    print(f"  |{sep[1:-1]}|")

    # Totals by bucket
    buckets = stats.cost_by_bucket()
    api = buckets.get("api", 0)
    sub = sum(v for k, v in buckets.items() if k != "api")
    total = stats.total_cost()
    print(
        f"  | {'Total':<20} {'':10} {stats.total_exchanges:>3}"
        f" {_fmt_cost(total):>7} {'':>5} {'':>5}"
        f" {_fmt_time(elapsed):>6} {'':>3} |"
    )
    print(
        f"  |   API: {_fmt_cost(api):<7}"
        f"  Virtual: {_fmt_cost(sub):<7}"
        f"  Wall: {_fmt_time(elapsed):<27}|"
    )
    print(f"  {sep}")
    if not _virtual_cost_note_shown and sub > 0:
        print(
            "    Virtual = Claude Code's API cost estimate."
            " Not charged on Max/Pro subscriptions."
        )
        _virtual_cost_note_shown = True
    print()


def get_run_id() -> str | None:
    """Return the current run ID, or None if not initialized."""
    return _run_id


# ---------------------------------------------------------------------------
# JSONL log parsing for resume support
# ---------------------------------------------------------------------------


@dataclass
class RunState:
    """Parsed state of a run from its JSONL log file."""

    run_id: str
    log_file: Path
    goal: str
    orchestrator: str
    model: str
    project_dir: str
    max_exchanges: int
    max_cycles: int
    team: list[str]
    completed_cycles: int
    last_summary: str
    finished: bool
    agent_session_ids: dict[str, str]  # agent_name → last session_id
    team_preset: str
    has_stages: bool
    completed_stages: list[int]
    stage_summaries: list[str]
    current_stage_cycles: int
    pending_exchanges: list[dict] = field(default_factory=list)


def parse_run(log_file: Path) -> RunState | None:
    """Parse a JSONL log file into a RunState. Returns None if no run_start found."""
    run_start: dict | None = None
    cli_args: dict | None = None
    completed_cycles = 0
    last_summary = ""
    finished = False
    agent_session_ids: dict[str, str] = {}
    # Stage tracking
    has_stages = False
    completed_stages: list[int] = []
    stage_summaries: list[str] = []
    current_stage_cycles = 0
    current_stage_index: int | None = None

    with open(log_file, encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            try:
                evt = json.loads(raw_line)
            except (json.JSONDecodeError, ValueError):
                continue  # tolerate corrupt lines

            event = evt.get("event")
            if event == "run_start":
                run_start = evt
                if evt.get("has_stages"):
                    has_stages = True
            elif event == "cli_args":
                cli_args = evt
            elif event == "cycle_end":
                completed_cycles += 1
                last_summary = evt.get("summary", "")
                if current_stage_index is not None:
                    current_stage_cycles += 1
            elif event == "run_end":
                finished = True
            elif event == "stage_start":
                current_stage_index = evt.get("stage_index")
                current_stage_cycles = 0
            elif event == "stage_end":
                stage_idx = evt.get("stage_index")
                if stage_idx is not None and evt.get("finished", False):
                    completed_stages.append(stage_idx)
                    stage_summaries.append(evt.get("summary", ""))
                current_stage_index = None
                current_stage_cycles = 0
            elif event == "session_query_end":
                sid = evt.get("session_id") or evt.get("chat_id")
                session_name = evt.get("session")
                if sid and session_name:
                    agent_session_ids[session_name] = sid
            elif event == "orchestrator_tool_call":
                pass
            elif event == "agent_run_end":
                agent_name = evt.get("agent")
                if agent_name:
                    for key in list(agent_session_ids.keys()):
                        if key in ("claude", "codex", "cursor", "gemini-cli"):
                            agent_session_ids[agent_name] = agent_session_ids.pop(key)

    if run_start is None or cli_args is None:
        return None

    # Require critical fields; return None if corrupted run_start
    try:
        goal = run_start["goal"]
    except KeyError:
        return None

    return RunState(
        run_id=_extract_run_id(log_file),
        log_file=log_file,
        goal=goal,
        orchestrator=run_start.get("orchestrator", "unknown"),
        model=run_start.get("model", "unknown"),
        project_dir=run_start.get("project_dir", ""),
        max_exchanges=run_start.get("max_exchanges", 0),
        max_cycles=run_start.get("max_cycles", 0),
        team=run_start.get("team", []),
        completed_cycles=completed_cycles,
        last_summary=last_summary,
        finished=finished,
        agent_session_ids=agent_session_ids,
        team_preset=cli_args.get("team", cli_args.get("mode", "saga")),
        has_stages=has_stages,
        completed_stages=completed_stages,
        stage_summaries=stage_summaries,
        current_stage_cycles=current_stage_cycles,
    )


def list_runs(project_dir: Path | None = None) -> list[RunState]:
    """Return all parsed runs, newest first. Optionally filter by *project_dir*."""
    candidates: list[Path] = []
    runs_dir = _runs_root()
    if runs_dir.exists():
        for d in sorted(runs_dir.iterdir(), reverse=True):
            if d.is_dir():
                f = d / "run.jsonl"
                if f.exists():
                    candidates.append(f)

    resolved = str(project_dir.resolve()) if project_dir else None
    runs: list[RunState] = []
    for f in candidates:
        state = parse_run(f)
        if state is None:
            continue
        if resolved and state.project_dir != resolved:
            continue
        runs.append(state)
    return runs


def find_incomplete_runs(project_dir: Path) -> list[RunState]:
    """Scan ~/.kodo/runs/ for incomplete runs belonging to *project_dir*, newest first.

    An incomplete run has a run_start + at least 1 cycle_end but no run_end.
    """
    return [
        r for r in list_runs(project_dir) if not r.finished and r.completed_cycles >= 1
    ]


def init_append(log_file: Path) -> Path:
    """Set module globals to append to an existing log file. Emits run_resumed marker."""
    global _log_file, _run_id, _start_time, _run_stats

    _log_file = log_file
    _run_id = _extract_run_id(log_file)
    _start_time = time.monotonic()
    _run_stats = RunStats()

    emit("run_resumed", log_file=str(log_file))
    return log_file


def _serialize(obj: Any) -> Any:
    """JSON fallback serializer."""
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    return repr(obj)
