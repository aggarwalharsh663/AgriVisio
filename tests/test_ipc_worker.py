import io
import json
import numpy as np
import cv2
import pytest
from backend.ipc_worker import (
    MAX_IPC_BYTES,
    DRAIN_CHUNK_SIZE,
    read_bounded_line,
    process_ipc_message,
    run_worker_loop,
    handle_health,
    handle_heartbeat,
    handle_scan,
    handle_list_batches,
    handle_get_batch,
    handle_scan_upload,
)


def test_ipc_worker_health():
    res = handle_health({})
    assert res == {"status": "ok"}


def test_ipc_worker_heartbeat():
    res = handle_heartbeat({})
    assert res == {"status": "alive"}


def test_ipc_worker_scan():
    res = handle_scan({
        "lighting": "ring_light",
        "n_grains": 20,
        "sample_name": "IPC Test",
        "variety": "Basmati",
        "seed": 42,
    })
    assert "batch_id" in res
    assert res["sample_name"] == "IPC Test"
    assert "grains" in res
    assert len(res["grains"]) > 0


def test_ipc_worker_batches():
    batches = handle_list_batches({})
    assert isinstance(batches, list)
    if batches:
        first_id = batches[0]["batch_id"]
        single = handle_get_batch({"batch_id": first_id})
        assert single["batch_id"] == first_id


def test_ipc_worker_scan_upload(tmp_path):
    img = np.full((100, 100, 3), 200, dtype=np.uint8)
    img_path = tmp_path / "test_upload.jpg"
    cv2.imwrite(str(img_path), img)

    res = handle_scan_upload({
        "file_path": str(img_path),
        "sample_name": "Upload Test",
        "variety": "Sona Masoori",
        "lighting": "daylight",
    })
    assert "batch_id" in res
    assert res["sample_name"] == "Upload Test"


def test_ipc_worker_scan_upload_direct_calibration_case_a(tmp_path):
    img = np.full((100, 100, 3), 200, dtype=np.uint8)
    img_path = tmp_path / "test_upload_calib.jpg"
    cv2.imwrite(str(img_path), img)

    res = handle_scan_upload({
        "file_path": str(img_path),
        "sample_name": "Direct Calib Test",
        "variety": "1121 Steam",
        "lighting": "ring_light",
        "calibration_mm_per_pixel": 0.08467,
    })
    assert res["calibration"]["calibration_status"] == "CALIBRATED"
    assert res["calibration"]["calibration_reference_type"] == "DIRECT_SCALE_INPUT"
    assert res["calibration"]["mm_per_pixel"] == 0.08467


def test_ipc_worker_scan_upload_physical_reference_case_b(tmp_path):
    img = np.full((100, 100, 3), 200, dtype=np.uint8)
    img_path = tmp_path / "test_upload_ref.jpg"
    cv2.imwrite(str(img_path), img)

    res = handle_scan_upload({
        "file_path": str(img_path),
        "sample_name": "Coin Reference Test",
        "variety": "Basmati",
        "lighting": "ring_light",
        "reference_length_mm": 24.26,
        "reference_pixels": 250.0,
    })
    assert res["calibration"]["calibration_status"] == "CALIBRATED"
    assert res["calibration"]["calibration_reference_type"] == "PHYSICAL_REFERENCE_OBJECT"
    expected_scale = round(24.26 / 250.0, 5)
    assert res["calibration"]["mm_per_pixel"] == expected_scale
    assert res["calibration"]["calibration_reference_length_mm"] == 24.26


def test_ipc_worker_scan_upload_no_calibration_case_c(tmp_path):
    img = np.full((100, 100, 3), 200, dtype=np.uint8)
    img_path = tmp_path / "test_upload_uncalib.jpg"
    cv2.imwrite(str(img_path), img)

    res = handle_scan_upload({
        "file_path": str(img_path),
        "sample_name": "Uncalibrated Test",
        "variety": "IR64",
        "lighting": "daylight",
        "calibration_mm_per_pixel": None,
        "reference_length_mm": None,
        "reference_pixels": None,
    })
    assert res["calibration"]["calibration_status"] == "UNCALIBRATED"
    assert res["calibration"]["calibration_reference_type"] == "DEFAULT_ESTIMATE"
    assert res["calibration"]["mm_per_pixel"] == 0.12


