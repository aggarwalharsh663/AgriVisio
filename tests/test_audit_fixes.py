"""Targeted regression tests for audit vulnerability and bug repairs (VULN-01 through BUG-10)."""

import io
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from backend import main
from backend.grain_analysis import _analyze_image, MIN_GRAIN_WIDTH_MM, MAX_PLAUSIBLE_LB_RATIO
from backend.hardware_scanner import _try_scan_wia, ScannerOperationError


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Isolate DATA_DIR and DATABASE_PATH for test isolation."""
    data_dir = tmp_path / "data"
    batches_dir = data_dir / "batches"
    batches_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "agrivisio.sqlite3"

    monkeypatch.setattr(main, "DATA_DIR", data_dir)
    monkeypatch.setattr(main, "DATABASE_PATH", db_path)
    monkeypatch.setattr(main, "AUTH_TOKEN_FILE", tmp_path / "auth_token.txt")
    files_mount = next(
        route for route in main.app.routes if getattr(route, "path", None) == "/files"
    )
    monkeypatch.setattr(files_mount, "app", StaticFiles(directory=data_dir))
    return data_dir, db_path


# 1. Test VULN-02: Hardware scanner handles paths with spaces via environment variable
def test_hardware_scan_path_with_spaces_env(monkeypatch):
    """Ensure _try_scan_wia passes AGRIVISIO_SCAN_OUTPUT in subprocess env."""
    monkeypatch.setattr("backend.hardware_scanner.is_hardware_scan_supported", lambda: True)

    captured_env = {}

    def mock_run(cmd, capture_output=True, text=True, timeout=90.0, env=None):
        nonlocal captured_env
        captured_env = env or {}
        # Simulate successful scan output
        out_path = captured_env.get("AGRIVISIO_SCAN_OUTPUT")
        if out_path:
            # Create a dummy image file at the output path
            Path(out_path).write_bytes(b"\xFF\xD8\xFF\xE0" + b"\x00" * 20)
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = "SCAN_OK:XRES=300:YRES=300"
        mock_res.stderr = ""
        return mock_res

    monkeypatch.setattr("subprocess.run", mock_run)

    # Temporary directory path with spaces simulated
    temp_img_path = _try_scan_wia()
    try:
        assert os.path.isfile(temp_img_path)
        assert captured_env.get("AGRIVISIO_SCAN_OUTPUT") == temp_img_path
    finally:
        if os.path.isfile(temp_img_path):
            os.remove(temp_img_path)


# 2. Test BUG-02: _analyze_image gracefully skips degenerate contours without crashing
def test_analyze_image_skips_degenerate_contours(tmp_path, monkeypatch):
    """Ensure degenerate contours that fail in _measure_grain_details do not abort image analysis."""
    # Create synthetic test image
    img = np.zeros((200, 200, 3), dtype=np.uint8)
    img[50:80, 50:80] = 200  # valid grain blob

    # Mock segment_grains to return a valid contour and a degenerate contour
    valid_contour = np.array([[[50, 50]], [[50, 80]], [[80, 80]], [[80, 50]]], dtype=np.int32)
    degenerate_contour = np.array([[[199, 199]]], dtype=np.int32)  # single point/degenerate

    monkeypatch.setattr(
        "backend.grain_analysis.segment_grains",
        lambda *args, **kwargs: (
            [valid_contour, degenerate_contour],
            {
                "effective_min_pixel_area": 10,
                "min_grain_area_mm2": 0.5,
                "contours_rejected": 0,
                "contours_retained": 2,
            },
        ),
    )

    img_file = tmp_path / "sample.jpg"
    cv2.imwrite(str(img_file), img)

    # Should succeed without raising ValueError: "contour does not contain any image pixels"
    res = _analyze_image(str(img_file), 0.1, str(tmp_path), ignore_calibration_card=True, split_touching=False)
    assert "grains" in res
    assert len(res["grains"]) == 1
    assert "annotated_image_path" in res


# 3. Test BUG-03: Disk fallback discovers both AV-* and legacy AS-* batch directories
def test_batch_listing_discovers_both_av_and_as_batches(isolated_env):
    data_dir, db_path = isolated_env
    batches_dir = data_dir / "batches"

    # Create an AV-* batch on disk (not in sqlite)
    av_dir = batches_dir / "AV-20261120-001"
    av_dir.mkdir()
    av_batch = {
        "batch_id": "AV-20261120-001",
        "created_at": "2026-11-20T12:00:00",
        "variety": "Basmati",
        "counts": {"good": 50},
        "summary": {"total_grains": 50},
    }
    (av_dir / "batch.json").write_text(json.dumps(av_batch), encoding="utf-8")

    # Create a legacy AS-* batch on disk
    as_dir = batches_dir / "AS-20261119-002"
    as_dir.mkdir()
    as_batch = {
        "batch_id": "AS-20261119-002",
        "created_at": "2026-11-19T12:00:00",
        "variety": "IR64",
        "counts": {"good": 40},
        "summary": {"total_grains": 40},
    }
    (as_dir / "batch.json").write_text(json.dumps(as_batch), encoding="utf-8")

    batch_list = main._get_sorted_batch_list()
    batch_ids = [b["batch_id"] for b in batch_list]

    assert "AV-20261120-001" in batch_ids
    assert "AS-20261119-002" in batch_ids


# 4. Test BUG-06: calibration_mm_per_pixel validates tuple/list length == 2
def test_calibration_mm_per_pixel_tuple_validation(isolated_env):
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    # Pass 1-element list: should raise ValueError, not IndexError
    with pytest.raises(ValueError, match="must contain exactly 2 positive finite numbers"):
        main._make_uploaded_batch(
            image=img,
            sample_name="Sample",
            variety="Basmati",
            lighting="ring_light",
            raw_bytes=b"fake",
            source_type="direct_input",
            calibration_mm_per_pixel=[0.08],
        )

    # Pass 3-element list: should raise ValueError
    with pytest.raises(ValueError, match="must contain exactly 2 positive finite numbers"):
        main._make_uploaded_batch(
            image=img,
            sample_name="Sample",
            variety="Basmati",
            lighting="ring_light",
            raw_bytes=b"fake",
            source_type="direct_input",
            calibration_mm_per_pixel=[0.08, 0.08, 0.08],
        )


# 5. Test AUDIT-02: PNG uploads preserve .png extension and prevent MIME mismatch
def test_png_upload_preserves_png_extension(isolated_env):
    data_dir, db_path = isolated_env
    # Generate 100x100 PNG image
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    img[20:60, 20:60] = 200
    _, png_bytes = cv2.imencode(".png", img)

    batch = main._make_uploaded_batch(
        image=img,
        sample_name="PNG Sample",
        variety="Basmati",
        lighting="ring_light",
        raw_bytes=png_bytes.tobytes(),
    )
    assert batch["images"]["raw"].endswith(".png")
    assert batch["images"]["calibrated"].endswith(".png")


# 6. Test AUDIT-03: Backend HTTP middleware includes Content-Security-Policy
def test_security_headers_include_csp(isolated_env):
    client = TestClient(main.app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert "Content-Security-Policy" in response.headers
    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp


# 7. Test VULN-01: IPC worker mode strictly suppresses stdout console banner
def test_ipc_worker_mode_suppresses_stdout_banner(capsys, monkeypatch):
    """In worker mode, stdout is reserved for JSON IPC; banner must not be printed to stdout."""
    monkeypatch.setenv("AGRIVISIO_IPC_MODE", "1")
    assert main.is_ipc_worker_mode() is True

    main.print_bootstrap_banner("test_secret_12345", ttl=300.0)
    captured = capsys.readouterr()
    assert captured.out == "", "Stdout must remain completely clean in IPC worker mode"
    assert "test_secret_12345" not in captured.out


# 8. Test BUG-01: Backend uploaded batch is marked with simulated=False and REAL_IMAGE_MEASUREMENT
def test_uploaded_batch_simulated_false(isolated_env):
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    img[20:60, 20:60] = 200
    batch = main._make_uploaded_batch(
        image=img,
        sample_name="Real Sample",
        variety="Basmati",
        lighting="ring_light",
    )
    assert batch["simulated"] is False
    assert batch["is_mock"] is False
    assert batch["measurement_status"] == "REAL_IMAGE_MEASUREMENT"


# 9. Test BUG-A2: IPC worker handle_list_batches supports page and page_size parameters
def test_ipc_worker_list_batches_pagination(isolated_env, monkeypatch):
    from backend import ipc_worker
    data_dir, db_path = isolated_env

    # Insert dummy batch records
    for i in range(5):
        main.save_batch({
            "batch_id": f"AV-TEST-{i:03d}",
            "created_at": f"2026-11-20T10:0{i}:00Z",
            "sample_name": f"Batch {i}",
            "variety": "Basmati",
            "lighting": "ring_light",
            "simulated": False,
            "grains": [],
            "summary": {"total_grains": 0},
        }, db_path)

    # Test unpaginated
    unpaginated = ipc_worker.handle_list_batches({})
    assert isinstance(unpaginated, list)
    assert len(unpaginated) == 5

    # Test paginated
    paginated = ipc_worker.handle_list_batches({"page": 1, "page_size": 2})
    assert isinstance(paginated, dict)
    assert paginated["total"] == 5
    assert len(paginated["items"]) == 2
    assert paginated["page"] == 1
    assert paginated["total_pages"] == 3


# 10. Test BUG-A4: index.html contains physical reference calibration UI inputs
def test_index_html_contains_calibration_inputs():
    index_html = (Path(main.__file__).parent.parent / "frontend" / "index.html").read_text(encoding="utf-8")
    assert 'id="calibration-mm-per-pixel"' in index_html
    assert 'id="reference-length-mm"' in index_html
    assert 'id="reference-pixels"' in index_html


# 11. Test CR-01: Alternate model training uses distinct manifest sidecar
def test_alternate_model_training_uses_sidecar_manifest(tmp_path):
    from backend import train_classifier
    custom_dest = tmp_path / "custom_experiment.joblib"
    prod_manifest = tmp_path / "model_manifest.json"
    prod_manifest.write_text(json.dumps({"sha256": "PROD_HASH"}), encoding="utf-8")

    dummy_features = np.ones((50, len(train_classifier.FEATURE_NAMES)), dtype=np.float32)
    dummy_labels = np.array(["good", "broken", "chalky", "discolored"] * 12 + ["good", "broken"])
    dummy_groups = np.repeat(np.arange(5), 10)

    with patch.object(train_classifier, "_collect_training_data", return_value=(dummy_features, dummy_labels, dummy_groups)):
        res = train_classifier.train_classifier(scene_count=5, grains_per_scene=10, model_path=custom_dest)
        assert (tmp_path / "custom_experiment_manifest.json").is_file()
        assert prod_manifest.read_text(encoding="utf-8") == json.dumps({"sha256": "PROD_HASH"})


# 12. Test CR-04: Hardware scanner without DPI readback does not inject assumed 300 DPI
def test_acquire_hardware_scan_missing_dpi_uncalibrated(tmp_path, monkeypatch):
    from backend import hardware_scanner
    sample_img = np.zeros((100, 100, 3), dtype=np.uint8)
    sample_path = tmp_path / "scanned.jpg"
    # Write image without DPI metadata
    cv2.imwrite(str(sample_path), sample_img)

    monkeypatch.setattr(hardware_scanner, "_try_scan_wia", lambda timeout=90.0: str(sample_path))
    monkeypatch.setattr(hardware_scanner, "_last_wia_readback_dpi", None)

    img, raw_bytes = hardware_scanner.acquire_hardware_scan()
    calib = hardware_scanner.resolve_scanner_calibration(raw_bytes=raw_bytes, image=img)
    assert calib["calibration_status"] == "UNCALIBRATED"
    assert calib["scanner_dpi"] is None


# 13. Test CR-05: IPC worker invalid pagination parameters raise ValueError
def test_ipc_worker_invalid_pagination_raises_value_error(isolated_env):
    from backend import ipc_worker
    with pytest.raises(ValueError, match="page must be an integer >= 1"):
        ipc_worker.handle_list_batches({"page": 0, "page_size": 20})

    with pytest.raises(ValueError, match="page_size must be an integer between 1 and 100"):
        ipc_worker.handle_list_batches({"page": 1, "page_size": 500})

    # Test fractional and boolean rejection (CR Finding 20)
    with pytest.raises(ValueError, match="cannot be a boolean"):
        ipc_worker.handle_list_batches({"page": True, "page_size": 20})

    with pytest.raises(ValueError, match="must be an integer"):
        ipc_worker.handle_list_batches({"page": 1.5, "page_size": 20})


# 14. Test CR Finding 24: Unusable calibration scale rejected before creating batch
def test_unusable_scale_underflow_rejected_before_batch_creation(isolated_env):
    data_dir, db_path = isolated_env
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    with pytest.raises(ValueError, match="calibration_mm_per_pixel produces an unusable"):
        main._make_uploaded_batch(
            image=img,
            sample_name="Sample",
            variety="Basmati",
            lighting="ring_light",
            calibration_mm_per_pixel=1e-200,
        )

    # Verify no batch directories were created
    batches_dir = data_dir / "batches"
    assert len(list(batches_dir.glob("AV-*"))) == 0


# 15. Test CR Finding 16: Summary excludes missing measurements without KeyError
def test_summary_measurements_excludes_missing_dimension_from_morphometrics():
    from backend.grain_analysis import compute_batch_summary
    # Incomplete record missing length_mm
    incomplete_grains = [
        {"id": 1, "label": "good", "width_mm": 2.0, "lb_ratio": 3.0, "confidence": 0.9}
    ]
    res = compute_batch_summary(incomplete_grains)
    assert res["total_grains"] == 1
    assert res["counts"]["good"] == 1
    # Morphometric statistics should skip the incomplete record
    assert res["mean_length_mm"] == 0.0


# 16. Test CR Finding 17: Exclude non-finite confidence from mean_confidence
def test_confidence_mean_ignores_non_finite_values():
    from backend.grain_analysis import compute_batch_summary
    grains = [
        {"id": 1, "length_mm": 6.0, "width_mm": 2.0, "lb_ratio": 3.0, "confidence": float("nan")},
        {"id": 2, "length_mm": 6.2, "width_mm": 2.1, "lb_ratio": 3.0, "confidence": 0.8},
    ]
    res = compute_batch_summary(grains)
    assert np.isfinite(res["mean_confidence"])
    assert res["mean_confidence"] == 0.8


# 17. Test CR Finding 20: Low resolution scale below 4px cutoff stays calibrated
def test_calculate_min_pixel_area_low_resolution_stays_calibrated():
    from backend.grain_analysis import calculate_min_pixel_area
    # At 0.60 mm/pixel, 1.0 mm² is 1.0 / 0.36 = 2.78 px -> 3 px
    min_pixels, is_calibrated = calculate_min_pixel_area(0.60, min_grain_area_mm2=1.0)
    assert is_calibrated is True
    assert min_pixels == 4


# 18. Test CR Finding 21: Overflowing scale rejected with HTTP 422
def test_overflowing_scale_rejected_with_422(isolated_env):
    token = main.get_or_create_api_token()
    client = TestClient(main.app)
    response = client.post(
        "/api/scan/upload",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "sample_name": "Test",
            "variety": "Basmati",
            "lighting": "ring_light",
            "calibration_mm_per_pixel": "1e200",
        },
        files={"image": ("test.jpg", b"fake", "image/jpeg")},
    )
    assert response.status_code == 422


# 19. Test CR Finding: Small fragment boundary shrink Pick's theorem compensation
def test_small_grain_fragment_retained_with_picks_theorem_compensation():
    from backend.grain_analysis import validate_grain_contour
    # Construct a 1.3mm x 1.0mm fragment at 0.12 mm/pixel
    # Raster dimensions: ~11 px x ~8 px (~70 pixels raster area)
    scale = 0.12
    canvas = np.zeros((60, 60), dtype=np.uint8)
    cv2.rectangle(canvas, (20, 20), (31, 28), 255, -1)
    contours, _ = cv2.findContours(canvas, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    assert len(contours) == 1
    c = contours[0]

    is_valid, reason, metrics = validate_grain_contour(
        c,
        mm_per_pixel=scale,
        min_grain_area_mm2=1.0,
        min_grain_width_mm=0.50,
    )
    assert is_valid is True, f"Expected small fragment to be retained, got {reason}: {metrics}"
    assert reason == ""
    assert metrics["effective_area_mm2"] >= 1.0
    assert metrics["effective_width_mm"] >= 0.50


# 20. Test CR Finding: Electron main scripts check stale process and error distinction
def test_electron_main_guards_against_stale_process_and_ipc_error_mapping():
    main_cjs = (Path(__file__).parent.parent / "electron" / "main.cjs").read_text(encoding="utf-8")
    assert "if (pythonProcess !== proc) return;" in main_cjs
    assert "/not found/i.test(errMsg) || /404/.test(errMsg)" in main_cjs
    assert "log('[get_batch IPC Error]', errMsg);" in main_cjs
    assert "log('[export IPC Error]', errMsg);" in main_cjs


# 21. Test CR Finding: _get_sorted_batch_list skips invalid recovery records without breaking sorting
def test_get_sorted_batch_list_validates_recovered_records(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    batches_dir = data_dir / "batches"
    batches_dir.mkdir(parents=True)

    # Valid recovery batch
    valid_dir = batches_dir / "AS-VALID"
    valid_dir.mkdir()
    (valid_dir / "batch.json").write_text(json.dumps({
        "batch_id": "AS-VALID",
        "created_at": "2026-11-21T12:00:00+00:00",
        "sample_name": "Valid",
    }), encoding="utf-8")

    # Invalid recovery batch with integer created_at (Finding 4)
    invalid_time_dir = batches_dir / "AS-INVALID-TIME"
    invalid_time_dir.mkdir()
    (invalid_time_dir / "batch.json").write_text(json.dumps({
        "batch_id": "AS-INVALID-TIME",
        "created_at": 42,
    }), encoding="utf-8")

    # Invalid recovery batch with non-string batch_id
    invalid_id_dir = batches_dir / "AS-INVALID-ID"
    invalid_id_dir.mkdir()
    (invalid_id_dir / "batch.json").write_text(json.dumps({
        "batch_id": 12345,
        "created_at": "2026-11-21T12:05:00+00:00",
    }), encoding="utf-8")

    monkeypatch.setattr(main, "DATA_DIR", data_dir)
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "empty.sqlite3")

    result = main._get_sorted_batch_list()
    # Only valid batch should be present; invalid records skipped safely
    assert len(result) == 1
    assert result[0]["batch_id"] == "AS-VALID"


# 22. Test CR Finding: Uncommitted batch directories cleaned up if analysis raises error
def test_uncommitted_batch_directory_removed_on_analysis_error(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    batches_dir = data_dir / "batches"
    batches_dir.mkdir(parents=True)

    monkeypatch.setattr(main, "DATA_DIR", data_dir)
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "test.sqlite3")

    # Mock analyze_uploaded_image to raise an error
    def failing_analyze(*args, **kwargs):
        raise RuntimeError("Simulated computer vision analysis failure")

    monkeypatch.setattr(main, "analyze_uploaded_image", failing_analyze)

    dummy_img = np.zeros((50, 50, 3), dtype=np.uint8)
    with pytest.raises(RuntimeError, match="Simulated computer vision analysis failure"):
        main._make_uploaded_batch(
            image=dummy_img,
            sample_name="FailTest",
            variety="Basmati",
            lighting="ring_light",
            raw_bytes=b"\x89PNG\r\n\x1a\n" + b"\x00" * 20,
        )

    # Verify no orphaned batch directories remain in batches_dir
    remaining_dirs = list(batches_dir.glob("AV-*")) + list(batches_dir.glob("AS-*"))
    assert len(remaining_dirs) == 0, f"Expected no leftover batch directories, found: {remaining_dirs}"









