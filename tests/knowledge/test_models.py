"""Tests for knowledge data models."""

from kodo.knowledge.models import (
    Artifact,
    ConvergenceState,
    KnowledgeGoal,
    KnowledgeResult,
    PatternType,
    QuestionType,
    Workspace,
)


class TestConvergenceState:
    def test_not_converged_initially(self):
        c = ConvergenceState()
        assert not c.converged
        assert c.verdict_type == "open_question"

    def test_converged_high_confidence_and_stability(self):
        c = ConvergenceState(confidence=0.96, stability=0.92)
        assert c.converged
        assert c.verdict_type == "strong_conclusion"

    def test_converged_via_diminishing_returns(self):
        c = ConvergenceState(confidence=0.75, stability=0.88)
        c.history = [
            {"stability": 0.87},
            {"stability": 0.90},
        ]
        assert c.diminishing_returns
        assert c.converged

    def test_not_converged_low_confidence(self):
        c = ConvergenceState(confidence=0.4, stability=0.95)
        assert not c.converged

    def test_verdict_types(self):
        assert ConvergenceState(confidence=0.95).verdict_type == "strong_conclusion"
        assert ConvergenceState(confidence=0.7).verdict_type == "qualified_conclusion"
        assert ConvergenceState(confidence=0.3).verdict_type == "open_question"

    def test_record_round(self):
        c = ConvergenceState(confidence=0.8, stability=0.7, round_number=2)
        c.record_round()
        assert len(c.history) == 1
        assert c.history[0]["round"] == 2
        assert c.history[0]["confidence"] == 0.8

    def test_diminishing_returns_needs_two_rounds(self):
        c = ConvergenceState()
        assert not c.diminishing_returns  # no history
        c.history = [{"stability": 0.9}]
        assert not c.diminishing_returns  # only one round


class TestWorkspace:
    def test_write_and_read(self):
        ws = Workspace()
        ws.write("answer", "Hello world")
        assert ws.read("answer") == "Hello world"

    def test_read_missing(self):
        ws = Workspace()
        assert ws.read("nonexistent") is None

    def test_version_increments(self):
        ws = Workspace()
        ws.write("doc", "v1")
        assert ws.artifacts["doc"].version == 1
        ws.write("doc", "v2")
        assert ws.artifacts["doc"].version == 2
        assert ws.read("doc") == "v2"

    def test_list_artifacts(self):
        ws = Workspace()
        assert ws.list_artifacts() == []
        ws.write("a", "x")
        ws.write("b", "y")
        assert sorted(ws.list_artifacts()) == ["a", "b"]

    def test_snapshot(self):
        ws = Workspace()
        assert ws.snapshot() == "(no artifacts yet)"
        ws.write("answer", "test content")
        snap = ws.snapshot()
        assert "answer" in snap
        assert "test content" in snap

    def test_snapshot_truncates_long_content(self):
        ws = Workspace()
        ws.write("big", "x" * 1000)
        snap = ws.snapshot()
        # Default truncation at 500 chars
        assert len(snap) < 1000
        assert "chars total" in snap

    def test_snapshot_unlimited(self):
        ws = Workspace()
        ws.write("big", "x" * 1000)
        snap = ws.snapshot(max_chars_per_artifact=0)
        assert "x" * 1000 in snap

    def test_snapshot_custom_limit(self):
        ws = Workspace()
        ws.write("doc", "y" * 200)
        snap = ws.snapshot(max_chars_per_artifact=100)
        assert "chars total" in snap


class TestKnowledgeGoal:
    def test_defaults(self):
        g = KnowledgeGoal(goal="test")
        assert g.effort == "standard"
        assert g.domain_hints == []
        assert g.constraints == []
        assert g.output_format is None
        assert g.reference_files == []

    def test_custom(self):
        g = KnowledgeGoal(
            goal="analyze X",
            effort="deep",
            domain_hints=["finance"],
            constraints=["use public data only"],
            output_format="executive_briefing",
            reference_files=["/path/to/doc.md"],
        )
        assert g.effort == "deep"
        assert g.domain_hints == ["finance"]
        assert g.reference_files == ["/path/to/doc.md"]


class TestKnowledgeResult:
    def test_defaults(self):
        r = KnowledgeResult(answer="42")
        assert r.answer == "42"
        assert r.confidence == 0.0
        assert r.rounds_used == 0


class TestEnums:
    def test_question_types(self):
        assert QuestionType.PROOF.value == "proof"
        assert QuestionType.CREATIVE.value == "creative"

    def test_pattern_types(self):
        assert PatternType.ADVERSARIAL.value == "adversarial"
        assert PatternType.TOURNAMENT.value == "tournament"
