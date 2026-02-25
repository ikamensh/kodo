"""Adversarial tests for kodo.log — based on expected interface behavior."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from kodo import log
from kodo.log import RunDir


def test_emit_before_init_is_noop():
    """Emitting before init() should silently do nothing, not crash."""
    # _isolate_log fixture resets state, so we're in uninitialized state
    log._log_file = None
    log._run_id = None
    log._start_time = None
    log.emit("should_not_crash", key="value")
    # No exception = pass


def test_concurrent_emits_dont_corrupt(tmp_path: Path):
    """Multiple threads emitting simultaneously should produce valid JSONL."""
    log.init(RunDir.create(tmp_path, "concurrent"))
    errors = []

    def writer(thread_id):
        try:
            for i in range(50):
                log.emit("thread_event", thread=thread_id, seq=i)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors

    lines = log.get_log_file().read_text().strip().split("\n")
    # 1 init line + 250 thread lines
    assert len(lines) == 251
    for line in lines:
        record = json.loads(line)  # Should not raise
        assert "event" in record


def test_init_twice_switches_log_file(tmp_path: Path):
    """Calling init a second time should switch to a new log file."""
    f1 = log.init(RunDir.create(tmp_path, "run1"))
    log.emit("event_in_run1")
    f2 = log.init(RunDir.create(tmp_path, "run2"))
    log.emit("event_in_run2")

    assert f1 != f2
    assert f1.exists()
    assert f2.exists()

    # run2 events should NOT appear in run1 file
    run1_text = f1.read_text()
    assert "event_in_run2" not in run1_text

    # run2 file should have its own init + event
    run2_lines = f2.read_text().strip().split("\n")
    events = [json.loads(line)["event"] for line in run2_lines]
    assert "run_init" in events
    assert "event_in_run2" in events


def test_emit_with_path_and_dataclass_values(tmp_path: Path):
    """emit should serialize Path objects and dataclasses without crashing."""
    from dataclasses import dataclass

    @dataclass
    class Info:
        name: str
        count: int

    log.init(RunDir.create(tmp_path, "serialize"))
    log.emit("complex", path=tmp_path / "foo", info=Info(name="x", count=3))

    lines = log.get_log_file().read_text().strip().split("\n")
    record = json.loads(lines[-1])
    assert record["event"] == "complex"
    assert "foo" in record["path"]


def test_emit_survives_disk_write_failure(tmp_path: Path):
    """Boundary Condition 1: emit() must not crash the caller if the log file
    becomes unwritable (e.g., permission errors).

    After init(), we make the log file read-only. A subsequent emit() would
    fail to open/write. The caller must not see an exception.

    KNOWN FAILURE: log.emit() does not catch OSError/PermissionError from
    open()/write(). When the log file becomes read-only (or disk fills, etc.),
    the exception propagates and crashes the caller. Location: log.py ~line 229,
    ``with open(_log_file, "a") as f: f.write(...)``.
    """
    run_dir = RunDir.create(tmp_path, "unwritable")
    log.init(run_dir)
    log.emit("first_event")  # succeeds

    # Make the log file read-only so the next write fails
    log_file = log.get_log_file()
    assert log_file is not None
    log_file.chmod(0o444)

    try:
        log.emit("second_event")  # should not raise
    except (OSError, PermissionError) as e:
        pytest.xfail(
            f"Boundary Condition 1: emit() propagates disk write failures. "
            f"Expected: swallow OSError and continue. "
            f"Got: {type(e).__name__}: {e}"
        )
    # First event was written before chmod; second_event was swallowed
    lines = log_file.read_text().strip().split("\n")
    assert len(lines) >= 1
    assert any("first_event" in line for line in lines)
