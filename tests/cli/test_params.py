"""Tests for CLI parameter selection helpers."""

from __future__ import annotations

import pytest

from kodo.cli._params import _labeled_choices


# ---------------------------------------------------------------------------
# _labeled_choices
# ---------------------------------------------------------------------------


class TestLabeledChoices:
    """Tests for _labeled_choices() helper function."""

    def test_default_item_gets_label(self):
        """Default item (at default_index) should have '(default)' appended."""
        options = ["option1", "option2", "option3"]
        choices = _labeled_choices(options, default_index=1)

        assert choices[0].title == "option1"
        assert choices[0].value == "option1"

        assert choices[1].title == "option2 (default)"
        assert choices[1].value == "option2"

        assert choices[2].title == "option3"
        assert choices[2].value == "option3"

    def test_non_default_items_plain(self):
        """Non-default items should not have '(default)' label."""
        options = ["alpha", "beta", "gamma"]
        choices = _labeled_choices(options, default_index=0)

        assert choices[0].title == "alpha (default)"
        assert choices[1].title == "beta"
        assert choices[2].title == "gamma"

        # Verify all values are unchanged
        for i, choice in enumerate(choices):
            assert choice.value == options[i]
