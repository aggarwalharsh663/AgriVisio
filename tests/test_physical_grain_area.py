from __future__ import annotations

import cv2
import numpy as np
import pytest

from backend.grain_analysis import (
    DEFAULT_FALLBACK_SCALE,
    MIN_GRAIN_AREA,
    MIN_GRAIN_AREA_MM2,
    calculate_min_pixel_area,
    measure_grain,
    segment_grains,
)


def test_calculate_min_pixel_area_uniform_scales():
    """Verify uniform scale calculation matches formula round(MIN_GRAIN_AREA_MM2 / (scale ** 2))."""
    # 0.10 mm/pixel -> 1.0 / 0.01 = 100 pixels
    pixels, is_calibrated = calculate_min_pixel_area(0.10, min_grain_area_mm2=1.0)
    assert is_calibrated is True
    assert pixels == 100

    # 0.12 mm/pixel (nominal baseline) -> 1.0 / 0.0144 = 69 pixels
    pixels, is_calibrated = calculate_min_pixel_area(0.12, min_grain_area_mm2=1.0)
    assert is_calibrated is True
    assert pixels == 69


def test_calculate_min_pixel_area_scanner_dpi():
    """Verify physical threshold calculation across standard flatbed scanner resolutions."""
    # 300 DPI = 25.4 / 300 = 0.0846667 mm/pixel
    dpi_300_scale = 25.4 / 300.0
    pixels_300, is_cal = calculate_min_pixel_area(dpi_300_scale, min_grain_area_mm2=1.0)
    assert is_cal is True
    expected_300 = round(1.0 / (dpi_300_scale ** 2))
    assert pixels_300 == expected_300  # ~139 pixels

    # 600 DPI = 25.4 / 600 = 0.0423333 mm/pixel
    dpi_600_scale = 25.4 / 600.0
    pixels_600, is_cal = calculate_min_pixel_area(dpi_600_scale, min_grain_area_mm2=1.0)
    assert is_cal is True
    expected_600 = round(1.0 / (dpi_600_scale ** 2))
    assert pixels_600 == expected_600  # ~558 pixels

    # 600 DPI pixel area should be approximately 4x that of 300 DPI
    assert pytest.approx(pixels_600 / pixels_300, rel=0.05) == 4.0


def test_calculate_min_pixel_area_anisotropic_scales():
    """Verify anisotropic scaling: round(MIN_GRAIN_AREA_MM2 / (scale_x * scale_y))."""
    scale_x = 0.08
    scale_y = 0.10
    pixels, is_cal = calculate_min_pixel_area((scale_x, scale_y), min_grain_area_mm2=1.0)
    assert is_cal is True
    expected = round(1.0 / (scale_x * scale_y))  # 1.0 / 0.008 = 125 pixels
    assert pixels == expected


def test_calculate_min_pixel_area_uncalibrated_fallback():
    """Verify uncalibrated fallback strategy uses documented baseline scale."""
    pixels, is_cal = calculate_min_pixel_area(None, min_grain_area_mm2=1.0)
    assert is_cal is False
    # Uses DEFAULT_FALLBACK_SCALE = 0.12
    expected = round(1.0 / (DEFAULT_FALLBACK_SCALE ** 2))
    assert pixels == expected


