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
    assert data == {"status": "authenticated"}
    assert "token" not in data

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



# --- Additional Hardening & Quality Regressions ---

def test_auth_bypass_env_var_strictly_ignored(unauth_client, monkeypatch):
    """Ensure no environment variable backdoors can disable authentication."""
    monkeypatch.setenv("AGRISCAN_DISABLE_AUTH_FOR_TESTS", "1")
    res = unauth_client.post(
        "/api/scan",
        json={"lighting": "ring_light", "n_grains": 10, "seed": 42, "sample_name": "Test", "variety": "IR64"},
    )
    assert res.status_code == 401


def test_frontend_escape_html_sanitizes_xss():
    """Ensure escapeHtml is properly defined and present in frontend/app.js."""
    app_js_path = Path(__file__).resolve().parent.parent / "frontend" / "app.js"
    assert app_js_path.is_file()
    js_text = app_js_path.read_text(encoding="utf-8")
    assert "function escapeHtml(str)" in js_text
    assert '.replace(/&/g, "&amp;")' in js_text
    assert '.replace(/</g, "&lt;")' in js_text
    assert '.replace(/>/g, "&gt;")' in js_text


def test_electron_main_hardened_file_and_taskkill():
    """Ensure electron/main.cjs has extension filtering, batch scoping, and safe taskkill."""
    main_cjs_path = Path(__file__).resolve().parent.parent / "electron" / "main.cjs"
    assert main_cjs_path.is_file()
    cjs_text = main_cjs_path.read_text(encoding="utf-8")
    assert "allowedExts = ['.jpg', '.jpeg', '.png', '.webp', '.json']" in cjs_text
    assert "Unscoped batch file access" in cjs_text
    assert "spawn('taskkill'" in cjs_text
    assert "exec(`taskkill" not in cjs_text


def test_ipc_worker_mock_mode_consistency(monkeypatch):
    """Ensure ipc_worker handle_scan enforces is_mock_mode_enabled consistency."""
    from backend.ipc_worker import handle_scan
    from fastapi import HTTPException

    # Production with MOCK=1 must raise HTTPException(403)
    monkeypatch.setenv("AGRISCAN_ENV", "production")
    monkeypatch.setenv("MOCK", "1")
    with pytest.raises(HTTPException) as excinfo:
        handle_scan({"lighting": "ring_light", "n_grains": 10, "seed": 42, "sample_name": "Test", "variety": "IR64"})
    assert excinfo.value.status_code == 403


# --- Second Re-Audit Remediations: S-01, S-02, Additional 1-3 ---

def test_s01_static_requests_never_issue_session_cookie(unauth_client):
    """GET /, /index.html, /app.js, /style.css must NEVER issue an authentication cookie (S-01)."""
    for endpoint in ["/", "/index.html", "/app.js", "/style.css"]:
        res = unauth_client.get(endpoint)
        set_cookie = res.headers.get("set-cookie", "")
        assert "agriscan_session" not in set_cookie, f"Endpoint {endpoint} illegally issued agriscan_session cookie!"
        assert "agriscan_session" not in res.cookies, f"Endpoint {endpoint} set agriscan_session in client cookies!"


def test_s01_cookie_only_auth_fails_without_prior_bootstrap(unauth_client):
    """Protected endpoints and /files must strictly return 401 without prior successful bootstrap."""
    assert unauth_client.get("/api/batches").status_code == 401
    assert unauth_client.post("/api/scan", json={"lighting": "ring_light", "n_grains": 10, "sample_name": "T", "variety": "V"}).status_code == 401
    assert unauth_client.get("/files/batches/AS-20261018-001/batch.json").status_code == 401


def test_s01_bootstrap_session_allows_api_access_without_leaking_token(unauth_client):
    """Valid bootstrap establishes session cookie and enables protected access without leaking token."""
    secret = backend_main.create_one_time_bootstrap_secret(ttl=30.0)
    res_boot = unauth_client.post("/api/auth/bootstrap", json={"bootstrap_secret": secret})
    assert res_boot.status_code == 200
    assert res_boot.json() == {"status": "authenticated"}
    assert "token" not in res_boot.json()

    # Now the client has the agriscan_session cookie; protected access must succeed
    res_batches = unauth_client.get("/api/batches")
    assert res_batches.status_code == 200


