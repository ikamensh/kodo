"""Shared helpers for the benchmark package."""

from __future__ import annotations

import json
import logging
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path

log = logging.getLogger("benchmark")

# Arm name sanitization — used for filenames and Docker container names.
# Reversible: ":" → "--" (unlike the old ":" → "_" which was lossy).
_UNSAFE_RE = re.compile(r"[^a-zA-Z0-9_.-]")


def fmt_duration(seconds: int) -> str:
    """Format seconds as human-readable duration: 7200 -> '2h', 300 -> '5m'."""
    if seconds >= 3600:
        h = seconds / 3600
        return f"{h:.0f}h" if h == int(h) else f"{h:.1f}h"
    if seconds >= 60:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def short_iid(instance_id: str) -> str:
    """Shorten instance_id for display: 'django__django-13195' -> 'django/django#13195'."""
    parts = instance_id.split("__", 1)
    if len(parts) != 2:
        return instance_id
    owner = parts[0].replace("_", "-")
    rest = parts[1]
    dash_idx = rest.rfind("-")
    if dash_idx > 0:
        repo = rest[:dash_idx].replace("_", "-")
        issue = rest[dash_idx + 1:]
        # Truncate long hashes (e.g. Go SWE-bench commit SHAs)
        if len(issue) > 12:
            issue = issue[:8]
        return f"{owner}/{repo}#{issue}"
    return f"{owner}/{rest}"


def docker_safe(name: str) -> str:
    """Replace chars invalid in Docker container names with underscores."""
    return _UNSAFE_RE.sub("_", name)


def load_json(path: Path) -> dict:
    """Load a JSON file, returning {} on missing/corrupt files."""
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            log.warning("Failed to parse %s", path)
    return {}


def load_jsonl(path: Path) -> list[dict]:
    """Load a JSONL file line-by-line, skipping bad lines."""
    results: list[dict] = []
    if not path.exists():
        return results
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                log.warning("Skipping bad JSONL line in %s", path)
    return results


def iter_jsonl(path: Path):
    """Iterate over JSONL lines without loading all into memory."""
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                log.warning("Skipping bad JSONL line in %s", path)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging for benchmark runs."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=level,
    )


# CLI tool name → arm name(s).  kodo is always available (it's this project).
_BACKEND_CLI_MAP: list[tuple[str, list[str]]] = [
    ("claude", ["claude"]),
    ("cursor-agent", ["cursor"]),
    ("codex", ["codex"]),
    ("gemini", ["gemini"]),
]


def ensure_docker_running(timeout: int = 60) -> bool:
    """Check if Docker daemon is running; attempt to start it if not.

    Returns True if Docker is available, False otherwise.
    On macOS, tries OrbStack first, then Docker Desktop.
    """
    if _docker_is_ready():
        return True

    log.info("Docker daemon is not running. Attempting to start...")

    if platform.system() != "Darwin":
        log.warning("Docker is not running. Start it manually:\n"
                     "  sudo systemctl start docker")
        return False

    if not _start_docker_macos():
        log.error("Could not start Docker. Please start it manually.")
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _docker_is_ready():
            log.info("Docker is now running.")
            return True
        time.sleep(2)

    log.error("Docker did not become ready within %ds.", timeout)
    return False


def _docker_is_ready() -> bool:
    """Return True if ``docker info`` succeeds."""
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _start_docker_macos() -> bool:
    """Try to start Docker on macOS via OrbStack or Docker Desktop."""
    if shutil.which("orbctl"):
        log.info("Starting Docker via OrbStack...")
        try:
            subprocess.run(["orbctl", "start"], check=True, timeout=30)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            log.warning("OrbStack start failed, trying Docker Desktop...")

    try:
        subprocess.run(["open", "-a", "Docker"], check=True, timeout=10)
        log.info("Starting Docker Desktop (this may take 30-60s)...")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def detect_backends() -> list[str]:
    """Auto-detect which benchmark backends are available on this machine.

    Checks PATH for each CLI tool.  ``kodo`` is always included since it's
    the project itself (runs via ``uv run kodo``).
    """
    found: list[str] = ["kodo"]
    for cli_name, arm_names in _BACKEND_CLI_MAP:
        if shutil.which(cli_name):
            found.extend(arm_names)
    return found
