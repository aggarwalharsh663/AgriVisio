"""Tests for the Hardware Scanner API route (/api/scan/hardware) and IPC integration."""

import io
import cv2
import numpy as np
import pytest
from PIL import Image
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from backend import main
from backend.hardware_scanner import (
    HardwareScanUnsupportedError,
    ScannerNotFoundError,
    ScannerOperationError,
    ScannerTimeoutError,
)
from backend.ipc_worker import handle_scan_hardware


@pytest.fixture
def test_setup(tmp_path, monkeypatch):
    """Isolate DATA_DIR and DATABASE_PATH for test isolation."""
    data_dir = tmp_path / "data"
    batches_dir = data_dir / "batches"
    batches_dir.mkdir(parents=True, exist_ok=True)
    database_path = tmp_path / "agrivisio.sqlite3"

    monkeypatch.setattr(main, "DATA_DIR", data_dir)
    monkeypatch.setattr(main, "DATABASE_PATH", database_path)
    monkeypatch.setattr(main, "AUTH_TOKEN_FILE", tmp_path / "auth_token.txt")
    files_mount = next(
        route for route in main.app.routes if getattr(route, "path", None) == "/files"
    )
    monkeypatch.setattr(files_mount, "app", StaticFiles(directory=data_dir))
    return data_dir, database_path


def _generate_synthetic_grain_image(dpi=(300, 300)):
    """Create a synthetic 400x400 image containing bright grain-like ellipses on a dark background."""
    img = np.zeros((400, 400, 3), dtype=np.uint8)
    # Draw several synthetic rice grains
    cv2.ellipse(img, (150, 150), (40, 15), 30, 0, 360, (230, 225, 210), -1)
    cv2.ellipse(img, (250, 250), (38, 14), -45, 0, 360, (235, 230, 215), -1)
    cv2.ellipse(img, (200, 300), (42, 16), 15, 0, 360, (220, 218, 205), -1)
    if dpi is not None:
        buf = io.BytesIO()
        pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        pil_img.save(buf, format="JPEG", dpi=dpi)
        return img, buf.getvalue()
    _, encoded = cv2.imencode(".jpg", img)
    return img, encoded.tobytes()


# 1. Authenticated hardware scan request
def test_authenticated_hardware_scan_success(test_setup, monkeypatch):
    synthetic_img, synthetic_bytes = _generate_synthetic_grain_image()

    monkeypatch.setattr("backend.main.is_hardware_scan_supported", lambda: True)
    monkeypatch.setattr("backend.main.acquire_hardware_scan", lambda timeout=90.0: (synthetic_img, synthetic_bytes))

    with TestClient(main.app) as client:
        token = main.get_api_token()
        response = client.post(
            "/api/scan/hardware",
            json={
                "sample_name": "Test Hardware Sample",
                "variety": "Basmati 370",
                "lighting": "ring_light",
            },
            headers={"X-AgriVisio-Token": token},
        )

        assert response.status_code == 200, response.text
        data = response.json()
        assert "batch_id" in data
        assert data["sample_name"] == "Test Hardware Sample"
        assert data["variety"] == "Basmati 370"
        assert data["calibration"]["calibration_status"] == "CALIBRATED"
        assert data["calibration"]["mm_per_pixel"] == round(25.4 / 300.0, 5)
        assert "summary" in data
        assert "grains" in data
        assert "fingerprint" in data
        assert data["simulated"] is False


# 2. Unauthenticated request
def test_unauthenticated_hardware_scan_rejected(test_setup):
    with TestClient(main.app) as client:
        response = client.post(
            "/api/scan/hardware",
            json={
                "sample_name": "Unauthenticated Sample",
                "variety": "IR64",
                "lighting": "ring_light",
            },
        )
        assert response.status_code == 401
        assert "Unauthorized" in response.text


# 3. Unsupported browser-mode hardware scan
def test_unsupported_hardware_scan_returns_501(test_setup, monkeypatch):
    monkeypatch.setattr("backend.main.is_hardware_scan_supported", lambda: False)

    with TestClient(main.app) as client:
        token = main.get_api_token()
        response = client.post(
            "/api/scan/hardware",
            json={
                "sample_name": "Linux/Browser Mode",
                "variety": "IR64",
                "lighting": "daylight",
            },
            headers={"X-AgriVisio-Token": token},
        )
        assert response.status_code == 501
        data = response.json()
        assert data["error"] == "hardware_scan_unsupported"
        assert "requires a local Windows environment" in data["detail"]


# 4. Scanner unavailable
def test_scanner_unavailable_returns_404(test_setup, monkeypatch):
    monkeypatch.setattr("backend.main.is_hardware_scan_supported", lambda: True)

    def mock_missing_scanner(timeout=90.0):
        raise ScannerNotFoundError("No scanner found. Please verify connection.")

    monkeypatch.setattr("backend.main.acquire_hardware_scan", mock_missing_scanner)

    with TestClient(main.app) as client:
        token = main.get_api_token()
        response = client.post(
            "/api/scan/hardware",
            json={
                "sample_name": "Detached Scanner",
                "variety": "Jasmine",
                "lighting": "ring_light",
            },
            headers={"X-AgriVisio-Token": token},
        )
        assert response.status_code == 404
        data = response.json()
        assert data["error"] == "scanner_not_found"
        assert "No scanner detected" in data["detail"]


