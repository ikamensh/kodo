"""Opt-in OpenCode configuration that confines native LLM calls to one model."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time

BUILTIN_AGENTS = ("build", "plan", "general", "explore", "title", "summary", "compaction")
CONFIG_TIMEOUT_S = 20


@contextmanager
def isolated_opencode_config(
    model: str,
    project_dir: Path,
    *,
    agent: str = "build",
    prompt: str | None = None,
    permission: str | dict | None = None,
    repository_instructions: bool = True,
    cancelled: threading.Event | None = None,
):
    """Yield a private subprocess environment after checking OpenCode's effective policy.

    Auth/session data remain in their normal location. User/project settings and
    plugins are excluded; repo AGENTS.md is explicitly retained when requested.
    A later org/managed override fails before a model call, rather than widening
    the permitted model catalog. Callers must also pass --pure and a fixed --title.
    """
    provider, separator, model_id = model.partition("/")
    if not separator or not provider or not model_id:
        raise ValueError("OpenCode model isolation requires an explicit provider/model")
    project_dir = project_dir.resolve()
    if isinstance(permission, str):
        permission = {"*": permission}
    agents = {name: {"model": model} for name in (*BUILTIN_AGENTS, agent)}
    if prompt is not None:
        agents[agent].update(mode="primary", prompt=prompt)
    if permission is not None:
        agents[agent]["permission"] = permission
    if permission is None:
        temp_roots = {Path(tempfile.gettempdir())}
        if os.name == "posix":
            temp_roots.add(Path("/tmp"))
        temp_roots |= {path.resolve() for path in temp_roots}
        permission = {
            "task": {"*": "deny", "general": "allow", "explore": "allow"},
            # Native cp/read of ordinary scratch files otherwise auto-rejects in headless mode.
            "external_directory": {str(path / "*"): "allow" for path in sorted(temp_roots)},
            "question": "deny", "plan_enter": "deny", "plan_exit": "deny",
        }
    instructions = []
    if repository_instructions and (project_dir / "AGENTS.md").is_file():
        instructions.append(str(project_dir / "AGENTS.md"))
    config = {
        "model": model, "small_model": model, "default_agent": agent,
        "share": "disabled", "enabled_providers": [provider],
        "provider": {provider: {"whitelist": [model_id]}},
        "agent": agents, "permission": permission, "instructions": instructions,
    }
    with tempfile.TemporaryDirectory(prefix="kodo-opencode-") as directory:
        root = Path(directory)
        (root / "home").mkdir()
        config_path = root / "opencode.json"
        config_path.write_text(json.dumps(config))
        env = {
            **os.environ, "PWD": str(project_dir),
            "XDG_CONFIG_HOME": str(root / "xdg"), "OPENCODE_CONFIG_DIR": directory,
            "OPENCODE_CONFIG": str(config_path), "OPENCODE_CONFIG_CONTENT": json.dumps(config),
            # OpenCode otherwise also discovers ~/.opencode despite a private XDG config.
            "OPENCODE_TEST_HOME": str(root / "home"),
            "OPENCODE_DISABLE_PROJECT_CONFIG": "1", "OPENCODE_DISABLE_CLAUDE_CODE": "1",
            "OPENCODE_PERMISSION": json.dumps(permission), "OPENCODE_AUTO_SHARE": "false",
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
        }
        env.pop("ANTHROPIC_API_KEY", None)
        try:
            process = subprocess.Popen(
                ["opencode", "debug", "config", "--pure"], cwd=project_dir, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
            )
            try:
                deadline = time.monotonic() + CONFIG_TIMEOUT_S
                while True:
                    if cancelled is not None and cancelled.is_set():
                        raise RuntimeError("OpenCode session cancelled")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(process.args, CONFIG_TIMEOUT_S)
                    try:
                        stdout, _ = process.communicate(
                            timeout=min(.1, remaining) if cancelled is not None else remaining,
                        )
                        break
                    except subprocess.TimeoutExpired:
                        continue
            except BaseException:
                # Config loading can start dependency installers that inherit its pipes.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()
                raise
            if process.returncode:
                raise subprocess.CalledProcessError(process.returncode, process.args)
            actual = json.loads(stdout)
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
            raise ValueError(
                f"OpenCode isolation preflight failed: config resolution ({type(error).__name__})"
            ) from error
        # Config content precedes org/managed settings in OpenCode's loader.
        # Check the effective values; never include config contents (possibly secrets) in errors.
        for key in ("model", "small_model", "default_agent", "share", "enabled_providers",
                    "permission", "instructions"):
            if actual.get(key) != config[key]:
                raise ValueError(f"OpenCode isolation preflight failed: overridden {key}")
        if actual.get("provider", {}).get(provider) != config["provider"][provider]:
            raise ValueError("OpenCode isolation preflight failed: overridden provider catalog")
        for name, expected in agents.items():
            effective = actual.get("agent", {}).get(name, {})
            if any(effective.get(key) != value for key, value in expected.items()):
                raise ValueError(f"OpenCode isolation preflight failed: overridden agent {name}")
        yield env
