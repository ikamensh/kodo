"""Deterministic improve discovery helpers for mocked scripts."""

from __future__ import annotations

import tomllib
from pathlib import Path

from kodo.orchestrators.types import GoalPlan, GoalStage


APP_STAGE_NAMES = [
    "App Entry Points & Workflows",
    "Install & Run Experience",
    "Public Interface Usability",
    "Simplification & Dead Weight",
    "Architecture & Boundaries",
    "Triage & Verify",
    "Fix & Report",
]

LIBRARY_STAGE_NAMES = [
    "Package API Surface",
    "Consumer Examples & Docs",
    "Contracts & Error Handling",
    "Simplification & Dead Weight",
    "Architecture & Boundaries",
    "Triage & Verify",
    "Fix & Report",
]


def detect_project_type(project_dir: Path) -> str:
    """Classify the small mocked fixtures as either runnable apps or libraries."""
    pyproject = project_dir / "pyproject.toml"
    if pyproject.exists():
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        scripts = data.get("project", {}).get("scripts", {})
        if scripts:
            return "app"

    app_markers = ["main.py", "app.py", "manage.py", "__main__.py"]
    if any((project_dir / marker).exists() for marker in app_markers):
        return "app"

    return "library"


def build_mock_discovery_plan(project_type: str) -> GoalPlan:
    stage_names = APP_STAGE_NAMES if project_type == "app" else LIBRARY_STAGE_NAMES
    return GoalPlan(
        context=f"Detected project type: {project_type}",
        stages=[
            GoalStage(
                index=i,
                name=name,
                description=(
                    f"Mocked improve stage for detected {project_type} project."
                ),
                acceptance_criteria=(
                    "Stage is present in the plan handed to orchestrator."
                ),
                parallel_group=1 if i <= 5 else None,
                persist_changes=i == 7,
            )
            for i, name in enumerate(stage_names, start=1)
        ],
    )
