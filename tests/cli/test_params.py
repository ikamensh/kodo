"""Tests for CLI parameter selection helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kodo.cli._params import (
    _build_params_from_flags,
    _labeled_choices,
    _load_or_select_params,
    _select_numeric,
    _select_one,
    select_params,
)
from kodo.models import (
    CLAUDE_OPUS,
    CLAUDE_SONNET,
    CODEX_DEFAULT,
    CURSOR_COMPOSER,
    GEMINI_ALIAS_FLASH,
    GEMINI_ALIAS_PRO,
    GEMINI_CLI_FLASH,
    GEMINI_CLI_PRO,
)


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
            patch("builtins.input", autospec=True, return_value="y"),
            patch("kodo.cli._params.get_team", autospec=True) as mock_get_team,
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
            patch("builtins.input", autospec=True, return_value="y"),
            patch("kodo.cli._params.get_team", autospec=True) as mock_get_team,
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
            patch("kodo.cli._params.get_team", autospec=True, side_effect=KeyError("nonexistent")),
            patch("kodo.cli._params.select_params", autospec=True, return_value=new_config) as mock_select,
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
            patch("builtins.input", autospec=True, return_value="n"),  # User says no
            patch("kodo.cli._params.get_team", autospec=True) as mock_get_team,
            patch("kodo.cli._params.select_params", autospec=True, return_value=new_config) as mock_select,
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
            patch("builtins.input", autospec=True, return_value="y"),
            patch("kodo.cli._params.get_team", autospec=True) as mock_get_team,
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


# ---------------------------------------------------------------------------
# Tier 1: _build_params_from_flags
# ---------------------------------------------------------------------------


class TestBuildParamsFromFlags:
    """Tier 1 tests for _build_params_from_flags() function."""

    def test_debug_mode_defaults(self, tmp_path):
        """Debug mode should use sensible defaults without real backend checks."""
        # Create args object with debug=True
        args = type('obj', (), {
            'debug': True,
            'team': None,
            'orchestrator': None,
            'exchanges': None,
            'cycles': None,
            'no_auto_commit': False,
            'effort': None,
        })()

        with patch("kodo.cli._params.get_team", autospec=True) as mock_get_team:
            # Mock team preset
            mock_team = type('obj', (), {
                'name': 'full',
                'description': 'Full team',
                'default_max_exchanges': 30,
                'default_max_cycles': 5,
            })()
            mock_get_team.return_value = mock_team

            result = _build_params_from_flags(args, tmp_path)

        # Debug mode should set orchestrator to "api" with default model
        assert result["orchestrator"] == "api"
        assert result["orchestrator_model"] == "opus"  # CLAUDE_OPUS constant value
        assert result["team"] == "full"
        assert result["max_exchanges"] == 30
        assert result["max_cycles"] == 5
        assert result["auto_commit"] is True

    def test_no_auto_commit_flag(self, tmp_path):
        """--no-auto-commit flag should disable auto-commit."""
        args = type('obj', (), {
            'debug': True,
            'team': 'full',
            'orchestrator': 'gemini-flash',
            'exchanges': 20,
            'cycles': 1,
            'no_auto_commit': True,
            'effort': None,
        })()

        with patch("kodo.cli._params.get_team", autospec=True) as mock_get_team:
            mock_team = type('obj', (), {
                'name': 'full',
                'default_max_exchanges': 30,
                'default_max_cycles': 5,
            })()
            mock_get_team.return_value = mock_team

            result = _build_params_from_flags(args, tmp_path)

        # auto_commit should be False when --no-auto-commit is set
        assert result["auto_commit"] is False

    def test_effort_flag_set(self, tmp_path):
        """--effort flag should be included in params when specified."""
        args = type('obj', (), {
            'debug': True,
            'team': 'quick',
            'orchestrator': 'gemini-flash',
            'exchanges': 10,
            'cycles': 1,
            'no_auto_commit': False,
            'effort': 'high',
        })()

        with patch("kodo.cli._params.get_team", autospec=True) as mock_get_team:
            mock_team = type('obj', (), {
                'name': 'quick',
                'default_max_exchanges': 20,
                'default_max_cycles': 1,
            })()
            mock_get_team.return_value = mock_team

            result = _build_params_from_flags(args, tmp_path)

        # effort should be set when provided via CLI flag
        assert result["effort"] == "high"

    def test_ollama_model_implies_api_orchestrator(self, tmp_path):
        args = type('obj', (), {
            'debug': False,
            'team': 'full',
            'orchestrator': 'ollama:qwen2.5-coder:14b',
            'exchanges': None,
            'cycles': None,
            'no_auto_commit': False,
            'effort': None,
        })()

        with (
            patch("kodo.cli._params.get_team", autospec=True) as mock_get_team,
            patch("kodo.cli._params.preferred_orchestrator", autospec=True, return_value="claude-code"),
            patch("kodo.cli._params.check_api_key", autospec=True, return_value=None),
        ):
            mock_team = type('obj', (), {
                'name': 'full',
                'default_max_exchanges': 30,
                'default_max_cycles': 5,
            })()
            mock_get_team.return_value = mock_team

            result = _build_params_from_flags(args, tmp_path)

        assert result["orchestrator"] == "api"
        assert result["orchestrator_model"] == "ollama:qwen2.5-coder:14b"

    def test_effort_not_set_when_none(self, tmp_path):
        """When no --effort flag, 'effort' key should not appear in params."""
        args = type('obj', (), {
            'debug': True,
            'team': 'full',
            'exchanges': None,
            'cycles': None,
            'no_auto_commit': False,
            'effort': None,
        })()

        with patch("kodo.cli._params.get_team", autospec=True) as mock_get_team:
            mock_team = type('obj', (), {
                'name': 'full',
                'default_max_exchanges': 30,
                'default_max_cycles': 5,
            })()
            mock_get_team.return_value = mock_team

            result = _build_params_from_flags(args, tmp_path)

        assert "effort" not in result, \
            "When --effort is not specified, 'effort' should not be in params"

    def test_effort_all_valid_values(self, tmp_path):
        """All valid effort values (low, standard, high, max) should pass through."""
        for effort_val in ("low", "standard", "high", "max"):
            args = type('obj', (), {
                'debug': True,
                'team': 'full',
                'orchestrator': None,
                'exchanges': None,
                'cycles': None,
                'no_auto_commit': False,
                'effort': effort_val,
            })()

            with patch("kodo.cli._params.get_team", autospec=True) as mock_get_team:
                mock_team = type('obj', (), {
                    'name': 'full',
                    'default_max_exchanges': 30,
                    'default_max_cycles': 5,
                })()
                mock_get_team.return_value = mock_team

                result = _build_params_from_flags(args, tmp_path)

            assert result["effort"] == effort_val, \
                f"--effort {effort_val} should produce params['effort'] == '{effort_val}'"


# ---------------------------------------------------------------------------
# Tier 2: _select_one and _select_numeric
# ---------------------------------------------------------------------------


class TestSelectOne:
    """Tier 2 tests for _select_one() function."""

    def test_select_one_returns_choice(self):
        """Should return the selected choice value."""
        with patch("questionary.select", autospec=True) as mock_select:
            mock_select.return_value.ask.return_value = "option2"

            result = _select_one("Test prompt:", ["option1", "option2", "option3"], default_index=0)

        assert result == "option2"
        mock_select.assert_called_once()
        # Verify choices were properly constructed
        call_args = mock_select.call_args
        assert call_args[0][0] == "Test prompt:"
        choices = call_args[1]["choices"]
        assert len(choices) == 3
        assert choices[0].title == "option1 (default)"
        assert choices[1].title == "option2"
        assert choices[2].title == "option3"

    def test_select_one_cancel_exits(self):
        """Should exit with code 1 when user cancels (returns None)."""
        with (
            patch("questionary.select", autospec=True) as mock_select,
            pytest.raises(SystemExit, match="1"),
        ):
            mock_select.return_value.ask.return_value = None

            _select_one("Test prompt:", ["option1", "option2"], default_index=0)


class TestSelectNumeric:
    """Tier 2 tests for _select_numeric() function."""

    def test_select_numeric_preset_chosen(self):
        """Should return preset value when user selects a preset."""
        with patch("questionary.select", autospec=True) as mock_select:
            mock_select.return_value.ask.return_value = "30"

            result = _select_numeric("Max exchanges:", ["20", "30", "50"], default_index=1)

        assert result == "30"
        # Verify "Custom..." was added to choices
        call_args = mock_select.call_args
        choices = call_args[1]["choices"]
        assert len(choices) == 4  # 3 presets + "Custom..."
        assert choices[3].value == "Custom..."

    def test_select_numeric_custom_valid_input(self):
        """Should accept valid custom numeric input on first try."""
        with (
            patch("questionary.select", autospec=True) as mock_select,
            patch("questionary.text", autospec=True) as mock_text,
        ):
            # User selects "Custom..."
            mock_select.return_value.ask.return_value = "Custom..."
            # User enters valid number
            mock_text.return_value.ask.return_value = "42"

            result = _select_numeric("Custom value:", ["10", "20", "30"], default_index=0, type_fn=int)

        assert result == "42"
        mock_text.assert_called_once()

    def test_select_numeric_custom_invalid_then_valid(self, capsys):
        """Should reject invalid input and prompt again until valid."""
        with (
            patch("questionary.select", autospec=True) as mock_select,
            patch("questionary.text", autospec=True) as mock_text,
        ):
            # User selects "Custom..."
            mock_select.return_value.ask.return_value = "Custom..."
            # User enters invalid, then valid
            mock_text.return_value.ask.side_effect = ["invalid", "abc", "25"]

            result = _select_numeric("Custom value:", ["10", "20"], default_index=0, type_fn=int)

        assert result == "25"
        # Should have been called 3 times (2 invalid, 1 valid)
        assert mock_text.call_count == 3
        # Check that error message was printed
        out = capsys.readouterr().out
        assert "Invalid input" in out
        assert "Expected int" in out

    def test_select_numeric_cancel_exits(self):
        """Should exit when user cancels at 'Custom...' text input."""
        with (
            patch("questionary.select", autospec=True) as mock_select,
            patch("questionary.text", autospec=True) as mock_text,
            pytest.raises(SystemExit, match="1"),
        ):
            # User selects "Custom..."
            mock_select.return_value.ask.return_value = "Custom..."
            # User cancels text input
            mock_text.return_value.ask.return_value = None

            _select_numeric("Custom value:", ["10", "20"], default_index=0)


# ---------------------------------------------------------------------------
# Tier 3: select_params — orchestrator branches
# ---------------------------------------------------------------------------


def _fake_team_preset(**overrides):
    """Build a minimal fake TeamPreset-like object."""
    defaults = {
        "name": "full",
        "description": "Full autonomous team",
        "default_max_exchanges": 30,
        "default_max_cycles": 5,
    }
    defaults.update(overrides)
    return type("FakeTeamPreset", (), defaults)()


def _fake_teams():
    """Return a minimal TEAMS dict matching the real structure."""
    return {
        "full": _fake_team_preset(name="full", description="Full autonomous team"),
        "quick": _fake_team_preset(
            name="quick",
            description="Quick single-agent",
            default_max_exchanges=20,
            default_max_cycles=1,
        ),
    }


class TestSelectParams:
    """Tier 3 tests for select_params() — mock _select_one/_select_numeric."""

    @pytest.fixture(autouse=True)
    def _common_patches(self):
        """Shared patches for select_params tests."""
        self._select_one_calls = []
        self._select_numeric_calls = []

        def mock_select_numeric(title, presets, default_index=0, type_fn=int):
            self._select_numeric_calls.append((title, presets, default_index))
            return presets[default_index]

        fake_backends = MagicMock()
        fake_backends.return_value = {
            "claude": True,
            "codex": False,
            "cursor": False,
            "gemini-cli": False,
        }
        fake_backends.cache_clear = MagicMock()

        with (
            patch("kodo.cli._params._select_numeric", autospec=True, side_effect=mock_select_numeric),
            patch("kodo.cli._params.check_api_key", autospec=True, return_value=None),
            patch("kodo.factory.available_backends", fake_backends),
            patch("kodo.cli._params.TEAMS", _fake_teams()),
            patch("kodo.cli._params.get_team", autospec=True, return_value=_fake_team_preset()),
            patch(
                "kodo.team_config.list_available_teams", autospec=True,
                return_value=[],
            ),
        ):
            yield

    def _patch_select_one(self, orchestrator_choice):
        """Return a mock _select_one that picks the requested orchestrator."""

        def mock_select_one(title, options, default_index=0):
            self._select_one_calls.append((title, options))
            if "Team:" in title:
                return options[0]  # pick first team
            if "Orchestrator:" in title:
                # Find the option matching the requested orchestrator
                for opt in options:
                    if opt.startswith(orchestrator_choice):
                        return opt
                return options[0]
            if "Orchestrator model:" in title:
                return options[0]  # pick first model
            return options[0]

        return mock_select_one

    def test_api_orchestrator(self):
        """API orchestrator should offer Claude + Gemini model choices."""
        with patch(
            "kodo.cli._params._select_one", autospec=True,
            side_effect=self._patch_select_one("api"),
        ):
            result = select_params()

        assert result["orchestrator"] == "api"
        assert result["team"] == "full"
        assert isinstance(result["max_exchanges"], int)
        assert isinstance(result["max_cycles"], int)

        # Find the model selection call
        model_call = [c for c in self._select_one_calls if "model" in c[0].lower()]
        assert len(model_call) == 1
        model_options = model_call[0][1]
        # Options are now formatted as "alias — display (provider)" or plain strings
        option_aliases = [opt.split(" — ")[0].strip() if " — " in opt else opt for opt in model_options]
        assert CLAUDE_OPUS in option_aliases
        assert CLAUDE_SONNET in option_aliases
        assert GEMINI_ALIAS_PRO in option_aliases
        assert GEMINI_ALIAS_FLASH in option_aliases

    def test_api_orchestrator_offers_ollama_when_local_model_detected(self):
        with (
            patch(
                "kodo.cli._params._select_one",
                autospec=True,
                side_effect=self._patch_select_one("api"),
            ),
            patch(
                "kodo.models.list_ollama_models",
                autospec=True,
                return_value=["qwen2.5-coder:14b", "llama3.2"],
            ),
            patch(
                "kodo.cli._params.list_ollama_models",
                autospec=True,
                return_value=["qwen2.5-coder:14b", "llama3.2"],
            ),
        ):
            select_params()

        model_call = [c for c in self._select_one_calls if "model" in c[0].lower()]
        assert len(model_call) == 1
        model_options = model_call[0][1]
        assert "ollama:qwen2.5-coder:14b" in model_options
        assert "ollama:llama3.2" in model_options

    def test_claude_code_orchestrator(self):
        """claude-code orchestrator should offer Claude model choices."""
        with patch(
            "kodo.cli._params._select_one", autospec=True,
            side_effect=self._patch_select_one("claude-code"),
        ):
            result = select_params()

        assert result["orchestrator"] == "claude-code"

        model_call = [c for c in self._select_one_calls if "model" in c[0].lower()]
        assert len(model_call) == 1
        model_options = model_call[0][1]
        assert CLAUDE_OPUS in model_options
        assert CLAUDE_SONNET in model_options
        assert len(model_options) == 2

    def test_gemini_cli_orchestrator(self):
        """gemini-cli orchestrator should offer Gemini CLI model choices."""
        # Need gemini-cli available as a backend
        fake_backends = MagicMock()
        fake_backends.return_value = {
            "claude": True,
            "codex": False,
            "cursor": False,
            "gemini-cli": True,
        }
        fake_backends.cache_clear = MagicMock()

        with (
            patch("kodo.factory.available_backends", fake_backends),
            patch(
                "kodo.cli._params._select_one", autospec=True,
                side_effect=self._patch_select_one("gemini-cli"),
            ),
        ):
            result = select_params()

        assert result["orchestrator"] == "gemini-cli"

        model_call = [c for c in self._select_one_calls if "model" in c[0].lower()]
        assert len(model_call) == 1
        model_options = model_call[0][1]
        assert GEMINI_CLI_FLASH in model_options
        assert GEMINI_CLI_PRO in model_options

    def test_codex_orchestrator(self):
        """codex orchestrator should offer Codex model choices."""
        fake_backends = MagicMock()
        fake_backends.return_value = {
            "claude": True,
            "codex": True,
            "cursor": False,
            "gemini-cli": False,
        }
        fake_backends.cache_clear = MagicMock()

        with (
            patch("kodo.factory.available_backends", fake_backends),
            patch(
                "kodo.cli._params._select_one", autospec=True,
                side_effect=self._patch_select_one("codex"),
            ),
        ):
            result = select_params()

        assert result["orchestrator"] == "codex"

        model_call = [c for c in self._select_one_calls if "model" in c[0].lower()]
        assert len(model_call) == 1
        model_options = model_call[0][1]
        assert CODEX_DEFAULT in model_options

    def test_cursor_orchestrator(self):
        """cursor orchestrator should offer Cursor model choices."""
        fake_backends = MagicMock()
        fake_backends.return_value = {
            "claude": True,
            "codex": False,
            "cursor": True,
            "gemini-cli": False,
        }
        fake_backends.cache_clear = MagicMock()

        with (
            patch("kodo.factory.available_backends", fake_backends),
            patch(
                "kodo.cli._params._select_one", autospec=True,
                side_effect=self._patch_select_one("cursor"),
            ),
        ):
            result = select_params()

        assert result["orchestrator"] == "cursor"

        model_call = [c for c in self._select_one_calls if "model" in c[0].lower()]
        assert len(model_call) == 1
        model_options = model_call[0][1]
        assert CURSOR_COMPOSER in model_options

    def test_no_backends_exits(self, capsys):
        """Should exit with error when no backends are available."""
        fake_backends = MagicMock()
        fake_backends.return_value = {
            "claude": False,
            "codex": False,
            "cursor": False,
            "gemini-cli": False,
        }
        fake_backends.cache_clear = MagicMock()

        with (
            patch("kodo.factory.available_backends", fake_backends),
            pytest.raises(SystemExit),
        ):
            select_params()

        err = capsys.readouterr().err
        assert "no worker backends found" in err.lower()

    def test_api_key_failure_exits(self, capsys):
        """Should exit when API key validation fails."""
        with (
            patch(
                "kodo.cli._params._select_one", autospec=True,
                side_effect=self._patch_select_one("api"),
            ),
            patch(  # noqa: autospec
                "kodo.cli._params.check_api_key",
                return_value="ANTHROPIC_API_KEY not set",
            ),
            pytest.raises(SystemExit),
        ):
            select_params()

        captured = capsys.readouterr()
        assert "ANTHROPIC_API_KEY not set" in captured.err

    def test_user_teams_listed(self):
        """User JSON teams should appear in team options."""
        user_team = ("my-custom", "user", {"description": "My custom team"}, Path("/tmp"))

        with (
            patch(  # noqa: autospec
                "kodo.team_config.list_available_teams",
                return_value=[user_team],
            ),
            patch(
                "kodo.cli._params._select_one", autospec=True,
                side_effect=self._patch_select_one("api"),
            ),
        ):
            select_params()

        # Find the Team: call
        team_call = [c for c in self._select_one_calls if "Team:" in c[0]]
        assert len(team_call) == 1
        team_options = team_call[0][1]
        # Should include the user team
        assert any("my-custom" in opt for opt in team_options)
        assert any("My custom team" in opt for opt in team_options)


# ---------------------------------------------------------------------------
# Tier 4: _load_or_select_params edge cases
# ---------------------------------------------------------------------------


class TestLoadOrSelectParamsEdgeCases:
    """Tier 4 tests for remaining _load_or_select_params paths."""

    def test_legacy_migration_save_error_suppressed(self, tmp_path):
        """PermissionError during legacy config re-save should be suppressed."""
        kodo_dir = tmp_path / ".kodo"
        kodo_dir.mkdir()

        # Create legacy config with "mode" key (triggers migration + re-save)
        legacy_config = {
            "mode": "full",
            "orchestrator": "api",
            "orchestrator_model": "opus",
            "max_exchanges": 30,
            "max_cycles": 5,
        }
        # Use last-config.json so cfg_path picks up legacy
        legacy_path = kodo_dir / "last-config.json"
        legacy_path.write_text(json.dumps(legacy_config))

        with (
            patch("builtins.input", autospec=True, return_value="y"),
            patch("kodo.cli._params.get_team", autospec=True) as mock_get_team,
            patch(
                "kodo.cli._params._save_config", autospec=True,
                side_effect=PermissionError("read-only"),
            ),
        ):
            mock_get_team.return_value = _fake_team_preset()

            # Should NOT raise — PermissionError is suppressed during migration
            result = _load_or_select_params(tmp_path)

        # Config should still be returned with migrated "team" key
        assert result["team"] == "full"
        assert "mode" not in result

    def test_select_params_save_error_calls_fail(self, tmp_path):
        """PermissionError after select_params should call _fail."""
        kodo_dir = tmp_path / ".kodo"
        kodo_dir.mkdir()

        # No config file → falls through to select_params
        new_config = {
            "team": "full",
            "orchestrator": "api",
            "orchestrator_model": "opus",
            "max_exchanges": 30,
            "max_cycles": 5,
        }

        with (
            patch("kodo.cli._params.select_params", autospec=True, return_value=new_config),
            patch(
                "kodo.cli._params._save_config", autospec=True,
                side_effect=PermissionError("read-only"),
            ),
            patch("kodo.cli._launch._original_stdout", None),
            pytest.raises(SystemExit),
        ):
            _load_or_select_params(tmp_path)


# ---------------------------------------------------------------------------
# A8: --team flag (non-api orchestrator values)
# ---------------------------------------------------------------------------


class TestTeamFlagFlowsThrough:
    """Verify --team flag value reaches params correctly."""

    def test_explicit_team_quick_sets_params(self, tmp_path):
        """--team quick must produce params['team'] == 'quick'."""
        args = type('obj', (), {
            'debug': True,
            'team': 'quick',
            'orchestrator': None,
            'exchanges': None,
            'cycles': None,
            'no_auto_commit': False,
            'effort': None,
        })()

        with patch("kodo.cli._params.get_team", autospec=True) as mock_get_team:
            mock_team = type('obj', (), {
                'name': 'quick',
                'default_max_exchanges': 20,
                'default_max_cycles': 1,
            })()
            mock_get_team.return_value = mock_team
            result = _build_params_from_flags(args, tmp_path)

        assert result["team"] == "quick"
        mock_get_team.assert_called_once_with("quick")

    def test_team_none_defaults_to_full(self, tmp_path):
        """No --team flag must default to 'full'."""
        args = type('obj', (), {
            'debug': True,
            'team': None,
            'orchestrator': None,
            'exchanges': None,
            'cycles': None,
            'no_auto_commit': False,
            'effort': None,
        })()

        with patch("kodo.cli._params.get_team", autospec=True) as mock_get_team:
            mock_team = type('obj', (), {
                'name': 'full',
                'default_max_exchanges': 30,
                'default_max_cycles': 5,
            })()
            mock_get_team.return_value = mock_team
            result = _build_params_from_flags(args, tmp_path)

        assert result["team"] == "full"
        mock_get_team.assert_called_once_with("full")


# ---------------------------------------------------------------------------
# A9: --orchestrator flag (explicit non-api values)
# ---------------------------------------------------------------------------


class TestOrchestratorFlagFlowsThrough:
    """Verify --orchestrator flag value reaches params correctly."""

    def test_explicit_claude_code_orchestrator(self, tmp_path):
        """--orchestrator claude-code:opus must produce params['orchestrator'] == 'claude-code'."""
        args = type('obj', (), {
            'debug': True,
            'team': 'full',
            'orchestrator': 'claude-code:opus',
            'exchanges': None,
            'cycles': None,
            'no_auto_commit': False,
            'effort': None,
        })()

        with patch("kodo.cli._params.get_team", autospec=True) as mock_get_team:
            mock_team = type('obj', (), {
                'name': 'full',
                'default_max_exchanges': 30,
                'default_max_cycles': 5,
            })()
            mock_get_team.return_value = mock_team
            result = _build_params_from_flags(args, tmp_path)

        assert result["orchestrator"] == "claude-code"

    def test_explicit_gemini_cli_orchestrator(self, tmp_path):
        """--orchestrator gemini-cli:gemini-pro must produce params['orchestrator'] == 'gemini-cli'."""
        args = type('obj', (), {
            'debug': True,
            'team': 'full',
            'orchestrator': 'gemini-cli:gemini-pro',
            'exchanges': None,
            'cycles': None,
            'no_auto_commit': False,
            'effort': None,
        })()

        with patch("kodo.cli._params.get_team", autospec=True) as mock_get_team:
            mock_team = type('obj', (), {
                'name': 'full',
                'default_max_exchanges': 30,
                'default_max_cycles': 5,
            })()
            mock_get_team.return_value = mock_team
            result = _build_params_from_flags(args, tmp_path)

        assert result["orchestrator"] == "gemini-cli"

    def test_orchestrator_not_overridden_by_defaults(self, tmp_path):
        """Explicit --orchestrator should NOT be replaced by auto-detection logic."""
        args = type('obj', (), {
            'debug': False,
            'team': 'full',
            'orchestrator': 'codex:codex-1',
            'exchanges': None,
            'cycles': None,
            'no_auto_commit': False,
            'effort': None,
        })()

        with (
            patch("kodo.cli._params.get_team", autospec=True) as mock_get_team,
            patch("kodo.cli._params.check_api_key", autospec=True, return_value=None),
            patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=False),
        ):
            mock_team = type('obj', (), {
                'name': 'full',
                'default_max_exchanges': 30,
                'default_max_cycles': 5,
            })()
            mock_get_team.return_value = mock_team
            result = _build_params_from_flags(args, tmp_path)

        # Even with GEMINI_API_KEY set, explicit orchestrator wins
        assert result["orchestrator"] == "codex"


# ---------------------------------------------------------------------------
# A10: --exchanges N / --cycles N
# ---------------------------------------------------------------------------


class TestExchangesCyclesFlagFlowsThrough:
    """Verify --exchanges and --cycles values reach params correctly."""

    def test_explicit_exchanges_overrides_team_default(self, tmp_path):
        """--exchanges 99 must produce params['max_exchanges'] == 99."""
        args = type('obj', (), {
            'debug': True,
            'team': 'full',
            'orchestrator': None,
            'exchanges': 99,
            'cycles': None,
            'no_auto_commit': False,
            'effort': None,
        })()

        with patch("kodo.cli._params.get_team", autospec=True) as mock_get_team:
            mock_team = type('obj', (), {
                'name': 'full',
                'default_max_exchanges': 30,
                'default_max_cycles': 5,
            })()
            mock_get_team.return_value = mock_team
            result = _build_params_from_flags(args, tmp_path)

        assert result["max_exchanges"] == 99

    def test_explicit_cycles_overrides_team_default(self, tmp_path):
        """--cycles 12 must produce params['max_cycles'] == 12."""
        args = type('obj', (), {
            'debug': True,
            'team': 'full',
            'orchestrator': None,
            'exchanges': None,
            'cycles': 12,
            'no_auto_commit': False,
            'effort': None,
        })()

        with patch("kodo.cli._params.get_team", autospec=True) as mock_get_team:
            mock_team = type('obj', (), {
                'name': 'full',
                'default_max_exchanges': 30,
                'default_max_cycles': 5,
            })()
            mock_get_team.return_value = mock_team
            result = _build_params_from_flags(args, tmp_path)

        assert result["max_cycles"] == 12

    def test_zero_exchanges_falls_back_to_default(self, tmp_path):
        """--exchanges 0 should use team default, not zero."""
        args = type('obj', (), {
            'debug': True,
            'team': 'full',
            'orchestrator': None,
            'exchanges': 0,
            'cycles': None,
            'no_auto_commit': False,
            'effort': None,
        })()

        with patch("kodo.cli._params.get_team", autospec=True) as mock_get_team:
            mock_team = type('obj', (), {
                'name': 'full',
                'default_max_exchanges': 30,
                'default_max_cycles': 5,
            })()
            mock_get_team.return_value = mock_team
            result = _build_params_from_flags(args, tmp_path)

        assert result["max_exchanges"] == 30  # team default, not 0

    def test_zero_cycles_falls_back_to_default(self, tmp_path):
        """--cycles 0 should use team default, not zero."""
        args = type('obj', (), {
            'debug': True,
            'team': 'full',
            'orchestrator': None,
            'exchanges': None,
            'cycles': 0,
            'no_auto_commit': False,
            'effort': None,
        })()

        with patch("kodo.cli._params.get_team", autospec=True) as mock_get_team:
            mock_team = type('obj', (), {
                'name': 'full',
                'default_max_exchanges': 30,
                'default_max_cycles': 5,
            })()
            mock_get_team.return_value = mock_team
            result = _build_params_from_flags(args, tmp_path)

        assert result["max_cycles"] == 5  # team default, not 0

    def test_negative_exchanges_falls_back_to_default(self, tmp_path):
        """--exchanges -1 should use team default (negative is invalid)."""
        args = type('obj', (), {
            'debug': True,
            'team': 'full',
            'orchestrator': None,
            'exchanges': -1,
            'cycles': None,
            'no_auto_commit': False,
            'effort': None,
        })()

        with patch("kodo.cli._params.get_team", autospec=True) as mock_get_team:
            mock_team = type('obj', (), {
                'name': 'full',
                'default_max_exchanges': 30,
                'default_max_cycles': 5,
            })()
            mock_get_team.return_value = mock_team
            result = _build_params_from_flags(args, tmp_path)

        assert result["max_exchanges"] == 30  # team default
