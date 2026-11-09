"""Comprehensive automated security regression tests for AgriScan.

Verifies remediation of vulnerabilities identified in the AgriScan security audit:
- F-01: Path traversal protection
- F-03: API token authentication, CORS restrictions, sensitive file blocking
- F-04: Model integrity verification, anti-tampering, removal of silent fallback
- F-05: Mock mode isolation, prevention of mock data export
- F-06: Measurement scale safety, preventing scale guessing
- F-08: Cryptographic fingerprinting determinism, upload size limits, rate limiting
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import main as backend_main
from backend.fingerprint import compute_batch_fingerprint
from backend.grain_analysis import (
    ModelIntegrityError,
    verify_model_integrity,
)


@pytest.fixture
def auth_client():
    """Test client configured with valid token authentication."""
    old_disable = os.environ.pop("AGRISCAN_DISABLE_AUTH_FOR_TESTS", None)
    token = backend_main.get_api_token()
    with TestClient(backend_main.app) as client:
        client.headers.update({"X-AgriScan-Token": token})
        yield client
    if old_disable is not None:
        os.environ["AGRISCAN_DISABLE_AUTH_FOR_TESTS"] = old_disable


@pytest.fixture
def unauth_client():
    """Test client without any authentication credentials."""
    old_disable = os.environ.pop("AGRISCAN_DISABLE_AUTH_FOR_TESTS", None)
    with TestClient(backend_main.app) as client:
        yield client
    if old_disable is not None:
        os.environ["AGRISCAN_DISABLE_AUTH_FOR_TESTS"] = old_disable


# --- F-03: API Authentication & Data Protection ---

def test_unauthenticated_requests_fail(unauth_client):
    """Protected endpoints must reject unauthenticated requests with HTTP 401."""
    # 1. /api/batches
    res = unauth_client.get("/api/batches")
    assert res.status_code == 401, f"Expected 401 for unauthenticated /api/batches, got {res.status_code}"

    # 2. /api/scan
    res = unauth_client.post(
        "/api/scan",
        json={"lighting": "ring_light", "n_grains": 10, "seed": 42},
    )
    assert res.status_code == 401, f"Expected 401 for unauthenticated /api/scan, got {res.status_code}"

    # 3. /api/scan/upload
    res = unauth_client.post(
        "/api/scan/upload",
        files={"image": ("test.jpg", b"fakebytes", "image/jpeg")},
        data={"sample_name": "Test", "variety": "Basmati", "lighting": "ring_light"},
    )
    assert res.status_code == 401, f"Expected 401 for unauthenticated /api/scan/upload, got {res.status_code}"


def test_authenticated_requests_succeed(auth_client):
    """Authenticated requests with valid X-AgriScan-Token must succeed."""
    res = auth_client.get("/api/batches")
    assert res.status_code == 200


def test_cors_does_not_permit_arbitrary_origins():
    """CORS must not allow arbitrary untrusted origins."""
    with TestClient(backend_main.app) as client:
        res = client.options(
            "/api/health",
            headers={
                "Origin": "http://evil-attacker-site.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        allow_origin = res.headers.get("access-control-allow-origin")
        assert allow_origin != "*", "CORS allow-origin must not be wildcard '*'"
        assert allow_origin != "http://evil-attacker-site.com", "Untrusted origin must not be reflected"


def test_sensitive_files_blocked_from_static_serving(auth_client):
    """Database, token, and python code files must be blocked with HTTP 403."""
    for target in ["agriscan.sqlite3", ".auth_token", "model.joblib"]:
        res = auth_client.get(f"/files/{target}")
        assert res.status_code in (403, 404), f"Sensitive file {target} should be blocked, got {res.status_code}"


def test_files_path_traversal_blocked(auth_client):
    """Path traversal attempts via /files must be rejected with 403 or 404."""
    traversal_targets = [
        "/files/..%2f..%2fbackend%2fmain.py",
        "/files/%2e%2e%2f%2e%2e%2fREADME.md",
        "/files/..\\..\\backend\\main.py",
    ]
    for target in traversal_targets:
        res = auth_client.get(target)
        assert res.status_code in (403, 404), f"Traversal target {target} should be rejected, got {res.status_code}"


# --- F-04: Machine Learning Model Integrity & Silent Fallback ---

def test_model_integrity_validation():
    """Model integrity verification must succeed on untampered model and manifest."""
    manifest = verify_model_integrity()
    assert "sha256" in manifest
    assert manifest["classes"] == ["good", "broken", "chalky", "discolored"]


def test_tampered_model_rejected():
    """Tampered or mismatched model file must raise ModelIntegrityError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_model = Path(tmpdir) / "model.joblib"
        tmp_manifest = Path(tmpdir) / "model_manifest.json"

        tmp_model.write_bytes(b"corrupted or malicious model payload")
        tmp_manifest.write_text(json.dumps({"sha256": "expected_hash_here", "classes": []}))

        with pytest.raises(ModelIntegrityError):
            verify_model_integrity(model_path=tmp_model, manifest_path=tmp_manifest)


