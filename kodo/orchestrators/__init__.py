"""Orchestrator implementations for multi-agent coordination."""

from kodo.orchestrators.base import (
    CycleResult,
    Orchestrator,
    OrchestratorBase,
    RunResult,
    TeamConfig,
)

__all__ = [
    "CycleResult",
    "RunResult",
    "Orchestrator",
    "OrchestratorBase",
    "TeamConfig",
    "ApiOrchestrator",
    "ClaudeCodeOrchestrator",
    "GeminiCliOrchestrator",
    "CodexOrchestrator",
    "CursorOrchestrator",
]

_LAZY_IMPORTS = {
    "ApiOrchestrator": ("kodo.orchestrators.api", "ApiOrchestrator"),
    "ClaudeCodeOrchestrator": ("kodo.orchestrators.claude_code", "ClaudeCodeOrchestrator"),
    "GeminiCliOrchestrator": ("kodo.orchestrators.gemini_cli", "GeminiCliOrchestrator"),
    "CodexOrchestrator": ("kodo.orchestrators.codex_cli", "CodexOrchestrator"),
    "CursorOrchestrator": ("kodo.orchestrators.cursor_cli", "CursorOrchestrator"),
}


def __getattr__(name: str):
    if name in _LAZY_IMPORTS:
        module_path, class_name = _LAZY_IMPORTS[name]
        import importlib
        mod = importlib.import_module(module_path)
        return getattr(mod, class_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
