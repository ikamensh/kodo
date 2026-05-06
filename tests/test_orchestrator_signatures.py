"""Verify all orchestrator `cycle` methods accept the `coach` kwarg.

`OrchestratorBase.run()` unconditionally forwards `coach=coach` to
`self.cycle(...)`. Any subclass that overrides `cycle` without declaring
`coach` raises ``TypeError`` on the first cycle. These tests guard against
regressions by inspecting the resolved signature for each orchestrator class
(works whether `coach` is declared on the override or inherited).
"""

import inspect

from kodo.orchestrators.api import ApiOrchestrator
from kodo.orchestrators.claude_code import ClaudeCodeOrchestrator
from kodo.orchestrators.codex_cli import CodexOrchestrator
from kodo.orchestrators.cursor_cli import CursorOrchestrator
from kodo.orchestrators.gemini_cli import GeminiCliOrchestrator
from kodo.orchestrators.kimi_code import KimiCodeOrchestrator

ORCHESTRATORS = [
    ApiOrchestrator,
    ClaudeCodeOrchestrator,
    CodexOrchestrator,
    CursorOrchestrator,
    GeminiCliOrchestrator,
    KimiCodeOrchestrator,
]


def test_all_orchestrators_accept_coach_kwarg():
    missing = []
    for cls in ORCHESTRATORS:
        sig = inspect.signature(cls.cycle)
        if "coach" not in sig.parameters:
            missing.append(cls.__name__)
    assert not missing, f"orchestrators missing 'coach' kwarg: {missing}"


def test_coach_param_is_keyword_compatible():
    """`coach` must be passable as a keyword argument."""
    for cls in ORCHESTRATORS:
        sig = inspect.signature(cls.cycle)
        param = sig.parameters.get("coach")
        assert param is not None, f"{cls.__name__}.cycle missing 'coach'"
        assert param.kind in (
            inspect.Parameter.KEYWORD_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ), f"{cls.__name__}.cycle 'coach' not keyword-compatible: {param.kind}"
