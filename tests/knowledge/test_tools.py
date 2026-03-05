"""Tests for knowledge tools."""

from kodo.knowledge.models import ConvergenceState, Workspace
from kodo.knowledge.tools import (
    _make_list_artifacts,
    _make_read_artifact,
    _make_write_artifact,
    _make_compute,
    _make_finish,
)
from kodo.orchestrators.base import DoneSignal


class TestWorkspaceTools:
    def test_read_artifact(self):
        ws = Workspace()
        ws.write("answer", "the answer")
        fn = _make_read_artifact(ws)
        assert fn("answer") == "the answer"

    def test_read_missing_artifact(self):
        ws = Workspace()
        fn = _make_read_artifact(ws)
        result = fn("missing")
        assert "not found" in result.lower()

    def test_write_artifact(self):
        ws = Workspace()
        fn = _make_write_artifact(ws)
        result = fn("doc", "content here")
        assert "updated" in result.lower()
        assert ws.read("doc") == "content here"

    def test_list_artifacts_empty(self):
        ws = Workspace()
        fn = _make_list_artifacts(ws)
        result = fn()
        assert "no artifacts" in result.lower()

    def test_list_artifacts_with_items(self):
        ws = Workspace()
        ws.write("a", "x")
        ws.write("b", "y")
        fn = _make_list_artifacts(ws)
        result = fn()
        assert "a" in result
        assert "b" in result


class TestComputeTool:
    def test_simple_calculation(self):
        fn = _make_compute()
        result = fn("print(2 + 2)")
        assert "4" in result

    def test_error_output(self):
        fn = _make_compute()
        result = fn("raise ValueError('test')")
        assert "STDERR" in result or "ValueError" in result


class TestFinishTool:
    def test_finish_sets_done_signal(self):
        done = DoneSignal()
        ws = Workspace()
        ws.write("answer", "final answer")
        conv = ConvergenceState(confidence=0.9)

        fn = _make_finish(done, ws, conv)
        result = fn("task complete")

        assert done.called
        assert done.success
        assert done.terminal == "goal_done"
        assert done.summary == "final answer"

    def test_finish_uses_summary_when_no_artifact(self):
        done = DoneSignal()
        ws = Workspace()
        conv = ConvergenceState()

        fn = _make_finish(done, ws, conv)
        fn("fallback summary")

        assert done.called
        assert done.summary == "fallback summary"
