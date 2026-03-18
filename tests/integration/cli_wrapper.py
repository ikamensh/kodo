"""CLI wrapper for integration tests."""

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

_PROJECT_DIR = Path(__file__).resolve().parents[2]


@dataclass
class CliResult:
    exit_code: int
    stdout: str
    stderr: str
    command: list[str]
    duration: float

    def success(self) -> bool:
        return self.exit_code == 0

    def output(self) -> str:
        return self.stdout + self.stderr


def run_kodo(
    *args: str,
    timeout: int = 30,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
) -> CliResult:
    command = ["uv", "run", "kodo"] + list(args)
    work_dir = Path(cwd) if cwd is not None else _PROJECT_DIR
    merged_env = {**os.environ, **(env or {})}

    start = time.perf_counter()
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=work_dir,
            env=merged_env,
        )
        duration = time.perf_counter() - start
        return CliResult(
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            command=command,
            duration=duration,
        )
    except subprocess.TimeoutExpired:
        duration = time.perf_counter() - start
        return CliResult(
            exit_code=-1,
            stdout="",
            stderr=f"Command timed out after {timeout}s",
            command=command,
            duration=duration,
        )
