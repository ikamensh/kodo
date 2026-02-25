"""Shared fixtures for kodo tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from kodo import log
from kodo.agent import Agent
from kodo.factory import clear_backend_cache
from kodo.sessions.base import QueryResult, SessionStats
from kodo.user_config import clear_user_config_cache


@pytest.fixture(autouse=True)
def _isolate_log(tmp_path: Path):
    """Save and restore log module state; redirect ~/.kodo/runs to tmp dir."""
    saved = log._test_snapshot()
    runs_tmp = tmp_path / "kodo_runs"
    runs_tmp.mkdir()
    log._test_redirect_runs(runs_tmp)
    clear_backend_cache()
    clear_user_config_cache()
    yield
    log._test_restore(saved)


class FakeSession:
    """Minimal Session implementation for testing."""

    def __init__(self, response_text: str = "done", is_error: bool = False):
        self._response_text = response_text
        self._is_error = is_error
        self._stats = SessionStats()
        self.model = "fake-model"
        self.prompts: list[str] = []

    @property
    def stats(self) -> SessionStats:
        return self._stats

    @property
    def cost_bucket(self) -> str:
        return "test"

    @property
    def session_id(self) -> str | None:
        return None

    def query(self, prompt: str, project_dir: Path, *, max_turns: int) -> QueryResult:
        self.prompts.append(prompt)
        self._stats.queries += 1
        return QueryResult(
            text=self._response_text,
            elapsed_s=0.1,
            is_error=self._is_error,
        )

    def reset(self) -> None:
        self._stats = SessionStats()

    def terminate(self) -> None:
        pass  # no subprocess to kill in tests

    def clone(self) -> "FakeSession":
        return FakeSession(response_text=self._response_text, is_error=self._is_error)


def make_agent(
    response_text: str = "done",
    prompt: str = "Test agent.",
    is_error: bool = False,
    max_turns: int = 10,
) -> Agent:
    """Create an Agent backed by a FakeSession."""
    session = FakeSession(response_text=response_text, is_error=is_error)
    return Agent(session, prompt, max_turns=max_turns)  # positional → description


def make_scripted_session(
    responses: list[str],
    project_dir: Path,
    write_file: dict | None = None,
) -> FakeSession:
    """FakeSession that returns canned responses in order.

    If write_file is given as {"on_query": N, "path": ..., "content": ...},
    the file is written when query() is called for the Nth time (0-indexed).
    """
    call_count = 0
    resps = list(responses)

    class ScriptedSession(FakeSession):
        def query(self, prompt, project_dir_arg, *, max_turns=10):
            nonlocal call_count
            idx = min(call_count, len(resps) - 1)
            call_count += 1
            if write_file and call_count - 1 == write_file["on_query"]:
                p = Path(write_file["path"])
                path = p if p.is_absolute() else project_dir / p
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(write_file["content"])
            self._response_text = resps[idx]
            return super().query(prompt, project_dir_arg, max_turns=max_turns)

    return ScriptedSession()


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Return a temporary project directory."""
    return tmp_path


# ── Shared fakes for API orchestrator tests ─────────────────────────────


@dataclass
class FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 50
    requests: int = 3


class FakeRunResult:
    def __init__(self, output: str = "done"):
        self.output = output

    def usage(self):
        return FakeUsage()

    def all_messages(self):
        return []