def test_ipc_worker_scan_upload_invalid_calibration_case_d(tmp_path):
    img = np.full((100, 100, 3), 200, dtype=np.uint8)
    img_path = tmp_path / "test_upload_invalid.jpg"
    cv2.imwrite(str(img_path), img)

    # Negative scale
    with pytest.raises(ValueError, match="greater than zero"):
        handle_scan_upload({
            "file_path": str(img_path),
            "sample_name": "Negative Scale",
            "variety": "Basmati",
            "lighting": "ring_light",
            "calibration_mm_per_pixel": -0.05,
        })

    # Non-numeric string
    with pytest.raises(ValueError, match="valid numeric value"):
        handle_scan_upload({
            "file_path": str(img_path),
            "sample_name": "String Scale",
            "variety": "Basmati",
            "lighting": "ring_light",
            "calibration_mm_per_pixel": "not-a-number",
        })

    # Zero reference dimension
    with pytest.raises(ValueError, match="greater than zero"):
        handle_scan_upload({
            "file_path": str(img_path),
            "sample_name": "Zero Ref",
            "variety": "Basmati",
            "lighting": "ring_light",
            "reference_length_mm": 0,
            "reference_pixels": 100,
        })


def test_ipc_worker_scan_upload_incomplete_reference_case_e(tmp_path):
    img = np.full((100, 100, 3), 200, dtype=np.uint8)
    img_path = tmp_path / "test_upload_incomplete.jpg"
    cv2.imwrite(str(img_path), img)

    # Missing pixels
    with pytest.raises(ValueError, match="Both reference_length_mm and reference_pixels must be provided"):
        handle_scan_upload({
            "file_path": str(img_path),
            "sample_name": "Incomplete Ref Length",
            "variety": "Basmati",
            "lighting": "ring_light",
            "reference_length_mm": 25.0,
            "reference_pixels": None,
        })

    # Missing length
    with pytest.raises(ValueError, match="Both reference_length_mm and reference_pixels must be provided"):
        handle_scan_upload({
            "file_path": str(img_path),
            "sample_name": "Incomplete Ref Pixels",
            "variety": "Basmati",
            "lighting": "ring_light",
            "reference_length_mm": None,
            "reference_pixels": 250.0,
        })


def test_ipc_worker_scan_upload_precedence_case_f(tmp_path):
    img = np.full((100, 100, 3), 200, dtype=np.uint8)
    img_path = tmp_path / "test_upload_precedence.jpg"
    cv2.imwrite(str(img_path), img)

    # Both physical reference and direct scale provided -> physical reference takes precedence
    res = handle_scan_upload({
        "file_path": str(img_path),
        "sample_name": "Precedence Test",
        "variety": "Basmati",
        "lighting": "ring_light",
        "calibration_mm_per_pixel": 0.05,
        "reference_length_mm": 20.0,
        "reference_pixels": 200.0,
    })
    assert res["calibration"]["calibration_status"] == "CALIBRATED"
    assert res["calibration"]["calibration_reference_type"] == "PHYSICAL_REFERENCE_OBJECT"
    assert res["calibration"]["mm_per_pixel"] == 0.1  # 20.0 / 200.0


def test_ipc_worker_json_ipc_roundtrip(tmp_path):
    img = np.full((100, 100, 3), 200, dtype=np.uint8)
    img_path = tmp_path / "test_upload_json.jpg"
    cv2.imwrite(str(img_path), img)

    ipc_payload = {
        "file_path": str(img_path),
        "sample_name": "JSON IPC Test",
        "variety": "Basmati",
        "lighting": "ring_light",
        "calibration_mm_per_pixel": 0.09173,
        "reference_length_mm": None,
        "reference_pixels": None,
    }
    # Simulate JSON line serialization over stdio pipe
    serialized = json.dumps(ipc_payload)
    deserialized = json.loads(serialized)

    res = handle_scan_upload(deserialized)
    assert res["calibration"]["calibration_status"] == "CALIBRATED"
    assert res["calibration"]["mm_per_pixel"] == 0.09173


# --- PROMPT 5: Bounded Reading and IPC Message Validation Tests ---

def test_max_ipc_bytes_constant():
    """Verify that MAX_IPC_BYTES is configured as a centralized 10 MB limit."""
    assert MAX_IPC_BYTES == 10 * 1024 * 1024
    assert DRAIN_CHUNK_SIZE == 64 * 1024


