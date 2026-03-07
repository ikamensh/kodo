"""Tests for CLI parameter selection helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from kodo.cli._params import _labeled_choices, _load_or_select_params


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


# ---------------------------------------------------------------------------
# _load_or_select_params (Tier 6)
# ---------------------------------------------------------------------------


class TestLoadOrSelectParams:
    """Tests for _load_or_select_params() function."""

    def test_legacy_fallback_config(self, tmp_path):
        """Should load from last-config.json if config.json doesn't exist."""
        kodo_dir = tmp_path / ".kodo"
        kodo_dir.mkdir()

        # Create legacy config file
        legacy_config = {
            "team": "full",
            "orchestrator": "api",
            "orchestrator_model": "opus",
            "max_exchanges": 30,
            "max_cycles": 5,
        }
        legacy_path = kodo_dir / "last-config.json"
        legacy_path.write_text(json.dumps(legacy_config))

        # Mock input to say yes to reuse
        with (
            patch("builtins.input", return_value="y"),
            patch("kodo.cli._params.get_team") as mock_get_team,
        ):
            # Mock get_team to return a fake team
            mock_team = type('obj', (), {
                'name': 'full',
                'description': 'Full team',
            })()
            mock_get_team.return_value = mock_team

            result = _load_or_select_params(tmp_path)

        # Should return the legacy config
        assert result == legacy_config

    def test_legacy_mode_to_team_migration(self, tmp_path, capsys):
        """Should migrate 'mode' key to 'team' key."""
        kodo_dir = tmp_path / ".kodo"
        kodo_dir.mkdir()

        # Create config with old "mode" key
        old_config = {
            "mode": "quick",
            "orchestrator": "api",
            "orchestrator_model": "gemini-flash",
            "max_exchanges": 20,
            "max_cycles": 1,
        }
        config_path = kodo_dir / "config.json"
        config_path.write_text(json.dumps(old_config))

        with (
            patch("builtins.input", return_value="y"),
            patch("kodo.cli._params.get_team") as mock_get_team,
        ):
            mock_team = type('obj', (), {
                'name': 'quick',
                'description': 'Quick team',
            })()
            mock_get_team.return_value = mock_team

            result = _load_or_select_params(tmp_path)

        # Should have "team" key, not "mode"
        assert "team" in result
        assert "mode" not in result
        assert result["team"] == "quick"

    def test_unknown_team_in_saved_config(self, tmp_path, capsys):
        """Should warn and fall through to select_params when team unknown."""
        kodo_dir = tmp_path / ".kodo"
        kodo_dir.mkdir()

        # Create config with unknown team
        bad_config = {
            "team": "nonexistent",
            "orchestrator": "api",
            "orchestrator_model": "opus",
            "max_exchanges": 30,
            "max_cycles": 5,
        }
        config_path = kodo_dir / "config.json"
        config_path.write_text(json.dumps(bad_config))

        new_config = {
            "team": "full",
            "orchestrator": "api",
            "orchestrator_model": "opus",
            "max_exchanges": 30,
            "max_cycles": 5,
        }

        with (
            patch("kodo.cli._params.get_team", side_effect=KeyError("nonexistent")),
            patch("kodo.cli._params.select_params", return_value=new_config) as mock_select,
        ):
            result = _load_or_select_params(tmp_path)

        # Should call select_params since team is unknown
        mock_select.assert_called_once()
        assert result == new_config

        # Should print warning
        out = capsys.readouterr().out
        assert "unknown team" in out.lower()
        assert "nonexistent" in out

    def test_user_says_no_to_reuse(self, tmp_path, capsys):
        """Should fall through to select_params when user declines to reuse."""
        kodo_dir = tmp_path / ".kodo"
        kodo_dir.mkdir()

        # Create valid config
        old_config = {
            "team": "full",
            "orchestrator": "api",
            "orchestrator_model": "opus",
            "max_exchanges": 30,
            "max_cycles": 5,
        }
        config_path = kodo_dir / "config.json"
        config_path.write_text(json.dumps(old_config))

        new_config = {
            "team": "quick",
            "orchestrator": "claude-code",
            "orchestrator_model": "sonnet",
            "max_exchanges": 20,
            "max_cycles": 1,
        }

        with (
            patch("builtins.input", return_value="n"),  # User says no
            patch("kodo.cli._params.get_team") as mock_get_team,
            patch("kodo.cli._params.select_params", return_value=new_config) as mock_select,
        ):
            mock_team = type('obj', (), {
                'name': 'full',
                'description': 'Full team',
            })()
            mock_get_team.return_value = mock_team

            result = _load_or_select_params(tmp_path)

        # Should call select_params since user declined
        mock_select.assert_called_once()
        assert result == new_config

    def test_migrated_legacy_config_resaved(self, tmp_path):
        """Should re-save migrated config to canonical path."""
        kodo_dir = tmp_path / ".kodo"
        kodo_dir.mkdir()

        # Create legacy config with "mode" key
        legacy_config = {
            "mode": "full",
            "orchestrator": "api",
            "orchestrator_model": "opus",
            "max_exchanges": 30,
            "max_cycles": 5,
        }
        legacy_path = kodo_dir / "last-config.json"
        legacy_path.write_text(json.dumps(legacy_config))

        canonical_path = kodo_dir / "config.json"
        assert not canonical_path.exists()

        with (
            patch("builtins.input", return_value="y"),
            patch("kodo.cli._params.get_team") as mock_get_team,
        ):
            mock_team = type('obj', (), {
                'name': 'full',
                'description': 'Full team',
            })()
            mock_get_team.return_value = mock_team

            result = _load_or_select_params(tmp_path)

        # Should save to canonical path
        assert canonical_path.exists()
        saved = json.loads(canonical_path.read_text())

        # Saved config should have "team", not "mode"
        assert "team" in saved
        assert "mode" not in saved
        assert saved["team"] == "full"

        # Result should also have "team", not "mode"
        assert "team" in result
        assert "mode" not in result
        assert result["team"] == "full"
