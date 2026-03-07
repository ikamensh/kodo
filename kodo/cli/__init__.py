"""kodo interactive CLI — guided project setup and launch."""

# Re-export public API so all existing `from kodo.cli import X` imports work.

from kodo.cli._improve import (  # noqa: F401
    _build_fallback_plan,
    _extract_section,
)
from kodo.cli._intake import (  # noqa: F401
    _load_goal_plan,
    _looks_staged,
    _parse_goal_plan,
    run_intake_auto,
    run_intake_chat,
    run_intake_noninteractive,
)
from kodo.cli._launch import launch_run  # noqa: F401
from kodo.cli._main import _main_inner, main  # noqa: F401
from kodo.cli._params import (  # noqa: F401
    _build_params_from_flags,
    _load_or_select_params,
)
from kodo.cli._subcommands import _cmd_runs  # noqa: F401
