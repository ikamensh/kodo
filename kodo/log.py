"""Structured JSONL logging for run reconstruction.

Every event is a single JSON line with at least: timestamp, event, and contextual fields.
Each run gets its own directory under ~/.kodo/runs/<run_id>/.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kodo.formatting import DIM as _DIM, RESET as _RESET

_log_file: Path | None = None
_run_id: str | None = None
_start_time: float | None = None
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Per-run directory
# ---------------------------------------------------------------------------


def _runs_root() -> Path:
    """Central run storage directory: ~/.kodo/runs/ (override via KODO_RUNS_DIR)."""
    override = os.environ.get("KODO_RUNS_DIR")
    if override:
        return Path(override)
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
        return self.root / "log.jsonl"

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

    @property
    def team_file(self) -> Path:
        return self.root / "team.json"


def _extract_run_id(log_file: Path) -> str:
    """Extract run_id from a log file path (~/.kodo/runs/<run_id>/log.jsonl)."""
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
        with _lock:
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
        with _lock:
            self.orchestrator_cost_usd += cost_usd
            self.orchestrator_bucket = bucket

    def snapshot(self) -> tuple[dict[str, "_AgentStats"], float, str]:
        """Return a consistent (agents_copy, orch_cost, orch_bucket) under lock.

        Callers that iterate agent data from other threads should use this
        to avoid ``RuntimeError: dictionary changed size during iteration``.
        """
        with _lock:
            return (
                copy.deepcopy(self.agents),
                self.orchestrator_cost_usd,
                self.orchestrator_bucket,
            )


_run_stats = RunStats()


# ---------------------------------------------------------------------------
# Live progress state (read by the interactive toolbar)
# ---------------------------------------------------------------------------


@dataclass
class RunProgress:
    """Lightweight snapshot of orchestrator progress for the status bar.

    Updated by the orchestrator; read by the interactive toolbar.
    All access goes through the module-level ``_lock``.
    """

    cycle: int = 0
    max_cycles: int = 0
    stage_label: str = ""
    active_agent: str = ""

    def update(
        self,
        *,
        cycle: int | None = None,
        max_cycles: int | None = None,
        stage_label: str | None = None,
        active_agent: str | None = None,
    ) -> None:
        with _lock:
            if cycle is not None:
                self.cycle = cycle
            if max_cycles is not None:
                self.max_cycles = max_cycles
            if stage_label is not None:
                self.stage_label = stage_label
            if active_agent is not None:
                self.active_agent = active_agent

    def snapshot(self) -> tuple[int, int, str, str]:
        """Return (cycle, max_cycles, stage_label, active_agent) under lock."""
        with _lock:
            return self.cycle, self.max_cycles, self.stage_label, self.active_agent


_run_progress = RunProgress()


def get_run_progress() -> RunProgress:
    """Return the live run progress state."""
    return _run_progress


# ---------------------------------------------------------------------------
# Test helpers — used by conftest.py to avoid coupling to private vars
# ---------------------------------------------------------------------------


def _test_snapshot():
    """Capture current module state for later restoration."""
    return (
        _log_file,
        _run_id,
        _start_time,
        _runs_root,
        _run_stats,
        _virtual_cost_note_shown,
    )


def _test_restore(snapshot):
    """Restore module state from a snapshot."""
    global \
        _log_file, \
        _run_id, \
        _start_time, \
        _runs_root, \
        _run_stats, \
        _virtual_cost_note_shown
    (
        _log_file,
        _run_id,
        _start_time,
        _runs_root,
        _run_stats,
        _virtual_cost_note_shown,
    ) = snapshot


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
    global \
        _log_file, \
        _run_id, \
        _start_time, \
        _run_stats, \
        _run_progress, \
        _virtual_cost_note_shown, \
        _last_table_time

    from kodo import __version__

    with _lock:
        if _run_id == run_dir.run_id and _log_file is not None:
            return _log_file

        _run_id = run_dir.run_id
        _start_time = time.monotonic()
        _run_stats = RunStats()
        _run_progress = RunProgress()
        _virtual_cost_note_shown = False
        _last_table_time = 0.0

        run_dir.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        run_dir.root.chmod(0o700)  # defensive; umask may override mode
        _log_file = run_dir.log_file

    emit("run_init", project_dir=str(run_dir.project_dir), version=__version__)
    return _log_file


def get_run_stats() -> RunStats:
    """Return the live run statistics accumulator."""
    with _lock:
        return _run_stats


def get_elapsed_s() -> float | None:
    """Return seconds since run start, or None if not started."""
    with _lock:
        if _start_time is None:
            return None
        return time.monotonic() - _start_time


def get_log_file() -> Path | None:
    """Return the current log file path, or None if not initialized."""
    with _lock:
        return _log_file


def emit(event: str, **data: Any) -> None:
    """Write a single log event."""
    with _lock:
        if _log_file is None:
            return

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "t": round(time.monotonic() - (_start_time or 0), 3),
            "event": event,
            **data,
        }

        try:
            with open(_log_file, "a") as f:
                f.write(json.dumps(record, default=_serialize) + "\n")
        except OSError:
            pass  # best-effort logging; don't crash on write failure


def save_conversation(
    agent_name: str, query_index: int, messages: list[dict]
) -> str | None:
    """Save full conversation messages to a gzip file alongside the run log.

    Returns the filename (relative to run dir) or None if logging is not active.
    Conversation logs go into a ``conversations/`` subdirectory.
    """
    import gzip

    with _lock:
        log_file = _log_file
    if log_file is None:
        return None

    try:
        conv_dir = log_file.parent / "conversations"
        conv_dir.mkdir(exist_ok=True)
        fname = f"{agent_name}_{query_index:03d}.jsonl.gz"
        data = "\n".join(json.dumps(m, default=_serialize) for m in messages)
        (conv_dir / fname).write_bytes(gzip.compress(data.encode()))
        return f"conversations/{fname}"
    except Exception:
        return None  # best-effort


def tprint(msg: str) -> None:
    """Print with elapsed time prefix, e.g. '[  12.3s] ...'."""
    with _lock:
        st = _start_time
    if st is not None:
        elapsed = time.monotonic() - st
        print(f"  {_DIM}[{elapsed:7.1f}s]{_RESET} {msg}", flush=True)
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
_last_table_time: float = 0.0
_TABLE_MIN_INTERVAL: float = 10.0  # seconds between progress tables


def print_stats_table(final: bool = False) -> None:
    """Print a compact stats table to the terminal.

    Called periodically during a run and once at termination.
    Non-final tables are throttled to at most once per 10 seconds.
    Uses a snapshot of RunStats to avoid races with concurrent record_agent().
    """
    global _virtual_cost_note_shown, _last_table_time

    with _lock:
        if not final:
            now = time.monotonic()
            if now - _last_table_time < _TABLE_MIN_INTERVAL:
                return
            _last_table_time = now
        stats = _run_stats
        start = _start_time

    # Take a consistent snapshot of agent data to avoid RuntimeError from
    # concurrent dict modification during parallel stages.
    agents, orch_cost, orch_bucket = stats.snapshot()
    if not agents:
        return

    elapsed = time.monotonic() - (start or time.monotonic())

    # Header — W is the inner width between │ bars, derived from data rows.
    #   data row = " " + 20 + " " + 10 + " " + 3 + " " + 7 + " " + 5
    #            + " " + 5 + " " + 6 + " " + 3 + " " = 68 chars
    W = 68
    label = "📊 FINAL STATS" if final else "📊 PROGRESS"
    # Emoji takes 2 display columns but Python counts 1 char → pad W-3.
    print(f"\n  ┌{'─' * W}┐")
    print(f"  │ {label:<{W - 3}} │")
    print(
        f"  │ {'Agent':<20} {'Bucket':<10} {'#':>3} {'Cost':>7}"
        f" {'In':>5} {'Out':>5} {'Time':>6} {'Err':>3} │",
    )
    print(f"  ├{'─' * W}┤")

    for agent, s in sorted(agents.items()):
        has_tokens = s.cost_bucket not in ("cursor_subscription",)
        in_tok = _fmt_tokens(s.input_tokens) if has_tokens else "-"
        out_tok = _fmt_tokens(s.output_tokens) if has_tokens else "-"
        print(
            f"  │ {_trunc(agent, 20):<20} {_trunc(_bucket_label(s.cost_bucket), 10):<10}"
            f" {s.calls:>3} {_fmt_cost(s.cost_usd):>7}"
            f" {in_tok:>5} {out_tok:>5}"
            f" {_fmt_time(s.elapsed_s):>6} {s.errors:>3} │",
        )

    # Orchestrator row
    if orch_cost > 0:
        print(
            f"  │ {'orchestrator':<20} {_trunc(_bucket_label(orch_bucket), 10):<10}"
            f" {'':>3} {_fmt_cost(orch_cost):>7}"
            f" {'':>5} {'':>5} {'':>6} {'':>3} │",
        )

    print(f"  ├{'─' * W}┤")

    # Totals by bucket — compute from the snapshot
    total_exch = sum(s.calls for s in agents.values())
    buckets: dict[str, float] = defaultdict(float)
    for s in agents.values():
        buckets[s.cost_bucket] += s.cost_usd
    buckets[orch_bucket] += orch_cost
    api = buckets.get("api", 0)
    sub = sum(v for k, v in buckets.items() if k != "api")
    total = sum(s.cost_usd for s in agents.values()) + orch_cost
    print(
        f"  │ {'Total':<20} {'':10} {total_exch:>3}"
        f" {_fmt_cost(total):>7} {'':>5} {'':>5}"
        f" {_fmt_time(elapsed):>6} {'':>3} │",
    )
    print(
        f"  │   API: {_fmt_cost(api):<7}"
        f"  Virtual: {_fmt_cost(sub):<7}"
        f"  Wall: {_fmt_time(elapsed):<27}│",
    )
    print(f"  └{'─' * W}┘")
    if not _virtual_cost_note_shown and sub > 0:
        print(
            "    Virtual = Claude Code's API cost estimate."
            " Not charged on Max/Pro subscriptions.",
        )
        _virtual_cost_note_shown = True
    print()


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
    is_debug: bool = False


def parse_run(log_file: Path) -> RunState | None:
    """Parse a JSONL log file into a RunState. Returns None if no run_start found."""
    run_start: dict | None = None
    cli_args: dict | None = None
    completed_cycles = 0
    last_summary = ""
    finished = False
    is_debug = False
    agent_session_ids: dict[str, str] = {}
    # Stage tracking
    has_stages = False
    completed_stages: list[int] = []
    stage_summaries: list[str] = []
    current_stage_cycles = 0
    current_stage_index: int | None = None

    try:
        fh = open(log_file, encoding="utf-8", errors="replace")
    except PermissionError:
        logging.warning("Cannot read %s (permission denied), skipping run", log_file)
        return None

    with fh:
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
            elif event == "debug_run_start":
                is_debug = True
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

    if cli_args is None:
        return None

    # When run_start exists, use it as primary source; otherwise
    # reconstruct from cli_args (run failed before orchestrator started,
    # e.g. team config validation error after refinement).
    if run_start is not None:
        try:
            goal = run_start["goal"]
        except KeyError:
            return None
        orchestrator = run_start.get("orchestrator", "unknown")
        model = run_start.get("model", "unknown")
        project_dir = run_start.get("project_dir", "")
        max_exchanges = run_start.get("max_exchanges", 0)
        max_cycles = run_start.get("max_cycles", 0)
        team_list = run_start.get("team", [])
    else:
        goal = cli_args.get("goal_text", "")
        if not goal:
            # Pre-launch crash: goal_text not yet known — try goal.md in run dir
            goal_file = log_file.parent / "goal.md"
            if goal_file.exists():
                try:
                    goal = goal_file.read_text(encoding="utf-8").strip()
                except OSError:
                    pass
        if not goal:
            return None
        orchestrator = cli_args.get("orchestrator", "unknown")
        model = cli_args.get("orchestrator_model", "unknown")
        project_dir = cli_args.get("project_dir", "")
        max_exchanges = cli_args.get("max_exchanges", 0)
        max_cycles = cli_args.get("max_cycles", 0)
        team_list = []

    return RunState(
        run_id=_extract_run_id(log_file),
        log_file=log_file,
        goal=goal,
        orchestrator=orchestrator,
        model=model,
        project_dir=project_dir,
        max_exchanges=max_exchanges,
        max_cycles=max_cycles,
        team=team_list,
        completed_cycles=completed_cycles,
        last_summary=last_summary,
        finished=finished,
        agent_session_ids=agent_session_ids,
        team_preset=cli_args.get("team", cli_args.get("mode", "full")),
        has_stages=has_stages,
        completed_stages=completed_stages,
        stage_summaries=stage_summaries,
        current_stage_cycles=current_stage_cycles,
        is_debug=is_debug,
    )


def _extract_project_dir(log_file: Path) -> str | None:
    """Read first few lines to extract project_dir without full parse."""
    try:
        with open(log_file, encoding="utf-8", errors="replace") as fh:
            for i, raw_line in enumerate(fh):
                if i > 10:
                    break
                try:
                    evt = json.loads(raw_line)
                except (json.JSONDecodeError, ValueError):
                    continue
                event = evt.get("event")
                if event in ("run_start", "cli_args"):
                    return evt.get("project_dir", "")
    except (OSError, PermissionError):
        pass
    return None


def list_runs(project_dir: Path | None = None) -> list[RunState]:
    """Return all parsed runs, newest first. Optionally filter by *project_dir*."""
    candidates: list[Path] = []
    runs_dir = _runs_root()
    if runs_dir.exists():
        try:
            entries = sorted(runs_dir.iterdir(), reverse=True)
        except PermissionError:
            logging.warning("Cannot list %s (permission denied)", runs_dir)
            return []
        for d in entries:
            if d.is_dir():
                f = d / "log.jsonl"
                if not f.exists():
                    f = d / "run.jsonl"  # legacy
                if f.exists():
                    candidates.append(f)

    resolved = str(project_dir.resolve()) if project_dir else None
    runs: list[RunState] = []
    for f in candidates:
        # Pre-filter by project_dir without full parse
        if resolved:
            run_project = _extract_project_dir(f)
            if run_project is not None and run_project != resolved:
                continue
        try:
            state = parse_run(f)
        except OSError as exc:
            tprint(f"  Warning: cannot read {f}: {exc}")
            continue
        if state is None:
            continue
        if resolved and state.project_dir != resolved:
            continue
        runs.append(state)
    return runs


def find_incomplete_runs(project_dir: Path) -> list[RunState]:
    """Scan ~/.kodo/runs/ for incomplete runs belonging to *project_dir*, newest first.

    An incomplete run is one that hasn't finished — either it started cycles
    but didn't complete, or it failed before the first cycle (e.g. team config
    error after refinement).
    """
    return [r for r in list_runs(project_dir) if not r.finished]


def init_append(log_file: Path) -> Path:
    """Set module globals to append to an existing log file. Emits run_resumed marker.

    Validates that the file exists and is a parseable kodo log (has a run_start
    event) before mutating global state.  Raises ``FileNotFoundError`` if the
    file is missing or ``ValueError`` if it is not a valid kodo run log.
    """
    global _log_file, _run_id, _start_time, _run_stats, _last_table_time

    if not log_file.exists():
        raise FileNotFoundError(f"Log file does not exist: {log_file}")

    # Validate that the file is a real kodo log (parse_run returns None on
    # invalid / missing run_start).
    state = parse_run(log_file)
    if state is None:
        raise ValueError(
            f"Not a valid kodo log (missing run_start or cli_args): {log_file}"
        )
    _ensure_jsonl_line_boundary(log_file)

    with _lock:
        _log_file = log_file
        _run_id = _extract_run_id(log_file)
        _start_time = time.monotonic()
        _run_stats = RunStats()
        _last_table_time = 0.0

    emit("run_resumed", log_file=str(log_file))
    return log_file


def _ensure_jsonl_line_boundary(log_file: Path) -> None:
    """Append one newline when a crash left the last JSONL record unterminated."""
    with open(log_file, "rb+") as fh:
        fh.seek(0, os.SEEK_END)
        if fh.tell() == 0:
            return
        fh.seek(-1, os.SEEK_END)
        if fh.read(1) != b"\n":
            fh.seek(0, os.SEEK_END)
            fh.write(b"\n")


def _serialize(obj: Any) -> Any:
    """JSON fallback serializer."""
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    try:
        return repr(obj)
    except Exception:
        return f"<{type(obj).__name__} unserializable>"
