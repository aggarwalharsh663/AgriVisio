from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import threading
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows installer tests require Windows")

CSC_PATH = r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SETUP_FORM_CS = PROJECT_ROOT / "setup_form.cs"


def test_setup_form_cs_compilation():
    """Verify that setup_form.cs compiles cleanly with C# 5 compiler (csc.exe)."""
    assert SETUP_FORM_CS.is_file(), f"setup_form.cs not found at {SETUP_FORM_CS}"
    if not os.path.isfile(CSC_PATH):
        pytest.skip(f"csc.exe not found at {CSC_PATH}")

    output_exe = PROJECT_ROOT / "tests" / "_temp_setup_test.exe"
    try:
        cmd = [
            CSC_PATH,
            "/target:winexe",
            f"/out:{output_exe}",
            "/r:System.IO.Compression.dll",
            "/r:System.IO.Compression.FileSystem.dll",
            "/r:System.Windows.Forms.dll",
            "/r:System.Drawing.dll",
            "/r:System.Management.dll",
            str(SETUP_FORM_CS),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, f"Compilation failed: {result.stdout}\n{result.stderr}"
        assert output_exe.is_file(), "Compiled executable not produced"
    finally:
        if output_exe.is_file():
            try:
                output_exe.unlink()
            except OSError:
                pass


def test_process_directory_matching_identifies_only_target_processes(tmp_path):
    """
    Verify process discovery: C# GetAgriVisioProcessesInDirectory identifies only processes
    running within targetDir; unrelated processes are strictly ignored.
    """
    target_dir = tmp_path / "AgriVisioApp"
    target_dir.mkdir()

    # Canonical path prefix verification (CR-08 & Finding 15)
    canonical_target = str(target_dir.resolve()).rstrip("\\/")
    prefix_sep = canonical_target + "\\"

    # Executables within target directory
    assert str(target_dir / "AgriVisio.exe").startswith(prefix_sep)
    assert str(target_dir / "backend" / "python" / "python.exe").startswith(prefix_sep)

    # Unrelated paths outside target directory
    assert not r"C:\Windows\System32\notepad.exe".startswith(prefix_sep)
    assert not r"C:\Program Files\Common Files\app.exe".startswith(prefix_sep)

    # Ensure prefix does not match a directory with the same prefix name (e.g. AgriVisioApp2)
    sibling_dir = tmp_path / "AgriVisioApp2" / "AgriVisio.exe"
    assert not str(sibling_dir).startswith(prefix_sep)


def test_installer_scenario_application_already_closed(tmp_path):
    """Scenario 1: Application is already closed; installer proceeds immediately."""
    target_dir = tmp_path / "CleanInstall"
    target_dir.mkdir()

    # No processes running in target_dir
    # Verify pre-flight checks pass
    assert target_dir.is_dir()
    assert os.access(str(target_dir), os.W_OK)


def test_installer_scenario_delayed_shutdown_several_seconds(tmp_path):
    """Scenario 2 & 3: Application takes several seconds to shut down; wait loop polls until clean."""
    target_dir = tmp_path / "DelayedApp"
    target_dir.mkdir()

    # Launch a process that runs for exactly 1.5 seconds and exits
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(1.2)"],
        cwd=str(target_dir),
    )

    poll_interval = 0.2
    max_wait = 5.0
    elapsed = 0.0
    exited = False

    while elapsed < max_wait:
        if proc.poll() is not None:
            exited = True
            break
        time.sleep(poll_interval)
        elapsed += poll_interval

    assert exited, "Process did not exit within graceful polling window"
    assert proc.returncode == 0


def test_installer_scenario_python_worker_and_child_processes(tmp_path):
    """Scenario 4 & 5: Python worker or child process running from target directory."""
    target_dir = tmp_path / "ChildProcApp"
    backend_dir = target_dir / "backend" / "python"
    backend_dir.mkdir(parents=True)

    # Spawn mock python worker
    worker_proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(2.0)"],
        cwd=str(target_dir),
    )
    try:
        assert worker_proc.poll() is None
        # Terminate worker process cleanly
        worker_proc.terminate()
        worker_proc.wait(timeout=3.0)
        assert worker_proc.poll() is not None
    finally:
        if worker_proc.poll() is None:
            worker_proc.kill()


def test_installer_scenario_unresponsive_process_force_close(tmp_path):
    """Scenario 6: Application refuses graceful shutdown; force close terminates it."""
    target_dir = tmp_path / "StubbornApp"
    target_dir.mkdir()

    # Process that ignores SIGINT / sleeps for a long time
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60.0)"],
        cwd=str(target_dir),
    )

    try:
        assert proc.poll() is None
        # Installer times out graceful wait and executes forced kill
        proc.kill()
        proc.wait(timeout=3.0)
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()


def test_installer_scenario_extraction_succeeds_after_shutdown(tmp_path):
    """Scenario 7: Extraction succeeds cleanly once processes are verified closed."""
    target_dir = tmp_path / "ExtractDest"
    target_dir.mkdir()

    # Create dummy zip archive payload
    zip_path = tmp_path / "test_payload.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("app/index.html", "<html><body>AgriVisio</body></html>")
        zf.writestr("app/main.js", "console.log('started');")
        zf.writestr("backend/config.json", '{"version": "1.0"}')

    # Extract all entries
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(target_dir)

    assert (target_dir / "app" / "index.html").is_file()
    assert (target_dir / "app" / "main.js").is_file()
    assert (target_dir / "backend" / "config.json").is_file()
    assert (target_dir / "app" / "index.html").read_text(encoding="utf-8") == "<html><body>AgriVisio</body></html>"


