import json
import numpy as np
import cv2
import pytest
from backend.ipc_worker import (
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
