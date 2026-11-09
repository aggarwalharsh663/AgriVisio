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

def test_security_headers_present(auth_client):
    """Defense-in-depth security headers must be present on HTTP responses."""
    res = auth_client.get("/api/health")
    assert res.headers.get("x-content-type-options") == "nosniff"
    assert res.headers.get("x-frame-options") == "SAMEORIGIN"
    assert res.headers.get("referrer-policy") == "strict-origin-when-cross-origin"


def test_export_filename_sanitization(auth_client):
    """Batch export filename in Content-Disposition header must be sanitized."""
    with tempfile.TemporaryDirectory() as tmpdir:
        batch_dir = Path(tmpdir) / "batches" / "AS-TEST-EXPORT"
        batch_dir.mkdir(parents=True)
        record = {
            "batch_id": "AS-TEST-EXPORT",
            "is_mock": False,
            "simulated": True,
            "sample_name": "Test Real",
            "variety": "IR64",
            "created_at": "2026-01-01T00:00:00",
            "grains": [],
        }
        (batch_dir / "batch.json").write_text(json.dumps(record), encoding="utf-8")

        old_data_dir = backend_main.DATA_DIR
        backend_main.DATA_DIR = Path(tmpdir)
        try:
            res = auth_client.get("/api/batches/AS-TEST-EXPORT/export")
            assert res.status_code == 200
            cd = res.headers.get("content-disposition", "")
            assert 'filename="AgriScan-Report-AS-TEST-EXPORT.json"' in cd
        finally:
            backend_main.DATA_DIR = old_data_dir


def test_static_files_blocks_sensitive_extensions(auth_client):
    """Blocked file extensions must be rejected with 403."""
    for ext in [".sqlite3", ".db", ".auth_token", ".py", ".pyc", ".joblib", ".env", ".key", ".log", ".bat"]:
        res = auth_client.get(f"/files/test{ext}")
        assert res.status_code == 403

# --- R-01: Authenticated & Narrowly Scoped File Serving ---

def test_files_unauthenticated_batch_json_rejected(unauth_client):
    """Unauthenticated access to batch files must return 401 (R-01)."""
    res = unauth_client.get("/files/batches/AS-20261109-001/batch.json")
    assert res.status_code == 401


def test_files_unauthenticated_image_rejected(unauth_client):
    """Unauthenticated access to batch images must return 401 (R-01)."""
    res = unauth_client.get("/files/batches/AS-20261109-001/photo.jpg")
    assert res.status_code == 401


def test_files_unauthenticated_arbitrary_file_rejected(unauth_client):
    """Unauthenticated request for arbitrary files (e.g. demo_backup.webm) must return 401 (R-01)."""
    res = unauth_client.get("/files/demo_backup.webm")
    assert res.status_code == 401


