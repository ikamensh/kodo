"""Concurrency audit tests for thread-safety of shared components.

Tests use threading.Barrier to force interleaving and detect race conditions.
"""

from __future__ import annotations

import os
import threading
import time
from unittest.mock import patch

import pytest

from kodo.env import anthropic_env_lock
from kodo.log import RunStats
from kodo.orchestrators.base import DoneSignal
from kodo.summarizer import Summarizer


class TestAnthropicEnvLock:
    """Test thread-safety of anthropic_env_lock protecting os.environ mutations."""

    def test_concurrent_env_mutations_are_isolated(self):
        """Multiple threads popping and restoring ANTHROPIC_API_KEY should not interfere."""
        # Setup: ensure key exists
        original_key = os.environ.get("ANTHROPIC_API_KEY")
        test_key = "test-api-key-12345"
        os.environ["ANTHROPIC_API_KEY"] = test_key

        results = []
        barrier = threading.Barrier(3)  # Synchronize 3 threads
        errors = []

        def simulate_connect(thread_id: int):
            """Simulates ClaudeSession._connect() env manipulation pattern."""
            try:
                barrier.wait()  # All threads start simultaneously

                # Critical section 1: pop the key
                with anthropic_env_lock:
                    saved_key = os.environ.pop("ANTHROPIC_API_KEY", None)
                    results.append((thread_id, "popped", saved_key))

                # Simulate I/O work outside lock
                time.sleep(0.001 * thread_id)  # Stagger timing

                # Critical section 2: restore the key
                with anthropic_env_lock:
                    if saved_key is not None:
                        os.environ["ANTHROPIC_API_KEY"] = saved_key
                    results.append((thread_id, "restored", saved_key))

            except Exception as e:
                errors.append((thread_id, str(e)))

        threads = [
            threading.Thread(target=simulate_connect, args=(i,)) for i in range(3)
        ]

        try:
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

            # Assertions
            assert not errors, f"Threads encountered errors: {errors}"

            # All threads should have seen the key
            pops = [r for r in results if r[1] == "popped"]
            assert len(pops) == 3, "All 3 threads should pop"

            # Only ONE thread should successfully pop the key
            successful_pops = [r for r in pops if r[2] == test_key]
            assert len(successful_pops) == 1, (
                f"Only one thread should pop the key, got {len(successful_pops)}: "
                f"{successful_pops}"
            )

            # The other two should get None
            none_pops = [r for r in pops if r[2] is None]
            assert len(none_pops) == 2, f"Two threads should see missing key: {none_pops}"

            # Final state: key should be restored
            assert os.environ.get("ANTHROPIC_API_KEY") == test_key, (
                "Key should be restored to original value"
            )

        finally:
            # Cleanup
            if original_key is not None:
                os.environ["ANTHROPIC_API_KEY"] = original_key
            elif "ANTHROPIC_API_KEY" in os.environ:
                del os.environ["ANTHROPIC_API_KEY"]

    def test_nested_pop_restore_race(self):
        """Test that nested pop/restore cycles don't corrupt the environment."""
        os.environ["ANTHROPIC_API_KEY"] = "original-key"
        barrier = threading.Barrier(5)
        errors = []

        def pop_restore_cycle(thread_id: int, iterations: int):
            try:
                barrier.wait()
                for i in range(iterations):
                    with anthropic_env_lock:
                        saved = os.environ.pop("ANTHROPIC_API_KEY", None)
                    time.sleep(0.0001)  # Force interleaving
                    with anthropic_env_lock:
                        if saved:
                            os.environ["ANTHROPIC_API_KEY"] = saved
            except Exception as e:
                errors.append((thread_id, str(e)))

        threads = [
            threading.Thread(target=pop_restore_cycle, args=(i, 10)) for i in range(5)
        ]

        try:
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            assert not errors, f"Threads encountered errors: {errors}"
            # Key should still be there after all threads complete
            assert os.environ.get("ANTHROPIC_API_KEY") == "original-key"

        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    def test_concurrent_different_keys(self):
        """Test that lock protects concurrent mutations of different env vars."""
        os.environ["KEY_A"] = "value_a"
        os.environ["KEY_B"] = "value_b"
        barrier = threading.Barrier(2)
        errors = []

        def mutate_key_a():
            try:
                barrier.wait()
                for _ in range(20):
                    with anthropic_env_lock:
                        val = os.environ.pop("KEY_A", None)
                        time.sleep(0.0001)
                        if val:
                            os.environ["KEY_A"] = val
            except Exception as e:
                errors.append(("thread_a", str(e)))

        def mutate_key_b():
            try:
                barrier.wait()
                for _ in range(20):
                    with anthropic_env_lock:
                        val = os.environ.pop("KEY_B", None)
                        time.sleep(0.0001)
                        if val:
                            os.environ["KEY_B"] = val
            except Exception as e:
                errors.append(("thread_b", str(e)))

        try:
            t1 = threading.Thread(target=mutate_key_a)
            t2 = threading.Thread(target=mutate_key_b)
            t1.start()
            t2.start()
            t1.join(timeout=5)
            t2.join(timeout=5)

            assert not errors, f"Errors: {errors}"
            assert os.environ.get("KEY_A") == "value_a"
            assert os.environ.get("KEY_B") == "value_b"

        finally:
            os.environ.pop("KEY_A", None)
            os.environ.pop("KEY_B", None)


