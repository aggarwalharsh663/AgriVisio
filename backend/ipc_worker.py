from __future__ import annotations

import io
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import cv2
import numpy as np

from backend.hardware_scanner import (
    HardwareScanUnsupportedError,
    ScannerNotFoundError,
    ScannerOperationError,
    ScannerTimeoutError,
    _try_scan_wia,
    acquire_hardware_scan,
    resolve_scanner_calibration,
)
from backend.image_security import (
    CorruptImageError,
    DecompressionBombError,
    OversizedFileError,
    UnsupportedFormatError,
    UPLOAD_MAX_BYTES,
    UPLOAD_MAX_PIXELS,
    validate_image_header,
)
from backend.main import (
    DATA_DIR,
    DATABASE_PATH,
    ScanRequest,
    _load_model,
    _make_mock_batch,
    _make_real_batch,
    _make_uploaded_batch,
    batches,
    get_batch,
    is_mock_mode_enabled,
    register_bootstrap_secret,
    consume_bootstrap_secret,
    get_or_create_api_token,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOGGER = logging.getLogger("agrivisio_ipc_worker")

# Centralized bounded IPC limits (10 MB max message size, UTF-8 encoded)
MAX_IPC_BYTES: int = 10 * 1024 * 1024
DRAIN_CHUNK_SIZE: int = 64 * 1024

# Thread synchronization and lifecycle management
_BUSY_WORK_LOCK = threading.RLock()
_SHUTDOWN_LOCK = threading.Lock()
_IS_SHUTTING_DOWN = threading.Event()
_CLEANUP_LOCK = threading.Lock()
_CLEANUP_DONE = False


def reset_cleanup_state() -> None:
    """Reset cleanup flag for testing lifecycle resets."""
    global _CLEANUP_DONE
    with _CLEANUP_LOCK:
        _CLEANUP_DONE = False


def cleanup_worker_resources() -> None:
    """
    Safely release application resources (strictly idempotent with single owner):
    1. Checkpoint and finalize SQLite connections.
    2. Clear model memory cache.
    3. Flush standard output and error buffers.
    """
    global _CLEANUP_DONE
    with _CLEANUP_LOCK:
        if _CLEANUP_DONE:
            return
        _CLEANUP_DONE = True

    LOGGER.info("Cleaning up IPC worker resources...")
    # 1. Close / checkpoint SQLite database to prevent lingering WAL/SHM locks
    try:
        if DATABASE_PATH.is_file():
            import sqlite3
            import contextlib
            with contextlib.closing(sqlite3.connect(DATABASE_PATH, timeout=5)) as conn:
                conn.execute("PRAGMA wal_checkpoint(PASSIVE);")
    except Exception as exc:
        LOGGER.warning("Could not execute final WAL checkpoint: %s", exc)

    # 2. Release model cache
    try:
        if hasattr(_load_model, "cache_clear"):
            _load_model.cache_clear()
    except Exception as exc:
        LOGGER.warning("Could not clear model cache: %s", exc)

    # 3. Flush output streams
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass


def graceful_shutdown(exit_code: int = 0, source: str = "normal") -> None:
    """
    Idempotent, thread-safe graceful shutdown.
    Ensures in-flight work/database writes finish safely before exit.
    """
    with _SHUTDOWN_LOCK:
        if _IS_SHUTTING_DOWN.is_set():
            return
        _IS_SHUTTING_DOWN.set()

    LOGGER.info("Graceful shutdown initiated (source: %s, code: %d).", source, exit_code)

    # Wait up to 15 seconds for any active database write / analysis to complete safely
    acquired = _BUSY_WORK_LOCK.acquire(timeout=15.0)
    if acquired:
        try:
            cleanup_worker_resources()
        finally:
            _BUSY_WORK_LOCK.release()
    else:
        LOGGER.warning("Work lock timed out during graceful shutdown; in-flight work still in progress.")

    # Terminate process cleanly
    if threading.current_thread() is threading.main_thread():
        sys.exit(exit_code)
    else:
        os._exit(exit_code)


def handle_health(data: dict) -> dict:
    return {"status": "ok"}


def handle_heartbeat(data: dict) -> dict:
    return {"status": "alive"}


def handle_scan(data: dict) -> dict:
    req = ScanRequest(**data)
    if is_mock_mode_enabled():
        return _make_mock_batch(req)
    return _make_real_batch(req)


def _parse_calibration_float(val: Any, field_name: str) -> float | None:
    """Validate and convert optional calibration parameter from IPC payload."""
    if val is None:
        return None
    if isinstance(val, bool):
        raise ValueError(f"{field_name} cannot be a boolean")
    if isinstance(val, str):
        s = val.strip()
        if not s or s.lower() in ("null", "none", "undefined"):
            return None
        try:
            num = float(s)
        except ValueError:
            raise ValueError(f"{field_name} must be a valid numeric value.")
    elif isinstance(val, (int, float)):
        num = float(val)
    else:
        raise ValueError(f"{field_name} must be a valid numeric value.")

    if not np.isfinite(num):
        raise ValueError(f"{field_name} must be a finite number.")
    if num <= 0:
        raise ValueError(f"{field_name} must be greater than zero.")
    if (num * num) <= 0 or not np.isfinite(num * num) or (num * num) < 1e-12:
        raise ValueError(f"{field_name} is too small to provide a valid pixel area resolution.")
    return num


def handle_scan_upload(data: dict) -> dict:
    file_path = data.get("file_path")
    sample_name = data.get("sample_name", "").strip()
    variety = data.get("variety", "").strip()
    lighting = data.get("lighting", "ring_light")

    if not sample_name or len(sample_name) > 80:
        raise ValueError("Sample name must contain 1 to 80 characters.")
    if not variety or len(variety) > 80:
        raise ValueError("Rice variety must contain 1 to 80 characters.")
    if lighting not in {"ring_light", "daylight", "tube_light"}:
        raise ValueError("Unknown lighting profile.")

    if not file_path or not os.path.isfile(file_path):
        raise FileNotFoundError(f"Uploaded file path not found: {file_path}")

    if os.path.getsize(file_path) > UPLOAD_MAX_BYTES:
        raise ValueError("Photo must be 10 MB or smaller.")

    with open(file_path, "rb") as f:
        raw_bytes = f.read(UPLOAD_MAX_BYTES + 1)

    if len(raw_bytes) > UPLOAD_MAX_BYTES:
        raise ValueError("Photo must be 10 MB or smaller.")

    # Pre-decode header validation to prevent decompression bombs and memory exhaustion
    try:
        width, height, img_fmt = validate_image_header(
            raw_bytes, max_bytes=UPLOAD_MAX_BYTES, max_pixels=UPLOAD_MAX_PIXELS
        )
    except OversizedFileError as exc:
        raise ValueError("Photo must be 10 MB or smaller.") from exc
    except DecompressionBombError as exc:
        raise ValueError("Photo dimensions must be 20 megapixels or smaller.") from exc
    except (UnsupportedFormatError, CorruptImageError) as exc:
        raise ValueError("Choose a valid JPEG, PNG, or WebP photo.") from exc

    # Decode safely with OpenCV after validation
    image = cv2.imdecode(np.frombuffer(raw_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Choose a valid JPEG, PNG, or WebP photo.")

    calibration_mm_per_pixel = _parse_calibration_float(
        data.get("calibration_mm_per_pixel"), "calibration_mm_per_pixel"
    )
    reference_length_mm = _parse_calibration_float(
        data.get("reference_length_mm"), "reference_length_mm"
    )
    reference_pixels = _parse_calibration_float(
        data.get("reference_pixels"), "reference_pixels"
    )

    if (reference_length_mm is not None and reference_pixels is None) or (
        reference_length_mm is None and reference_pixels is not None
    ):
        raise ValueError(
            "Both reference_length_mm and reference_pixels must be provided together for physical reference calibration."
        )

    return _make_uploaded_batch(
        image,
        sample_name,
        variety,
        lighting,
        raw_bytes=raw_bytes,
        calibration_mm_per_pixel=calibration_mm_per_pixel,
        reference_length_mm=reference_length_mm,
        reference_pixels=reference_pixels,
    )


def handle_scan_hardware(data: dict) -> dict:
    """Acquire image from hardware scanner and run grain analysis."""
    sample_name = data.get("sample_name", "").strip()
    variety = data.get("variety", "").strip()
    lighting = data.get("lighting", "ring_light")
    calibration_mm_per_pixel = _parse_calibration_float(
        data.get("calibration_mm_per_pixel"), "calibration_mm_per_pixel"
    )
    scanner_dpi = data.get("scanner_dpi") or data.get("dpi")
    if scanner_dpi is not None:
        try:
            if isinstance(scanner_dpi, (tuple, list)) and len(scanner_dpi) == 2:
                scanner_dpi = (float(scanner_dpi[0]), float(scanner_dpi[1]))
            else:
                scanner_dpi = float(scanner_dpi)
                if scanner_dpi <= 0 or not np.isfinite(scanner_dpi):
                    scanner_dpi = None
        except (ValueError, TypeError):
            scanner_dpi = None

    if not sample_name or len(sample_name) > 80:
        raise ValueError("Sample name must contain 1 to 80 characters.")
    if not variety or len(variety) > 80:
        raise ValueError("Rice variety must contain 1 to 80 characters.")
    if lighting not in {"ring_light", "daylight", "tube_light"}:
        raise ValueError("Unknown lighting profile.")

    image, raw_bytes = acquire_hardware_scan()
    calib = resolve_scanner_calibration(
        raw_bytes=raw_bytes,
        image=image,
        explicit_dpi=scanner_dpi,
        explicit_scale=calibration_mm_per_pixel,
    )

    return _make_uploaded_batch(
        image,
        sample_name,
        variety,
        lighting,
        raw_bytes=raw_bytes,
        calibration_mm_per_pixel=calib["mm_per_pixel"],
        calibration_status=calib["calibration_status"],
        reference_type=calib["calibration_reference_type"],
        scanner_dpi=calib["scanner_dpi"],
        source_type="FLATBED_SCANNER",
    )


def _parse_int_param(val: Any, name: str, min_val: int, max_val: int | None = None) -> int | None:
    if val is None:
        return None
    if isinstance(val, bool):
        raise ValueError(f"{name} cannot be a boolean")
    if isinstance(val, float):
        if not val.is_integer():
            raise ValueError(f"{name} must be an integer, got non-integer float: {val}")
        val = int(val)
    elif isinstance(val, str):
        val = val.strip()
        if not re.match(r"^[+-]?\d+$", val):
            raise ValueError(f"{name} must be an integer string")
        val = int(val)
    elif not isinstance(val, int):
        raise ValueError(f"Invalid type for {name}: {type(val).__name__}")

    if val < min_val or (max_val is not None and val > max_val):
        bounds_str = f">= {min_val}" if max_val is None else f"between {min_val} and {max_val}"
        raise ValueError(f"{name} must be an integer {bounds_str}")
    return val


def handle_list_batches(data: dict) -> list[dict] | dict[str, Any]:
    page = data.get("page")
    page_size = data.get("page_size")
    if page is not None or page_size is not None:
        page_val = _parse_int_param(page, "page", min_val=1)
        page_size_val = _parse_int_param(page_size, "page_size", min_val=1, max_val=100)
        return batches(page=page_val, page_size=page_size_val)
    return batches()


def handle_get_batch(data: dict) -> dict:
    batch_id = data.get("batch_id")
    if not batch_id:
        raise ValueError("batch_id is required.")
    return get_batch(batch_id)


def handle_register_bootstrap(data: dict) -> dict:
    """Register a one-time bootstrap secret passed securely over stdio (S-02)."""
    secret = data.get("secret", "").strip()
    if not secret or len(secret) < 16:
        raise ValueError("Invalid bootstrap secret format.")
    register_bootstrap_secret(secret)
    return {"status": "registered"}


def handle_bootstrap(data: dict) -> dict:
    """Validate bootstrap secret and issue internal session token over stdio (S-02)."""
    secret = data.get("secret", "").strip()
    if not secret or not consume_bootstrap_secret(secret):
        raise ValueError("Invalid, expired, or previously consumed bootstrap secret.")
    token = get_or_create_api_token()
    return {"status": "authenticated", "session_token": token}


def handle_shutdown(data: dict) -> dict:
    """Handle graceful shutdown requested by Electron parent process (R-06)."""
    LOGGER.info("Graceful shutdown signal received from Electron parent.")
    graceful_shutdown(exit_code=0, source="electron_ipc_request")
    return {"status": "shutting_down"}


HANDLERS = {
    "health": handle_health,
    "heartbeat": handle_heartbeat,
    "register_bootstrap": handle_register_bootstrap,
    "bootstrap": handle_bootstrap,
    "scan": handle_scan,
    "scan_upload": handle_scan_upload,
    "scan_hardware": handle_scan_hardware,
    "list_batches": handle_list_batches,
    "get_batch": handle_get_batch,
    "shutdown": handle_shutdown,
}


def read_bounded_line(
    stream: Any,
    max_bytes: int = MAX_IPC_BYTES,
) -> tuple[bytes | None, bool, bool]:
    """
    Read a single bounded line from the input stream.

    Returns:
        tuple (data, is_oversized, has_newline):
        - data: bytes read (or None if EOF reached with 0 bytes read)
        - is_oversized: True if line exceeded max_bytes
        - has_newline: True if line contained a terminating newline
    """
    chunk = stream.readline(max_bytes + 1)
    if not chunk:
        return None, False, False

    if isinstance(chunk, str):
        chunk_bytes = chunk.encode("utf-8", errors="surrogateescape")
    else:
        chunk_bytes = chunk

    has_newline = chunk_bytes.endswith(b"\n")

    if len(chunk_bytes) > max_bytes:
        # Message exceeds limit!
        # If line did not end with newline, drain remainder in bounded chunks
        if not has_newline:
            while True:
                drain = stream.readline(DRAIN_CHUNK_SIZE)
                if not drain:
                    break
                if isinstance(drain, str):
                    if drain.endswith("\n"):
                        break
                else:
                    if drain.endswith(b"\n"):
                        break
        return chunk_bytes, True, has_newline

    # Within bounds (<= max_bytes)
    return chunk_bytes, False, has_newline


def process_ipc_message(raw_bytes: bytes) -> dict:
    """
    Validate message structure, parse JSON, and dispatch to handler.
    Guarantees structured error responses and never crashes on malformed input.
    """
    # 1. Validate UTF-8 decoding
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        return {
            "id": None,
            "ok": False,
            "error": f"Invalid UTF-8 encoding in IPC message: {exc}",
        }

    # 2. Parse JSON safely
    try:
        req = json.loads(text)
    except json.JSONDecodeError as exc:
        return {
            "id": None,
            "ok": False,
            "error": f"Malformed JSON payload: {exc}",
        }

    # 3. Verify message is a JSON object (dict)
    if not isinstance(req, dict):
        return {
            "id": None,
            "ok": False,
            "error": f"Message payload must be a JSON object, got {type(req).__name__}.",
        }

    # 4. Validate 'id' field if provided
    req_id = req.get("id")
    if req_id is not None and not isinstance(req_id, (str, int)):
        return {
            "id": None,
            "ok": False,
            "error": f"Message 'id' must be a string, integer, or null, got {type(req_id).__name__}.",
        }

    # 5. Validate 'action'
    action = req.get("action")
    if not isinstance(action, str) or not action.strip():
        return {
            "id": req_id,
            "ok": False,
            "error": "Missing or invalid 'action' in IPC message.",
        }
    action = action.strip()

    # 6. Validate 'data'
    data = req.get("data")
    if data is None:
        data = {}
    elif not isinstance(data, dict):
        return {
            "id": req_id,
            "ok": False,
            "error": f"Message 'data' must be a JSON object, got {type(data).__name__}.",
        }

    # 7. Check handler
    handler = HANDLERS.get(action)
    if not handler:
        return {
            "id": req_id,
            "ok": False,
            "error": f"Unknown action: {action}",
        }

    # 8. Execute handler safely
    try:
        result = handler(data)
        return {"id": req_id, "ok": True, "result": result}
    except SystemExit:
        raise
    except Exception as exc:
        LOGGER.exception("Error processing action '%s': %s", action, exc)
        return {"id": req_id, "ok": False, "error": str(exc)}


def get_parent_pid(explicit_pid: int | None = None) -> int | None:
    """Resolve target parent process ID from argument, environment, or command-line."""
    if explicit_pid is not None and explicit_pid > 0:
        return explicit_pid

    # Check command-line arguments (e.g., --parent-pid 1234)
    if "--parent-pid" in sys.argv:
        try:
            idx = sys.argv.index("--parent-pid")
            if idx + 1 < len(sys.argv):
                val = int(sys.argv[idx + 1])
                if val > 0:
                    return val
        except (ValueError, IndexError):
            pass

    # Check environment variable
    env_pid = os.environ.get("AGRIVISIO_PARENT_PID")
    if env_pid:
        try:
            val = int(env_pid.strip())
            if val > 0:
                return val
        except ValueError:
            pass

    return None


def start_parent_watchdog(
    parent_pid: int | None = None,
    poll_interval: float = 0.5,
) -> threading.Thread | None:
    """
    Start a background watchdog daemon thread to monitor the parent process.
    If the parent process terminates unexpectedly, triggers graceful worker shutdown.
    Uses Win32 OpenProcess with SYNCHRONIZE on Windows, or os.kill on POSIX.
    """
    resolved_pid = get_parent_pid(parent_pid)
    if not resolved_pid or resolved_pid <= 1:
        LOGGER.info("No valid parent PID configured; parent watchdog idle.")
        return None

    LOGGER.info("Starting parent process watchdog for PID %d...", resolved_pid)

    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        WAIT_OBJECT_0 = 0x00000000
        WAIT_TIMEOUT = 0x00000102

        # Open process handle with SYNCHRONIZE permission
        # As long as this handle remains open, Windows keeps the process object valid
        # and the handle becomes signaled when the parent terminates.
        h_parent = kernel32.OpenProcess(
            SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            wintypes.DWORD(resolved_pid),
        )

        if not h_parent:
            err = kernel32.GetLastError()
            LOGGER.warning(
                "OpenProcess failed for parent PID %d (error: %d); parent may already be dead. Exiting.",
                resolved_pid,
                err,
            )
            graceful_shutdown(exit_code=0, source="parent_missing_on_start")
            return None

        def _windows_watchdog() -> None:
            try:
                poll_ms = int(poll_interval * 1000)
                while not _IS_SHUTTING_DOWN.is_set():
                    wait_res = kernel32.WaitForSingleObject(h_parent, poll_ms)
                    if wait_res == WAIT_OBJECT_0:
                        LOGGER.warning(
                            "Parent process (PID %d) terminated unexpectedly. Initiating watchdog shutdown.",
                            resolved_pid,
                        )
                        graceful_shutdown(exit_code=0, source="parent_terminated_watchdog")
                        break
                    elif wait_res == WAIT_TIMEOUT:
                        continue
                    else:
                        # Fallback query exit code if wait fails unexpectedly
                        exit_code = wintypes.DWORD()
                        if (
                            not kernel32.GetExitCodeProcess(h_parent, ctypes.byref(exit_code))
                            or exit_code.value != 259  # 259 = STILL_ACTIVE
                        ):
                            LOGGER.warning(
                                "Parent process (PID %d) is no longer active (exit code: %s).",
                                resolved_pid,
                                exit_code.value,
                            )
                            graceful_shutdown(exit_code=0, source="parent_inactive_watchdog")
                            break
            finally:
                kernel32.CloseHandle(h_parent)

        thread = threading.Thread(target=_windows_watchdog, name="ParentProcessWatchdog", daemon=True)
        thread.start()
        return thread

    else:
        # POSIX fallback
        def _posix_watchdog() -> None:
            while not _IS_SHUTTING_DOWN.is_set():
                time.sleep(poll_interval)
                try:
                    os.kill(resolved_pid, 0)
                    if os.getppid() == 1 and resolved_pid != 1:
                        LOGGER.warning("Parent process died (reparented to init). Initiating shutdown.")
                        graceful_shutdown(exit_code=0, source="posix_reparented_watchdog")
                        break
                except OSError:
                    LOGGER.warning("Parent process (PID %d) disappeared. Initiating shutdown.", resolved_pid)
                    graceful_shutdown(exit_code=0, source="posix_disappeared_watchdog")
                    break

        thread = threading.Thread(target=_posix_watchdog, name="ParentProcessWatchdog", daemon=True)
        thread.start()
        return thread


def run_worker_loop(
    in_stream: Any = None,
    out_stream: Any = None,
    max_bytes: int = MAX_IPC_BYTES,
) -> None:
    """Bounded, memory-safe IPC event loop."""
    if in_stream is None:
        in_stream = getattr(sys.stdin, "buffer", sys.stdin)
    if out_stream is None:
        out_stream = sys.stdout

    def _write_response(resp_obj: dict) -> None:
        payload = json.dumps(resp_obj) + "\n"
        if hasattr(out_stream, "buffer") and isinstance(out_stream.buffer, io.BufferedIOBase):
            out_stream.write(payload)
        elif isinstance(out_stream, (io.RawIOBase, io.BufferedIOBase)):
            out_stream.write(payload.encode("utf-8"))
        else:
            out_stream.write(payload)
        out_stream.flush()

    try:
        while not _IS_SHUTTING_DOWN.is_set():
            try:
                data, is_oversized, has_newline = read_bounded_line(in_stream, max_bytes=max_bytes)

                # EOF reached
                if data is None:
                    LOGGER.info("IPC stdin reached EOF; worker loop exiting.")
                    break

                if is_oversized:
                    LOGGER.warning("Rejected oversized IPC message exceeding %d bytes.", max_bytes)
                    _write_response({
                        "id": None,
                        "ok": False,
                        "error": f"Message exceeds maximum allowed IPC limit of {max_bytes} bytes.",
                    })
                    continue

                if not has_newline:
                    # Data was read up to EOF without a newline delimiter
                    stripped = data.strip()
                    if not stripped:
                        break
                    LOGGER.warning("Rejected IPC message missing newline delimiter before EOF.")
                    _write_response({
                        "id": None,
                        "ok": False,
                        "error": "Message missing newline delimiter (stream closed or truncated).",
                    })
                    break

                stripped_data = data.strip().removeprefix(b"\xef\xbb\xbf")
                if not stripped_data:
                    # Blank line (keepalive or whitespace)
                    continue

                with _BUSY_WORK_LOCK:
                    if _IS_SHUTTING_DOWN.is_set():
                        LOGGER.info("Rejecting request received during shutdown.")
                        break
                    resp = process_ipc_message(stripped_data)
                _write_response(resp)
            except (BrokenPipeError, OSError) as exc:
                LOGGER.info("IPC pipe closed or broken (%s); worker loop terminating.", exc)
                break
            except SystemExit:
                LOGGER.info("IPC worker shutting down via SystemExit.")
                break
            except Exception as exc:
                LOGGER.exception("Unexpected error in worker loop: %s", exc)
                try:
                    _write_response({
                        "id": None,
                        "ok": False,
                        "error": f"Internal IPC worker error: {exc}",
                    })
                except OSError:
                    break
    finally:
        # Drain active work and ensure single-owner cleanup upon loop termination
        with _BUSY_WORK_LOCK:
            cleanup_worker_resources()


def main():
    os.environ["AGRIVISIO_IPC_MODE"] = "1"
    try:
        _load_model()
    except Exception as exc:
        LOGGER.warning("Could not pre-load classifier model: %s", exc)

    # Initialize shutdown state before starting the parent watchdog
    _IS_SHUTTING_DOWN.clear()
    reset_cleanup_state()

    # Start parent-process watchdog monitoring
    start_parent_watchdog()

    # Signal portless readiness to Electron parent process
    print(json.dumps({"event": "ready", "app": "agrivisio"}), flush=True)

    run_worker_loop()


if __name__ == "__main__":
    main()
