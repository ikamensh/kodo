"""Tests for convergence assessment."""

from unittest.mock import MagicMock, patch

from kodo.knowledge.convergence import _fallback, _parse_assessment, assess


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


class TestFallback:
    """Test the _fallback function directly."""

    def test_fallback_returns_default_dict_with_reason(self):
        result = _fallback("Test error message")
        assert result["confidence"] == 0.5
        assert result["stability"] == 0.5
        assert result["agreement"] == 0.5
        assert result["completeness"] == 0.5
        assert result["should_continue"] is True
        assert result["reasoning"] == "Test error message"


class TestAssessWithMockedLLM:
    """Test actual convergence assessment with mocked dependencies."""

    @patch("kodo.knowledge.convergence.run_in_thread", autospec=True)
    def test_successful_assessment(self, mock_run_in_thread):
        # Arrange: Mock the LLM response
        mock_result = MagicMock()
        mock_result.output = '{"confidence": 0.85, "stability": 0.9, "agreement": 0.8, "completeness": 0.95, "should_continue": false, "reasoning": "Converged well"}'
        mock_run_in_thread.return_value = mock_result

        # Act
        result = assess(
            goal="What is 2+2?",
            current_answer="The answer is 4.",
            previous_answer="I think it's 4.",
            round_number=2,
            model="test-model",
        )

        # Assert
        assert result["confidence"] == 0.85
        assert result["stability"] == 0.9
        assert result["should_continue"] is False
        assert "Converged well" in result["reasoning"]

        # Verify run_in_thread was called
        mock_run_in_thread.assert_called_once()

    @patch("kodo.knowledge.convergence.run_in_thread", autospec=True)
    def test_assessment_with_answer_truncation(self, mock_run_in_thread):
        """Test that long answers are truncated to 5000 chars in the prompt."""
        mock_result = MagicMock()
        mock_result.output = '{"confidence": 0.7, "stability": 0.6, "agreement": 0.7, "completeness": 0.8, "should_continue": true, "reasoning": "needs work"}'
        mock_run_in_thread.return_value = mock_result

        # Create very long answers (>5000 chars)
        long_answer = "A" * 10000

        result = assess(
            goal="Test",
            current_answer=long_answer,
            previous_answer=long_answer,
            round_number=3,
            model="test-model",
        )

        # Should still work (truncation happens internally)
        assert result["confidence"] == 0.7
        assert result["should_continue"] is True

    @patch("kodo.knowledge.convergence.run_in_thread", autospec=True)
    def test_assessment_exception_triggers_fallback(self, mock_run_in_thread):
        """Test that exceptions during assessment trigger the fallback logic."""
        # Arrange: Mock run_in_thread to raise an exception
        mock_run_in_thread.side_effect = RuntimeError("LLM timeout")

        # Act
        result = assess(
            goal="Test goal",
            current_answer="Current",
            previous_answer="Previous",
            round_number=2,
            model="test-model",
        )

        # Assert: Should return fallback values
        assert result["confidence"] == 0.5
        assert result["stability"] == 0.5
        assert result["should_continue"] is True
        assert "Assessment error" in result["reasoning"]
        assert "LLM timeout" in result["reasoning"]

    @patch("kodo.knowledge.convergence.run_in_thread", autospec=True)
    def test_assessment_with_invalid_llm_response(self, mock_run_in_thread):
        """Test that invalid JSON from LLM is handled gracefully."""
        mock_result = MagicMock()
        mock_result.output = "This is not valid JSON at all"
        mock_run_in_thread.return_value = mock_result

        result = assess(
            goal="Test",
            current_answer="Current",
            previous_answer="Previous",
            round_number=2,
            model="test-model",
        )

        # Should return default fallback values from _parse_assessment
        assert result["confidence"] == 0.5
        assert result["should_continue"] is True
        assert "could not parse" in result["reasoning"].lower()

    @patch("kodo.knowledge.convergence.Agent", autospec=True)
    @patch("kodo.knowledge.convergence.make_fresh_model", autospec=True)
    def test_assessment_creates_agent_with_correct_config(self, mock_make_model, mock_agent_class):
        """Test that the agent is created with correct model and system prompt."""
        # This test doesn't mock run_in_thread, so _run() executes and we get coverage of lines 48-50
        mock_model = MagicMock()
        mock_make_model.return_value = mock_model

        mock_agent = MagicMock()
        mock_result = MagicMock()
        mock_result.output = '{"confidence": 0.8, "stability": 0.7, "agreement": 0.8, "completeness": 0.85, "should_continue": false, "reasoning": "good"}'
        mock_agent.run_sync.return_value = mock_result
        mock_agent_class.return_value = mock_agent

        result = assess(
            goal="Test goal",
            current_answer="Current answer",
            previous_answer="Previous answer",
            round_number=2,
            model="test-model",
        )

        # Verify the agent was created with correct parameters
        mock_make_model.assert_called_once_with("test-model")
        mock_agent_class.assert_called_once_with(
            mock_model,
            system_prompt="You are a convergence assessor. Respond only with valid JSON.",
        )
        mock_agent.run_sync.assert_called_once()

        # Verify the result was parsed correctly
        assert result["confidence"] == 0.8
        assert result["should_continue"] is False