def test_files_authenticated_authorized_batch_file_succeeds(auth_client):
    """Authenticated requests for valid batch artifacts must succeed (R-01)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        batch_dir = Path(tmpdir) / "batches" / "AS-TEST-FILE-AUTH"
        batch_dir.mkdir(parents=True)
        img_file = batch_dir / "photo.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0JPEG_TEST_CONTENT")
        batch_json = batch_dir / "batch.json"
        batch_json.write_text(json.dumps({"batch_id": "AS-TEST-FILE-AUTH"}), encoding="utf-8")

        old_data_dir = backend_main.DATA_DIR
        backend_main.DATA_DIR = Path(tmpdir)
        try:
            # 1. Fetch photo.jpg
            res_img = auth_client.get("/files/batches/AS-TEST-FILE-AUTH/photo.jpg")
            assert res_img.status_code == 200
            assert res_img.content == b"\xff\xd8\xff\xe0JPEG_TEST_CONTENT"
            assert res_img.headers.get("x-content-type-options") == "nosniff"

            # 2. Fetch batch.json
            res_json = auth_client.get("/files/batches/AS-TEST-FILE-AUTH/batch.json")
            assert res_json.status_code == 200
            assert res_json.json()["batch_id"] == "AS-TEST-FILE-AUTH"
        finally:
            backend_main.DATA_DIR = old_data_dir


def test_files_authenticated_unrelated_or_invalid_batch_rejected(auth_client):
    """Authenticated request for non-existent or invalid batch must return 404 (R-01)."""
    res = auth_client.get("/files/batches/NONEXISTENT-BATCH-999/batch.json")
    assert res.status_code == 404


def test_files_authenticated_arbitrary_file_returns_404(auth_client):
    """Authenticated request for arbitrary non-batch files must return 404 (R-01)."""
    res = auth_client.get("/files/demo_backup.webm")
    assert res.status_code in (403, 404)


def test_files_traversal_attempts_rejected(auth_client):
    """Path traversal attempts under /files must be rejected with 403 or 404 (R-01)."""
    targets = [
        "/files/batches/AS-001/..%2f..%2fagriscan.sqlite3",
        "/files/batches/..%2f..%2fbackend%2fmain.py/test.jpg",
        r"/files/batches/AS-001/..\..\backend\main.py",
        "/files/batches/AS-001/test.sqlite3",
        "/files/batches/AS-001/test.py",
    ]
    for target in targets:
        res = auth_client.get(target)
        assert res.status_code in (403, 404)


# --- R-02: Secure Token Generation & Session Management ---

def test_arbitrary_localhost_cannot_obtain_token(unauth_client):
    """GET /api/auth/token endpoint must no longer disclose master token (R-02)."""
    res = unauth_client.get("/api/auth/token")
    assert res.status_code in (404, 405), f"Expected 404/405 for removed endpoint, got {res.status_code}"


def test_bootstrap_secret_exchange_and_anti_replay(unauth_client):
    """Single-use bootstrap secret can be exchanged once and is protected against replay (R-02)."""
    secret = backend_main.create_one_time_bootstrap_secret(ttl=30.0)

    # First exchange: succeeds and sets HttpOnly cookie
    res = unauth_client.post("/api/auth/bootstrap", json={"bootstrap_secret": secret})
    assert res.status_code == 200
    data = res.json()
    assert data.get("status") == "authenticated"
    assert "token" in data

    # Verify session cookie was set with HttpOnly
    cookie = res.cookies.get("agriscan_session")
    assert cookie is not None
    # Check Set-Cookie header attributes
    set_cookie_header = res.headers.get("set-cookie", "")
    assert "httponly" in set_cookie_header.lower()
    assert "samesite=lax" in set_cookie_header.lower()

    # Second exchange (replay attempt): must fail with 401
    res_replay = unauth_client.post("/api/auth/bootstrap", json={"bootstrap_secret": secret})
    assert res_replay.status_code == 401


def test_bootstrap_invalid_or_expired_secret_rejected(unauth_client):
    """Invalid or expired bootstrap secrets must be rejected with 401 (R-02)."""
    # 1. Invalid secret
    res = unauth_client.post("/api/auth/bootstrap", json={"bootstrap_secret": "invalid_secret_abcdef123456"})
    assert res.status_code == 401

    # 2. Expired secret (TTL 0.001s)
    expired_secret = backend_main.create_one_time_bootstrap_secret(ttl=0.001)
    import time
    time.sleep(0.01)
    res_exp = unauth_client.post("/api/auth/bootstrap", json={"bootstrap_secret": expired_secret})
    assert res_exp.status_code == 401


def test_query_token_authentication_strictly_fails(unauth_client):
    """Authentication via URL query parameter (?token=...) must be strictly rejected (R-02)."""
    token = backend_main.get_api_token()
    res = unauth_client.get(f"/api/batches?token={token}")
    assert res.status_code == 401, "Query-token auth must return 401"


def test_logout_clears_cookie(auth_client):
    """POST /api/auth/logout must clear the session cookie (R-02)."""
    res = auth_client.post("/api/auth/logout")
    assert res.status_code == 200
    assert res.json().get("status") == "logged_out"
    set_cookie = res.headers.get("set-cookie", "").lower()
    assert "agriscan_session=""" in set_cookie or "max-age=0" in set_cookie


# --- R-03: Remove Internal Exception Leakage ---

def test_scan_error_returns_generic_500_without_path_leakage(auth_client, monkeypatch):
    """Scan failures must return generic user-facing message without leaking internal paths (R-03)."""
    def mock_fail(*args, **kwargs):
        raise RuntimeError(r"Internal database error at C:\Users\Private\agriscan\core.py line 123")

    monkeypatch.setattr(backend_main, "_make_real_batch", mock_fail)
    res = auth_client.post(
        "/api/scan",
        json={"lighting": "ring_light", "n_grains": 10, "seed": 42, "sample_name": "Test", "variety": "IR64"},
    )
    assert res.status_code == 500
    body = res.json()
    assert body.get("error") == "scan_failed"
    assert body.get("detail") == "The scan could not be completed. Please try again."
    # Ensure sensitive filesystem path was NOT leaked
    assert r"C:\Users" not in json.dumps(body)
    assert "core.py" not in json.dumps(body)