def test_synthetic_scene_segmentation_across_resolutions():
    """
    Test the same physical scene at 300 DPI and 600 DPI:
    - 1 whole grain (7.0mm x 2.0mm)
    - 1 broken grain fragment (2.0mm x 1.2mm, area ~1.88 mm²)
    - 1 tiny dust speck (0.4mm x 0.4mm, area ~0.13 mm²)
    - 1 fiber (1.5mm x 0.1mm, area ~0.12 mm²)

    Expected: Whole grain and broken fragment are retained; dust and fiber are discarded.
    """
    for dpi in [300, 600]:
        scale = 25.4 / float(dpi)
        # Create blank image representing 20mm x 20mm tray area
        img_w = int(20.0 / scale)
        img_h = int(20.0 / scale)
        image = np.full((img_h, img_w, 3), 20, dtype=np.uint8)

        # 1. Whole grain: 7.0mm x 2.0mm -> radii 3.5mm / scale, 1.0mm / scale
        wg_rx = int(3.5 / scale)
        wg_ry = int(1.0 / scale)
        cv2.ellipse(image, (int(img_w * 0.25), int(img_h * 0.5)), (wg_rx, wg_ry), 10, 0, 360, (230, 230, 230), -1)

        # 2. Broken grain fragment: 2.0mm x 1.2mm -> radii 1.0mm / scale, 0.6mm / scale
        bg_rx = int(1.0 / scale)
        bg_ry = int(0.6 / scale)
        cv2.ellipse(image, (int(img_w * 0.55), int(img_h * 0.5)), (bg_rx, bg_ry), 30, 0, 360, (230, 230, 230), -1)

        # 3. Dust speck: 0.4mm x 0.4mm -> radius 0.2mm / scale
        dust_r = max(1, int(0.2 / scale))
        cv2.circle(image, (int(img_w * 0.75), int(img_h * 0.3)), dust_r, (230, 230, 230), -1)

        # 4. Fiber: 1.5mm x 0.1mm -> line / thin rect
        fiber_rx = max(1, int(0.75 / scale))
        fiber_ry = max(1, int(0.05 / scale))
        cv2.ellipse(image, (int(img_w * 0.85), int(img_h * 0.7)), (fiber_rx, fiber_ry), 45, 0, 360, (230, 230, 230), -1)

        # Segment with physical threshold = 1.0 mm²
        contours, diag = segment_grains(
            image,
            mm_per_pixel=scale,
            min_grain_area_mm2=1.0,
            ignore_calibration_card=False,
            split_touching=False,
            return_diagnostics=True,
        )

        assert diag["is_calibrated"] is True
        assert diag["contours_retained"] == 2, f"At {dpi} DPI, expected 2 grains retained (whole + broken), got {len(contours)}"
        # Match retained contour centers to expected whole and broken grain positions
        centers = []
        for c in contours:
            M = cv2.moments(c)
            if M["m00"] > 0:
                centers.append((float(M["m10"] / M["m00"]), float(M["m01"] / M["m00"])))

        expected_wg = (img_w * 0.25, img_h * 0.5)
        expected_bg = (img_w * 0.55, img_h * 0.5)

        has_wg = any(np.hypot(cx - expected_wg[0], cy - expected_wg[1]) < 15 for cx, cy in centers)
        has_bg = any(np.hypot(cx - expected_bg[0], cy - expected_bg[1]) < 15 for cx, cy in centers)
        assert has_wg, f"At {dpi} DPI, retained contours did not match whole grain position"
        assert has_bg, f"At {dpi} DPI, retained contours did not match broken grain position"


def test_segmentation_diagnostics_structure():
    """Verify return_diagnostics=True provides complete diagnostic contract."""
    img = np.full((100, 100, 3), 20, dtype=np.uint8)
    cv2.circle(img, (50, 50), 15, (230, 230, 230), -1)

    contours, diag = segment_grains(
        img,
        mm_per_pixel=0.08467,
        min_grain_area_mm2=1.0,
        return_diagnostics=True,
    )
    assert isinstance(contours, list)
    assert len(contours) == 1
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
    assert diag["min_grain_area_mm2"] == 1.0
    assert diag["is_calibrated"] is True
    assert diag["contours_retained"] == 1


def test_broken_grain_fragment_preservation_at_camera_resolution():
    """
    Verify that in low resolution smartphone photos (0.18 mm/pixel),
    small broken grain fragments are NOT discarded by an arbitrary fixed pixel threshold.
    """
    scale = 0.18  # mm/pixel
    # Broken fragment of 1.5mm x 1.1mm (area ~1.3 mm²)
    # In pixels at 0.18 mm/pixel:
    # 1.3 mm² / (0.18^2) = 1.3 / 0.0324 = ~40 pixels.
    # Note: With the old fixed threshold of 80 pixels, this 40-pixel fragment was WRONGLY DISCARDED!
    # With physical threshold (1.0 mm²), effective_min_pixels = round(1.0 / 0.0324) = 31 pixels.
    # The 40-pixel fragment is properly retained!

    img = np.full((120, 120, 3), 20, dtype=np.uint8)
    rx = int(1.5 / 2.0 / scale)  # ~4 px
    ry = int(1.1 / 2.0 / scale)  # ~3 px
    cv2.ellipse(img, (60, 60), (rx, ry), 0, 0, 360, (230, 230, 230), -1)

    contours = segment_grains(
        img,
        mm_per_pixel=scale,
        min_grain_area_mm2=1.0,
        ignore_calibration_card=False,
    )
    assert len(contours) == 1, "Small broken grain fragment was improperly discarded at camera resolution"
