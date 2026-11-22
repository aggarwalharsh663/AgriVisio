"""Basic grain segmentation and measurements for simulated rice images."""

import hashlib
import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import joblib
import numpy as np
import scipy.ndimage

LOGGER = logging.getLogger("agrivisio.grain_analysis")

MODEL_PATH = Path(__file__).with_name("model.joblib")
MANIFEST_PATH = Path(__file__).with_name("model_manifest.json")


class ModelIntegrityError(RuntimeError):
    """Raised when the classifier model artifact fails integrity or provenance verification."""
    pass


def verify_model_integrity(model_path: Path = MODEL_PATH, manifest_path: Path = MANIFEST_PATH) -> dict:
    """Verify SHA-256 integrity and provenance of the model artifact before loading."""
    if not model_path.is_file():
        raise ModelIntegrityError(f"Classifier model file not found at {model_path}.")
    if not manifest_path.is_file():
        raise ModelIntegrityError(f"Model manifest not found at {manifest_path}.")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ModelIntegrityError(f"Could not parse model manifest: {exc}")

    expected_sha256 = manifest.get("sha256")
    if not expected_sha256:
        raise ModelIntegrityError("Model manifest does not specify expected 'sha256' checksum.")

    hasher = hashlib.sha256()
    with open(model_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    actual_sha256 = hasher.hexdigest().lower()

    if actual_sha256 != expected_sha256.lower():
        raise ModelIntegrityError(
            f"Security Error: Classifier model integrity verification failed! "
            f"Expected SHA-256: {expected_sha256}, Actual: {actual_sha256}. "
            "Artifact may be modified, substituted, or corrupted."
        )
    return manifest


CARD_IGNORE_X = 970
BLUR_KERNEL_SIZE = (5, 5)
OPENING_KERNEL_SIZE = (3, 3)

# Physical minimum grain area threshold in square millimeters (Step 2)
# 1.0 mm² corresponds to small broken rice fragments (~1.3mm x ~1.0mm)
# Dust specks (<0.3 mm²) and paper fibers (<0.2 mm²) are safely rejected.
MIN_GRAIN_AREA_MM2: float = 1.0
DEFAULT_FALLBACK_SCALE: float = 0.12
MIN_GRAIN_AREA: int = 80  # Backward-compatible nominal threshold (at 0.12 mm/pixel, ~1.15 mm²)


def calculate_min_pixel_area(
    mm_per_pixel: float | tuple[float, float] | None,
    min_grain_area_mm2: float = MIN_GRAIN_AREA_MM2,
    image_shape: tuple[int, int] | None = None,
) -> tuple[int, bool]:
    """
    Calculate minimum contour area in pixels from physical mm² threshold.

    Returns:
        (min_area_pixels, is_calibrated):
        - min_area_pixels: Minimum contour area in pixels (clamped to >= 4)
        - is_calibrated: True if physical calibration was provided and valid
    """
    if min_grain_area_mm2 <= 0 or not np.isfinite(min_grain_area_mm2):
        min_grain_area_mm2 = MIN_GRAIN_AREA_MM2

    # Case 1: Separate horizontal and vertical scales (anisotropic)
    if isinstance(mm_per_pixel, (tuple, list)) and len(mm_per_pixel) == 2:
        try:
            scale_x, scale_y = float(mm_per_pixel[0]), float(mm_per_pixel[1])
            if scale_x > 0 and scale_y > 0 and np.isfinite(scale_x) and np.isfinite(scale_y):
                pixel_area_mm2 = scale_x * scale_y
                if pixel_area_mm2 >= 1e-12 and np.isfinite(pixel_area_mm2):
                    min_pixels = round(min_grain_area_mm2 / pixel_area_mm2)
                    if np.isfinite(min_pixels) and min_pixels >= 0:
                        return max(4, int(min_pixels)), True
        except (ValueError, TypeError, OverflowError, ZeroDivisionError):
            pass

    # Case 2: Uniform physical scale
    if isinstance(mm_per_pixel, (int, float)):
        try:
            scale = float(mm_per_pixel)
            if scale > 0 and np.isfinite(scale):
                pixel_area_mm2 = scale * scale
                if pixel_area_mm2 >= 1e-12 and np.isfinite(pixel_area_mm2):
                    min_pixels = round(min_grain_area_mm2 / pixel_area_mm2)
                    if np.isfinite(min_pixels) and min_pixels >= 0:
                        return max(4, int(min_pixels)), True
        except (ValueError, TypeError, OverflowError, ZeroDivisionError):
            pass

    # Case 3: Missing / uncalibrated fallback
    # When uncalibrated, use the documented fallback strategy based on DEFAULT_FALLBACK_SCALE (0.12 mm/pixel).
    # This maintains physical plausibility without claiming verified accuracy, and avoids
    # arbitrary pixel distortion from image cropping or non-standard aspect ratios.
    min_pixels = round(min_grain_area_mm2 / (DEFAULT_FALLBACK_SCALE ** 2))
    return max(4, min_pixels), False


# Morphological & aspect-ratio validation thresholds for rice kernels (Prompt 9)
# In rice agronomic classification (IRRI / USDA / ISO 7301 standards):
# - Round/Short grains: L/B ~ 1.4 - 2.0
# - Medium grains: L/B ~ 2.1 - 3.0
# - Long grains: L/B ~ 3.0 - 3.5
# - Extra-long slender grains (e.g. Basmati, Pusa 1121): L/B ~ 3.5 - 5.5 (max ~6.0)
# - Broken grains/chips: L/B ~ 1.0 - 2.0
# Rice grains cannot physically have width < 0.50 mm (even broken pinpoints are >= 0.8 mm).
# No legitimate rice cultivar exceeds L/B = 6.0; L/B = 8.0 provides a generous safety margin
# ensuring 100% retention of extra-long varieties while rejecting scratches and fibers.
MIN_GRAIN_WIDTH_MM: float = 0.50
MAX_PLAUSIBLE_LB_RATIO: float = 8.0
MIN_CONTOUR_SOLIDITY: float = 0.55
HIGH_ASPECT_RATIO_FLAG_THRESHOLD: float = 5.5


def validate_grain_contour(
    contour: np.ndarray,
    mm_per_pixel: float | tuple[float, float] | None = None,
    min_grain_area_mm2: float = MIN_GRAIN_AREA_MM2,
    min_grain_width_mm: float = MIN_GRAIN_WIDTH_MM,
    max_lb_ratio: float = MAX_PLAUSIBLE_LB_RATIO,
    min_solidity: float = MIN_CONTOUR_SOLIDITY,
) -> tuple[bool, str, dict[str, Any]]:
    """
    Validate whether a contour exhibits physical dimensions and shape plausible for a rice grain.

    Rejects:
    - Degenerate contours (< 4 pixels or collinear 1D lines)
    - Dust / noise artifacts (area < min_grain_area_mm2)
    - Extremely thin scratches / hair / fibers (width < min_grain_width_mm)
    - Extreme aspect-ratio outliers (lb_ratio > max_lb_ratio)
    - Highly concave / serpentine artifacts (solidity < min_solidity)

    Returns:
        (is_valid, rejection_reason, metrics_dict)
    """
    if min_solidity is None or min_solidity < 0 or not np.isfinite(min_solidity):
        min_solidity = MIN_CONTOUR_SOLIDITY

    if contour is None or len(contour) < 3:
        length_mm = 0.0
        if contour is not None and len(contour) == 2:
            p1, p2 = contour[0][0], contour[1][0]
            dist_px = float(np.hypot(p1[0] - p2[0], p1[1] - p2[1]))
            length_mm = dist_px * (float(mm_per_pixel) if isinstance(mm_per_pixel, (int, float)) and mm_per_pixel > 0 else DEFAULT_FALLBACK_SCALE)
        return False, "degenerate_contour", {
            "reason": "Contour has fewer than 3 vertices (collinear segment/scratch)",
            "width_mm": 0.0,
            "length_mm": round(length_mm, 3),
            "lb_ratio": 999.0,
            "width_px": 0.0,
            "area_px": 0.0,
            "area_mm2": 0.0,
            "solidity": 0.0,
        }

    area_px = float(cv2.contourArea(contour))

    # Resolve scale (calibrated vs fallback)
    if isinstance(mm_per_pixel, (tuple, list)) and len(mm_per_pixel) == 2:
        try:
            scale_x, scale_y = float(mm_per_pixel[0]), float(mm_per_pixel[1])
            if scale_x <= 0 or scale_y <= 0 or not (np.isfinite(scale_x) and np.isfinite(scale_y)):
                scale_x, scale_y = DEFAULT_FALLBACK_SCALE, DEFAULT_FALLBACK_SCALE
        except (ValueError, TypeError):
            scale_x, scale_y = DEFAULT_FALLBACK_SCALE, DEFAULT_FALLBACK_SCALE
    elif isinstance(mm_per_pixel, (int, float)):
        try:
            s = float(mm_per_pixel)
            scale_x = scale_y = s if s > 0 and np.isfinite(s) else DEFAULT_FALLBACK_SCALE
        except (ValueError, TypeError):
            scale_x, scale_y = DEFAULT_FALLBACK_SCALE, DEFAULT_FALLBACK_SCALE
    else:
        scale_x = scale_y = DEFAULT_FALLBACK_SCALE

    # Compute rotated bounding box
    if abs(scale_x - scale_y) < 1e-7:
        rect = cv2.minAreaRect(contour)
        side_a, side_b = rect[1]
        length_mm = max(side_a, side_b) * scale_x
        width_mm = min(side_a, side_b) * scale_x
        pitch_in_width_dir = scale_x
        width_px = min(side_a, side_b)
    else:
        contour_mm = contour.astype(np.float32) * np.array([scale_x, scale_y], dtype=np.float32)
        rect = cv2.minAreaRect(contour_mm)
        box = cv2.boxPoints(rect)
        edge1 = box[1] - box[0]
        edge2 = box[2] - box[1]
        len1 = float(np.hypot(edge1[0], edge1[1]))
        len2 = float(np.hypot(edge2[0], edge2[1]))
        if len1 >= len2:
            length_mm = len1
            width_mm = len2
            short_axis = edge2
        else:
            length_mm = len2
            width_mm = len1
            short_axis = edge1

        if width_mm > 1e-7:
            ux = float(short_axis[0] / width_mm)
            uy = float(short_axis[1] / width_mm)
            pitch_in_width_dir = float(abs(ux) * scale_x + abs(uy) * scale_y)
        else:
            pitch_in_width_dir = min(scale_x, scale_y)
        width_px = width_mm / pitch_in_width_dir if pitch_in_width_dir > 0 else 0.0

    area_mm2 = area_px * (scale_x * scale_y)
    safe_width = max(width_mm, 1e-4)
    lb_ratio = float(length_mm / safe_width)

    hull = cv2.convexHull(contour)
    hull_area = float(cv2.contourArea(hull))
    solidity = float(area_px / hull_area) if hull_area > 0 else 0.0

    # Pick's theorem boundary compensation: cv2.contourArea connects pixel centers,
    # underestimating discrete raster pixel area by ~ perimeter/2 + 1.
    perimeter_px = float(cv2.arcLength(contour, True))
    effective_pixel_area = max(area_px, area_px + 0.5 * perimeter_px + 1.0)
    effective_area_mm2 = effective_pixel_area * (scale_x * scale_y)

    # In minAreaRect, distance between pixel centers is ~1 pixel narrower than discrete raster footprint
    # along the short axis direction of the metric bounding box
    effective_width_mm = width_mm + pitch_in_width_dir
    effective_width_px = effective_width_mm / pitch_in_width_dir if pitch_in_width_dir > 0 else 0.0

    metrics: dict[str, Any] = {
        "area_px": round(area_px, 1),
        "area_mm2": round(area_mm2, 3),
        "effective_area_mm2": round(effective_area_mm2, 3),
        "effective_pixel_area": round(effective_pixel_area, 1),
        "length_mm": round(length_mm, 3),
        "width_mm": round(width_mm, 3),
        "effective_width_mm": round(effective_width_mm, 3),
        "lb_ratio": round(lb_ratio, 3),
        "solidity": round(solidity, 3),
        "width_px": round(width_px, 1),
    }

    # 1. Zero or near-zero width (e.g. 1-pixel line / collinear points / scratches)
    if width_mm < 0.1 or width_px < 1.0:
        return False, "near_zero_width", {**metrics, "reason": f"Near-zero width ({width_mm:.3f} mm, {width_px:.1f} px)"}

    # 2. Minimum width check (scratch / hair / fiber)
    # Compensates for pixel-center boundary shrink so valid small broken fragments >= min_grain_width_mm are retained
    if effective_width_mm < min_grain_width_mm:
        return False, "too_thin", {**metrics, "reason": f"Width {width_mm:.2f} mm (effective {effective_width_mm:.2f} mm) < minimum {min_grain_width_mm:.2f} mm (scratch/hair)"}

    # 3. Maximum plausible aspect ratio (scratch / hair / artifact)
    if lb_ratio > max_lb_ratio:
        return False, "extreme_aspect_ratio", {**metrics, "reason": f"Aspect ratio {lb_ratio:.2f} > maximum {max_lb_ratio:.1f}"}

    # 4. Solidity check (irregular / serpentine shape / hair)
    if solidity < min_solidity:
        return False, "low_solidity", {**metrics, "reason": f"Solidity {solidity:.2f} < minimum {min_solidity:.2f} (irregular shape)"}

    # 5. Minimum area check (dust / sensor noise)
    # Uses Pick's theorem boundary compensation to align with calculate_min_pixel_area and pre-filters
    if effective_area_mm2 < min_grain_area_mm2:
        return False, "below_min_area", {**metrics, "reason": f"Area {area_mm2:.2f} mm² (effective {effective_area_mm2:.2f} mm²) < minimum {min_grain_area_mm2:.2f} mm²"}

    if effective_pixel_area < 4.0:
        return False, "degenerate_contour", {"reason": f"Contour area {area_px:.1f} px < 4 px", "area_px": area_px}

    return True, "", metrics
MIN_THRESHOLD_BRIGHTNESS = 50
BRIGHT_VALUE_THRESHOLD = 238
LOW_SATURATION_THRESHOLD = 12
LOW_CONFIDENCE_THRESHOLD = 0.6
FEATURE_NAMES = (
    "length_mm",
    "width_mm",
    "lb_ratio",
    "chalk_fraction",
    "mean_red",
    "mean_green",
    "mean_blue",
    "saturation",
)
FEATURE_LABELS = {
    "length_mm": "grain length",
    "width_mm": "grain width",
    "lb_ratio": "length-to-width ratio",
    "chalk_fraction": "chalk fraction",
    "mean_red": "mean red colour",
    "mean_green": "mean green colour",
    "mean_blue": "mean blue colour",
    "saturation": "colour saturation",
}
LABEL_COLORS_BGR = {
    "good": (0, 200, 0),
    "broken": (0, 0, 255),
    "chalky": (0, 165, 255),
    "discolored": (180, 0, 180),
}

def _measure_grain_details(
    contour: np.ndarray,
    image: np.ndarray,
    mm_per_pixel: float | tuple[float, float],
    is_blue_tray: bool = False,
) -> tuple[dict, float]:
    """Measure one grain and return physical dimensions and mean HSV saturation for classification."""
    if isinstance(mm_per_pixel, (tuple, list)):
        if len(mm_per_pixel) != 2:
            raise ValueError("mm_per_pixel tuple must have exactly 2 elements (scale_x, scale_y)")
        scale_x = float(mm_per_pixel[0])
        scale_y = float(mm_per_pixel[1])
    else:
        scale_x = float(mm_per_pixel)
        scale_y = float(mm_per_pixel)

    if scale_x <= 0 or scale_y <= 0 or not np.isfinite(scale_x) or not np.isfinite(scale_y):
        raise ValueError("mm_per_pixel must be greater than zero and finite")
    if image is None or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("image must be a three-channel BGR image")

    if abs(scale_x - scale_y) < 1e-7:
        (center_x, center_y), (side_a, side_b), _ = cv2.minAreaRect(contour)
        length_mm = max(side_a, side_b) * scale_x
        width_mm = min(side_a, side_b) * scale_x
    else:
        # Anisotropic scaling: convert pixel contour vertices into metric coordinates in mm
        # to guarantee accurate rotation-invariant minimum bounding box without aspect ratio distortion
        contour_mm = contour.astype(np.float32) * np.array([scale_x, scale_y], dtype=np.float32)
        (center_x_mm, center_y_mm), (side_a_mm, side_b_mm), _ = cv2.minAreaRect(contour_mm)
        length_mm = max(side_a_mm, side_b_mm)
        width_mm = min(side_a_mm, side_b_mm)
        center_x = center_x_mm / scale_x
        center_y = center_y_mm / scale_y
    x, y, box_width, box_height = cv2.boundingRect(contour)

    crop_bgr = image[y : y + box_height, x : x + box_width]
    contour_mask = np.zeros((box_height, box_width), dtype=np.uint8)
    cv2.drawContours(
        contour_mask, [contour - [x, y]], -1, 255, thickness=cv2.FILLED
    )
    grain_pixels = contour_mask > 0
    grain_bgr = crop_bgr[grain_pixels]
    if grain_bgr.size == 0:
        raise ValueError("contour does not contain any image pixels")

    grain_hsv = cv2.cvtColor(
        grain_bgr.reshape(-1, 1, 3), cv2.COLOR_BGR2HSV
    ).reshape(-1, 3)
    grain_rgb = grain_bgr[:, ::-1]
    if is_blue_tray:
        # In flatbed scanner light (blue optical tray), chalky regions are opaque white,
        # reflecting light and blocking the blue background (higher red, low saturation).
        chalk_pixels = (
            (grain_rgb[:, 0] >= 150)
            & (grain_hsv[:, 1] <= 55)
            & (grain_hsv[:, 2] >= 155)
            & (grain_rgb[:, 0] > grain_rgb[:, 2] - 25)
        )
    else:
        # In optical chamber and standard photos, chalky endosperm is opaque neutral white:
        # high brightness, low color saturation, and minimal channel spread.
        rgb_spread = grain_rgb.max(axis=1) - grain_rgb.min(axis=1)
        chalk_pixels = (
            (grain_hsv[:, 2] >= BRIGHT_VALUE_THRESHOLD)
            & (grain_hsv[:, 1] <= LOW_SATURATION_THRESHOLD)
            & (rgb_spread <= 11)
        )
    mean_rgb = grain_rgb.mean(axis=0)
    safe_width = max(width_mm, 1e-4)
    measurement = {
        "length_mm": round(float(length_mm), 3),
        "width_mm": round(float(width_mm), 3),
        "lb_ratio": round(float(length_mm / safe_width), 3),
        "bbox": [int(x), int(y), int(box_width), int(box_height)],
        "center": [round(float(center_x), 2), round(float(center_y), 2)],
        "mean_rgb": [round(float(channel), 1) for channel in mean_rgb],
        "chalk_fraction": round(float(chalk_pixels.mean()), 4),
    }
    return measurement, float(grain_hsv[:, 1].mean())


def feature_vector(measurement: dict, saturation: float) -> list[float]:
    """Return the classifier features in the documented order."""
    red, green, blue = measurement["mean_rgb"]
    return [
        float(measurement["length_mm"]),
        float(measurement["width_mm"]),
        float(measurement["lb_ratio"]),
        float(measurement["chalk_fraction"]),
        float(red),
        float(green),
        float(blue),
        float(saturation),
    ]


def _split_touching_grains(
    image_bgr: np.ndarray,
    foreground: np.ndarray,
    seed_spacing: int | None = None,
    min_distance: float = 3.0,
    min_area: int | None = None,
    return_rejections: bool = False,
) -> list[np.ndarray] | tuple[list[np.ndarray], int]:
    """Split touching foreground grains using distance-transform watershed."""
    height, width = foreground.shape
    distance = cv2.distanceTransform(foreground, cv2.DIST_L2, 5)
    if seed_spacing is None:
        seed_spacing = min(15, max(9, round(min(height, width) * 0.018)))
    if min_area is None:
        min_area = MIN_GRAIN_AREA
    seed_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (seed_spacing, seed_spacing)
    )
    local_maxima = (
        (distance >= cv2.dilate(distance, seed_kernel) - 1e-4)
        & (distance >= min_distance)
        & (foreground > 0)
    ).astype(np.uint8)
    grouped_maxima = cv2.dilate(local_maxima, seed_kernel)
    group_count, group_labels = cv2.connectedComponents(grouped_maxima, 8)

    maxima_y, maxima_x = np.nonzero(local_maxima)
    if len(maxima_x) == 0:
        return ([], 0) if return_rejections else []

    best_peaks = {}
    for y, x in zip(maxima_y, maxima_x):
        gid = group_labels[y, x]
        if gid > 0:
            d = distance[y, x]
            if gid not in best_peaks or d > best_peaks[gid][0]:
                best_peaks[gid] = (d, y, x)

    markers = np.ones((height, width), dtype=np.int32)
    markers[foreground > 0] = 0
    seed_labels = []
    for gid in range(1, group_count):
        if gid in best_peaks:
            _, py, px = best_peaks[gid]
            seed_label = len(seed_labels) + 2
            seed_labels.append(seed_label)
            markers[py, px] = seed_label

    if not seed_labels:
        return ([], 0) if return_rejections else []

    watershed_labels = cv2.watershed(image_bgr.copy(), markers)
    filtered_labels = np.where(foreground > 0, watershed_labels, 0)
    slices = scipy.ndimage.find_objects(filtered_labels)

    contours = []
    dropped_for_area = 0
    for seed_label in seed_labels:
        idx = seed_label - 1
        if idx >= len(slices) or slices[idx] is None:
            continue
        ys, xs = slices[idx]
        y0 = max(0, ys.start - 1)
        y1 = min(height, ys.stop + 1)
        x0 = max(0, xs.start - 1)
        x1 = min(width, xs.stop + 1)
        sub_mask = (filtered_labels[y0:y1, x0:x1] == seed_label).astype(np.uint8) * 255
        if cv2.countNonZero(sub_mask) < min_area:
            dropped_for_area += 1
            continue
        found, _ = cv2.findContours(
            sub_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE, offset=(x0, y0)
        )
        if found:
            contours.append(max(found, key=cv2.contourArea))
    if return_rejections:
        return contours, dropped_for_area
    return contours


