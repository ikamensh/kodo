"""Catch-all for prompts that don't fit other categories."""

SUMMARIZER_TEMPLATE = (
    "Summarize in 1 sentence what was accomplished. "
    "Be specific (mention file names, features, decisions). No preamble.\n\n"
    "Task: {task}\n"
    "Result: {report}"
)
