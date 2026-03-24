"""Meta-test: detect hardcoded deprecated AI model identifiers.

AI model IDs go stale quickly. This test scans source code for deprecated
model strings so they get updated to current versions.

Add ``# noqa: deprecated-model`` on a line to suppress a false positive.

To update: add new deprecated patterns to DEPRECATED_PATTERNS below,
then run the test to find all occurrences that need updating.
"""

import re
from pathlib import Path

import pytest

WORKSPACE_ROOT = Path(__file__).parent.parent
SRC_DIR = WORKSPACE_ROOT / "kodo"

# ---------------------------------------------------------------------------
# Deprecated model patterns
# ---------------------------------------------------------------------------
# (compiled_regex, human-readable reason)
# Matched against every non-comment source line in kodo/**/*.py.

DEPRECATED_PATTERNS: list[tuple[str, str]] = [
    # ---- Google Gemini ---- 2.x and older sunsetting June 2026
    (r"gemini-[012]\.", "Gemini 2.x and older are deprecated; use gemini-3.x+"),
    (r"gemini-3-pro-preview", "gemini-3-pro-preview shut down 2026-03-09; use gemini-3.1-pro-preview"),

    # ---- OpenAI ---- GPT-4 family, legacy reasoning models, stale GPT-5.x
    (r"gpt-3\.5", "GPT-3.5 is long deprecated"),
    (r"gpt-4", "GPT-4/4o/4.1 family is retired; use gpt-5.x"),
    (r"gpt-5\.[0-3]", "GPT-5.0–5.3 are outdated; use gpt-5.4"),
    (r'["\']o3["\']', "o3 is deprecated; use current OpenAI models"),
    (r"\bo3-", "o3-mini/o3-pro are deprecated"),
    (r"\bo1[\"'\s,\-]", "o1/o1-mini/o1-preview are deprecated"),
    (r"\bo4-mini\b", "o4-mini is retired"),

    # ---- Anthropic Claude ---- 3.x and older, plus stale 4.x
    (r"claude-instant", "claude-instant is long deprecated"),
    (r"claude-2[\.\b]", "Claude 2.x is deprecated"),
    (r"claude-3-", "Claude 3/3.5 are retired; use claude-*-4-5 or claude-*-4-6"),
    (r"(?:sonnet|opus|haiku)-4-0\b", "Claude 4.0 is outdated; use 4-5 or 4-6"),
    (r"(?:sonnet|opus)-4-5\b", "Claude Sonnet/Opus 4.5 is outdated; use 4-6"),

    # ---- xAI Grok ---- pre-4.1
    (r"grok-[23]\b", "Grok 2/3 are superseded; use grok-4.1"),
    (r"grok-4\b(?!\.)", "Grok 4.0 is superseded; use grok-4.1"),

    # ---- Mistral ---- deprecated aliases and dated versions
    (r"mistral-(?:tiny|medium)\b", "mistral-tiny/medium are long deprecated"),
    (r"codestral-\d{4}\b", "Dated codestral is deprecated; use codestral-latest or devstral-small-latest"),
]

_COMPILED = [(re.compile(pat), reason) for pat, reason in DEPRECATED_PATTERNS]

NOQA_TAG = "noqa: deprecated-model"


def _is_comment_only(line: str) -> bool:
    """True if the line is a pure comment (ignoring leading whitespace)."""
    return line.lstrip().startswith("#")


def _scan_source_files() -> list[tuple[Path, int, str, str]]:
    """Scan kodo/ sources for deprecated model strings.

    Returns list of (filepath, lineno, matched_text, reason).
    """
    violations: list[tuple[Path, int, str, str]] = []

    for py_file in sorted(SRC_DIR.rglob("*.py")):
        try:
            rel = py_file.relative_to(WORKSPACE_ROOT)
            if "archive" in rel.parts:
                continue
        except ValueError:
            continue

        try:
            lines = py_file.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue

        for lineno, line in enumerate(lines, start=1):
            if _is_comment_only(line):
                continue
            if NOQA_TAG in line:
                continue
            for pattern, reason in _COMPILED:
                match = pattern.search(line)
                if match:
                    violations.append((py_file, lineno, match.group(), reason))

    return violations


def test_no_deprecated_models():
    """Source code must not contain hardcoded deprecated model identifiers.

    Why: Model IDs go stale every few months. Hardcoded old IDs silently
    break or degrade when providers retire them. Centralise model IDs in
    kodo/models.py and keep them current.

    Fix: Update the model string to the current version. If suppression
    is truly needed, add ``# noqa: deprecated-model`` to the line.
    """
    violations = _scan_source_files()

    if violations:
        messages = []
        for filepath, lineno, matched, reason in violations:
            rel_path = filepath.relative_to(WORKSPACE_ROOT)
            messages.append(f"  {rel_path}:{lineno}  {matched!r} — {reason}")

        # Deduplicate (same file+line can match multiple patterns)
        unique = sorted(set(messages))

        pytest.fail(
            f"Found {len(unique)} deprecated model reference(s) in source code:\n"
            + "\n".join(unique)
            + "\n\nUpdate to current model IDs or add '# noqa: deprecated-model' to suppress."
        )