def segment_grains(
    image_bgr: np.ndarray,
    ignore_calibration_card: bool = True,
    split_touching: bool = False,
    *,
    mm_per_pixel: float | tuple[float, float] | None = None,
    min_grain_area_mm2: float | None = None,
    min_grain_width_mm: float | None = None,
    max_lb_ratio: float | None = None,
    min_solidity: float | None = None,
    return_diagnostics: bool = False,
) -> list[np.ndarray] | tuple[list[np.ndarray], dict]:
    """Find grain contours, ordered from left to right."""
    if image_bgr is None or image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
        raise ValueError("image_bgr must be a three-channel BGR image")

    height, width = image_bgr.shape[:2]
    card_start = min(CARD_IGNORE_X, width) if ignore_calibration_card else width
    opening_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, OPENING_KERNEL_SIZE
    )

    # Normalize thresholds at the boundary
    if min_grain_area_mm2 is not None and (min_grain_area_mm2 <= 0 or not np.isfinite(min_grain_area_mm2)):
        min_grain_area_mm2 = MIN_GRAIN_AREA_MM2
    effective_min_mm2 = min_grain_area_mm2 if min_grain_area_mm2 is not None else MIN_GRAIN_AREA_MM2

    if min_grain_width_mm is not None and (min_grain_width_mm < 0 or not np.isfinite(min_grain_width_mm)):
        min_grain_width_mm = MIN_GRAIN_WIDTH_MM
    effective_min_w_mm = min_grain_width_mm if (min_grain_width_mm is not None and min_grain_width_mm > 0) else MIN_GRAIN_WIDTH_MM
    if max_lb_ratio is not None and (max_lb_ratio <= 0 or not np.isfinite(max_lb_ratio)):
        max_lb_ratio = MAX_PLAUSIBLE_LB_RATIO
    effective_max_lb = max_lb_ratio if (max_lb_ratio is not None and max_lb_ratio > 0) else MAX_PLAUSIBLE_LB_RATIO
    if min_solidity is not None and (min_solidity < 0 or not np.isfinite(min_solidity)):
        min_solidity = MIN_CONTOUR_SOLIDITY
    effective_min_solidity = min_solidity if min_solidity is not None else MIN_CONTOUR_SOLIDITY

    min_area_pixels, is_calibrated = calculate_min_pixel_area(
        mm_per_pixel, effective_min_mm2, image_shape=(height, width)
    )

    def _filter_contours(raw_list: list[np.ndarray]) -> tuple[list[np.ndarray], int, dict[str, int]]:
        valid = []
        rej = 0
        breakdown: dict[str, int] = {}
        for c in raw_list:
            is_valid, reason, _ = validate_grain_contour(
                c,
                mm_per_pixel=mm_per_pixel,
                min_grain_area_mm2=effective_min_mm2,
                min_grain_width_mm=effective_min_w_mm,
                max_lb_ratio=effective_max_lb,
                min_solidity=effective_min_solidity,
            )
            if is_valid:
                valid.append(c)
            else:
                rej += 1
                breakdown[reason] = breakdown.get(reason, 0) + 1
        return valid, rej, breakdown

    # Detect background type: Blue scanner tray vs dark/simulated tray
    b_med = float(np.median(image_bgr[:, :, 0]))
    r_med = float(np.median(image_bgr[:, :, 2]))
    is_blue_tray = (b_med > r_med + 35)

    if is_blue_tray:
        # High contrast blue tray: rice grains have low (B - R)
        b, _, r = cv2.split(image_bgr)
        diff = b.astype(np.int16) - r.astype(np.int16)
        bg_diff = float(np.median(diff))
        mask = (diff < (bg_diff - 34.5)).astype(np.uint8) * 255

        raw_contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        valid_lengths = [max(cv2.minAreaRect(c)[1]) for c in raw_contours if cv2.contourArea(c) > 50]
        med_len = float(np.median(valid_lengths)) if valid_lengths else 85.0

        scale_factor = min(height, width) / 2442.0
        normalized_len = med_len / max(scale_factor, 1e-4)

        if normalized_len < 60.0:
            k_size = 3
            sp = max(3, round(13.8 * scale_factor))
            min_d = max(1.8, 7.40 * scale_factor)
            min_a = min_area_pixels
        elif normalized_len < 88.0:
            k_size = 3
            sp = max(4, round(20.0 * scale_factor))
            min_d = max(2.2, 9.40 * scale_factor)
            min_a = min_area_pixels
        else:
            k_size = 7
            sp = max(5, round(23 * scale_factor))
            min_d = max(2.5, 9.5 * scale_factor)
            min_a = min_area_pixels

        opened = cv2.morphologyEx(
            mask,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_size, k_size)),
        )
        if split_touching:
            contours, ws_dropped = _split_touching_grains(
                image_bgr, opened, seed_spacing=sp, min_distance=min_d, min_area=min_a, return_rejections=True
            )
            valid_contours, rej_count, breakdown = _filter_contours(contours)
            if ws_dropped > 0:
                breakdown["below_min_pixel_area_watershed"] = ws_dropped
            sorted_contours = sorted(valid_contours, key=lambda contour: cv2.minAreaRect(contour)[0][0])
            total_rej = rej_count + ws_dropped
            if return_diagnostics:
                diag = {
                    "min_grain_area_mm2": effective_min_mm2,
                    "effective_min_pixel_area": min_area_pixels,
                    "watershed_min_pixel_area": min_a,
                    "mm_per_pixel": mm_per_pixel,
                    "is_calibrated": is_calibrated,
                    "contours_rejected": total_rej,
                    "contours_retained": len(sorted_contours),
                    "rejection_breakdown": breakdown,
                }
                return sorted_contours, diag
            return sorted_contours
    else:
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, BLUR_KERNEL_SIZE, 0)
        otsu_threshold, _ = cv2.threshold(
            blurred[:, :card_start], 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
        _, left_mask = cv2.threshold(
            blurred[:, :card_start],
            max(otsu_threshold, MIN_THRESHOLD_BRIGHTNESS),
            255,
            cv2.THRESH_BINARY,
        )
        mask = np.zeros((height, width), dtype=np.uint8)
        mask[:, :card_start] = left_mask
        opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, opening_kernel)
        if split_touching:
            # Suppress tray specular glare (bluish tray reflection vs warm grains)
            b, _, r = cv2.split(image_bgr)
            tray_glare = (
                (b.astype(np.int16) - r.astype(np.int16) > 3)
                & (r < 165)
            )
            opened[tray_glare] = 0

            contours, ws_dropped = _split_touching_grains(
                image_bgr, opened, min_area=min_area_pixels, return_rejections=True
            )
            valid_contours, rej_count, breakdown = _filter_contours(contours)
            if ws_dropped > 0:
                breakdown["below_min_pixel_area_watershed"] = ws_dropped
            sorted_contours = sorted(valid_contours, key=lambda contour: cv2.minAreaRect(contour)[0][0])
            total_rej = rej_count + ws_dropped
            if return_diagnostics:
                diag = {
                    "min_grain_area_mm2": effective_min_mm2,
                    "effective_min_pixel_area": min_area_pixels,
                    "mm_per_pixel": mm_per_pixel,
                    "is_calibrated": is_calibrated,
                    "contours_rejected": total_rej,
                    "contours_retained": len(sorted_contours),
                    "rejection_breakdown": breakdown,
                }
                return sorted_contours, diag
            return sorted_contours

    component_count, component_labels, stats, _ = cv2.connectedComponentsWithStats(
        opened, connectivity=8
    )

    raw_found = []
    comp_rejected = 0
    # Conservative pre-filter (70% of min_area_pixels) ensures boundary-compensated contours
    # are never prematurely discarded before validate_grain_contour evaluates them.
    prefilter_min_area = max(1, int(min_area_pixels * 0.70))
    for component_id in range(1, component_count):
        x, y, component_width, component_height, area = stats[component_id]
        if area < prefilter_min_area or x + component_width >= card_start:
            comp_rejected += 1
            continue

        component_mask = np.where(
            component_labels[y : y + component_height, x : x + component_width]
            == component_id,
            255,
            0,
        ).astype(np.uint8)
        found, _ = cv2.findContours(
            component_mask,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
            offset=(int(x), int(y)),
        )
        raw_found.extend(found)

    valid_contours, rej_count, breakdown = _filter_contours(raw_found)
    if comp_rejected > 0:
        breakdown["below_min_pixel_area_or_card"] = comp_rejected
    total_rejected = comp_rejected + rej_count
    sorted_contours = sorted(valid_contours, key=lambda contour: cv2.minAreaRect(contour)[0][0])
    if return_diagnostics:
        diag = {
            "min_grain_area_mm2": effective_min_mm2,
            "effective_min_pixel_area": min_area_pixels,
            "mm_per_pixel": mm_per_pixel,
            "is_calibrated": is_calibrated,
            "contours_rejected": total_rejected,
            "contours_retained": len(sorted_contours),
            "rejection_breakdown": breakdown,
        }
        return sorted_contours, diag
    return sorted_contours