def test_bounded_read_normal_message():
    """Normal IPC message succeeds with expected response structure."""
    in_stream = io.BytesIO(b'{"id": "req-1", "action": "health", "data": {}}\n')
    out_stream = io.StringIO()

    run_worker_loop(in_stream=in_stream, out_stream=out_stream)

    output = out_stream.getvalue().strip()
    res = json.loads(output)
    assert res["id"] == "req-1"
    assert res["ok"] is True
    assert res["result"] == {"status": "ok"}


def test_bounded_read_message_exactly_at_limit():
    """Message whose line length is exactly at the limit is accepted and parsed."""
    test_limit = 500
    prefix = b'{"id": "exact", "action": "health", "data": {"pad": "'
    suffix = b'"}}\n'
    pad_len = test_limit - len(prefix) - len(suffix)
    assert pad_len > 0
    payload = prefix + (b"A" * pad_len) + suffix
    assert len(payload) == test_limit

    in_stream = io.BytesIO(payload)
    out_stream = io.StringIO()

    run_worker_loop(in_stream=in_stream, out_stream=out_stream, max_bytes=test_limit)

    output = out_stream.getvalue().strip()
    res = json.loads(output)
    assert res["id"] == "exact"
    assert res["ok"] is True
    assert res["result"] == {"status": "ok"}


def test_bounded_read_message_one_byte_above_limit():
    """Message one byte above the limit is rejected as oversized."""
    test_limit = 500
    prefix = b'{"id": "over", "action": "health", "data": {"pad": "'
    suffix = b'"}}\n'
    pad_len = (test_limit + 1) - len(prefix) - len(suffix)
    payload = prefix + (b"A" * pad_len) + suffix
    assert len(payload) == test_limit + 1

    in_stream = io.BytesIO(payload)
    out_stream = io.StringIO()

    run_worker_loop(in_stream=in_stream, out_stream=out_stream, max_bytes=test_limit)

    output = out_stream.getvalue().strip()
    res = json.loads(output)
    assert res["id"] is None
    assert res["ok"] is False
    assert "exceeds maximum allowed IPC limit" in res["error"]


def test_bounded_read_missing_newline_at_eof():
    """Stream that closes without a newline delimiter is rejected safely."""
    in_stream = io.BytesIO(b'{"id": "no-nl", "action": "health"}')
    out_stream = io.StringIO()

    run_worker_loop(in_stream=in_stream, out_stream=out_stream)

    output = out_stream.getvalue().strip()
    res = json.loads(output)
    assert res["id"] is None
    assert res["ok"] is False
    assert "missing newline delimiter" in res["error"]


def test_bounded_read_invalid_utf8():
    """Invalid UTF-8 byte sequences are caught and safely rejected."""
    in_stream = io.BytesIO(b'\xff\xfe\xfd\x80\n')
    out_stream = io.StringIO()

    run_worker_loop(in_stream=in_stream, out_stream=out_stream)

    output = out_stream.getvalue().strip()
    res = json.loads(output)
    assert res["id"] is None
    assert res["ok"] is False
    assert "Invalid UTF-8 encoding" in res["error"]


def test_bounded_read_malformed_json():
    """Malformed JSON payloads are rejected safely with clear errors."""
    in_stream = io.BytesIO(b'{"action": "health", invalid_syntax}\n')
    out_stream = io.StringIO()

    run_worker_loop(in_stream=in_stream, out_stream=out_stream)

    output = out_stream.getvalue().strip()
    res = json.loads(output)
    assert res["id"] is None
    assert res["ok"] is False
    assert "Malformed JSON payload" in res["error"]


def test_bounded_read_empty_input_and_blank_lines():
    """Empty inputs terminate cleanly; blank lines are ignored without errors."""
    # Completely empty stream
    empty_stream = io.BytesIO(b"")
    empty_out = io.StringIO()
    run_worker_loop(in_stream=empty_stream, out_stream=empty_out)
    assert empty_out.getvalue() == ""

    # Stream with blank lines surrounding a valid message
    blank_stream = io.BytesIO(b"\n   \n\t\n{\"id\": \"alive\", \"action\": \"heartbeat\"}\n\n  \n")
    blank_out = io.StringIO()
    run_worker_loop(in_stream=blank_stream, out_stream=blank_out)

    lines = [line.strip() for line in blank_out.getvalue().strip().split("\n") if line.strip()]
    assert len(lines) == 1
    res = json.loads(lines[0])
    assert res["id"] == "alive"
    assert res["ok"] is True
    assert res["result"] == {"status": "alive"}


