"""Exercise model isolation across real subprocess config resolution and worker calls."""

import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile

import pytest

from kodo.sessions.opencode import OpenCodeSession


@pytest.fixture
def isolated_cli(tmp_path, monkeypatch):
    """Personal/project paid settings cannot survive the worker's isolated configuration."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("Keep the repository instructions.\n")
    hostile = {"model": "paid/main", "agent": {"title": {"model": "paid/title"},
                                               "explore": {"model": "paid/explore"}}}
    (project / "opencode.json").write_text(json.dumps(hostile))
    config = tmp_path / "personal" / "opencode"
    config.mkdir(parents=True)
    (config / "opencode.json").write_text(json.dumps(hostile))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config.parent))
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", json.dumps(hostile))
    cli = tmp_path / "opencode"
    cli.write_text(f"#!{sys.executable}\n" + '''import json, os, sys
from pathlib import Path
args = sys.argv[1:]
config = json.loads(os.environ["OPENCODE_CONFIG_CONTENT"])
assert "--pure" in args
assert os.environ["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"
assert Path(os.environ["OPENCODE_CONFIG_DIR"]) != Path.cwd()
assert Path(os.environ["XDG_CONFIG_HOME"]) != Path(os.environ["HOSTILE_CONFIG_HOME"])
assert os.environ["PWD"] == str(Path.cwd())
assert Path(os.environ["OPENCODE_TEST_HOME"]).is_dir()
assert "ANTHROPIC_API_KEY" not in os.environ
if "debug" in args:
    if os.environ.get("OVERRIDE_AFTER_ENV") == "compaction":
        config["agent"]["compaction"]["model"] = "paid/compaction"
    if os.environ.get("OVERRIDE_AFTER_ENV") == "provider":
        config["provider"]["opencode"]["whitelist"].append("paid-model")
    print(json.dumps(config))
else:
    assert args[args.index("--dir") + 1] == str(Path.cwd())
    assert args[args.index("--agent") + 1] == "build"
    assert args[args.index("--title") + 1]
    assert config["small_model"] == config["model"] == "opencode/test-free"
    assert config["enabled_providers"] == ["opencode"]
    assert config["provider"]["opencode"]["whitelist"] == ["test-free"]
    assert {a["model"] for a in config["agent"].values()} == {"opencode/test-free"}
    assert "Keep the repository instructions." in Path(config["instructions"][0]).read_text()
    with Path("calls.jsonl").open("a") as out:
        out.write(json.dumps({"args": args, "config": config, "config_dir": os.environ["OPENCODE_CONFIG_DIR"]}) + "\\n")
    print(json.dumps({"type": "text", "sessionID": "isolated-session", "part": {"text": "done"}}))
''')
    cli.chmod(0o755)
    monkeypatch.setenv("HOSTILE_CONFIG_HOME", str(config.parent))
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    return project


def test_isolation_survives_resume_clone_and_preserves_caller_environment(isolated_cli):
    """Every worker subprocess is pinned, while parent settings and repo files stay intact."""
    before = os.environ.copy()
    session = OpenCodeSession(model="opencode/test-free", isolated_model=True)
    for active in (session, session, session.clone()):
        result = active.query("work", isolated_cli, max_turns=1)
        assert not result.is_error, result.text
    calls = [json.loads(line) for line in (isolated_cli / "calls.jsonl").read_text().splitlines()]
    assert "--session" not in calls[0]["args"]
    assert "--session" in calls[1]["args"]
    assert "--session" not in calls[2]["args"]
    assert all(not Path(call["config_dir"]).exists() for call in calls)
    assert os.environ == before
    assert json.loads((isolated_cli / "opencode.json").read_text())["model"] == "paid/main"


@pytest.mark.parametrize("override", ["compaction", "provider"])
def test_later_managed_model_overrides_stop_before_model_execution(
    isolated_cli, monkeypatch, override,
):
    """OpenCode's later org/managed config merge cannot silently widen the selected model."""
    monkeypatch.setenv("OVERRIDE_AFTER_ENV", override)
    with pytest.raises(ValueError, match="OpenCode isolation preflight failed:"):
        OpenCodeSession(model="opencode/test-free", isolated_model=True).query(
            "work", isolated_cli, max_turns=1,
        )
    assert not (isolated_cli / "calls.jsonl").exists()


