"""Session protocol and shared types — re-export from kodo_workers.

This module is a compatibility shim: the canonical implementation now
lives in ``kodo_workers.sessions.base``.  Importing from here keeps
existing ``from kodo.sessions.base import …`` paths working, including
test patches like ``patch('kodo.sessions.base.subprocess', …)``.
"""

# Re-export the whole module namespace so mock.patch() targets and
# private helpers (e.g. ``_AUTH_PATTERNS``) resolve exactly as they used
# to when base.py lived under kodo/.
from kodo_workers.sessions.base import *  # noqa: F401,F403
from kodo_workers.sessions.base import (  # noqa: F401  (explicit for linters / underscored names)
    QueryResult,
    Session,
    SessionStats,
    SubprocessSession,
    _AUTH_PATTERNS,
    _BINARY_PATTERNS,
    _FALLBACK_SIGNAL_NAMES,
    _SUBSCRIPTION_PATTERNS,
    _SpawnedResult,
    _signal_name,
    classify_session_error,
    re,
    subprocess,
    threading,
    time,
)