def test_bounded_read_multiple_valid_messages():
    """Multiple sequential messages are processed in order."""
    messages = [
        {"id": "msg-1", "action": "health"},
        {"id": "msg-2", "action": "heartbeat"},
        {"id": "msg-3", "action": "list_batches"},
    ]
    raw = "".join(json.dumps(m) + "\n" for m in messages).encode("utf-8")
    in_stream = io.BytesIO(raw)
    out_stream = io.StringIO()

    run_worker_loop(in_stream=in_stream, out_stream=out_stream)

    lines = [line.strip() for line in out_stream.getvalue().strip().split("\n") if line.strip()]
    assert len(lines) == 3
    r1, r2, r3 = [json.loads(line) for line in lines]

    assert r1["id"] == "msg-1" and r1["ok"] is True and r1["result"]["status"] == "ok"
    assert r2["id"] == "msg-2" and r2["ok"] is True and r2["result"]["status"] == "alive"
    assert r3["id"] == "msg-3" and r3["ok"] is True and isinstance(r3["result"], list)


def test_bounded_read_oversized_message_followed_by_additional_input():
    """
    Oversized message is rejected, its remainder safely drained up to the newline,
    and subsequent valid message is successfully processed without stream corruption.
    """
    test_limit = 200
    # Create an oversized message of 1500 bytes on a single line
    oversized_line = (b'{"id": "huge", "action": "health", "padding": "' + (b"X" * 1400) + b'"}\n')
    valid_line = b'{"id": "valid-next", "action": "heartbeat"}\n'

    in_stream = io.BytesIO(oversized_line + valid_line)
    out_stream = io.StringIO()

    run_worker_loop(in_stream=in_stream, out_stream=out_stream, max_bytes=test_limit)

    lines = [line.strip() for line in out_stream.getvalue().strip().split("\n") if line.strip()]
    assert len(lines) == 2

    # First response: rejected oversized
    res_err = json.loads(lines[0])
    assert res_err["ok"] is False
    assert res_err["id"] is None
    assert "exceeds maximum allowed IPC limit" in res_err["error"]

    # Second response: successfully executed next message
    res_ok = json.loads(lines[1])
    assert res_ok["ok"] is True
    assert res_ok["id"] == "valid-next"
    assert res_ok["result"] == {"status": "alive"}


def test_message_structure_validation_non_object():
    """Non-dictionary JSON payloads (lists, primitives) are rejected."""
    in_stream = io.BytesIO(b'[1, 2, 3]\n"just a string"\n12345\n')
    out_stream = io.StringIO()

    run_worker_loop(in_stream=in_stream, out_stream=out_stream)

    lines = [line.strip() for line in out_stream.getvalue().strip().split("\n") if line.strip()]
    assert len(lines) == 3
    for line in lines:
        res = json.loads(line)
        assert res["ok"] is False
        assert "must be a JSON object" in res["error"]


def test_message_structure_validation_missing_or_invalid_action():
    """Missing, empty, non-string, or unknown action names are rejected."""
    # Missing action
    res1 = process_ipc_message(b'{"id": "1", "data": {}}')
    assert res1["ok"] is False
    assert "Missing or invalid 'action'" in res1["error"]

    # Non-string action
    res2 = process_ipc_message(b'{"id": "2", "action": 123}')
    assert res2["ok"] is False
    assert "Missing or invalid 'action'" in res2["error"]

    # Unknown action
    res3 = process_ipc_message(b'{"id": "3", "action": "nonexistent_action"}')
    assert res3["ok"] is False
    assert "Unknown action: nonexistent_action" in res3["error"]


def test_message_structure_validation_invalid_data_and_id_types():
    """Invalid data type (non-object) or invalid id type are rejected."""
    # Data is not an object
    res1 = process_ipc_message(b'{"id": "1", "action": "health", "data": "not-an-object"}')
    assert res1["ok"] is False
    assert "Message 'data' must be a JSON object" in res1["error"]

    # ID is a list
    res2 = process_ipc_message(b'{"id": [1, 2], "action": "health"}')
    assert res2["ok"] is False
    assert "Message 'id' must be a string, integer, or null" in res2["error"]