def test_config_timeout_terminates_dependency_children(tmp_path, monkeypatch):
    """A stalled config loader and its installer cannot survive preflight timeout."""
    from kodo import opencode_config

    cli = tmp_path / "opencode"
    cli.write_text(f"#!{sys.executable}\n" + '''import json, os, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
Path("pids.json").write_text(json.dumps([os.getpid(), child.pid]))
time.sleep(60)
''')
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(opencode_config, "CONFIG_TIMEOUT_S", .3)

    with pytest.raises(ValueError, match="OpenCode isolation preflight failed:.*TimeoutExpired"):
        OpenCodeSession(model="opencode/test-free", isolated_model=True).query("work", tmp_path, max_turns=1)

    for pid in json.loads((tmp_path / "pids.json").read_text()):
        state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
        assert not state or state.startswith("Z"), f"preflight child {pid} survived: {state}"


def test_blanket_permissions_use_opencode_effective_map(isolated_cli):
    """OpenCode normalizes a permission string into a wildcard map when loading config."""
    from kodo.opencode_config import isolated_opencode_config

    with isolated_opencode_config(
        "opencode/test-free", isolated_cli, agent="hive-llm", prompt="Answer only.",
        permission="deny", repository_instructions=False,
    ) as env:
        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        assert config["permission"] == {"*": "deny"}
        assert config["agent"]["hive-llm"]["permission"] == {"*": "deny"}
        assert json.loads(env["OPENCODE_PERMISSION"]) == {"*": "deny"}


def test_isolated_worker_can_use_system_temp_without_interactive_permissions(isolated_cli):
    """Autonomous code/test work includes temporary files, while unknown subagents stay denied."""
    result = OpenCodeSession(model="opencode/test-free", isolated_model=True).query(
        "work", isolated_cli, max_turns=1,
    )
    assert not result.is_error
    call = json.loads((isolated_cli / "calls.jsonl").read_text())
    permissions = call["config"]["permission"]
    roots = {Path(tempfile.gettempdir()), Path(tempfile.gettempdir()).resolve(), Path("/tmp"), Path("/tmp").resolve()}
    for root in roots:
        assert permissions["external_directory"][str(root / "*")] == "allow"
    assert permissions["task"] == {"*": "deny", "general": "allow", "explore": "allow"}
    assert permissions["question"] == permissions["plan_enter"] == permissions["plan_exit"] == "deny"
    assert "--auto" not in call["args"]


def test_cancel_during_preflight_never_launches_worker(tmp_path, monkeypatch):
    """Stopping an active task includes config resolution, before any coding model starts."""
    import concurrent.futures
    import time

    cli = tmp_path / "opencode"
    cli.write_text(f"#!{sys.executable}\n" + '''import json, os, sys, time
from pathlib import Path
if "debug" in sys.argv:
    Path("preflight-started").touch()
    time.sleep(.8)
    print(os.environ["OPENCODE_CONFIG_CONTENT"])
else:
    Path("worker-started").touch()
    print(json.dumps({"type": "text", "part": {"text": "ran after cancellation"}}))
''')
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    session = OpenCodeSession(model="opencode/test-free", isolated_model=True)
    with concurrent.futures.ThreadPoolExecutor() as executor:
        pending = executor.submit(session.query, "work", tmp_path, max_turns=1)
        deadline = time.monotonic() + 3
        while not (tmp_path / "preflight-started").exists():
            assert time.monotonic() < deadline
            time.sleep(.01)
        session.terminate()
        with pytest.raises(RuntimeError, match="cancelled"):
            pending.result(timeout=3)
    assert not (tmp_path / "worker-started").exists()
    session.reset()
    assert not session.query("work again", tmp_path, max_turns=1).is_error
    assert (tmp_path / "worker-started").exists()


def test_cancel_worker_stops_native_tool_children(tmp_path, monkeypatch):
    """Native commands cannot keep changing the checkout after task cancellation."""
    import concurrent.futures
    import time

    cli = tmp_path / "opencode"
    cli.write_text(f"#!{sys.executable}\n" + '''import json, os, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, "-c", "import time; from pathlib import Path; time.sleep(.8); Path('late-write').touch()"])
Path("child-started").write_text(str(child.pid))
time.sleep(10)
''')
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    session = OpenCodeSession(model="opencode/test-free")
    with concurrent.futures.ThreadPoolExecutor() as executor:
        pending = executor.submit(session.query, "work", tmp_path, max_turns=1)
        deadline = time.monotonic() + 3
        while not (tmp_path / "child-started").exists():
            assert time.monotonic() < deadline
            time.sleep(.01)
        session.terminate()
        assert pending.result(timeout=3).is_error
    time.sleep(1)
    assert not (tmp_path / "late-write").exists()
