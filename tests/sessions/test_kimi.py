"""Exercise the native Kimi ACP adapter through a real child process."""

from __future__ import annotations

import os
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from kodo.sessions.kimi import KimiSession


def test_native_query_streams_text_and_resumes_exact_session(tmp_path, fake_kimi):
    """Each query resumes its own session, independent of other workers' sessions."""
    with KimiSession(system_prompt="Be concise.") as session:
        first = session.query("first task", tmp_path, max_turns=5)
        saved_id = session.session_id
        second = session.query("second task", tmp_path, max_turns=5)
        assert first.text == second.text == "Hello world"
        assert not first.is_error and not second.is_error
        assert saved_id == session.session_id
        assert session.stats.queries == 2
        # ACP supplies no billing/token usage; unknown is distinct from free.
        assert first.cost_usd is first.input_tokens is first.output_tokens is None
    requests = fake_kimi()
    assert len([r for r in requests if r.get("method") == "session/new"]) == 1
    load = next(r for r in requests if r.get("method") == "session/load")
    assert load["params"]["sessionId"] == saved_id
    prompts = [
        r["params"]["prompt"][0]["text"]
        for r in requests
        if r.get("method") == "session/prompt"
    ]
    assert prompts == ["Be concise.\n\nfirst task", "second task"]
    assert all(r.get("method") != "session/set_model" for r in requests)
    assert all(proc.poll() is not None for proc in fake_kimi.processes)


def test_explicit_native_model_alias_is_preserved(tmp_path, fake_kimi):
    session = KimiSession(model="custom-provider/my-coding-model")
    result = session.query("hello", tmp_path, max_turns=5)
    assert not result.is_error
    selected = next(r for r in fake_kimi() if r.get("method") == "session/set_model")
    assert selected["params"] == {
        "sessionId": session.session_id,
        "modelId": "custom-provider/my-coding-model",
    }


def test_clone_and_reset_start_fresh_conversations(tmp_path, fake_kimi):
    session = KimiSession(model="my-model", system_prompt="Be concise.")
    session.query("first", tmp_path, max_turns=5)
    original_id = session.session_id
    clone = session.clone()
    assert clone.session_id is None
    assert not clone.query("clone", tmp_path, max_turns=5).is_error
    assert clone.session_id != original_id
    assert session.session_id == original_id
    session.reset()
    assert session.session_id is None
    assert session.stats.queries == 0
    assert not session.query("after reset", tmp_path, max_turns=5).is_error
    assert session.session_id not in (original_id, clone.session_id)
    prompts = [
        r["params"]["prompt"][0]["text"]
        for r in fake_kimi()
        if r.get("method") == "session/prompt"
    ]
    assert prompts == [
        "Be concise.\n\nfirst",
        "Be concise.\n\nclone",
        "Be concise.\n\nafter reset",
    ]


def test_changing_project_creates_a_new_session(tmp_path, fake_kimi):
    session = KimiSession()
    session.query("hello", tmp_path, max_turns=5)
    previous_id = session.session_id
    other_project = tmp_path / "other"
    other_project.mkdir()
    assert not session.query("hello", other_project, max_turns=5).is_error
    assert session.session_id != previous_id
    assert all(r.get("method") != "session/load" for r in fake_kimi())


def test_saved_run_resumes_exact_kimi_conversation(tmp_path, fake_kimi):
    from kodo.agent import Agent
    from kodo.orchestrators.resume import inject_resume_sessions
    from kodo.orchestrators.types import ResumeState

    saved_id = "saved-kimi-worker-id"
    session = KimiSession()
    resume = ResumeState(1, "prior work", {"worker": saved_id}, [], [], 0)
    inject_resume_sessions({"worker": Agent(session, "worker")}, resume)
    assert not session.query("continue", tmp_path, max_turns=5).is_error
    loaded = next(r for r in fake_kimi() if r.get("method") == "session/load")
    assert loaded["params"]["sessionId"] == session.session_id == saved_id
    assert all(r.get("method") != "session/new" for r in fake_kimi())


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("error", "Provider unavailable"), ("auth", "kimi login")],
)
def test_native_errors_are_returned_without_retry(
    tmp_path, fake_kimi, monkeypatch, mode, expected
):
    monkeypatch.setenv("KODO_FAKE_KIMI_MODE", mode)
    result = KimiSession().query("hello", tmp_path, max_turns=5)
    assert result.is_error
    assert expected in result.text
    assert len(fake_kimi.processes) == 1
    assert fake_kimi.processes[0].poll() is not None


def test_permission_request_allows_current_tool_once(tmp_path, fake_kimi, monkeypatch):
    monkeypatch.setenv("KODO_FAKE_KIMI_MODE", "permission")
    result = KimiSession().query("inspect files", tmp_path, max_turns=5)
    assert not result.is_error
    permission = next(r for r in fake_kimi() if r.get("id") == "permission-1")
    assert permission["result"]["outcome"] == {
        "outcome": "selected",
        "optionId": "allow",
    }


def test_tool_limit_cancels_current_prompt(tmp_path, fake_kimi, monkeypatch):
    monkeypatch.setenv("KODO_FAKE_KIMI_MODE", "limit")
    result = KimiSession().query("inspect files", tmp_path, max_turns=3)
    assert result.is_error
    assert result.incomplete_reason == "max_turns"
    assert sum(r.get("method") == "session/cancel" for r in fake_kimi()) == 1


@pytest.mark.parametrize("timeout", [0, 1])
def test_deadline_reaps_hanging_child(tmp_path, fake_kimi, monkeypatch, timeout):
    monkeypatch.setenv("KODO_FAKE_KIMI_MODE", "hang")
    result = KimiSession(session_timeout_s=timeout).query(
        "hello", tmp_path, max_turns=5
    )
    assert result.is_error
    assert result.incomplete_reason == "timeout"
    assert fake_kimi.processes[0].poll() is not None


def test_close_interrupts_blocked_query(tmp_path, fake_kimi, monkeypatch):
    monkeypatch.setenv("KODO_FAKE_KIMI_MODE", "hang")
    session = KimiSession(session_timeout_s=5)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(session.query, "hello", tmp_path, max_turns=5)
        try:
            deadline = time.monotonic() + 3
            while not any(r.get("method") == "session/prompt" for r in fake_kimi()):
                assert time.monotonic() < deadline, "Kimi did not reach its prompt"
                threading.Event().wait(0.01)
            session.close()
            result = pending.result(timeout=2)
            assert result.is_error
            assert result.incomplete_reason != "timeout"
            assert fake_kimi.processes[0].poll() is not None
        finally:
            session.close()


def test_deadline_returns_when_descendant_inherits_output(tmp_path, fake_kimi, monkeypatch):
    """A descendant holding stdout open cannot trap timed-out query cleanup."""
    child_pid = tmp_path / "descendant.pid"
    monkeypatch.setenv("KODO_FAKE_KIMI_MODE", "inherited_stdout")
    monkeypatch.setenv("KODO_FAKE_KIMI_CHILD_PID", str(child_pid))
    session = KimiSession(session_timeout_s=1)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(session.query, "run tool", tmp_path, max_turns=5)
        try:
            result = pending.result(timeout=8)
            assert child_pid.exists(), "The fixture must start its descendant"
            assert result.incomplete_reason == "timeout"
            assert result.is_error
            assert fake_kimi.processes[0].poll() is not None
        finally:
            # Stop the fixture's descendant even when the regression fails.
            if child_pid.exists():
                try:
                    os.kill(int(child_pid.read_text()), signal.SIGTERM)
                except ProcessLookupError:
                    pass
            session.close()