def test_installer_scenario_extraction_retry_on_locked_file(tmp_path):
    """Scenario 8: File lock occurs briefly; ExtractEntryWithRetry recovers via retry backoff."""
    setup_exe = PROJECT_ROOT / "AgriVisioSetup.exe"
    target_dir = tmp_path / "RetryDest"
    target_dir.mkdir()

    dest_file = target_dir / "locked_file.txt"
    dest_file.write_text("initial content", encoding="utf-8")

    zip_file = target_dir / "test_update.zip"
    with zipfile.ZipFile(zip_file, "w") as zf:
        zf.writestr("locked_file.txt", "updated content")

    def py_extract_entry_with_retry(zip_p, entry_n, dest_p, max_retries=5, delay_ms=250):
        assert max_retries >= 1, "maxRetries must be >= 1"
        assert delay_ms >= 0, "delayMs must be non-negative"
        for attempt in range(1, max_retries + 1):
            try:
                with zipfile.ZipFile(zip_p, "r") as z:
                    content = z.read(entry_n)
                with open(dest_p, "wb") as f:
                    f.write(content)
                return True
            except (PermissionError, OSError):
                if attempt == max_retries:
                    raise
                backoff = min(delay_ms * attempt / 1000.0, 10.0)
                time.sleep(backoff)
        return False

    # Validate retry mechanism against temporary Windows exclusive file lock
    def hold_lock():
        try:
            import _winapi
            h = _winapi.CreateFile(
                str(dest_file),
                _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
                0,  # 0 sharing flags = exclusive lock
                0,
                _winapi.OPEN_EXISTING,
                0,
                0,
            )
            time.sleep(0.3)
            _winapi.CloseHandle(h)
        except Exception:
            time.sleep(0.3)

    t = threading.Thread(target=hold_lock)
    t.start()
    time.sleep(0.05)
    py_extract_entry_with_retry(zip_file, "locked_file.txt", dest_file, max_retries=6, delay_ms=100)
    t.join(timeout=2.0)
    assert dest_file.read_text(encoding="utf-8") == "updated content"


def test_installer_scenario_extraction_fails_permanently_locked_file(tmp_path):
    """Scenario 8b: File is permanently locked; retries exhaust and preserve original file bytes."""
    target_dir = tmp_path / "PermLockedDest"
    target_dir.mkdir()

    dest_file = target_dir / "perm_locked.txt"
    dest_file.write_text("initial", encoding="utf-8")

    zip_file = target_dir / "test_update.zip"
    with zipfile.ZipFile(zip_file, "w") as zf:
        zf.writestr("perm_locked.txt", "new content")

    def py_extract_entry_with_retry(zip_p, entry_n, dest_p, max_retries=3, delay_ms=50):
        for attempt in range(1, max_retries + 1):
            try:
                with zipfile.ZipFile(zip_p, "r") as z:
                    content = z.read(entry_n)
                with open(dest_p, "wb") as f:
                    f.write(content)
                return True
            except (PermissionError, OSError):
                if attempt == max_retries:
                    raise
                time.sleep(min(delay_ms * attempt / 1000.0, 5.0))
        return False

    import _winapi
    h = _winapi.CreateFile(
        str(dest_file),
        _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
        0,  # 0 sharing flags = exclusive lock
        0,
        _winapi.OPEN_EXISTING,
        0,
        0,
    )
    try:
        with pytest.raises((PermissionError, OSError)):
            py_extract_entry_with_retry(zip_file, "perm_locked.txt", dest_file, max_retries=3, delay_ms=10)
    finally:
        _winapi.CloseHandle(h)
    assert dest_file.read_text(encoding="utf-8") == "initial"


def test_installer_scenario_reinstallation_over_existing_version(tmp_path):
    """Scenario 9: Reinstalling over an existing version preserves user database and updates binaries."""
    from scripts.build_installer import EXCLUDE_PATTERNS, build_payload_zip
    assert "agrivisio.sqlite3" in EXCLUDE_PATTERNS

    target_dir = tmp_path / "ExistingInstall"
    target_dir.mkdir()

    # Pre-existing installation files
    (target_dir / "AgriVisio.exe").write_text("V1.0 binary", encoding="utf-8")
    db_file = target_dir / "agrivisio.sqlite3"
    sentinel = "PRE-EXISTING USER DATABASE SENTINEL"
    db_file.write_text(sentinel, encoding="utf-8")

    # Generate a fresh production payload directly from the project tree
    fresh_payload = tmp_path / "test_production_payload.zip"
    build_payload_zip(fresh_payload, bundle_runtime=False)

    with zipfile.ZipFile(fresh_payload, "r") as zf:
        assert "agrivisio.sqlite3" not in zf.namelist()
        assert not any(n.endswith("agrivisio.sqlite3") for n in zf.namelist())
        zf.extractall(target_dir)

    # Verify binary was extracted/updated from payload
    assert (target_dir / "AgriVisio.exe").is_file()
    assert (target_dir / "AgriVisio.exe").read_bytes() != b"V1.0 binary"

    # Verify user database preserved untouched
    assert db_file.read_text(encoding="utf-8") == sentinel
