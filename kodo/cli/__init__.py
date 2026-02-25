"""kodo interactive CLI — guided project setup and launch."""

# Re-export public API so all existing `from kodo.cli import X` imports work.

from kodo.cli._main import main, _main_inner  # noqa: F401
from kodo.cli._intake import (  # noqa: F401
    run_intake_auto,
    run_intake_chat,
    run_intake_noninteractive,
    get_goal,
    _looks_staged,
    _parse_goal_plan,
    _load_goal_plan,
)
from kodo.cli._improve import (  # noqa: F401
    run_improve_discovery,
    _build_fallback_plan,
    _extract_section,
)
from kodo.cli._launch import (  # noqa: F401
    launch_run,
    launch_resume,
    EXIT_SUCCESS,
    EXIT_ERROR,
    EXIT_PARTIAL,
    _fail,
    _emit_json_and_exit,
    _format_json_output,
)
from kodo.cli._subcommands import _cmd_runs, _cmd_backends, _cmd_teams  # noqa: F401
from kodo.cli._params import (  # noqa: F401
    select_params,
    _build_params_from_flags,
    _load_or_select_params,
)
from kodo.cli._ui import _print_banner, _atomic_write  # noqa: F401