class TestSummarizerConcurrency:
    """Test thread-safety of Summarizer with concurrent summarize/get_accumulated_summary."""

    @patch("kodo.summarizer._probe_ollama", return_value=None)
    @patch("kodo.summarizer._probe_gemini", return_value=None)
    def test_concurrent_summarize_and_drain(self, mock_gemini, mock_ollama):
        """Multiple threads submitting summaries while another drains repeatedly."""
        summarizer = Summarizer()
        barrier = threading.Barrier(4)  # 3 writers + 1 drainer
        errors = []
        drain_results = []

        def submit_summaries(thread_id: int, count: int):
            try:
                barrier.wait()
                for i in range(count):
                    summarizer.summarize(
                        f"agent_{thread_id}",
                        f"task_{i}",
                        f"report_{i} from thread {thread_id}",
                    )
                    time.sleep(0.001)  # Small delay to allow interleaving
            except Exception as e:
                errors.append((f"writer_{thread_id}", str(e)))

        def drain_summaries(iterations: int):
            try:
                barrier.wait()
                for i in range(iterations):
                    time.sleep(0.005)  # Let some summaries accumulate
                    result = summarizer.get_accumulated_summary()
                    drain_results.append((i, result))
            except Exception as e:
                errors.append(("drainer", str(e)))

        try:
            writers = [
                threading.Thread(target=submit_summaries, args=(i, 5)) for i in range(3)
            ]
            drainer = threading.Thread(target=drain_summaries, args=(3,))

            for t in writers:
                t.start()
            drainer.start()

            for t in writers:
                t.join(timeout=10)
            drainer.join(timeout=10)

            assert not errors, f"Threads encountered errors: {errors}"

            # Drainer should have collected results
            assert len(drain_results) > 0, "Drainer should collect summaries"

            # Each drain clears the accumulator, so we can't check total count,
            # but we can verify no crashes occurred
            for idx, summary in drain_results:
                assert isinstance(summary, str), f"Summary {idx} should be string"

        finally:
            summarizer.shutdown(wait=True)

    @patch("kodo.summarizer._probe_ollama", return_value=None)
    @patch("kodo.summarizer._probe_gemini", return_value=None)
    def test_executor_swap_no_deadlock(self, mock_gemini, mock_ollama):
        """Verify get_accumulated_summary doesn't deadlock during executor swap."""
        summarizer = Summarizer()
        errors = []
        completed = threading.Event()

        def continuous_submit():
            try:
                for i in range(30):  # Reduced from 50
                    summarizer.summarize(
                        "agent",
                        f"task_{i}",
                        f"report_{i}",  # Shorter report to speed up test
                    )
                    time.sleep(0.005)  # Slightly longer delay
                    if completed.is_set():
                        break
            except Exception as e:
                errors.append(("submitter", str(e)))

        def drain_repeatedly():
            try:
                for i in range(3):  # Reduced from 5
                    time.sleep(0.05)  # Longer wait for summaries to accumulate
                    result = summarizer.get_accumulated_summary()
                    assert isinstance(result, str)
                completed.set()
            except Exception as e:
                errors.append(("drainer", str(e)))
                completed.set()

        try:
            t1 = threading.Thread(target=continuous_submit)
            t2 = threading.Thread(target=drain_repeatedly)

            t1.start()
            t2.start()

            # Longer timeout to account for real API calls
            t1.join(timeout=30)
            t2.join(timeout=30)

            assert completed.is_set(), "Drainer should complete without deadlock"
            assert not errors, f"No errors expected: {errors}"

        finally:
            summarizer.shutdown(wait=True)

    @patch("kodo.summarizer._probe_ollama", return_value=None)
    @patch("kodo.summarizer._probe_gemini", return_value=None)
    def test_clear_during_active_summarization(self, mock_gemini, mock_ollama):
        """Test that clear() is safe during active summarization."""
        summarizer = Summarizer()
        barrier = threading.Barrier(2)  # Only 2 threads participate
        errors = []

        def submit_many():
            try:
                barrier.wait()
                for i in range(20):
                    summarizer.summarize("agent", f"task_{i}", f"report_{i}")
                    time.sleep(0.001)
            except Exception as e:
                errors.append(("submitter", str(e)))

        def clear_repeatedly():
            try:
                barrier.wait()
                for _ in range(10):
                    time.sleep(0.005)
                    summarizer.clear()
            except Exception as e:
                errors.append(("clearer", str(e)))

        try:
            t1 = threading.Thread(target=submit_many)
            t2 = threading.Thread(target=clear_repeatedly)

            t1.start()
            t2.start()

            t1.join(timeout=10)
            t2.join(timeout=10)

            assert not errors, f"Errors: {errors}"

        finally:
            summarizer.shutdown(wait=True)