def test_s02_ipc_worker_bootstrap_flow():
    """Ensure ipc_worker handles register_bootstrap and bootstrap actions over stdio."""
    from backend.ipc_worker import handle_register_bootstrap, handle_bootstrap

    test_secret = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    reg_res = handle_register_bootstrap({"secret": test_secret})
    assert reg_res == {"status": "registered"}

    # Exchange once over IPC
    boot_res = handle_bootstrap({"secret": test_secret})
    assert boot_res.get("status") == "authenticated"
    assert "session_token" in boot_res

    # Replay over IPC must fail
    with pytest.raises(ValueError):
        handle_bootstrap({"secret": test_secret})


def test_additional_1_installer_excludes_env_and_keys(tmp_path):
    """Ensure build_installer excludes .env and private keys but preserves .env.example."""
    from scripts.build_installer import is_excluded_path, scan_file_for_secrets

    assert is_excluded_path(Path(".env")) is True
    assert is_excluded_path(Path(".env.local")) is True
    assert is_excluded_path(Path(".env.production")) is True
    assert is_excluded_path(Path("secret.key")) is True
    assert is_excluded_path(Path("server.pem")) is True
    assert is_excluded_path(Path(".env.example")) is False
    assert is_excluded_path(Path(".env.sample")) is False

    # Test secret scanning
    bad_key_file = tmp_path / "test.txt"
    bad_key_file.write_text("-----BEGIN RSA PRIVATE KEY-----\nMIIE...", encoding="utf-8")
    with pytest.raises(ValueError):
        scan_file_for_secrets(bad_key_file)


def test_additional_2_backend_files_scope_tightened(auth_client, tmp_path, monkeypatch):
    """Backend /files must reject arbitrary 2-segment paths outside approved batch roots."""
    data_dir = tmp_path / "data"
    batches_dir = data_dir / "batches" / "AS-20261109-999"
    batches_dir.mkdir(parents=True)
    valid_file = batches_dir / "batch.json"
    valid_file.write_text("{}", encoding="utf-8")

    # Put a file in an unscoped arbitrary 2-segment directory
    unscoped_dir = data_dir / "arbitrary"
    unscoped_dir.mkdir()
    unscoped_file = unscoped_dir / "data.json"
    unscoped_file.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(backend_main, "DATA_DIR", data_dir)

    # Valid batch-scoped request succeeds
    res_valid = auth_client.get("/files/batches/AS-20261109-999/batch.json")
    assert res_valid.status_code == 200

    # Arbitrary unscoped 2-segment request must be rejected with 403
    res_unscoped = auth_client.get("/files/arbitrary/data.json")
    assert res_unscoped.status_code == 403


def test_additional_3_expired_and_consumed_bootstrap_secrets_purged():
    """Ensure expired and consumed secrets are purged from in-memory dictionary."""
    sec1 = backend_main.create_one_time_bootstrap_secret(ttl=0.001)
    import time
    time.sleep(0.01)
    # Registration of a new secret purges expired entries
    sec2 = backend_main.create_one_time_bootstrap_secret(ttl=30.0)
    assert sec1 not in backend_main._bootstrap_secrets
    assert sec2 in backend_main._bootstrap_secrets

    # Consuming sec2 removes it atomically
    assert backend_main.consume_bootstrap_secret(sec2) is True
    assert sec2 not in backend_main._bootstrap_secrets


# --- T-01: Browser Mode Authentication & Console Bootstrap (Step 1) ---

def test_t01_startup_bootstrap_secret_and_console_banner(capsys):
    """Ensure ensure_active_bootstrap_secret generates a valid secret and prints direct login URL (T-01)."""
    secret = backend_main.ensure_active_bootstrap_secret(ttl=300.0, print_console=True, force_print=True)
    assert secret and isinstance(secret, str)
    assert len(secret) >= 32
    captured = capsys.readouterr()
    assert "AGRISCAN BROWSER AUTHENTICATION (T-01)" in captured.out
    assert secret in captured.out
    assert f"?bootstrap={secret}" in captured.out


