"""Tests for the `kodo doctor` readiness checks."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from kodo.cli._main import _main_inner
from kodo.doctor import run_doctor


def _proc(cmd: list[str], returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)


def test_doctor_succeeds_with_one_usable_backend(tmp_path: Path):
    """A single installed, authenticated backend is enough for a ready repo."""

    def fake_which(binary: str) -> str | None:
        return f"/usr/bin/{binary}" if binary == "codex" else None

    def fake_run(cmd, **kwargs):
        if cmd == ["git", "rev-parse", "--is-inside-work-tree"]:
            return _proc(cmd, 0, stdout="true\n")
        if cmd == ["codex", "--version"]:
            return _proc(cmd, 0, stdout="codex 1.0.0\n")
        if cmd == ["codex", "auth", "status"]:
            return _proc(cmd, 0, stdout="logged in\n")
        raise AssertionError(f"unexpected command: {cmd}")

    with (
        patch("kodo.doctor.shutil.which", autospec=True, side_effect=fake_which),
        patch("subprocess.run", autospec=True, side_effect=fake_run),
    ):
        checks, exit_code = run_doctor(tmp_path)

    assert exit_code == 0
    lines = [check.line() for check in checks]
    assert any(line.startswith("git: ok") for line in lines)
    assert any(line.startswith("codex: ok - codex 1.0.0") for line in lines)
    assert any(line.startswith("claude: missing") for line in lines)


def test_doctor_fails_without_usable_backend(tmp_path: Path):
    """No backend on PATH means kodo cannot launch agents."""

    def fake_run(cmd, **kwargs):
        assert cmd == ["git", "rev-parse", "--is-inside-work-tree"]
        return _proc(cmd, 0, stdout="true\n")

    with (
        patch("kodo.doctor.shutil.which", autospec=True, return_value=None),
        patch("subprocess.run", autospec=True, side_effect=fake_run),
    ):
        checks, exit_code = run_doctor(tmp_path)

    assert exit_code == 1
    assert all(
        check.status == "missing" for check in checks if check.name not in {"python", "git"}
    )


def test_doctor_reports_auth_failure(tmp_path: Path):
    """Auth-status output such as "not logged in" makes the backend broken."""

    def fake_which(binary: str) -> str | None:
        return f"/usr/bin/{binary}" if binary == "codex" else None

    def fake_run(cmd, **kwargs):
        if cmd == ["git", "rev-parse", "--is-inside-work-tree"]:
            return _proc(cmd, 0, stdout="true\n")
        if cmd == ["codex", "--version"]:
            return _proc(cmd, 0, stdout="codex 1.0.0\n")
        if cmd == ["codex", "auth", "status"]:
            return _proc(cmd, 1, stderr="not logged in\n")
        raise AssertionError(f"unexpected command: {cmd}")

    with (
        patch("kodo.doctor.shutil.which", autospec=True, side_effect=fake_which),
        patch("subprocess.run", autospec=True, side_effect=fake_run),
    ):
        checks, exit_code = run_doctor(tmp_path)

    assert exit_code == 1
    codex = next(check for check in checks if check.name == "codex")
    assert codex.status == "broken"
    assert "codex login" in codex.detail


def test_doctor_ignores_unsupported_auth_status_command(tmp_path: Path):
    """Unsupported auth probes are not proof that the backend is unusable."""

    def fake_which(binary: str) -> str | None:
        return f"/usr/bin/{binary}" if binary == "codex" else None

    def fake_run(cmd, **kwargs):
        if cmd == ["git", "rev-parse", "--is-inside-work-tree"]:
            return _proc(cmd, 0, stdout="true\n")
        if cmd == ["codex", "--version"]:
            return _proc(cmd, 0, stdout="codex 1.0.0\n")
        if cmd == ["codex", "auth", "status"]:
            return _proc(cmd, 2, stderr="error: unrecognized command 'auth'\n")
        raise AssertionError(f"unexpected command: {cmd}")

    with (
        patch("kodo.doctor.shutil.which", autospec=True, side_effect=fake_which),
        patch("subprocess.run", autospec=True, side_effect=fake_run),
    ):
        checks, exit_code = run_doctor(tmp_path)

    assert exit_code == 0
    codex = next(check for check in checks if check.name == "codex")
    assert codex.status == "ok"


def test_doctor_auth_failure_skips_cached_credential_noise(tmp_path: Path):
    """Generic auth failures should surface the actionable error line."""

    def fake_which(binary: str) -> str | None:
        return f"/usr/bin/{binary}" if binary == "gemini" else None

    def fake_run(cmd, **kwargs):
        if cmd == ["git", "rev-parse", "--is-inside-work-tree"]:
            return _proc(cmd, 0, stdout="true\n")
        if cmd == ["gemini", "--version"]:
            return _proc(cmd, 0, stdout="0.32.1\n")
        if cmd == ["gemini", "auth", "status"]:
            return _proc(
                cmd,
                1,
                stdout=(
                    "Loaded cached credentials.\n"
                    "Error authenticating: client no longer supported\n"
                ),
            )
        raise AssertionError(f"unexpected command: {cmd}")

    with (
        patch("kodo.doctor.shutil.which", autospec=True, side_effect=fake_which),
        patch("subprocess.run", autospec=True, side_effect=fake_run),
    ):
        checks, exit_code = run_doctor(tmp_path)

    assert exit_code == 1
    gemini = next(check for check in checks if check.name == "gemini-cli")
    assert "Loaded cached credentials" not in gemini.detail
    assert "client no longer supported" in gemini.detail


def test_main_dispatches_doctor_subcommand():
    with (
        patch("sys.argv", ["kodo", "doctor"]),
        patch("kodo.doctor.print_doctor", autospec=True, return_value=0) as print_doctor,
        pytest.raises(SystemExit) as exc_info,
    ):
        _main_inner()

    assert exc_info.value.code == 0
    print_doctor.assert_called_once_with()