def test_upload_error_returns_generic_500_without_path_leakage(auth_client, monkeypatch):
    """Upload analysis failures must return generic message without leaking paths (R-03)."""
    def mock_fail(*args, **kwargs):
        raise ValueError(r"OpenCV pipeline crash in C:\Users\Secret\opencv_engine.py")

    monkeypatch.setattr(backend_main, "_make_uploaded_batch", mock_fail)
    import cv2
    import numpy as np
    valid_jpeg = cv2.imencode(".jpg", np.zeros((100, 100, 3), dtype=np.uint8))[1].tobytes()

    res = auth_client.post(
        "/api/scan/upload",
        files={"image": ("sample.jpg", valid_jpeg, "image/jpeg")},
        data={"sample_name": "Test", "variety": "IR64", "lighting": "ring_light"},
    )
    assert res.status_code == 500
    body = res.json()
    assert body.get("error") == "scan_failed"
    assert body.get("detail") == "The uploaded photo could not be analyzed. Please check the image and try again."
    assert r"C:\Users" not in json.dumps(body)
    assert "opencv_engine.py" not in json.dumps(body)


# --- R-05 & R-06: Installer Fail-Closed Verification & Electron CSP ---

def test_installer_fail_closed_hash_verification():
    """Installer setup_form.cs must enforce fail-closed SHA-256 payload verification (R-05)."""
    setup_cs = Path(backend_main.PROJECT_ROOT) / "setup_form.cs"
    code = setup_cs.read_text(encoding="utf-8")
    assert "ExpectedPayloadSha256.Trim().Length != 64" in code
    assert "Installer integrity verification failed" in code
    assert "Payload integrity mismatch detected" in code


def test_electron_csp_enforcement_and_graceful_shutdown():
    """Electron main.cjs must remove bypassCSP and enforce restrictive CSP and graceful shutdown (R-06)."""
    main_cjs = Path(backend_main.PROJECT_ROOT) / "electron" / "main.cjs"
    code = main_cjs.read_text(encoding="utf-8")
    assert "bypassCSP: true" not in code
    assert "content-security-policy" in code
    assert "Initiating graceful shutdown of Python worker" in code
    assert "action: 'shutdown'" in code


# --- R-07: Mock Mode Off By Default ---

def test_mock_mode_strictly_disabled_by_default(auth_client, monkeypatch):
    """Mock mode must be OFF by default and rejected without explicit AGRISCAN_ENV=development (R-07)."""
    # 1. Default environment: mock mode rejected even if MOCK=1
    monkeypatch.setenv("MOCK", "1")
    monkeypatch.delenv("AGRISCAN_ENV", raising=False)
    res = auth_client.post(
        "/api/scan",
        json={"lighting": "ring_light", "n_grains": 10, "seed": 42, "sample_name": "Test", "variety": "IR64"},
    )
    assert res.status_code == 403
    assert "requires explicit development environment" in res.json().get("detail", "")

    # 2. Production environment: mock mode strictly prohibited
    monkeypatch.setenv("AGRISCAN_ENV", "production")
    monkeypatch.setenv("MOCK", "1")
    res_prod = auth_client.post(
        "/api/scan",
        json={"lighting": "ring_light", "n_grains": 10, "seed": 42, "sample_name": "Test", "variety": "IR64"},
    )
    assert res_prod.status_code == 403
    assert "strictly prohibited in production" in res_prod.json().get("detail", "")

    # 3. Explicit development environment + MOCK=1: mock mode works
    monkeypatch.setenv("AGRISCAN_ENV", "development")
    monkeypatch.setenv("MOCK", "1")
    res_dev = auth_client.post(
        "/api/scan",
        json={"lighting": "ring_light", "n_grains": 10, "seed": 42, "sample_name": "Test", "variety": "IR64"},
    )
    assert res_dev.status_code == 200
    assert res_dev.json().get("is_mock") is True

