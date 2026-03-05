"""Load and filter SWE-bench tasks (Pro or Lite)."""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field

DATASET_PRO = "ScaleAI/SWE-bench_Pro"
DATASET_VERIFIED = "princeton-nlp/SWE-bench_Verified"
DATASET_LITE = "princeton-nlp/SWE-bench_Lite"


@dataclass
class SWETask:
    instance_id: str  # e.g. "ansible__ansible-12345"
    repo: str  # e.g. "ansible/ansible"
    base_commit: str
    problem_statement: str
    fail_to_pass: list[str]
    pass_to_pass: list[str]
    version: str = ""
    repo_language: str = ""
    issue_categories: list[str] = field(default_factory=list)


def load_tasks(
    *,
    dataset: str = DATASET_PRO,
    limit: int | None = None,
    instance_ids: list[str] | None = None,
    repo_filter: str | None = None,
    language: str | None = None,
    offset: int = 0,
) -> list[SWETask]:
    """Load SWE-bench tasks from HuggingFace, apply filters."""
    from datasets import load_dataset

    ds = load_dataset(dataset, split="test")
    tasks = [_row_to_task(row) for row in ds]

    if instance_ids:
        id_set = set(instance_ids)
        tasks = [t for t in tasks if t.instance_id in id_set]
    else:
        if repo_filter:
            tasks = [t for t in tasks if t.repo == repo_filter]
        if language:
            tasks = [t for t in tasks if t.repo_language == language]

    tasks = tasks[offset:]
    if limit:
        tasks = tasks[:limit]

    return tasks


def _row_to_task(row: dict) -> SWETask:
    # Pro uses lowercase field names, Lite uses uppercase
    ftp = row.get("fail_to_pass") or row.get("FAIL_TO_PASS") or "[]"
    ptp = row.get("pass_to_pass") or row.get("PASS_TO_PASS") or "[]"

    return SWETask(
        instance_id=row["instance_id"],
        repo=row["repo"],
        base_commit=row["base_commit"],
        problem_statement=row["problem_statement"],
        fail_to_pass=_parse_list_field(ftp),
        pass_to_pass=_parse_list_field(ptp),
        version=row.get("version", ""),
        repo_language=row.get("repo_language", ""),
        issue_categories=_parse_list_field(row.get("issue_categories") or "[]"),
    )


def _parse_list_field(value: str | list) -> list:
    """Parse a field that may be a list, JSON string, or Python repr string."""
    if isinstance(value, list):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError):
        pass
    # SWE-bench Pro sometimes uses Python repr with single quotes
    try:
        result = ast.literal_eval(value)
        if isinstance(result, list):
            return result
    except (ValueError, SyntaxError):
        pass
    return []
