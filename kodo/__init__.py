"""kodo — autonomous goal-driven coding agent."""

__version__ = "0.5.0"

# ---------------------------------------------------------------------------
# Compatibility shim: pydantic-ai 1.20 imports ``UserLocation`` from anthropic
# but anthropic 0.84 renamed it to ``BetaUserLocationParam``.  Alias it so
# pydantic-ai's ``from ... import UserLocation`` succeeds without upgrading
# mcp (blocked by kimi-agent-sdk's mcp<1.17 pin).
# Remove once kimi-agent-sdk lifts its mcp cap and we can upgrade pydantic-ai.
# ---------------------------------------------------------------------------
try:
    from anthropic.types.beta import beta_web_search_tool_20250305_param as _ws_mod

    if not hasattr(_ws_mod, "UserLocation") and hasattr(
        _ws_mod, "BetaUserLocationParam"
    ):
        _ws_mod.UserLocation = _ws_mod.BetaUserLocationParam  # type: ignore[attr-defined]
except ImportError:
    pass

from kodo import log
from kodo_workers import make_session


__all__ = [
    "__version__",
    "cli",
    "log",
    "make_session",
]


def __getattr__(name: str):
    """Lazy ``kodo.cli`` so ``patch('kodo.cli._params.…')`` resolves after ``import kodo`` only."""
    if name == "cli":
        import importlib

        return importlib.import_module("kodo.cli")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
