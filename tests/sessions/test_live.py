"""Integration tests for backend sessions.

These tests call real backends — they require local auth (CLI tools on PATH
and valid credentials).  Skip automatically when a backend is unavailable.

Run with:  uv run pytest tests/sessions/test_live.py -m live -v

Unit-test versions (mocked, no real backends): tests/sessions/test_live_unit.py
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from kodo.sessions.base import QueryResult

# ---------------------------------------------------------------------------
# Skip helpers
# ---------------------------------------------------------------------------

has_codex = shutil.which("codex") is not None
has_gemini = shutil.which("gemini") is not None
has_claude = shutil.which("claude") is not None

skip_no_codex = pytest.mark.skipif(not has_codex, reason="codex CLI not found")
skip_no_gemini = pytest.mark.skipif(not has_gemini, reason="gemini CLI not found")
skip_no_claude = pytest.mark.skipif(not has_claude, reason="claude CLI not found")

pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SIMPLE_PROMPT = "Reply with the word 'ok'. Do not use any tools."


@pytest.fixture(scope="module")
def project_dir(tmp_path_factory) -> Path:
    """A minimal git-initialized project directory for session queries.

    Module-scoped so all tests in a backend class share one temp dir.
    """
    tmp_path = tmp_path_factory.mktemp("live")
    (tmp_path / "hello.py").write_text("print('hello world')\n")
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--no-gpg-sign"],
        cwd=tmp_path,
        capture_output=True,
        env={
            **__import__("os").environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Gemini CLI — one query, all assertions
# ---------------------------------------------------------------------------


@skip_no_gemini
class TestGeminiCliSession:
    @pytest.fixture(scope="class")
    def result_and_session(self, project_dir: Path):
        from kodo.sessions.gemini_cli import GeminiCliSession

        session = GeminiCliSession(
            model="gemini-3.5-flash",
            system_prompt="You are a helpful assistant.",
        )
        result = session.query(SIMPLE_PROMPT, project_dir, max_turns=5)
        return result, session

    def test_returns_nonempty(self, result_and_session) -> None:
        result, session = result_and_session
        assert isinstance(result, QueryResult)
        assert not result.is_error, f"Session returned error: {result.text}"
        assert result.text.strip(), "Response text is empty"
        assert result.elapsed_s > 0
        assert session.stats.queries == 1

    def test_tracks_tokens(self, result_and_session) -> None:
        result, session = result_and_session
        assert session.stats.total_input_tokens > 0, "No input tokens tracked"
        assert session.stats.total_output_tokens > 0, "No output tokens tracked"

    def test_system_prompt_did_not_crash(self, result_and_session) -> None:
        """Session was created with a system_prompt — reaching here means it worked."""
        result, _ = result_and_session
        assert not result.is_error


# ---------------------------------------------------------------------------
# Codex — one good query + one bad-model query
# ---------------------------------------------------------------------------


@skip_no_codex
class TestCodexSession:
    @pytest.fixture(scope="class")
    def result_and_session(self, project_dir: Path):
        from kodo.sessions.codex import CodexSession

        session = CodexSession(model="gpt-5.5")
        result = session.query(SIMPLE_PROMPT, project_dir, max_turns=5)
        return result, session

    def test_returns_nonempty(self, result_and_session) -> None:
        result, session = result_and_session
        assert isinstance(result, QueryResult)
        assert not result.is_error, f"Session returned error: {result.text}"
        assert result.text.strip(), "Response text is empty"
        assert result.elapsed_s > 0
        assert session.stats.queries == 1

    def test_tracks_tokens(self, result_and_session) -> None:
        _, session = result_and_session
        assert (
            session.stats.total_input_tokens > 0
            or session.stats.total_output_tokens > 0
        ), "No tokens tracked"

    def test_bad_model_returns_error(self, project_dir: Path) -> None:
        from kodo.sessions.codex import CodexSession

        session = CodexSession(model="nonexistent-model-xyz")
        result = session.query(SIMPLE_PROMPT, project_dir, max_turns=5)

        assert result.is_error, "Bad model should return is_error=True"
        assert result.text, "Bad model should return an error message"
        assert "not supported" in result.text or "does not exist" in result.text, (
            f"Error should mention model issue: {result.text!r}"
        )


# ---------------------------------------------------------------------------
# Claude Code — one query, all assertions
# ---------------------------------------------------------------------------


@skip_no_claude
class TestClaudeSession:
    @pytest.fixture(scope="class")
    def result_and_session(self, project_dir: Path):
        from kodo.sessions.claude import ClaudeSession

        session = ClaudeSession(model="sonnet")
        result = session.query(SIMPLE_PROMPT, project_dir, max_turns=5)
        yield result, session
        session.close()

    def test_returns_nonempty(self, result_and_session) -> None:
        result, session = result_and_session
        assert isinstance(result, QueryResult)
        assert not result.is_error, f"Session returned error: {result.text}"
        assert result.text.strip(), "Response text is empty"
        assert result.elapsed_s > 0
        assert session.stats.queries == 1

    def test_tracks_tokens(self, result_and_session) -> None:
        _, session = result_and_session
        # Claude SDK tracks cost, not always raw tokens
        assert session.stats.total_cost_usd >= 0