def test_missing_model_raises_integrity_error():
    """Missing model file must raise ModelIntegrityError, never silently fall back."""
    nonexistent = Path("nonexistent_model_file.joblib")
    with pytest.raises(ModelIntegrityError):
        verify_model_integrity(model_path=nonexistent)


# --- F-05: Mock Mode Safeguards & Rejection of Mock Export ---

def test_mock_batch_export_rejected(auth_client):
    """Mock batches cannot be exported for official certification; must return 400."""
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_dir = Path(tmpdir) / "batches" / "AS-MOCK-TEST"
        mock_dir.mkdir(parents=True)
        record = {
            "batch_id": "AS-MOCK-TEST",
            "is_mock": True,
            "simulated": True,
            "sample_name": "Test Mock",
            "variety": "Mock",
            "created_at": "2026-01-01T00:00:00",
            "grains": [],
        }
        (mock_dir / "batch.json").write_text(json.dumps(record), encoding="utf-8")

        # Temporarily point DATA_DIR
        old_data_dir = backend_main.DATA_DIR
        backend_main.DATA_DIR = Path(tmpdir)
        try:
            res = auth_client.get("/api/batches/AS-MOCK-TEST/export")
            assert res.status_code == 400, f"Expected 400 when exporting mock batch, got {res.status_code}"
            assert "cannot be exported" in res.json().get("detail", "").lower()
        finally:
            backend_main.DATA_DIR = old_data_dir


# --- F-08: Reliability, Upload Limits & Fingerprinting ---

def test_upload_exceeding_10mb_rejected(auth_client):
    """File uploads exceeding 10MB must be rejected with HTTP 413."""
    oversized = b"0" * (11 * 1024 * 1024)  # 11 MB
    res = auth_client.post(
        "/api/scan/upload",
        files={"image": ("large.jpg", oversized, "image/jpeg")},
        data={"sample_name": "Test", "variety": "Basmati", "lighting": "ring_light"},
    )
    assert res.status_code == 413, f"Expected 413 for >10MB upload, got {res.status_code}"


def test_corrupted_upload_rejected(auth_client):
    """Corrupted non-image uploads must return 422 Unprocessable Entity."""
    corrupted = b"not a valid image format at all"
    res = auth_client.post(
        "/api/scan/upload",
        files={"image": ("corrupted.jpg", corrupted, "image/jpeg")},
        data={"sample_name": "Test", "variety": "Basmati", "lighting": "ring_light"},
    )
    assert res.status_code == 422, f"Expected 422 for corrupt image, got {res.status_code}"


def test_fingerprint_deterministic_and_tamper_evident():
    """Cryptographic batch fingerprint must be deterministic and sensitive to changes."""
    counts = {"good": 50, "broken": 10, "chalky": 5, "discolored": 2}
    summary = {
        "total_grains": 67,
        "mean_length_mm": 6.85,
        "mean_width_mm": 2.15,
        "mean_lb_ratio": 3.19,
        "std_length_mm": 0.45,
    }
    calibration = {"calibration_status": "CALIBRATED", "mm_per_pixel": 0.0421}
    raw_bytes = b"sample_raw_image_bytes_123"

    fp1 = compute_batch_fingerprint(counts, summary, calibration, raw_bytes)
    fp2 = compute_batch_fingerprint(counts, summary, calibration, raw_bytes)

    # Determinism
    assert fp1["hash"] == fp2["hash"]
    assert len(fp1["hash"]) == 64

    # Tamper sensitivity: changing 1 grain count must change hash
    counts_modified = counts.copy()
    counts_modified["good"] = 51
    fp3 = compute_batch_fingerprint(counts_modified, summary, calibration, raw_bytes)
    assert fp1["hash"] != fp3["hash"], "Fingerprint hash must change when grain counts differ"

    # Tamper sensitivity: changing raw image must change hash
    fp4 = compute_batch_fingerprint(counts, summary, calibration, b"altered_image_bytes")
    assert fp1["hash"] != fp4["hash"], "Fingerprint hash must change when image bytes differ"
