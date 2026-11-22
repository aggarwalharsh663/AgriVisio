"""Comprehensive tests for flatbed scanner DPI calibration, effective scale calculations,
resolution verification, anisotropic measurement invariance, and physical reference object accuracy.
"""

import io
import math
import cv2
import numpy as np
import pytest
from PIL import Image

from backend import main
from backend.database import get_batch
from backend.grain_analysis import _measure_grain_details
from backend.hardware_scanner import (
    calculate_scanner_scale,
    extract_dpi_from_image,
    resolve_scanner_calibration,
)
from backend.ipc_worker import handle_scan_hardware


@pytest.fixture
def test_setup(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    batches_dir = data_dir / "batches"
    batches_dir.mkdir(parents=True, exist_ok=True)
    database_path = tmp_path / "agrivisio.sqlite3"

    monkeypatch.setattr(main, "DATA_DIR", data_dir)
    monkeypatch.setattr(main, "DATABASE_PATH", database_path)
    return data_dir, database_path


def _create_jpeg_bytes_with_dpi(image_bgr: np.ndarray, dpi: tuple[float, float] | None) -> bytes:
    """Encode an image as JPEG bytes with optional JFIF/EXIF DPI metadata."""
    if dpi is None:
        _, encoded = cv2.imencode(".jpg", image_bgr)
        return encoded.tobytes()
    buf = io.BytesIO()
    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    pil_im = Image.fromarray(rgb)
    pil_im.save(buf, format="JPEG", dpi=dpi)
    return buf.getvalue()


# 1. Correct 300 DPI conversion
def test_correct_300_dpi_conversion():
    img = np.zeros((200, 200, 3), dtype=np.uint8)
    raw_bytes = _create_jpeg_bytes_with_dpi(img, (300, 300))

    detected = extract_dpi_from_image(raw_bytes=raw_bytes)
    assert detected == (300.0, 300.0)

    calib = calculate_scanner_scale(300, 300)
    assert calib["calibration_status"] == "CALIBRATED"
    assert calib["calibration_reference_type"] == "FLATBED_SCANNER_300DPI"
    assert calib["scanner_dpi"] == 300
    assert calib["mm_per_pixel"] == round(25.4 / 300.0, 5)
    assert calib["is_anisotropic"] is False
    assert math.isclose(calib["scale_x"], 25.4 / 300.0, rel_tol=1e-5)


# 2. Correct 600 DPI conversion
def test_correct_600_dpi_conversion():
    img = np.zeros((200, 200, 3), dtype=np.uint8)
    raw_bytes = _create_jpeg_bytes_with_dpi(img, (600, 600))

    detected = extract_dpi_from_image(raw_bytes=raw_bytes)
    assert detected == (600.0, 600.0)

    calib = calculate_scanner_scale(600, 600)
    assert calib["calibration_status"] == "CALIBRATED"
    assert calib["calibration_reference_type"] == "FLATBED_SCANNER_600DPI"
    assert calib["scanner_dpi"] == 600
    assert calib["mm_per_pixel"] == round(25.4 / 600.0, 5)
    assert calib["is_anisotropic"] is False
    assert math.isclose(calib["scale_x"], 25.4 / 600.0, rel_tol=1e-5)


# 3. Correct handling of different DPI values (150 DPI, 1200 DPI, anisotropic, resized)
def test_handling_different_dpi_values():
    # 150 DPI
    c150 = calculate_scanner_scale(150, 150)
    assert c150["calibration_reference_type"] == "FLATBED_SCANNER_150DPI"
    assert c150["scanner_dpi"] == 150
    assert c150["mm_per_pixel"] == round(25.4 / 150.0, 5)

    # 1200 DPI
    c1200 = calculate_scanner_scale(1200, 1200)
    assert c1200["calibration_reference_type"] == "FLATBED_SCANNER_1200DPI"
    assert c1200["scanner_dpi"] == 1200
    assert c1200["mm_per_pixel"] == round(25.4 / 1200.0, 5)

    # Anisotropic 300x600 DPI
    c_aniso = calculate_scanner_scale(300, 600)
    assert c_aniso["is_anisotropic"] is True
    assert c_aniso["calibration_reference_type"] == "FLATBED_SCANNER_ANISOTROPIC_300x600DPI"
    assert c_aniso["scanner_dpi"] == (300, 600)
    assert c_aniso["mm_per_pixel"] == (round(25.4 / 300.0, 5), round(25.4 / 600.0, 5))

    # Resized scanner image: scanned at 300 DPI, downsampled from 2000x2000 to 1000x1000
    c_resized = calculate_scanner_scale(
        300, 300, current_shape=(1000, 1000), original_shape=(2000, 2000)
    )
    assert c_resized["scanner_dpi"] == 150
    assert c_resized["calibration_reference_type"] == "FLATBED_SCANNER_150DPI"
    assert c_resized["mm_per_pixel"] == round(25.4 / 150.0, 5)


# 4. Missing DPI metadata
def test_missing_dpi_metadata():
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    raw_bytes_no_dpi = _create_jpeg_bytes_with_dpi(img, None)

    detected = extract_dpi_from_image(raw_bytes=raw_bytes_no_dpi)
    assert detected is None

    # Should safely fall back to uncalibrated estimate (0.12 mm/pixel)
    calib = resolve_scanner_calibration(raw_bytes=raw_bytes_no_dpi, image=img)
    assert calib["calibration_status"] == "UNCALIBRATED"
    assert calib["calibration_reference_type"] == "DEFAULT_ESTIMATE"
    assert calib["mm_per_pixel"] == 0.12
    assert calib["scanner_dpi"] is None


# 5. Incorrect DPI metadata
def test_incorrect_dpi_metadata():
    img = np.zeros((100, 100, 3), dtype=np.uint8)

    # Negative / zero / corrupt values passed explicitly
    calib_neg = resolve_scanner_calibration(image=img, explicit_dpi=-300)
    assert calib_neg["calibration_status"] == "UNCALIBRATED"
    assert calib_neg["mm_per_pixel"] == 0.12

    calib_zero = resolve_scanner_calibration(image=img, explicit_dpi=0)
    assert calib_zero["calibration_status"] == "UNCALIBRATED"
    assert calib_zero["mm_per_pixel"] == 0.12

    calib_nan = resolve_scanner_calibration(image=img, explicit_dpi=float("nan"))
    assert calib_nan["calibration_status"] == "UNCALIBRATED"
    assert calib_nan["mm_per_pixel"] == 0.12


# 6. Scale forwarding into batch creation
def test_scale_forwarding_into_batch_creation(test_setup, monkeypatch):
    img = np.zeros((400, 400, 3), dtype=np.uint8)
    cv2.ellipse(img, (200, 200), (40, 15), 0, 0, 360, (230, 225, 210), -1)
    raw_bytes = _create_jpeg_bytes_with_dpi(img, (300, 300))

    monkeypatch.setattr(
        "backend.ipc_worker.acquire_hardware_scan",
        lambda timeout=90.0: (img, raw_bytes),
    )

    res = handle_scan_hardware({
        "sample_name": "Forwarding Test",
        "variety": "Basmati",
        "lighting": "ring_light",
    })

    assert res["calibration"]["calibration_status"] == "CALIBRATED"
    assert res["calibration"]["calibration_reference_type"] == "FLATBED_SCANNER_300DPI"
    assert res["calibration"]["mm_per_pixel"] == round(25.4 / 300.0, 5)
    assert res["calibration"]["scanner_dpi"] == 300
    assert res["calibration"]["measurement_source"] == "FLATBED_SCANNER"
    assert res["calibration"]["source_type"] == "FLATBED_SCANNER"

    # Verify SQLite record persistence
    _, db_path = test_setup
    saved = get_batch(res["batch_id"], db_path)
    assert saved is not None
    assert saved["calibration"]["mm_per_pixel"] == round(25.4 / 300.0, 5)
    assert saved["calibration"]["scanner_dpi"] == 300


# 7. Correct batch calibration status (verified vs unverified)
def test_correct_batch_calibration_status(test_setup, monkeypatch):
    img = np.zeros((400, 400, 3), dtype=np.uint8)
    cv2.ellipse(img, (200, 200), (40, 15), 0, 0, 360, (230, 225, 210), -1)

    # 7a. Verified 300 DPI
    raw_300 = _create_jpeg_bytes_with_dpi(img, (300, 300))
    monkeypatch.setattr("backend.ipc_worker.acquire_hardware_scan", lambda timeout=90.0: (img, raw_300))
    res_calib = handle_scan_hardware({"sample_name": "Calibrated", "variety": "IR64"})
    assert res_calib["calibration"]["calibration_status"] == "CALIBRATED"

    # 7b. Unverified missing DPI
    raw_missing = _create_jpeg_bytes_with_dpi(img, None)
    monkeypatch.setattr("backend.ipc_worker.acquire_hardware_scan", lambda timeout=90.0: (img, raw_missing))
    res_uncalib = handle_scan_hardware({"sample_name": "Uncalibrated", "variety": "IR64"})
    assert res_uncalib["calibration"]["calibration_status"] == "UNCALIBRATED"
    assert res_uncalib["calibration"]["mm_per_pixel"] == 0.12


# 8. No regression in ordinary image uploads
def test_no_regression_in_ordinary_image_uploads(test_setup):
    img = np.zeros((300, 300, 3), dtype=np.uint8)
    cv2.ellipse(img, (150, 150), (35, 12), 0, 0, 360, (220, 220, 220), -1)

    # Ordinary uncalibrated upload
    b_uncalib = main._make_uploaded_batch(img, "Photo", "Basmati", "ring_light")
    assert b_uncalib["calibration"]["source_type"] == "UPLOADED_IMAGE"
    assert b_uncalib["calibration"]["calibration_status"] == "UNCALIBRATED"
    assert b_uncalib["calibration"]["calibration_reference_type"] == "DEFAULT_ESTIMATE"
    assert b_uncalib["calibration"]["mm_per_pixel"] == 0.12

    # Direct scale calibration upload
    b_direct = main._make_uploaded_batch(img, "Photo", "Basmati", "ring_light", calibration_mm_per_pixel=0.088)
    assert b_direct["calibration"]["source_type"] == "UPLOADED_IMAGE"
    assert b_direct["calibration"]["calibration_status"] == "CALIBRATED"
    assert b_direct["calibration"]["calibration_reference_type"] == "DIRECT_SCALE_INPUT"
    assert b_direct["calibration"]["mm_per_pixel"] == 0.088


# 9. Measurement test using a known physical reference object
def test_measurement_using_known_physical_reference_object():
    """Verify that a known 10.0 mm x 2.0 mm physical gauge block measures accurately
    at 300 DPI (approx 10.0 mm), while old 0.12 mm/pixel produced a 41.7% error (approx 14.17 mm).
    """
    scale_300 = 25.4 / 300.0  # 0.0846666... mm/pixel

    # True dimensions
    true_length_mm = 10.0
    true_width_mm = 2.0

    length_px = round(true_length_mm / scale_300)  # 118 px
    width_px = round(true_width_mm / scale_300)    # 24 px

    # Create synthetic scanner image with known physical reference object
    image = np.zeros((300, 300, 3), dtype=np.uint8)
    cv2.rectangle(image, (50, 50), (50 + length_px, 50 + width_px), (220, 220, 220), -1)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    contours, _ = cv2.findContours(gray, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    assert len(contours) == 1
    contour = contours[0]

    # Measure with corrected 300 DPI scale
    meas_300, _ = _measure_grain_details(contour, image, scale_300)
    measured_len_300 = meas_300["length_mm"]
    measured_wid_300 = meas_300["width_mm"]

    # Measure with old buggy 0.12 mm/pixel scale
    meas_old, _ = _measure_grain_details(contour, image, 0.12)
    measured_len_old = meas_old["length_mm"]

    # Assert accurate physical recovery with 300 DPI scale (error < 0.5%)
    len_error_300 = abs(measured_len_300 - true_length_mm)
    assert len_error_300 < 0.1, f"Expected ~10.0 mm, got {measured_len_300:.3f} mm"
    assert abs(measured_wid_300 - true_width_mm) < 0.1, f"Expected ~2.0 mm, got {measured_wid_300:.3f} mm"

    # Confirm that old 0.12 scale exhibited ~41.7% overestimation error
    overestimation_pct = (measured_len_old - true_length_mm) / true_length_mm * 100
    assert 40.0 <= overestimation_pct <= 43.0, f"Expected ~41.7% overestimation, got {overestimation_pct:.1f}%"


# 10. Anisotropic measurement rotation invariance
def test_anisotropic_measurement_rotation_invariance():
    """Verify that anisotropic scaling (300 DPI x 600 DPI) measures rotated physical objects
    without aspect ratio distortion or angle dependence.
    """
    scale_x = 25.4 / 300.0  # 0.08467 mm/px
    scale_y = 25.4 / 600.0  # 0.04233 mm/px
    aniso_scales = (scale_x, scale_y)

    true_len = 6.0
    true_wid = 2.0

    # 1. Horizontal grain in physical space
    # (x_px = 6.0 / scale_x ~ 71 px, y_px = 2.0 / scale_y ~ 47 px)
    img_horiz = np.zeros((300, 300, 3), dtype=np.uint8)
    cv2.ellipse(
        img_horiz,
        (150, 150),
        (round((true_len / 2.0) / scale_x), round((true_wid / 2.0) / scale_y)),
        0, 0, 360, (220, 220, 220), -1
    )
    c_h, _ = cv2.findContours(cv2.cvtColor(img_horiz, cv2.COLOR_BGR2GRAY), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    meas_h, _ = _measure_grain_details(c_h[0], img_horiz, aniso_scales)

    assert abs(meas_h["length_mm"] - true_len) < 0.2
    assert abs(meas_h["width_mm"] - true_wid) < 0.2

    # 2. Rotated 45-degree grain in physical space
    # Physical ellipse: semi-major a=3.0, semi-minor b=1.0 rotated by 45 degrees
    theta = np.linspace(0, 2 * np.pi, 200)
    pts_phys = np.stack([(true_len / 2.0) * np.cos(theta), (true_wid / 2.0) * np.sin(theta)], axis=1)
    rot_45 = np.array([[math.cos(math.pi / 4), -math.sin(math.pi / 4)],
                       [math.sin(math.pi / 4), math.cos(math.pi / 4)]])
    pts_rot_phys = pts_phys @ rot_45.T
    # Map physical mm to pixel coords
    pts_px = np.stack([
        pts_rot_phys[:, 0] / scale_x + 150,
        pts_rot_phys[:, 1] / scale_y + 150,
    ], axis=1).astype(np.int32)

    img_rot = np.zeros((300, 300, 3), dtype=np.uint8)
    cv2.fillPoly(img_rot, [pts_px], (220, 220, 220))
    c_r, _ = cv2.findContours(cv2.cvtColor(img_rot, cv2.COLOR_BGR2GRAY), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    meas_r, _ = _measure_grain_details(c_r[0], img_rot, aniso_scales)

    assert abs(meas_r["length_mm"] - true_len) < 0.2
    assert abs(meas_r["width_mm"] - true_wid) < 0.2