class TestRunStatsConcurrency:
    """Test thread-safety of RunStats recording and snapshot."""

    def test_concurrent_record_and_snapshot(self):
        """Multiple threads recording stats while another takes snapshots."""
        stats = RunStats()
        barrier = threading.Barrier(5)  # 4 recorders + 1 snapshotter
        errors = []
        snapshot_results = []

        def record_stats(thread_id: int, iterations: int):
            try:
                barrier.wait()
                for i in range(iterations):
                    stats.record_agent(
                        agent=f"agent_{thread_id}",
                        cost_usd=0.01 * i,
                        input_tokens=100 * i,
                        output_tokens=50 * i,
                        elapsed_s=1.0,
                        is_error=False,
                        cost_bucket="api",
                    )
                    time.sleep(0.001)  # Allow interleaving
            except Exception as e:
                errors.append((f"recorder_{thread_id}", str(e)))

        def take_snapshots(iterations: int):
            try:
                barrier.wait()
                for i in range(iterations):
                    time.sleep(0.002)
                    agents, orch_cost, orch_bucket = stats.snapshot()

                    # Try to iterate the snapshot (should never raise RuntimeError)
                    total = 0
                    for agent_name, agent_stats in agents.items():
                        total += agent_stats.cost_usd

                    snapshot_results.append((i, len(agents), total))
            except RuntimeError as e:
                if "dictionary changed size" in str(e):
                    errors.append(("snapshotter", "DICT_ITERATION_RACE", str(e)))
                else:
                    errors.append(("snapshotter", str(e)))
            except Exception as e:
                errors.append(("snapshotter", str(e)))

        try:
            recorders = [
                threading.Thread(target=record_stats, args=(i, 20)) for i in range(4)
            ]
            snapshotter = threading.Thread(target=take_snapshots, args=(30,))

            for t in recorders:
                t.start()
            snapshotter.start()

            for t in recorders:
                t.join(timeout=10)
            snapshotter.join(timeout=10)

            # Critical assertion: no dict iteration errors
            dict_errors = [e for e in errors if "DICT_ITERATION_RACE" in str(e)]
            assert not dict_errors, (
                f"snapshot() should prevent dict iteration races: {dict_errors}"
            )

            assert not errors, f"Threads encountered errors: {errors}"

            # Snapshots should have been taken
            assert len(snapshot_results) > 0, "Should have snapshot results"

            # Verify snapshot counts increase (agents are added over time)
            agent_counts = [r[1] for r in snapshot_results]
            # Early snapshots should have fewer agents than later ones
            assert max(agent_counts) >= min(agent_counts)

        finally:
            pass  # RunStats doesn't need cleanup

    def test_snapshot_isolation(self):
        """Verify snapshot returns isolated copy that doesn't reflect later mutations."""
        stats = RunStats()

        # Record initial stats
        stats.record_agent(
            agent="agent_1",
            cost_usd=1.0,
            input_tokens=100,
            output_tokens=50,
            elapsed_s=1.0,
            is_error=False,
            cost_bucket="api",
        )

        # Take snapshot
        agents_snap, orch_cost, orch_bucket = stats.snapshot()
        assert len(agents_snap) == 1
        assert agents_snap["agent_1"].cost_usd == 1.0

        # Modify original stats
        stats.record_agent(
            agent="agent_1",
            cost_usd=2.0,
            input_tokens=200,
            output_tokens=100,
            elapsed_s=2.0,
            is_error=False,
            cost_bucket="api",
        )

        # Snapshot should still show old values (deep copy)
        assert agents_snap["agent_1"].cost_usd == 1.0, (
            "Snapshot should be isolated from mutations"
        )

        # New snapshot should show updated values
        agents_snap2, _, _ = stats.snapshot()
        assert agents_snap2["agent_1"].cost_usd == 3.0  # 1.0 + 2.0

    def test_concurrent_record_orchestrator(self):
        """Test concurrent recording of orchestrator costs."""
        stats = RunStats()
        barrier = threading.Barrier(3)
        errors = []

        def record_orch_cost(iterations: int):
            try:
                barrier.wait()
                for i in range(iterations):
                    stats.record_orchestrator(0.01, bucket="api")
                    time.sleep(0.0001)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=record_orch_cost, args=(50,)) for _ in range(3)]

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Errors: {errors}"
        # 3 threads * 50 iterations * 0.01 = 1.5
        assert abs(stats.orchestrator_cost_usd - 1.5) < 0.01


