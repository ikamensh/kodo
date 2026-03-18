"""Validation helpers for benchmark results uploaded to the online store.

The online viewer should exclude obviously broken rows such as empty uploads,
no-op results, and Kodo runs where the orchestrator completed but a worker
failed internally.

    >>> suspicious_upload_reason(status="ok", elapsed_s=42, patch_len=12, agent_output={})
    'empty_agent_output'
    >>> suspicious_upload_reason(status="ok", elapsed_s=1.2, patch_len=0, agent_output={"status": "ok"})
    'no_patch'
    >>> suspicious_upload_reason(status="error", elapsed_s=42, patch_len=12, arm="kodo:solo", agent_output={"status": "error"})
    'kodo_worker_broken'
    >>> suspicious_upload_reason(status="ok", elapsed_s=42, patch_len=12, agent_output={"status": "ok"}) is None
    True
"""

from __future__ import annotations

from collections.abc import Iterable
import re

_BACKEND_FAILURE_MARKERS = (
    "usage limit",
    "rate limit",
    "quota",
    "upgrade to pro",
    "authentication",
    "api key",
    "not logged in",
    "login required",
    "unauthorized",
    "forbidden",
    "permission denied",
    "billing",
)

_KODO_WORKER_FAILURE_MARKERS = (
    "bound to a different event loop",
    "[worker] error:",
    "unknown error",
    "raise_issue",
)

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def suspicious_upload_reason(
    *,
    arm: str = "",
    status: str,
    elapsed_s: float,
    patch: str = "",
    patch_len: int | None = None,
    error: str = "",
    agent_output: dict | list | str | None = None,
) -> str | None:
    """Return a stable reason string when a result should not be uploaded."""
    effective_patch_len = patch_len if patch_len is not None else len(patch)
    texts = list(_iter_strings(agent_output))
    joined_text = _normalized_text("\n".join([error, *texts]))

    if _is_kodo_worker_broken(
        arm=arm, agent_output=agent_output, joined_text=joined_text
    ):
        return "kodo_worker_broken"

    if _is_empty_agent_output(agent_output):
        return "empty_agent_output"

    if effective_patch_len == 0:
        return "no_patch"

    if _contains_error_type(agent_output):
        return "agent_reported_error"

    if any(marker in joined_text for marker in _BACKEND_FAILURE_MARKERS):
        return "backend_failure"

    return None


def _is_empty_agent_output(agent_output: object) -> bool:
    return agent_output in ("", None, [], {})


def _is_kodo_worker_broken(*, arm: str, agent_output: object, joined_text: str) -> bool:
    if not arm.lower().startswith("kodo"):
        return False
    if isinstance(agent_output, dict) and agent_output.get("status") == "error":
        return True
    return "[worker]" in joined_text and any(
        marker in joined_text for marker in _KODO_WORKER_FAILURE_MARKERS
    )


def _normalized_text(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text).lower()


def _contains_error_type(value: object) -> bool:
    if isinstance(value, dict):
        node_type = value.get("type")
        if isinstance(node_type, str) and node_type.lower() == "error":
            return True
        return any(_contains_error_type(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_error_type(v) for v in value)
    return False


def _iter_strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)
