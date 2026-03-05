"""Tests for convergence assessment."""

from kodo.knowledge.convergence import _parse_assessment, assess


class TestParseAssessment:
    def test_valid_json(self):
        raw = '{"confidence": 0.8, "stability": 0.7, "agreement": 0.9, "completeness": 0.6, "should_continue": true, "reasoning": "needs more"}'
        result = _parse_assessment(raw)
        assert result["confidence"] == 0.8
        assert result["stability"] == 0.7
        assert result["should_continue"] is True

    def test_json_with_code_fences(self):
        raw = '```json\n{"confidence": 0.9, "stability": 0.8, "agreement": 1.0, "completeness": 0.95, "should_continue": false, "reasoning": "done"}\n```'
        result = _parse_assessment(raw)
        assert result["confidence"] == 0.9
        assert result["should_continue"] is False

    def test_invalid_json_returns_defaults(self):
        result = _parse_assessment("this is not json")
        assert result["confidence"] == 0.5
        assert result["should_continue"] is True
        assert "could not parse" in result["reasoning"].lower()


class TestAssessFirstRound:
    def test_first_round_returns_low_confidence(self):
        result = assess(
            goal="test",
            current_answer="answer",
            previous_answer="",
            round_number=1,
            model="test-model",
        )
        assert result["confidence"] == 0.3
        assert result["should_continue"] is True