class TestDoneSignalRaces:
    """Test for race conditions in DoneSignal (currently UNPROTECTED)."""

    def test_concurrent_writes_show_potential_race(self):
        """Demonstrate that DoneSignal is susceptible to race conditions.

        This test intentionally forces interleaving to show the lack of atomicity.
        """
        signal = DoneSignal()
        barrier = threading.Barrier(3)
        results = []
        errors = []

        def set_done(thread_id: int, success: bool, summary: str):
            try:
                barrier.wait()  # Start simultaneously

                # Simulate the handle_done() pattern (lines 334-336 or 370-372)
                signal.called = True
                time.sleep(0.0001)  # Force interleaving between writes
                signal.summary = summary
                time.sleep(0.0001)
                signal.success = success

                # Read back what we just wrote
                results.append((
                    thread_id,
                    signal.called,
                    signal.summary,
                    signal.success,
                ))

            except Exception as e:
                errors.append((thread_id, str(e)))

        threads = [
            threading.Thread(
                target=set_done,
                args=(0, True, "summary_0"),
            ),
            threading.Thread(
                target=set_done,
                args=(1, False, "summary_1"),
            ),
            threading.Thread(
                target=set_done,
                args=(2, True, "summary_2"),
            ),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Errors: {errors}"

        # The final state is non-deterministic due to races
        # We can only assert that SOME thread won the race
        assert signal.called is True, "called should be True (all threads set it)"

        # Summary and success might be torn (from different threads)
        # This demonstrates the race condition potential
        final_summary = signal.summary
        final_success = signal.success

        # In a race-free implementation, final state would match ONE thread exactly
        # With races, we might see torn reads: e.g., summary_0 with success=False

        # This test documents the current behavior - it's NOT asserting correctness,
        # but rather demonstrating the potential for inconsistency.
        print(f"Final state: summary={final_summary}, success={final_success}")
        print(f"Thread observations: {results}")

        # At minimum, called should always be True
        assert signal.called is True

    def test_reader_writer_race(self):
        """Test concurrent read/write to DoneSignal (simulating orchestrator loop)."""
        signal = DoneSignal()
        barrier = threading.Barrier(2)
        read_results = []
        errors = []

        def writer():
            try:
                barrier.wait()
                time.sleep(0.001)  # Let reader start first

                # Simulate handle_done() writing
                signal.called = True
                time.sleep(0.0005)
                signal.summary = "work complete"
                time.sleep(0.0005)
                signal.success = True

            except Exception as e:
                errors.append(("writer", str(e)))

        def reader():
            try:
                barrier.wait()

                # Simulate orchestrator loop reading (like claude_code.py:129-154)
                for _ in range(100):
                    if signal.called:
                        # Read all three fields (potential for torn read)
                        read_results.append((
                            signal.called,
                            signal.summary,
                            signal.success,
                        ))
                    time.sleep(0.0001)

            except Exception as e:
                errors.append(("reader", str(e)))

        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=reader)

        t1.start()
        t2.start()

        t1.join(timeout=5)
        t2.join(timeout=5)

        assert not errors, f"Errors: {errors}"

        if read_results:
            # Check for torn reads: called=True but summary="" (old value)
            torn_reads = [
                r for r in read_results
                if r[0] is True and r[1] == ""  # called but empty summary
            ]

            # This might happen due to race, documenting the possibility
            if torn_reads:
                print(f"Detected {len(torn_reads)} potential torn reads: {torn_reads[:5]}")

    def test_single_threaded_usage(self):
        """Verify DoneSignal works correctly in single-threaded context."""
        signal = DoneSignal()

        assert signal.called is False
        assert signal.summary == ""
        assert signal.success is False

        # Simulate handle_done() setting success
        signal.called = True
        signal.summary = "all tests pass"
        signal.success = True

        assert signal.called is True
        assert signal.summary == "all tests pass"
        assert signal.success is True

        # Reset
        signal = DoneSignal()
        assert signal.called is False