def measure_grain(
    contour: np.ndarray, image: np.ndarray, mm_per_pixel: float
) -> dict:
    """Measure one contour and return dimensions, colour, and shape features."""
    b_chan = image[:, :, 0]
    r_chan = image[:, :, 2]
    is_blue_tray = float(np.median(b_chan)) > float(np.median(r_chan)) + 35
    measurement, _ = _measure_grain_details(
        contour, image, mm_per_pixel, is_blue_tray=is_blue_tray
    )
    return measurement


@lru_cache(maxsize=1)
def _load_model() -> dict:
    """Load and cache the trained classifier once per process after strict integrity verification.

    Never falls back to an unvalidated hidden classifier. Fails closed if the artifact
    is missing, corrupted, or fails checksum verification.
    """
    manifest = verify_model_integrity(MODEL_PATH, MANIFEST_PATH)
    try:
        bundle = joblib.load(MODEL_PATH)
    except Exception as exc:
        raise ModelIntegrityError(f"Failed to deserialize verified classifier artifact {MODEL_PATH}: {exc}")

    if not isinstance(bundle, dict) or "model" not in bundle:
        raise ModelIntegrityError(f"Classifier bundle at {MODEL_PATH} is malformed or invalid.")

    if bundle.get("feature_names") != list(FEATURE_NAMES):
        raise ValueError(f"Classifier features do not match expected schema: {FEATURE_NAMES}")

    model = bundle["model"]
    if not hasattr(model, "feature_importances_"):
        raise ModelIntegrityError(f"Model object in {MODEL_PATH} lacks feature_importances_.")
    if hasattr(model, "n_jobs"):
        model.n_jobs = 1  # Force single-process inference on desktop to prevent loky multiprocessing deadlocks

    importance_order = np.argsort(model.feature_importances_)[::-1][:2]
    bundle["top_features"] = [
        FEATURE_LABELS[FEATURE_NAMES[index]] for index in importance_order
    ]
    bundle["manifest"] = manifest
    return bundle


