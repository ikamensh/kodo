"""User-level configuration from ~/.kodo/config.json."""

from __future__ import annotations

import functools
import json
from pathlib import Path


def _user_config_path() -> Path:
    """Lazy path to avoid Path.home() at import time."""
    return Path.home() / ".kodo" / "config.json"


@functools.lru_cache(maxsize=1)
def load_user_config() -> dict:
    """Load ~/.kodo/config.json. Returns empty dict if missing or invalid."""
    path = _user_config_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}


def clear_user_config_cache() -> None:
    """Invalidate the load_user_config() cache. Call after config changes or in tests."""
    load_user_config.cache_clear()


def get_user_default(key: str, default=None):
    """Get a user preference, e.g. get_user_default("fallback_model")."""
    return load_user_config().get(key, default)