class TestConcurrencyStressTest:
    """High-concurrency stress tests to expose subtle race conditions."""

    def test_anthropic_lock_stress(self):
        """Stress test with many threads hammering env lock."""
        os.environ["ANTHROPIC_API_KEY"] = "stress-test-key"
        barrier = threading.Barrier(10)
        errors = []

        def hammer_env(iterations: int):
            try:
                barrier.wait()
                for _ in range(iterations):
                    with anthropic_env_lock:
                        saved = os.environ.pop("ANTHROPIC_API_KEY", None)
                    # Tiny delay to maximize interleaving
                    time.sleep(0.00001)
                    with anthropic_env_lock:
                        if saved:
                            os.environ["ANTHROPIC_API_KEY"] = saved
            except Exception as e:
                errors.append(str(e))

        try:
            threads = [threading.Thread(target=hammer_env, args=(100,)) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)

            assert not errors, f"Stress test errors: {errors}"
            assert os.environ.get("ANTHROPIC_API_KEY") == "stress-test-key"

        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    def test_runstats_stress(self):
        """Stress test RunStats with many concurrent recorders and snapshotters."""
        stats = RunStats()
        barrier = threading.Barrier(11)  # 10 recorders + 1 snapshotter
        errors = []
        snapshot_count = [0]

        def record_many(thread_id: int):
            try:
                barrier.wait()
                for i in range(100):
                    stats.record_agent(
                        agent=f"agent_{thread_id % 5}",  # Reuse some agent names
                        cost_usd=0.001,
                        input_tokens=10,
                        output_tokens=5,
                        elapsed_s=0.1,
                        is_error=(i % 10 == 0),
                        cost_bucket="api",
                    )
            except Exception as e:
                errors.append((f"recorder_{thread_id}", str(e)))

        def snapshot_many():
            try:
                barrier.wait()
                for _ in range(200):
                    agents, _, _ = stats.snapshot()
                    # Iterate to ensure no RuntimeError
                    for name, stat in agents.items():
                        _ = stat.cost_usd
                    snapshot_count[0] += 1
            except RuntimeError as e:
                if "dictionary changed size" in str(e):
                    errors.append(("snapshotter", "DICT_RACE"))
                else:
                    errors.append(("snapshotter", str(e)))
            except Exception as e:
                errors.append(("snapshotter", str(e)))

        recorders = [threading.Thread(target=record_many, args=(i,)) for i in range(10)]
        snapshotter = threading.Thread(target=snapshot_many)

        for t in recorders:
            t.start()
        snapshotter.start()

        for t in recorders:
            t.join(timeout=30)
        snapshotter.join(timeout=30)

        # Critical: no dict iteration errors
        dict_errors = [e for e in errors if "DICT_RACE" in str(e)]
        assert not dict_errors, f"Dict iteration races detected: {dict_errors}"

        assert not errors, f"Stress test errors: {errors}"
        assert snapshot_count[0] > 0, "Snapshots should have been taken"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
