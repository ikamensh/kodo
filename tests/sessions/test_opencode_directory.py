"""OpenCode must write in the requested checkout despite its caller's logical PWD."""

import os
from pathlib import Path
import sys

import pytest

from kodo.sessions.opencode import OpenCodeSession


@pytest.mark.parametrize("resume_session", [None, "existing-session"])
def test_worker_uses_requested_checkout_in_fresh_and_resumed_sessions(
    tmp_path, monkeypatch, resume_session,
):
    """A real subprocess writes only in the selected project, including relative paths."""
    caller = tmp_path / "caller"
    project = caller / "target checkout"
    project.mkdir(parents=True)
    cli = tmp_path / "opencode"
    cli.write_text(f"#!{sys.executable}\n" + '''import json, os, sys
from pathlib import Path
args = sys.argv[1:]
# OpenCode run may select its project from inherited logical PWD unless --dir is explicit.
selected = Path(args[args.index("--dir") + 1] if "--dir" in args else os.environ["PWD"])
assert selected.is_absolute()
if "--session" in args:
    assert args[args.index("--session") + 1] == "existing-session"
    session = "existing-session"
else:
    session = "fresh-session"
(selected / "worker-result.txt").write_text(str(Path.cwd()))
print(json.dumps({"type": "text", "sessionID": session, "part": {"text": "done"}}))
''')
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("PWD", str(caller))
    monkeypatch.chdir(caller)

    session = OpenCodeSession(model="opencode/test-free", resume_session_id=resume_session)
    result = session.query("write the result", Path("target checkout"), max_turns=1)

    assert not result.is_error, result.text
    assert not (caller / "worker-result.txt").exists()
    assert (project / "worker-result.txt").read_text() == str(project.resolve())
    assert session.session_id == (resume_session or "fresh-session")