# 5. Scanner operation failure
def test_scanner_operation_failure_returns_500(test_setup, monkeypatch):
    monkeypatch.setattr("backend.main.is_hardware_scan_supported", lambda: True)

    def mock_hardware_fault(timeout=90.0):
        raise ScannerOperationError("WIA device error: Paper feeder jam or optical head fault.")

    monkeypatch.setattr("backend.main.acquire_hardware_scan", mock_hardware_fault)

    with TestClient(main.app) as client:
        token = main.get_api_token()
        response = client.post(
            "/api/scan/hardware",
            json={
                "sample_name": "Faulty Scanner",
                "variety": "Sona Masoori",
                "lighting": "tube_light",
            },
            headers={"X-AgriVisio-Token": token},
        )
        assert response.status_code == 500
        data = response.json()
        assert data["error"] == "scan_failed"
        assert "Scanner operation failed" in data["detail"]


# 5b. Scanner operation timeout
def test_scanner_timeout_returns_504(test_setup, monkeypatch):
    monkeypatch.setattr("backend.main.is_hardware_scan_supported", lambda: True)

    def mock_scanner_timeout(timeout=90.0):
        raise ScannerTimeoutError("Scanner acquisition timed out after 90 seconds.")

    monkeypatch.setattr("backend.main.acquire_hardware_scan", mock_scanner_timeout)

    with TestClient(main.app) as client:
        token = main.get_api_token()
        response = client.post(
            "/api/scan/hardware",
            json={
                "sample_name": "Timeout Scanner",
                "variety": "Basmati",
                "lighting": "ring_light",
            },
            headers={"X-AgriVisio-Token": token},
        )
        assert response.status_code == 504
        data = response.json()
        assert data["error"] == "scan_timeout"
        assert "timed out" in data["detail"]


# 6. Invalid request parameters
def test_invalid_request_parameters_rejected(test_setup):
    with TestClient(main.app) as client:
        token = main.get_api_token()

        # Invalid lighting profile
        resp1 = client.post(
            "/api/scan/hardware",
            json={
                "sample_name": "Invalid Light",
                "variety": "Basmati",
                "lighting": "neon_disco_light",
            },
            headers={"X-AgriVisio-Token": token},
        )
        assert resp1.status_code == 422

        # Empty / whitespace-only sample name
        resp2 = client.post(
            "/api/scan/hardware",
            json={
                "sample_name": "   ",
                "variety": "Basmati",
                "lighting": "ring_light",
            },
            headers={"X-AgriVisio-Token": token},
        )
        assert resp2.status_code == 422

        # Missing variety
        resp3 = client.post(
            "/api/scan/hardware",
            json={
                "sample_name": "Missing Variety",
                "lighting": "ring_light",
            },
            headers={"X-AgriVisio-Token": token},
        )
        assert resp3.status_code == 422


# 7. Rate-limit behavior
def test_rate_limit_behavior_on_hardware_scan(test_setup, monkeypatch):
    monkeypatch.setattr("backend.main.RATE_LIMIT_MAX_REQUESTS", 3)
    monkeypatch.setattr("backend.main._rate_limit_records", {})
    monkeypatch.setattr("backend.main.is_hardware_scan_supported", lambda: False)

    with TestClient(main.app) as client:
        token = main.get_api_token()
        headers = {"X-AgriVisio-Token": token}
        payload = {
            "sample_name": "Rate Limit Test",
            "variety": "Basmati",
            "lighting": "ring_light",
        }

        # 3 requests allowed under threshold
        for _ in range(3):
            r = client.post("/api/scan/hardware", json=payload, headers=headers)
            assert r.status_code == 501

        # 4th request exceeds rate limit
        r_exceeded = client.post("/api/scan/hardware", json=payload, headers=headers)
        assert r_exceeded.status_code == 429
        assert "Rate limit exceeded" in r_exceeded.text


# 8. Successful Electron hardware scan integration
def test_electron_ipc_worker_hardware_scan_integration(test_setup, monkeypatch):
    synthetic_img, synthetic_bytes = _generate_synthetic_grain_image()

    monkeypatch.setattr(
        "backend.ipc_worker.acquire_hardware_scan",
        lambda timeout=90.0: (synthetic_img, synthetic_bytes),
    )

    result = handle_scan_hardware({
        "sample_name": "Electron Hardware Batch",
        "variety": "Pusa 1121",
        "lighting": "ring_light",
    })

    assert isinstance(result, dict)
    assert "batch_id" in result
    assert result["sample_name"] == "Electron Hardware Batch"
    assert result["variety"] == "Pusa 1121"
    assert result["calibration"]["calibration_status"] == "CALIBRATED"
    assert result["calibration"]["mm_per_pixel"] == round(25.4 / 300.0, 5)
    assert len(result["grains"]) >= 0


def test_electron_ipc_worker_hardware_scan_missing_scanner(test_setup, monkeypatch):
    def mock_fail(timeout=90.0):
        raise ScannerNotFoundError("No scanner found")

    monkeypatch.setattr("backend.ipc_worker.acquire_hardware_scan", mock_fail)

    with pytest.raises(ScannerNotFoundError) as exc_info:
        handle_scan_hardware({
            "sample_name": "Failing Hardware Batch",
            "variety": "Pusa 1121",
            "lighting": "ring_light",
        })
    assert "No scanner found" in str(exc_info.value)