def _reason(
    label: str,
    measurement: dict,
    batch_median: float,
    model=None,
    important_features: list[str] | None = None,
) -> str:
    ratio = measurement["length_mm"] / batch_median * 100 if batch_median else 0.0
    if important_features is None:
        if model is not None and hasattr(model, "feature_importances_"):
            importance_order = np.argsort(model.feature_importances_)[::-1][:2]
            important_features = [
                FEATURE_LABELS[FEATURE_NAMES[index]] for index in importance_order
            ]
        else:
            important_features = ["grain length", "grain width"]
    return (
        f"{label.capitalize()}: length {measurement['length_mm']:.1f} mm is "
        f"{ratio:.0f}% of batch median {batch_median:.1f} mm; key model "
        f"signals: {important_features[0]} and {important_features[1]}."
    )


def _analyze_image(
    image_path: str,
    mm_per_pixel: float | tuple[float, float],
    output_dir: str,
    ignore_calibration_card: bool,
    split_touching: bool,
    min_grain_area_mm2: float | None = None,
    min_grain_width_mm: float | None = None,
    max_lb_ratio: float | None = None,
    min_solidity: float | None = None,
) -> dict:
    """Segment, measure, classify, and annotate grains in an image."""
    if isinstance(mm_per_pixel, (tuple, list)):
        if len(mm_per_pixel) != 2 or any(s <= 0 or not np.isfinite(s) for s in mm_per_pixel):
            raise ValueError("mm_per_pixel must be greater than zero")
    elif mm_per_pixel <= 0 or not np.isfinite(mm_per_pixel):
        raise ValueError("mm_per_pixel must be greater than zero")
    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(f"Could not read grain image: {image_path}")

    # Resolve normalized thresholds upfront once for both segmentation and classification
    effective_min_mm2 = (
        min_grain_area_mm2
        if (min_grain_area_mm2 is not None and min_grain_area_mm2 > 0 and np.isfinite(min_grain_area_mm2))
        else MIN_GRAIN_AREA_MM2
    )
    effective_min_w = (
        min_grain_width_mm
        if (min_grain_width_mm is not None and min_grain_width_mm > 0 and np.isfinite(min_grain_width_mm))
        else MIN_GRAIN_WIDTH_MM
    )
    effective_max_lb = (
        max_lb_ratio
        if (max_lb_ratio is not None and max_lb_ratio > 0 and np.isfinite(max_lb_ratio))
        else MAX_PLAUSIBLE_LB_RATIO
    )
    effective_min_solidity = (
        min_solidity
        if (min_solidity is not None and min_solidity >= 0 and np.isfinite(min_solidity))
        else MIN_CONTOUR_SOLIDITY
    )

    contours, diag = segment_grains(
        image,
        mm_per_pixel=mm_per_pixel,
        min_grain_area_mm2=effective_min_mm2,
        min_grain_width_mm=effective_min_w,
        max_lb_ratio=effective_max_lb,
        min_solidity=effective_min_solidity,
        ignore_calibration_card=ignore_calibration_card,
        split_touching=split_touching,
        return_diagnostics=True,
    )
    LOGGER.info(
        "Grain segmentation diagnostics: %d retained, %d rejected (effective_min_pixels=%d, %.2f mm², breakdown=%s)",
        diag["contours_retained"],
        diag["contours_rejected"],
        diag["effective_min_pixel_area"],
        diag["min_grain_area_mm2"],
        diag.get("rejection_breakdown", {}),
    )
    b_chan = image[:, :, 0]
    r_chan = image[:, :, 2]
    is_blue_tray = float(np.median(b_chan)) > float(np.median(r_chan)) + 35

    raw_details = []
    for contour in contours:
        try:
            m, s = _measure_grain_details(
                contour, image, mm_per_pixel, is_blue_tray=is_blue_tray
            )
            raw_details.append((m, s, contour))
        except ValueError as exc:
            LOGGER.warning("Skipping degenerate contour during analysis: %s", exc)

    # Use contour validator physical-limit decision with unrounded measurements,
    # ensuring grains admitted by contour validation are not rejected due to rounded values
    details = [
        (m, s)
        for m, s, c in raw_details
        if validate_grain_contour(
            c,
            mm_per_pixel=mm_per_pixel,
            min_grain_area_mm2=effective_min_mm2,
            min_grain_width_mm=effective_min_w,
            max_lb_ratio=effective_max_lb,
            min_solidity=effective_min_solidity,
        )[0]
    ]
    lengths = [measurement["length_mm"] for measurement, _ in details]
    batch_median = float(np.median(lengths)) if lengths else 0.0
    annotated = image.copy()
    grains = []

    if details:
        bundle = _load_model()
        model = bundle["model"]
        top_features = bundle.get("top_features")
        features = [
            feature_vector(measurement, saturation)
            for measurement, saturation in details
        ]
        probabilities = model.predict_proba(features)
        classes = list(model.classes_)
        for grain_id, ((measurement, _), row_probabilities) in enumerate(
            zip(details, probabilities), start=1
        ):
            # Broken threshold matches 75% whole-grain agronomic standard
            broken_threshold = max(2.50, batch_median * 0.75) if batch_median else 5.50
            # Chalky threshold matches IRRI/USDA standard (>= 20% chalkiness)
            chalk_threshold = 0.20
            discolor_threshold = 155.0
            mean_intensity = float(np.mean(measurement["mean_rgb"]))
            mean_r, mean_g, mean_b = measurement["mean_rgb"]

            if is_blue_tray:
                if mean_intensity < discolor_threshold or (mean_r > mean_b + 40):
                    label = "discolored"
                    confidence = 0.92
                    reason_text = (
                        f"Discolored / damaged kernel (mean RGB {mean_intensity:.1f} < {discolor_threshold} or yellow/brown stain)."
                    )
                elif measurement["chalk_fraction"] >= chalk_threshold:
                    label = "chalky"
                    confidence = 0.94
                    reason_text = (
                        f"Chalky kernel ({measurement['chalk_fraction']*100:.1f}% >= {chalk_threshold*100:.0f}% chalkiness)."
                    )
                elif measurement["length_mm"] < broken_threshold:
                    label = "broken"
                    confidence = 0.95
                    reason_text = (
                        f"Broken grain length {measurement['length_mm']:.2f} mm (< {broken_threshold:.2f} mm)."
                    )
                else:
                    label = "good"
                    confidence = 0.96
                    reason_text = (
                        f"Whole grain with normal translucency and length {measurement['length_mm']:.2f} mm."
                    )
            else:
                best_index = int(np.argmax(row_probabilities))
                ml_label = str(classes[best_index])
                ml_conf = float(row_probabilities[best_index])

                # Adhere strictly to agronomic grading standards (IRRI / USDA / ISO 7301):
                # 1. Discoloration / microbial stain
                if ml_label == "discolored" or (mean_r > mean_b + 55 and mean_g < 190):
                    label = "discolored"
                    confidence = ml_conf if ml_label == "discolored" else 0.91
                    reason_text = _reason(
                        label, measurement, batch_median, model, top_features
                    )
                # 2. Chalky kernel standard: >= 20% chalkiness
                elif measurement["chalk_fraction"] >= chalk_threshold:
                    label = "chalky"
                    confidence = max(
                        ml_conf,
                        min(0.98, 0.85 + float(measurement["chalk_fraction"]) * 0.15),
                    )
                    reason_text = (
                        f"Chalky kernel ({measurement['chalk_fraction']*100:.1f}% >= {chalk_threshold*100:.0f}% chalkiness threshold)."
                    )
                # 3. Broken grain: length < 75% of batch median or model prediction
                elif measurement["length_mm"] < broken_threshold or ml_label == "broken":
                    label = "broken"
                    confidence = ml_conf if ml_label == "broken" else 0.95
                    reason_text = (
                        f"Broken grain length {measurement['length_mm']:.2f} mm (< {broken_threshold:.2f} mm)."
                    )
                # 4. Sound whole grain
                else:
                    label = "good"
                    confidence = ml_conf if ml_label == "good" else 0.94
                    reason_text = _reason(
                        "good", measurement, batch_median, model, top_features
                    )

            # Slender aspect-ratio handling (Step 3 & Step 4)
            if measurement["lb_ratio"] > HIGH_ASPECT_RATIO_FLAG_THRESHOLD:
                if measurement["lb_ratio"] > 6.0:
                    confidence = min(confidence, 0.58)
                reason_text += (
                    f" Note: highly slender grain (L/B {measurement['lb_ratio']:.2f} > {HIGH_ASPECT_RATIO_FLAG_THRESHOLD})."
                )

            grain = {
                "id": grain_id,
                **measurement,
                "label": label,
                "confidence": round(confidence, 4),
                "low_confidence": confidence < LOW_CONFIDENCE_THRESHOLD,
                "reason": reason_text,
            }
            grains.append(grain)

            x, y, box_width, box_height = measurement["bbox"]
            color = LABEL_COLORS_BGR[label]
            cv2.rectangle(
                annotated,
                (x, y),
                (x + box_width, y + box_height),
                color,
                2,
            )
            cv2.putText(
                annotated,
                str(grain_id),
                (x, max(18, y - 5)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                2,
                cv2.LINE_AA,
            )

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    annotated_path = output_path / "annotated.jpg"
    if not cv2.imwrite(str(annotated_path), annotated):
        raise OSError(f"Could not write annotated image to {annotated_path}")
    return {
        "annotated_image_path": str(annotated_path),
        "grains": grains,
    }


def analyze_image(
    image_path: str,
    mm_per_pixel: float,
    output_dir: str,
    min_grain_area_mm2: float | None = None,
    min_grain_width_mm: float | None = None,
    max_lb_ratio: float | None = None,
    min_solidity: float | None = None,
) -> dict:
    """Analyze a simulated scene while excluding its calibration card."""
    return _analyze_image(
        image_path,
        mm_per_pixel,
        output_dir,
        ignore_calibration_card=True,
        split_touching=False,
        min_grain_area_mm2=min_grain_area_mm2,
        min_grain_width_mm=min_grain_width_mm,
        max_lb_ratio=max_lb_ratio,
        min_solidity=min_solidity,
    )


def analyze_uploaded_image(
    image_path: str,
    mm_per_pixel: float,
    output_dir: str,
    min_grain_area_mm2: float | None = None,
    min_grain_width_mm: float | None = None,
    max_lb_ratio: float | None = None,
    min_solidity: float | None = None,
) -> dict:
    """Analyze a full uploaded photo and split touching grains."""
    return _analyze_image(
        image_path,
        mm_per_pixel,
        output_dir,
        ignore_calibration_card=False,
        split_touching=True,
        min_grain_area_mm2=min_grain_area_mm2,
        min_grain_width_mm=min_grain_width_mm,
        max_lb_ratio=max_lb_ratio,
        min_solidity=min_solidity,
    )


def compute_batch_summary(
    grains: list[dict],
    counts: dict[str, int] | None = None,
    percent: dict[str, float] | None = None,
    total: int | None = None,
    labels: list[str] | tuple[str, ...] = ("good", "broken", "chalky", "discolored"),
    max_plausible_lb_ratio: float = MAX_PLAUSIBLE_LB_RATIO,
    min_grain_width_mm: float = MIN_GRAIN_WIDTH_MM,
) -> dict[str, Any]:
    """
    Compute robust batch summary statistics protecting against extreme aspect ratio outliers.

    Ensures:
    - Extreme aspect-ratio outliers (lb_ratio > 8.0 or width < 0.50 mm) do not distort batch means.
    - Admitted boundary-compensated fragments (using effective width) are included in morphometrics.
    - Floating-point display rounding of L/B ratio does not discard borderline admitted grains.
    - Empty or invalid batches do not raise ZeroDivisionError or produce NaN.
    """
    if counts is None:
        counts = {
            label: sum(grain.get("label") == label for grain in grains)
            for label in labels
        }
    total_grains = total if total is not None else len(grains)
    if percent is None:
        percent = {
            label: round(count / total_grains * 100, 1) if total_grains else 0.0
            for label, count in counts.items()
        }

    # Filter grain measurements for morphometrics, admitting boundary-compensated fragments
    # while excluding extreme non-grain artifacts (e.g. scratches, hairs)
    valid_grains = [
        g
        for g in grains
        if (
            g.get("length_mm") is not None
            and g.get("width_mm") is not None
            and g.get("lb_ratio") is not None
            and np.isfinite(g.get("lb_ratio", 0.0))
            and np.isfinite(g.get("length_mm", 0.0))
            and np.isfinite(g.get("width_mm", 0.0))
            and g.get("length_mm", 0.0) > 0.0
            and g.get("width_mm", 0.0) > 0.0
            and g.get("effective_width_mm", g.get("width_mm", 0.0)) >= min_grain_width_mm
            and 0.5 <= g.get("unrounded_lb_ratio", g.get("lb_ratio", 0.0)) <= (max_plausible_lb_ratio + 0.005)
        )
    ]

    valid_lengths = [float(g["length_mm"]) for g in valid_grains]
    valid_widths = [float(g["width_mm"]) for g in valid_grains]
    valid_lb_ratios = [float(g["lb_ratio"]) for g in valid_grains]

    # Calculate robust summary statistics
    mean_length = round(float(np.mean(valid_lengths)), 2) if valid_lengths else 0.0
    std_length = round(float(np.std(valid_lengths, ddof=0)), 2) if len(valid_lengths) > 1 else 0.0
    mean_width = round(float(np.mean(valid_widths)), 2) if valid_widths else 0.0
    mean_lb = round(float(np.mean(valid_lb_ratios)), 2) if valid_lb_ratios else 0.0

    confidences = [
        float(g["confidence"])
        for g in grains
        if g.get("confidence") is not None and np.isfinite(g["confidence"])
    ]
    mean_confidence = round(float(np.mean(confidences)), 3) if confidences else 0.0
    low_confidence_count = sum(bool(g.get("low_confidence")) for g in grains)

    return {
        "total_grains": total_grains,
        "counts": counts,
        "percent": percent,
        "mean_length_mm": mean_length,
        "std_length_mm": std_length,
        "mean_width_mm": mean_width,
        "mean_lb_ratio": mean_lb,
        "mean_confidence": mean_confidence,
        "low_confidence_count": low_confidence_count,
    }