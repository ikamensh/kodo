"""Load and filter SWE-bench Lite tasks."""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class SWETask:
    instance_id: str  # e.g. "django__django-11099"
    repo: str  # e.g. "django/django"
    base_commit: str
    problem_statement: str
    fail_to_pass: list[str]
    pass_to_pass: list[str]
    version: str


def load_tasks(
    *,
    limit: int | None = None,
    instance_ids: list[str] | None = None,
    repo_filter: str | None = None,
    offset: int = 0,
) -> list[SWETask]:
    """Load SWE-bench Lite from HuggingFace, apply filters."""
    from datasets import load_dataset

    ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
    tasks = [_row_to_task(row) for row in ds]

    if instance_ids:
        id_set = set(instance_ids)
        tasks = [t for t in tasks if t.instance_id in id_set]
    elif repo_filter:
        tasks = [t for t in tasks if t.repo == repo_filter]

    tasks = tasks[offset:]
    if limit:
        tasks = tasks[:limit]

    return tasks


def _row_to_task(row: dict) -> SWETask:
    return SWETask(
        instance_id=row["instance_id"],
        repo=row["repo"],
        base_commit=row["base_commit"],
        problem_statement=row["problem_statement"],
        fail_to_pass=json.loads(row["FAIL_TO_PASS"]),
        pass_to_pass=json.loads(row["PASS_TO_PASS"]),
        version=row.get("version", ""),
    )