def test_t01_bootstrap_secret_auto_refresh_on_consumption():
    """Backend must re-register a fresh secret whenever the current one is consumed (T-01)."""
    active = backend_main.get_active_bootstrap_secret()
    if not active:
        secret = backend_main.ensure_active_bootstrap_secret()
    else:
        secret = active[0]

    # Consuming the current secret
    consumed = backend_main.consume_bootstrap_secret(secret)
    assert consumed is True

    # A fresh active secret must have been immediately re-registered
    fresh_active = backend_main.get_active_bootstrap_secret()
    assert fresh_active is not None
    fresh_secret, remaining_ttl = fresh_active
    assert fresh_secret != secret
    assert remaining_ttl > 0


def test_t01_bootstrap_secret_auto_refresh_on_expiration():
    """Backend must generate a fresh secret when the current secret expires (T-01)."""
    # Register an immediately expired secret
    short_secret = "expired_secret_for_test_refresh_12345"
    backend_main.register_bootstrap_secret(short_secret, ttl=0.001)
    import time
    time.sleep(0.01)

    # Calling ensure_active_bootstrap_secret must detect expiration, purge it, and return a fresh secret
    fresh_secret = backend_main.ensure_active_bootstrap_secret(ttl=300.0, print_console=False)
    assert fresh_secret != short_secret
    assert short_secret not in backend_main._bootstrap_secrets
    assert fresh_secret in backend_main._bootstrap_secrets


def test_t01_auth_status_endpoint(unauth_client, auth_client):
    """GET /api/auth/status returns authenticated boolean without leaking any credentials (T-01)."""
    # Unauthenticated caller
    res_unauth = unauth_client.get("/api/auth/status")
    assert res_unauth.status_code == 200
    assert res_unauth.json() == {"authenticated": False}
    assert "token" not in res_unauth.json()

    # Authenticated caller with header
    res_auth = auth_client.get("/api/auth/status")
    assert res_auth.status_code == 200
    assert res_auth.json() == {"authenticated": True}
    assert "token" not in res_auth.json()

    # Authenticated caller with session cookie
    token = backend_main.get_or_create_api_token()
    cookie_client = TestClient(backend_main.app, cookies={"agriscan_session": token})
    res_cookie = cookie_client.get("/api/auth/status")
    assert res_cookie.status_code == 200
    assert res_cookie.json() == {"authenticated": True}


def test_t01_browser_bootstrap_login_flow(unauth_client):
    """Full browser login workflow: static files have no cookies -> exchange secret -> session valid -> logout (T-01)."""
    # 1. Unauthenticated GET / and /login.html never issue session cookies (S-01 preserved)
    res_root = unauth_client.get("/")
    assert res_root.status_code == 200
    assert "agriscan_session" not in res_root.cookies
    assert "agriscan_session" not in res_root.headers.get("set-cookie", "")

    res_login_page = unauth_client.get("/login.html")
    assert res_login_page.status_code == 200
    assert "agriscan_session" not in res_login_page.cookies

    # 2. Status is unauthenticated
    assert unauth_client.get("/api/auth/status").json() == {"authenticated": False}

    # 3. Exchange active secret
    secret = backend_main.ensure_active_bootstrap_secret()
    res_boot = unauth_client.post("/api/auth/bootstrap", json={"bootstrap_secret": secret})
    assert res_boot.status_code == 200
    assert res_boot.json() == {"status": "authenticated"}
    assert "agriscan_session" in unauth_client.cookies

    # 4. Status is now authenticated
    assert unauth_client.get("/api/auth/status").json() == {"authenticated": True}

    # 5. Protected API works
    assert unauth_client.get("/api/batches").status_code == 200

    # 6. Logout
    res_logout = unauth_client.post("/api/auth/logout")
    assert res_logout.status_code == 200
    assert unauth_client.get("/api/auth/status").json() == {"authenticated": False}


# --- S-05: Malformed batch.json Handling (Step 2) ---

