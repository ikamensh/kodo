"""Readiness checks for running kodo in the current project."""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from kodo.factory import BackendDefinition, backend_definitions, check_backend_status
from kodo.sessions.base import _AUTH_PATTERNS, _SUBSCRIPTION_PATTERNS


Status = Literal["ok", "missing", "broken"]

_AUTH_STATUS_PATTERNS = re.compile(
    r"not logged in|not signed in|login required|sign in required",
    re.IGNORECASE,
)
_UNSUPPORTED_AUTH_STATUS_PATTERNS = re.compile(
    r"unknown (command|subcommand)|unrecognized (command|argument)"
    r"|invalid choice|no such command|unexpected argument|usage:",
    re.IGNORECASE,
)
_AUTH_NOISE_PATTERNS = re.compile(
    r"^loaded cached credentials\.?$|^\s*at .+|^\s*[{}]\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: Status
    detail: str
    usable_backend: bool = False

    def line(self) -> str:
        return f"{self.name}: {self.status} - {self.detail}"


def run_doctor(cwd: Path | None = None) -> tuple[list[DoctorCheck], int]:
    """Return doctor checks and process exit code.

    The command succeeds only when the local project checks pass and at least
    one backend passes every cheap check available for that backend.
    """
    cwd = Path.cwd() if cwd is None else cwd
    checks = [_check_python(), _check_git(cwd)]
    backend_checks = [_check_backend(definition) for definition in backend_definitions()]
    checks.extend(backend_checks)

    local_ready = all(check.status == "ok" for check in checks[:2])
    backend_ready = any(check.usable_backend for check in backend_checks)
    return checks, 0 if local_ready and backend_ready else 1


def _check_python() -> DoctorCheck:
    version = platform.python_version()
    if sys.version_info < (3, 13):
        return DoctorCheck(
            "python",
            "broken",
            f"{version}; kodo requires Python 3.13+",
        )
    return DoctorCheck("python", "ok", version)


def _check_git(cwd: Path) -> DoctorCheck:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except FileNotFoundError:
        return DoctorCheck("git", "missing", "git not found; install Git")
    except subprocess.TimeoutExpired:
        return DoctorCheck("git", "broken", "git check timed out")
    except OSError as exc:
        return DoctorCheck("git", "broken", str(exc))

    if proc.returncode == 0 and proc.stdout.strip() == "true":
        return DoctorCheck("git", "ok", "current directory is a git repo")
    return DoctorCheck("git", "broken", "not a git repo; run from a project checkout")


def _check_backend(definition: BackendDefinition) -> DoctorCheck:
    if shutil.which(definition.binary) is None:
        return DoctorCheck(
            definition.key,
            "missing",
            f"{definition.binary} not on PATH; {definition.install_hint}",
        )

    version, warning = check_backend_status(definition.key)
    if warning:
        hint = _hint_for_warning(warning, definition)
        return DoctorCheck(definition.key, "broken", f"{version}; {hint}")

    auth_warning = _check_auth_status(definition)
    if auth_warning is not None:
        return DoctorCheck(definition.key, "broken", auth_warning)

    return DoctorCheck(definition.key, "ok", version, usable_backend=True)


def _hint_for_warning(warning: str, definition: BackendDefinition) -> str:
    if "authentication" in warning.lower():
        return f"not logged in; {definition.login_hint}"
    return warning


def _check_auth_status(definition: BackendDefinition) -> str | None:
    for cmd in definition.auth_status_cmds:
        try:
            proc = subprocess.run(
                list(cmd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
        except FileNotFoundError:
            return f"{definition.binary} disappeared from PATH; check your shell PATH"
        except subprocess.TimeoutExpired:
            return f"auth check timed out; {definition.login_hint}"
        except OSError as exc:
            return f"auth check failed: {exc}"

        combined = f"{proc.stderr}\n{proc.stdout}"
        if _AUTH_PATTERNS.search(combined) or _AUTH_STATUS_PATTERNS.search(combined):
            return f"not logged in; {definition.login_hint}"
        if _SUBSCRIPTION_PATTERNS.search(combined):
            return "quota/billing issue; check your account status"
        if proc.returncode == 0:
            return None
        if _UNSUPPORTED_AUTH_STATUS_PATTERNS.search(combined):
            continue

        snippet = _first_meaningful_line(combined)
        detail = f"auth check failed (exit {proc.returncode})"
        if snippet:
            detail = f"{detail}: {snippet}"
        return f"{detail}; {definition.login_hint}"

    return None


def _first_meaningful_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or _AUTH_NOISE_PATTERNS.search(stripped):
            continue
        return stripped[:180]
    return ""


def print_doctor(cwd: Path | None = None) -> int:
    checks, exit_code = run_doctor(cwd)
    for check in checks:
        print(check.line())
    return exit_code
