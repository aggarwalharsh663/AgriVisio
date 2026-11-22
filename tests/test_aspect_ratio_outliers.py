from __future__ import annotations

import cv2
import numpy as np
import pytest

from backend.fingerprint import compute_batch_fingerprint
from backend.grain_analysis import (
    HIGH_ASPECT_RATIO_FLAG_THRESHOLD,
    MAX_PLAUSIBLE_LB_RATIO,
    MIN_CONTOUR_SOLIDITY,
    MIN_GRAIN_AREA_MM2,
    MIN_GRAIN_WIDTH_MM,
    compute_batch_summary,
    measure_grain,
    segment_grains,
    validate_grain_contour,
)


def _make_ellipse_contour(cx: int, cy: int, rx: int, ry: int, angle: int = 0) -> np.ndarray:
    """Helper to generate a clean synthetic ellipse contour."""
    canvas = np.zeros((max(200, cy + ry + 30), max(200, cx + rx + 30)), dtype=np.uint8)
    cv2.ellipse(canvas, (cx, cy), (rx, ry), angle, 0, 360, 255, -1)
    contours, _ = cv2.findContours(canvas, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return contours[0]


# --- Step 5 Tests: 1 through 10 ---

def test_1_normal_rice_grain_contour():
    """1. Normal rice grain contour (e.g., Medium/Long Indica, ~6.4mm x ~2.1mm, L/B ~3.05)."""
    scale = 0.12  # mm/pixel
    # 6.4mm length -> rx = 27 px, 2.1mm width -> ry = 9 px
    contour = _make_ellipse_contour(100, 100, 27, 9, angle=25)
    is_valid, reason, metrics = validate_grain_contour(contour, mm_per_pixel=scale)

    assert is_valid is True
    assert reason == ""
    assert 2.5 <= metrics["lb_ratio"] <= 3.5
    assert metrics["width_mm"] >= MIN_GRAIN_WIDTH_MM
    assert metrics["area_mm2"] >= MIN_GRAIN_AREA_MM2
    assert metrics["solidity"] >= MIN_CONTOUR_SOLIDITY


def test_2_long_grain_basmati_contour():
    """2. Long grain (e.g., Extra-long slender Basmati, ~8.2mm x ~1.7mm, L/B ~4.82)."""
    scale = 0.12
    # 8.2mm -> rx = 34 px, 1.7mm -> ry = 7 px
    contour = _make_ellipse_contour(100, 100, 34, 7, angle=10)
    is_valid, reason, metrics = validate_grain_contour(contour, mm_per_pixel=scale)

    assert is_valid is True
    assert reason == ""
    assert 4.0 <= metrics["lb_ratio"] <= 6.0
    assert metrics["width_mm"] >= MIN_GRAIN_WIDTH_MM
    assert metrics["lb_ratio"] <= MAX_PLAUSIBLE_LB_RATIO
    assert metrics["area_mm2"] >= MIN_GRAIN_AREA_MM2


def test_3_short_grain_japonica_contour():
    """3. Short grain (e.g., Round Japonica, ~4.8mm x ~2.7mm, L/B ~1.78)."""
    scale = 0.12
    # 4.8mm -> rx = 20 px, 2.7mm -> ry = 11 px
    contour = _make_ellipse_contour(100, 100, 20, 11, angle=0)
    is_valid, reason, metrics = validate_grain_contour(contour, mm_per_pixel=scale)

    assert is_valid is True
    assert reason == ""
    assert 1.4 <= metrics["lb_ratio"] <= 2.2
    assert metrics["width_mm"] >= MIN_GRAIN_WIDTH_MM
    assert metrics["area_mm2"] >= MIN_GRAIN_AREA_MM2


def test_4_broken_grain_contour():
    """4. Broken grain fragment (~2.2mm x ~1.5mm, L/B ~1.47, area ~2.5 mm²)."""
    scale = 0.12
    # 2.2mm -> rx = 9 px, 1.5mm -> ry = 6 px
    contour = _make_ellipse_contour(100, 100, 9, 6, angle=45)
    is_valid, reason, metrics = validate_grain_contour(contour, mm_per_pixel=scale)

    assert is_valid is True
    assert reason == ""
    assert metrics["area_mm2"] >= MIN_GRAIN_AREA_MM2
    assert metrics["width_mm"] >= MIN_GRAIN_WIDTH_MM
    assert metrics["lb_ratio"] <= MAX_PLAUSIBLE_LB_RATIO


def test_5_very_thin_scratch_rejected():
    """5. Very thin scratch on scanner glass (length 10mm, width 0.15mm, L/B ~66)."""
    scale = 0.12
    # Length 83 px (10mm), width 1 px (0.12mm)
    canvas = np.zeros((150, 150), dtype=np.uint8)
    cv2.line(canvas, (20, 20), (100, 20), 255, thickness=1)
    contours, _ = cv2.findContours(canvas, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    scratch_contour = contours[0]

    is_valid, reason, metrics = validate_grain_contour(scratch_contour, mm_per_pixel=scale)

    assert is_valid is False
    assert reason in {"too_thin", "near_zero_width", "extreme_aspect_ratio", "degenerate_contour"}
    assert metrics["width_mm"] < MIN_GRAIN_WIDTH_MM or metrics["lb_ratio"] > MAX_PLAUSIBLE_LB_RATIO


def test_6_hair_like_contour_rejected():
    """6. Curved / serpentine hair-like contour (length ~14mm, width ~0.15mm, low solidity)."""
    scale = 0.12
    # Curved polyline simulating a hair
    canvas = np.zeros((200, 200), dtype=np.uint8)
    curve_pts = np.array(
        [[20, 30], [50, 80], [30, 130], [80, 160], [120, 180]], dtype=np.int32
    )
    cv2.polylines(canvas, [curve_pts], isClosed=False, color=255, thickness=1)
    contours, _ = cv2.findContours(canvas, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    hair_contour = contours[0]

    is_valid, reason, metrics = validate_grain_contour(hair_contour, mm_per_pixel=scale)

    assert is_valid is False
    assert reason in {"low_solidity", "too_thin", "near_zero_width", "extreme_aspect_ratio", "degenerate_contour"}


def test_7_dust_and_background_artifact_rejected():
    """7. Dust speck (0.4mm x 0.4mm, area ~0.13 mm² < 1.0 mm²)."""
    scale = 0.12
    # Radius 2 px (~0.24mm) -> area ~12 px * 0.0144 = 0.17 mm²
    contour = _make_ellipse_contour(50, 50, 2, 2)
    is_valid, reason, metrics = validate_grain_contour(contour, mm_per_pixel=scale)

    assert is_valid is False
    assert reason in {"below_min_area", "too_thin"}
    assert metrics["area_mm2"] < MIN_GRAIN_AREA_MM2


def test_8_zero_or_near_zero_width_rejected():
    """8. Zero or near-zero width contour (degenerate / 1D collinear line)."""
    scale = 0.12
    collinear_contour = np.array([[[10, 20]], [[10, 40]], [[10, 60]]], dtype=np.int32)
    is_valid, reason, metrics = validate_grain_contour(collinear_contour, mm_per_pixel=scale)

    assert is_valid is False
    assert reason in {"near_zero_width", "degenerate_contour", "too_thin"}
    assert metrics.get("width_mm", 0.0) < 0.1 or metrics.get("width_px", 0.0) < 1.0


def test_9_empty_batch_summary():
    """9. Empty batch does not cause ZeroDivisionError and produces valid 0.0 statistics."""
    summary = compute_batch_summary([])

    assert summary["total_grains"] == 0
    assert summary["mean_lb_ratio"] == 0.0
    assert summary["mean_length_mm"] == 0.0
    assert summary["mean_width_mm"] == 0.0
    assert summary["std_length_mm"] == 0.0
    assert summary["mean_confidence"] == 0.0
    assert summary["low_confidence_count"] == 0
    assert set(summary) == {
        "total_grains",
        "counts",
        "percent",
        "mean_length_mm",
        "std_length_mm",
        "mean_width_mm",
        "mean_lb_ratio",
        "mean_confidence",
        "low_confidence_count",
    }


def test_10_batch_containing_valid_grains_and_rejected_artifacts():
    """10. Batch containing both valid grains and extreme non-grain artifacts:
    Verify the batch mean is NOT distorted by extreme aspect-ratio contours."""
    valid_grains = [
        {
            "id": i,
            "length_mm": 6.3,
            "width_mm": 2.1,
            "lb_ratio": 3.0,
            "confidence": 0.95,
            "low_confidence": False,
            "label": "good",
        }
        for i in range(1, 11)
    ]
    # Extreme artifacts that might have bypassed upstream or come from external input
    artifacts = [
        {
            "id": 11,
            "length_mm": 12.0,
            "width_mm": 0.15,  # Too thin (scratch)
            "lb_ratio": 80.0,
            "confidence": 0.40,
            "low_confidence": True,
            "label": "good",
        },
        {
            "id": 12,
            "length_mm": 18.0,
            "width_mm": 0.10,  # Hair
            "lb_ratio": 180.0,
            "confidence": 0.35,
            "low_confidence": True,
            "label": "good",
        },
    ]

    all_grains = valid_grains + artifacts
    summary = compute_batch_summary(all_grains)

    # If unmanaged, (10 * 3.0 + 80.0 + 180.0) / 12 = 24.17 (severe distortion!)
    # With robust protection, mean_lb_ratio MUST be exactly 3.00
    assert summary["mean_lb_ratio"] == 3.00
    assert summary["mean_length_mm"] == 6.30
    assert summary["mean_width_mm"] == 2.10
    assert summary["total_grains"] == 12


# --- Additional Metrological & Contract Validation Tests ---

def test_measure_grain_does_not_silently_clamp_valid_slender_grain():
    """Ensure valid measurements are not artificially clamped or fabricated."""
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    # Genuinely slender grain (radius 30, radius 6 -> L ~ 6.0mm, W ~ 1.2mm, L/B ~ 5.0)
    cv2.ellipse(image, (50, 50), (30, 6), 0, 0, 360, (230, 220, 210), -1)
    contours, _ = cv2.findContours(image[:, :, 0], cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    measured = measure_grain(contours[0], image, mm_per_pixel=0.10)

    assert set(measured) == {
        "length_mm",
        "width_mm",
        "lb_ratio",
        "bbox",
        "center",
        "mean_rgb",
        "chalk_fraction",
    }
    # Value must reflect geometric ratio (~5.0), not silently modified to 3.0 or 10.0
    assert 4.5 <= measured["lb_ratio"] <= 5.5
    assert measured["length_mm"] == pytest.approx(6.0, abs=0.3)
    assert measured["width_mm"] == pytest.approx(1.2, abs=0.2)


def test_segment_grains_diagnostics_reports_breakdown():
    """Verify segment_grains returns detailed rejection_breakdown when return_diagnostics=True."""
    # Create image with:
    # - 1 normal grain (ellipse rx=25, ry=8)
    # - 1 scratch (line length=80, width=3 so it survives morphological opening)
    # - 1 dust speck (circle radius=3, area ~28 px < 69 px)
    image = np.full((200, 300, 3), 20, dtype=np.uint8)
    cv2.ellipse(image, (80, 100), (25, 8), 0, 0, 360, (230, 225, 210), -1)
    cv2.line(image, (160, 40), (240, 40), (240, 240, 240), thickness=3)
    cv2.circle(image, (180, 140), 3, (240, 240, 240), -1)

    contours, diag = segment_grains(
        image,
        mm_per_pixel=0.12,
        ignore_calibration_card=False,
        return_diagnostics=True,
    )

    # Normal grain is retained; scratch and dust are rejected
    assert len(contours) == 1
    # Verify the retained contour is the normal grain centered near (80, 100)
    m = cv2.moments(contours[0])
    assert m["m00"] > 0
    cx = m["m10"] / m["m00"]
    cy = m["m01"] / m["m00"]
    assert abs(cx - 80) < 10 and abs(cy - 100) < 10

    assert diag["contours_retained"] == 1
    assert diag["contours_rejected"] >= 2
    assert set(diag.keys()) == {
        "min_grain_area_mm2",
        "effective_min_pixel_area",
        "mm_per_pixel",
        "is_calibrated",
        "contours_rejected",
        "contours_retained",
        "rejection_breakdown",
    }
    assert isinstance(diag["rejection_breakdown"], dict)
    assert sum(diag["rejection_breakdown"].values()) >= 1


def test_fingerprint_generation_protected_against_aspect_ratio_outliers():
    """Verify cryptographic batch fingerprint matches expected vector and hash under outlier protection."""
    valid_grains = [
        {"id": 1, "length_mm": 6.5, "width_mm": 2.1, "lb_ratio": 3.1, "label": "good", "confidence": 0.95},
        {"id": 2, "length_mm": 6.4, "width_mm": 2.0, "lb_ratio": 3.2, "label": "good", "confidence": 0.94},
    ]
    summary_clean = compute_batch_summary(valid_grains)

    # Batch with scratch artifact injected
    corrupted_grains = valid_grains + [
        {"id": 3, "length_mm": 15.0, "width_mm": 0.12, "lb_ratio": 125.0, "label": "good", "confidence": 0.40}
    ]
    summary_protected = compute_batch_summary(corrupted_grains)

    # Morphometric means must remain identical because the scratch is excluded from morphometrics
    assert summary_protected["mean_lb_ratio"] == summary_clean["mean_lb_ratio"]
    assert summary_protected["mean_length_mm"] == summary_clean["mean_length_mm"]
    assert summary_protected["mean_width_mm"] == summary_clean["mean_width_mm"]

    labels = ["good", "broken", "chalky", "discolored"]
    counts_clean = {l: sum(g["label"] == l for g in valid_grains) for l in labels}
    counts_corrupted = {l: sum(g["label"] == l for g in corrupted_grains) for l in labels}
    calib = {"calibration_status": "CALIBRATED", "mm_per_pixel": 0.12}

    fp_clean = compute_batch_fingerprint(counts=counts_clean, summary=summary_clean, calibration=calib, labels=labels)
    fp_corrupted = compute_batch_fingerprint(counts=counts_corrupted, summary=summary_protected, calibration=calib, labels=labels)

    assert isinstance(fp_corrupted["hash"], str) and len(fp_corrupted["hash"]) == 64
    assert isinstance(fp_clean["hash"], str) and len(fp_clean["hash"]) == 64
    assert fp_clean["hash"] == "16d02ca045f7a3093912316b7fedb25d83039b9ef53115539624dbe7d008ca8c"
    assert fp_corrupted["hash"] == "5fcfab7ed20c8db2bb3dfb290dddc08b30610d094d0418e2ec1e3455f01f409d"
    assert fp_corrupted["vector"] == [1.0, 0.0, 0.0, 0.0]
    assert fp_clean["vector"] == [1.0, 0.0, 0.0, 0.0]
    # Clean and contaminated fixtures have different grain counts so their hashes differ
    assert fp_clean["hash"] != fp_corrupted["hash"]