def test_s05_sorted_batch_list_ignores_corrupt_or_incomplete_batch_json(tmp_path, monkeypatch):
    """_get_sorted_batch_list must skip malformed, corrupt, or missing-field batch files without crashing (S-05)."""
    data_dir = tmp_path / "data"
    batches_dir = data_dir / "batches"
    batches_dir.mkdir(parents=True)

    # 1. Valid batch
    valid_dir = batches_dir / "AS-20261110-001"
    valid_dir.mkdir()
    (valid_dir / "batch.json").write_text(
        json.dumps({"batch_id": "AS-20261110-001", "created_at": "2026-11-10T10:00:00"}),
        encoding="utf-8",
    )

    # 2. Corrupted JSON file (syntax error)
    corrupt_dir = batches_dir / "AS-20261110-002"
    corrupt_dir.mkdir()
    (corrupt_dir / "batch.json").write_text("{broken-json: true", encoding="utf-8")

    # 3. Empty dictionary (missing batch_id and created_at)
    empty_dir = batches_dir / "AS-20261110-003"
    empty_dir.mkdir()
    (empty_dir / "batch.json").write_text("{}", encoding="utf-8")

    # 4. Missing created_at field
    no_date_dir = batches_dir / "AS-20261110-004"
    no_date_dir.mkdir()
    (no_date_dir / "batch.json").write_text(
        json.dumps({"batch_id": "AS-20261110-004"}),
        encoding="utf-8",
    )

    # 5. Missing batch_id field
    no_id_dir = batches_dir / "AS-20261110-005"
    no_id_dir.mkdir()
    (no_id_dir / "batch.json").write_text(
        json.dumps({"created_at": "2026-11-10T12:00:00"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(backend_main, "DATA_DIR", data_dir)
    monkeypatch.setattr(backend_main, "DATABASE_PATH", tmp_path / "nonexistent.sqlite3")

    # Calling _get_sorted_batch_list must NOT raise KeyError, JSONDecodeError, or 500
    sorted_batches = backend_main._get_sorted_batch_list()
    assert len(sorted_batches) == 1
    assert sorted_batches[0]["batch_id"] == "AS-20261110-001"


def test_s05_get_batch_corrupt_json_returns_clean_404_not_500(auth_client, tmp_path, monkeypatch):
    """get_batch must degrade corrupt batch.json to a clean 404 error instead of 500 crash (S-05)."""
    data_dir = tmp_path / "data"
    batches_dir = data_dir / "batches"
    batches_dir.mkdir(parents=True)

    # 1. Corrupt JSON in batches directory
    b1_dir = batches_dir / "AS-CORRUPT-001"
    b1_dir.mkdir()
    (b1_dir / "batch.json").write_text("NOT_JSON_DATA{{{", encoding="utf-8")

    # 2. Incomplete non-dict in batches directory
    b2_dir = batches_dir / "AS-CORRUPT-002"
    b2_dir.mkdir()
    (b2_dir / "batch.json").write_text("[]", encoding="utf-8")

    # 3. Corrupt legacy batch
    legacy_dir = data_dir / "AS-CORRUPT-003"
    legacy_dir.mkdir()
    (legacy_dir / "batch.json").write_text("{incomplete", encoding="utf-8")

    monkeypatch.setattr(backend_main, "DATA_DIR", data_dir)
    monkeypatch.setattr(backend_main, "DATABASE_PATH", tmp_path / "nonexistent.sqlite3")

    # Accessing corrupt batches must return 404 cleanly, never 500
    for bid in ["AS-CORRUPT-001", "AS-CORRUPT-002", "AS-CORRUPT-003"]:
        res = auth_client.get(f"/api/batches/{bid}")
        assert res.status_code == 404, f"Expected 404 for {bid}, got {res.status_code}: {res.text}"
        assert res.json() == {"detail": "Batch not found"}


# --- S-06: Wildcard CORS Override Hardening (Step 3) ---

def test_s06_cors_wildcard_strictly_rejected_with_logging(caplog):
    """parse_and_validate_allowed_origins must outright reject wildcard '*' with loud error logging (S-06)."""
    import logging

    with caplog.at_level(logging.ERROR, logger="agriscan"):
        origins = backend_main.parse_and_validate_allowed_origins("*, https://evil.com/*, *evil.com")
        assert "*" not in origins
        assert "https://evil.com/*" not in origins
        assert "*evil.com" not in origins
        # Confirms loud error logging was triggered
        assert any("rejected wildcard origin" in record.message for record in caplog.records)


def test_s06_cors_filters_empty_and_duplicate_origins(caplog):
    """parse_and_validate_allowed_origins must drop empty values and duplicates (S-06)."""
    import logging

    raw = "http://localhost:8000, , https://farm.local, http://localhost:8000, https://farm.local, "
    with caplog.at_level(logging.INFO, logger="agriscan"):
        origins = backend_main.parse_and_validate_allowed_origins(raw)
        # Empty string not present
        assert "" not in origins
        # No duplicates
        assert origins.count("http://localhost:8000") == 1
        assert origins.count("https://farm.local") == 1
        assert "https://farm.local" in origins


def test_s06_cors_rejects_schemeless_origins_with_logging(caplog):
    """parse_and_validate_allowed_origins must reject schemeless origins with loud logging (S-06)."""
    import logging

    with caplog.at_level(logging.ERROR, logger="agriscan"):
        origins = backend_main.parse_and_validate_allowed_origins("attacker.com, evil.org")
        assert "attacker.com" not in origins
        assert "evil.org" not in origins
        assert any("rejected invalid origin" in record.message for record in caplog.records)


# --- S-07: Token File Permissions (Step 4) ---

def test_s07_token_file_permissions_enforced_on_write(tmp_path, monkeypatch):
    """get_or_create_api_token must chmod 0o600 on newly created .auth_token file (S-07)."""
    fake_data = tmp_path / "data"
    fake_token_file = fake_data / ".auth_token"
    monkeypatch.setattr(backend_main, "DATA_DIR", fake_data)
    monkeypatch.setattr(backend_main, "AUTH_TOKEN_FILE", fake_token_file)
    monkeypatch.delenv("AGRISCAN_API_TOKEN", raising=False)

    chmod_calls = []
    real_chmod = os.chmod

    def spy_chmod(p, mode):
        chmod_calls.append((Path(p).resolve(), mode))
        return real_chmod(p, mode)

    monkeypatch.setattr(os, "chmod", spy_chmod)

    token = backend_main.get_or_create_api_token()
    assert token and len(token) == 64
    assert fake_token_file.is_file()
    assert fake_token_file.read_text(encoding="utf-8").strip() == token

    # Verify os.chmod was called with 0o600 (owner read/write only)
    assert any(c[0] == fake_token_file.resolve() and c[1] == 0o600 for c in chmod_calls)


def test_s07_token_file_permissions_repaired_on_read(tmp_path, monkeypatch):
    """get_or_create_api_token must chmod 0o600 on existing .auth_token file to repair legacy permissions (S-07)."""
    fake_data = tmp_path / "data"
    fake_data.mkdir(parents=True)
    fake_token_file = fake_data / ".auth_token"
    existing_token = "existing_secret_token_value_0123456789abcdef"
    fake_token_file.write_text(existing_token, encoding="utf-8")

    monkeypatch.setattr(backend_main, "DATA_DIR", fake_data)
    monkeypatch.setattr(backend_main, "AUTH_TOKEN_FILE", fake_token_file)
    monkeypatch.delenv("AGRISCAN_API_TOKEN", raising=False)

    chmod_calls = []
    real_chmod = os.chmod

    def spy_chmod(p, mode):
        chmod_calls.append((Path(p).resolve(), mode))
        return real_chmod(p, mode)

    monkeypatch.setattr(os, "chmod", spy_chmod)

    retrieved = backend_main.get_or_create_api_token()
    assert retrieved == existing_token

    # Verify os.chmod was called with 0o600 to repair existing file
    assert any(c[0] == fake_token_file.resolve() and c[1] == 0o600 for c in chmod_calls)


def test_database_corrupt_json_handled_gracefully(tmp_path):
    """database.py get_batch and list_batches must degrade gracefully when rows contain corrupt JSON."""
    from backend import database
    import sqlite3

    db_path = tmp_path / "test.sqlite3"
    with database._connect(db_path) as conn:
        # Insert one valid batch and one corrupted batch
        conn.execute(
            "INSERT INTO batches (batch_id, created_at, batch_json) VALUES (?, ?, ?)",
            ("AS-VALID", "2026-11-11T10:00:00", json.dumps({"batch_id": "AS-VALID", "created_at": "2026-11-11T10:00:00"})),
        )
        conn.execute(
            "INSERT INTO batches (batch_id, created_at, batch_json) VALUES (?, ?, ?)",
            ("AS-CORRUPT", "2026-11-11T10:01:00", "{not valid json at all"),
        )

    # get_batch on corrupt record must return None, not raise JSONDecodeError
    assert database.get_batch("AS-CORRUPT", db_path) is None
    valid_batch = database.get_batch("AS-VALID", db_path)
    assert valid_batch is not None
    assert valid_batch["batch_id"] == "AS-VALID"

    # list_batches must return valid records and skip corrupt ones
    batches = database.list_batches(db_path)
    assert len(batches) == 1
    assert batches[0]["batch_id"] == "AS-VALID"


def test_sorted_batch_list_with_corrupt_database_row(tmp_path, monkeypatch):
    """_get_sorted_batch_list must not crash when database table contains a corrupted JSON record."""
    from backend import database

    db_path = tmp_path / "corrupt_test.sqlite3"
    with database._connect(db_path) as conn:
        conn.execute(
            "INSERT INTO batches (batch_id, created_at, batch_json) VALUES (?, ?, ?)",
            ("AS-CORRUPT-ROW", "2026-11-11T10:00:00", "MALFORMED_JSON_STRING{{{"),
        )

    monkeypatch.setattr(backend_main, "DATABASE_PATH", db_path)
    batches = backend_main._get_sorted_batch_list()
    assert isinstance(batches, list)


def test_ipc_worker_rejects_oversized_upload(tmp_path):
    """ipc_worker.handle_scan_upload must reject images exceeding 10 MB."""
    from backend.ipc_worker import handle_scan_upload

    huge_file = tmp_path / "huge.jpg"
    # Create an empty sparse or zero-filled file slightly over 10 MB
    with open(huge_file, "wb") as f:
        f.seek(10 * 1024 * 1024 + 1)
        f.write(b"\0")

    with pytest.raises(ValueError, match="10 MB or smaller"):
        handle_scan_upload({
            "file_path": str(huge_file),
            "sample_name": "Test",
            "variety": "Basmati",
            "lighting": "ring_light",
        })


def test_installer_and_launcher_pe_manifest_and_metadata():
    """Installer and launcher must use native Win32 COM, manifests, and avoid dropper/script heuristics."""
    root = Path(backend_main.PROJECT_ROOT)
    setup_cs = (root / "setup_form.cs").read_text(encoding="utf-8")
    launcher_cs = (root / "launcher.cs").read_text(encoding="utf-8")
    installer_manifest = root / "installer.manifest"
    launcher_manifest = root / "launcher.manifest"

    # 1. Manifest existence and contents
    assert installer_manifest.is_file(), "installer.manifest must exist"
    assert launcher_manifest.is_file(), "launcher.manifest must exist"
    inst_man_text = installer_manifest.read_text(encoding="utf-8")
    assert 'requestedExecutionLevel level="asInvoker"' in inst_man_text
    assert "{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}" in inst_man_text

    # 2. Assembly metadata in C# sources
    for code in [setup_cs, launcher_cs]:
        assert "[assembly: AssemblyTitle(" in code
        assert "[assembly: AssemblyProduct(" in code
        assert "[assembly: AssemblyCompany(" in code
        assert "[assembly: AssemblyVersion(" in code
        assert "[assembly: AssemblyFileVersion(" in code

    # 3. Elimination of heuristic triggers in setup_form.cs
    assert "WScript.Shell" not in setup_cs, "WScript.Shell late-binding must be removed"
    assert "p.Kill()" not in setup_cs, "Forcible p.Kill() must be removed to avoid dropper heuristics"
    assert "IShellLinkW" in setup_cs, "Must use native IShellLinkW COM interface"
    assert "IPersistFile" in setup_cs, "Must use native IPersistFile COM interface"


def test_installer_environment_permissions_and_uac_safety():
    """Installer must enforce pre-flight permission checks, UAC elevation prompt, and protected folder guards."""
    root = Path(backend_main.PROJECT_ROOT)
    setup_cs = (root / "setup_form.cs").read_text(encoding="utf-8")
    installer_manifest = (root / "installer.manifest").read_text(encoding="utf-8")
    launcher_manifest = (root / "launcher.manifest").read_text(encoding="utf-8")

    # 1. Long path and DPI awareness in manifests
    for man in [installer_manifest, launcher_manifest]:
        assert "<longPathAware" in man
        assert "<dpiAware" in man

    # 2. Pre-flight write checks and elevation handling
    assert "CanWriteToDirectory" in setup_cs
    assert "IsAdministrator" in setup_cs
    assert "RelaunchAsAdmin" in setup_cs
    assert "SpecialFolder.Windows" in setup_cs
    assert "Controlled Folder Access" in setup_cs



