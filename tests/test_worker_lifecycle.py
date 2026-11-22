from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from backend.ipc_worker import (
    _IS_SHUTTING_DOWN,
    _SHUTDOWN_LOCK,
    cleanup_worker_resources,
    get_parent_pid,
    graceful_shutdown,
    handle_health,
    handle_shutdown,
    process_ipc_message,
    run_worker_loop,
    start_parent_watchdog,
)
from backend.database import save_batch, get_batch, list_batches


def test_cleanup_worker_resources_executes_safely():
    """Verify that cleanup_worker_resources runs without errors and is safe to call repeatedly."""
    cleanup_worker_resources()
    cleanup_worker_resources()  # Verify idempotency


def test_worker_idempotent_shutdown(monkeypatch):
    """Verify that multiple concurrent graceful_shutdown calls are safe and idempotent (CR-10)."""
    _IS_SHUTTING_DOWN.clear()
    threads = []
    errors = []
    cleanup_calls = []

    def mock_cleanup():
        cleanup_calls.append(1)

    monkeypatch.setattr(sys, "exit", lambda code=0: None)
    monkeypatch.setattr(os, "_exit", lambda code=0: None)
    monkeypatch.setattr("backend.ipc_worker.cleanup_worker_resources", mock_cleanup)

    def _call_shutdown(idx: int):
        try:
            graceful_shutdown(exit_code=0, source=f"test_{idx}")
        except Exception as exc:
            errors.append(exc)

    for i in range(10):
        t = threading.Thread(target=_call_shutdown, args=(i,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=5.0)
        assert not t.is_alive()

    assert len(errors) == 0
    assert _IS_SHUTTING_DOWN.is_set()
    assert len(cleanup_calls) == 1


def test_worker_shutdown_ipc_action():
    """Shutdown frame followed by health frame cleanly terminates worker loop without dispatching health."""
    _IS_SHUTTING_DOWN.clear()
    input_data = (
        b'{"id": "msg-1", "action": "shutdown"}\n'
        b'{"id": "msg-2", "action": "health"}\n'
    )
    in_stream = io.BytesIO(input_data)
    out_stream = io.StringIO()

    run_worker_loop(in_stream=in_stream, out_stream=out_stream)

    # Worker must exit and NOT dispatch the subsequent health message
    lines = [line.strip() for line in out_stream.getvalue().strip().split("\n") if line.strip()]
    assert not any("msg-2" in line for line in lines)
    assert _IS_SHUTTING_DOWN.is_set()


def test_worker_exception_does_not_crash_loop():
    """An unhandled error inside an action handler returns ok: False without crashing worker loop."""
    _IS_SHUTTING_DOWN.clear()

    # Message 1 triggers an error (get_batch without batch_id)
    # Message 2 is valid (health)
    input_data = (
        b'{"id": "err-1", "action": "get_batch", "data": {}}\n'
        b'{"id": "ok-2", "action": "health"}\n'
    )
    in_stream = io.BytesIO(input_data)
    out_stream = io.StringIO()

    run_worker_loop(in_stream=in_stream, out_stream=out_stream)

    lines = [line.strip() for line in out_stream.getvalue().strip().split("\n") if line.strip()]
    assert len(lines) == 2

    res1 = json.loads(lines[0])
    assert res1["id"] == "err-1"
    assert res1["ok"] is False
    assert "batch_id is required" in res1["error"]

    res2 = json.loads(lines[1])
    assert res2["id"] == "ok-2"
    assert res2["ok"] is True
    assert res2["result"] == {"status": "ok"}


def test_parent_watchdog_detects_parent_exit():
    """
    Controlled test: spawn a temporary dummy parent process that terminates after a short sleep.
    Verify that the watchdog thread detects termination and initiates graceful shutdown.
    """
    _IS_SHUTTING_DOWN.clear()

    # Spawn short-lived dummy parent process
    parent_proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(0.3)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    parent_pid = parent_proc.pid

    shutdown_triggered = threading.Event()

    def mock_shutdown(exit_code=0, source="test"):
        _IS_SHUTTING_DOWN.set()
        shutdown_triggered.set()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("backend.ipc_worker.graceful_shutdown", mock_shutdown)
        watchdog = None
        try:
            watchdog = start_parent_watchdog(parent_pid=parent_pid, poll_interval=0.1)
            assert watchdog is not None

            # Wait for dummy parent to finish
            parent_proc.wait(timeout=3.0)

            # Watchdog should detect parent exit and trigger shutdown
            detected = shutdown_triggered.wait(timeout=3.0)
            assert detected, "Watchdog failed to detect parent process termination"
        finally:
            _IS_SHUTTING_DOWN.set()
            if watchdog is not None and watchdog.is_alive():
                watchdog.join(timeout=2.0)

    _IS_SHUTTING_DOWN.clear()


def test_parent_watchdog_nonexistent_parent():
    """Verify that passing a nonexistent PID is safely handled without hanging."""
    _IS_SHUTTING_DOWN.clear()
    shutdown_triggered = threading.Event()

    def mock_shutdown(exit_code=0, source="test"):
        _IS_SHUTTING_DOWN.set()
        shutdown_triggered.set()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("backend.ipc_worker.graceful_shutdown", mock_shutdown)
        watchdog = None
        try:
            # 9999999 is an invalid / non-existent PID
            watchdog = start_parent_watchdog(parent_pid=9999999, poll_interval=0.1)

            # On Windows, OpenProcess should fail immediately and trigger shutdown
            if os.name == "nt":
                assert watchdog is None
                assert shutdown_triggered.is_set()
            else:
                assert shutdown_triggered.wait(timeout=2.0)
        finally:
            _IS_SHUTTING_DOWN.set()
            if watchdog is not None and watchdog.is_alive():
                watchdog.join(timeout=3.0)
                assert not watchdog.is_alive()

    _IS_SHUTTING_DOWN.clear()


def test_repeated_worker_launches_and_database_access(tmp_path, monkeypatch):
    """
    Verify that multiple sequential worker launches can access the SQLite database
    without lock contention, file-in-use errors, or lingering connections.
    """
    from backend.ipc_worker import reset_cleanup_state
    db_file = tmp_path / "lifecycle_test.sqlite3"
    monkeypatch.setattr("backend.ipc_worker.DATABASE_PATH", db_file)

    batch_1 = {
        "batch_id": "BATCH-001",
        "created_at": "2026-11-20T08:00:00Z",
        "sample_name": "Worker 1 Batch",
        "variety": "Basmati",
    }
    batch_2 = {
        "batch_id": "BATCH-002",
        "created_at": "2026-11-20T08:05:00Z",
        "sample_name": "Worker 2 Batch",
        "variety": "IR64",
    }

    # Worker 1 writes batch 1
    save_batch(batch_1, db_file)
    assert get_batch("BATCH-001", db_file)["sample_name"] == "Worker 1 Batch"

    # Worker 1 terminates and cleans up
    cleanup_worker_resources()
    reset_cleanup_state()

    # Worker 2 immediately launches and accesses the same database
    save_batch(batch_2, db_file)
    batches = list_batches(db_file)
    assert len(batches) == 2
    batch_ids = {b["batch_id"] for b in batches}
    assert batch_ids == {"BATCH-001", "BATCH-002"}

    # Worker 2 cleans up
    cleanup_worker_resources()
    reset_cleanup_state()


def test_get_parent_pid_resolution():
    """Verify that get_parent_pid resolves PID from args and env correctly."""
    # Explicit arg
    assert get_parent_pid(1234) == 1234

    # Env var
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("AGRIVISIO_PARENT_PID", "5678")
        assert get_parent_pid() == 5678

    # CLI arg
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("AGRIVISIO_PARENT_PID", raising=False)
        mp.setattr(sys, "argv", ["ipc_worker.py", "--parent-pid", "9012"])
        assert get_parent_pid() == 9012
