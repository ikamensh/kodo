"""Tests for kodo.orchestrators.kimi_code.KimiCodeOrchestrator."""

from __future__ import annotations

from kodo.models import KIMI_K2_5


def test_kimi_code_orchestrator_construction():
    """KimiCodeOrchestrator can be constructed with defaults."""
    from kodo.orchestrators.kimi_code import KimiCodeOrchestrator

    orch = KimiCodeOrchestrator()
    assert orch.model == KIMI_K2_5
    assert orch._orchestrator_name == "kimi-code"
    assert orch._summarizer is not None


def test_kimi_code_orchestrator_custom_model():
    """KimiCodeOrchestrator respects custom model."""
    from kodo.orchestrators.kimi_code import KimiCodeOrchestrator

    orch = KimiCodeOrchestrator(model="kimi-k2", system_prompt="Custom prompt")
    assert orch.model == "kimi-k2"
    assert orch._system_prompt == "Custom prompt"


def test_build_orchestrator_kimi_code():
    """build_orchestrator('kimi-code') returns a KimiCodeOrchestrator."""
    from kodo.factory import build_orchestrator
    from kodo.orchestrators.kimi_code import KimiCodeOrchestrator

    orch = build_orchestrator("kimi-code")
    assert isinstance(orch, KimiCodeOrchestrator)
    assert orch.model == KIMI_K2_5


def test_build_orchestrator_kimi_code_custom_model():
    """build_orchestrator('kimi-code', model=...) passes model through."""
    from kodo.factory import build_orchestrator
    from kodo.orchestrators.kimi_code import KimiCodeOrchestrator

    orch = build_orchestrator("kimi-code", model="kimi-k2")
    assert isinstance(orch, KimiCodeOrchestrator)
    assert orch.model == "kimi-k2"
