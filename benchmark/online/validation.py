"""Validation helpers for benchmark results uploaded to the online store.

The online viewer should exclude obvious dummy rows such as instant no-op
results or backend quota/auth failures disguised as successful runs.

    >>> suspicious_upload_reason(status="ok", elapsed_s=1.2, patch_len=0)
    'no_patch'
    >>> suspicious_upload_reason(status="ok", elapsed_s=42, patch_len=0)
    'no_patch'
    >>> suspicious_upload_reason(status="ok", elapsed_s=42, patch_len=12) is None
    True
"""

from __future__ import annotations

from collections.abc import Iterable

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


def suspicious_upload_reason(
    *,
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
    joined_text = "\n".join([error, *texts]).lower()

    if effective_patch_len == 0:
        return "no_patch"

    if _contains_error_type(agent_output):
        return "agent_reported_error_without_patch"

    if any(marker in joined_text for marker in _BACKEND_FAILURE_MARKERS):
        return "backend_failure_without_patch"

    return None


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
