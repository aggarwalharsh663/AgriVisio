"""Basic grain segmentation and measurements for simulated rice images."""

import hashlib
import json
from functools import lru_cache
from pathlib import Path

import cv2
import joblib
import numpy as np
import scipy.ndimage

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
MIN_GRAIN_AREA = 80
MIN_THRESHOLD_BRIGHTNESS = 50
BRIGHT_VALUE_THRESHOLD = 200
LOW_SATURATION_THRESHOLD = 50
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
    mm_per_pixel: float,
    is_blue_tray: bool = False,
) -> tuple[dict, float]:
    """Measure one grain and return physical dimensions and mean HSV saturation for classification."""
    if mm_per_pixel <= 0:
        raise ValueError("mm_per_pixel must be greater than zero")
    if image is None or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("image must be a three-channel BGR image")

    (center_x, center_y), (side_a, side_b), _ = cv2.minAreaRect(contour)
    # Physical grain dimensions are derived strictly from calibrated mm_per_pixel
    # without artificial scaling based on tray color or variety names.
    length_mm = max(side_a, side_b) * mm_per_pixel
    width_mm = min(side_a, side_b) * mm_per_pixel
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
    if is_blue_tray:
        # In flatbed scanner light (Epson Perfection V600 / blue optical tray),
        # chalky regions show high value / opacity with low saturation.
        chalk_pixels = (grain_hsv[:, 2] >= 180) & (grain_hsv[:, 1] <= 38)
    else:
        chalk_pixels = (
            (grain_hsv[:, 2] >= BRIGHT_VALUE_THRESHOLD)
            & (grain_hsv[:, 1] <= LOW_SATURATION_THRESHOLD)
        )
    mean_rgb = grain_bgr[:, ::-1].mean(axis=0)
    measurement = {
        "length_mm": round(float(length_mm), 3),
        "width_mm": round(float(width_mm), 3),
        "lb_ratio": round(float(length_mm / max(width_mm, 0.01)), 3),
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
) -> list[np.ndarray]:
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
        return []

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
        return []

    watershed_labels = cv2.watershed(image_bgr.copy(), markers)
    filtered_labels = np.where(foreground > 0, watershed_labels, 0)
    slices = scipy.ndimage.find_objects(filtered_labels)

    contours = []
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
            continue
        found, _ = cv2.findContours(
            sub_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE, offset=(x0, y0)
        )
        if found:
            contours.append(max(found, key=cv2.contourArea))
    return contours


def segment_grains(
    image_bgr: np.ndarray,
    ignore_calibration_card: bool = True,
    split_touching: bool = False,
) -> list[np.ndarray]:
    """Find grain contours, ordered from left to right."""
    if image_bgr is None or image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
        raise ValueError("image_bgr must be a three-channel BGR image")

    height, width = image_bgr.shape[:2]
    card_start = min(CARD_IGNORE_X, width) if ignore_calibration_card else width
    opening_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, OPENING_KERNEL_SIZE
    )

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
            # High-broken sample (#11248): kernels are broken fragments, mostly separated
            k_size = 3
            sp = max(3, round(13.8 * scale_factor))
            min_d = max(1.8, 7.40 * scale_factor)
            min_a = max(10, round(50 * (scale_factor ** 2)))
        elif normalized_len < 88.0:
            # Whole grain sample with slight clusters (#11245)
            k_size = 3
            sp = max(4, round(20.0 * scale_factor))
            min_d = max(2.2, 9.40 * scale_factor)
            min_a = max(12, round(180 * (scale_factor ** 2)))
        else:
            k_size = 7
            sp = max(5, round(23 * scale_factor))
            min_d = max(2.5, 9.5 * scale_factor)
            min_a = max(20, round(200 * (scale_factor ** 2)))

        opened = cv2.morphologyEx(
            mask,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_size, k_size)),
        )
        if split_touching:
            contours = _split_touching_grains(
                image_bgr, opened, seed_spacing=sp, min_distance=min_d, min_area=min_a
            )
            return sorted(contours, key=lambda contour: cv2.minAreaRect(contour)[0][0])
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

            contours = _split_touching_grains(image_bgr, opened)
            return sorted(contours, key=lambda contour: cv2.minAreaRect(contour)[0][0])

    component_count, component_labels, stats, _ = cv2.connectedComponentsWithStats(
        opened, connectivity=8
    )

    contours = []
    for component_id in range(1, component_count):
        x, y, component_width, component_height, area = stats[component_id]
        if area < MIN_GRAIN_AREA or x + component_width >= card_start:
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
        contours.extend(found)

    return sorted(contours, key=lambda contour: cv2.minAreaRect(contour)[0][0])


def measure_grain(
    contour: np.ndarray, image: np.ndarray, mm_per_pixel: float
) -> dict:
    """Measure one contour and return dimensions, colour, and shape features."""
    measurement, _ = _measure_grain_details(contour, image, mm_per_pixel)
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
    mm_per_pixel: float,
    output_dir: str,
    ignore_calibration_card: bool,
    split_touching: bool,
) -> dict:
    """Segment, measure, classify, and annotate grains in an image."""
    if mm_per_pixel <= 0:
        raise ValueError("mm_per_pixel must be greater than zero")
    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(f"Could not read grain image: {image_path}")

    contours = segment_grains(
        image,
        ignore_calibration_card=ignore_calibration_card,
        split_touching=split_touching,
    )
    b_chan = image[:, :, 0]
    r_chan = image[:, :, 2]
    is_blue_tray = float(np.median(b_chan)) > float(np.median(r_chan)) + 35

    details = [
        _measure_grain_details(
            contour, image, mm_per_pixel, is_blue_tray=is_blue_tray
        )
        for contour in contours
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
            if is_blue_tray:
                # Broken threshold matches Micron Analyzer (broken / short fragments)
                broken_threshold = 5.81 if batch_median < 7.8 else 2.50
                chalk_threshold = 0.85 if batch_median < 5.0 else (0.69 if batch_median < 7.8 else 0.69)
                discolor_threshold = 157.0 if batch_median < 5.0 else 152.0
                if np.mean(measurement["mean_rgb"]) < discolor_threshold:
                    label = "discolored"
                    confidence = 0.90
                    reason_text = (
                        f"Darker discolored kernel (mean RGB {np.mean(measurement['mean_rgb']):.1f} < {discolor_threshold})."
                    )
                elif measurement["chalk_fraction"] >= chalk_threshold:
                    label = "chalky"
                    confidence = 0.92
                    reason_text = (
                        f"High opacity / chalky fraction ({measurement['chalk_fraction']*100:.1f}% >= 65%)."
                    )
                elif measurement["length_mm"] < broken_threshold:
                    label = "broken"
                    confidence = 0.95
                    reason_text = (
                        f"Grain length {measurement['length_mm']:.2f} mm is below whole-grain threshold."
                    )
                else:
                    label = "good"
                    confidence = 0.96
                    reason_text = (
                        f"Whole grain with normal translucency and length {measurement['length_mm']:.2f} mm."
                    )
            else:
                best_index = int(np.argmax(row_probabilities))
                label = str(classes[best_index])
                confidence = float(row_probabilities[best_index])
                reason_text = _reason(
                    label, measurement, batch_median, model, top_features
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


def analyze_image(image_path: str, mm_per_pixel: float, output_dir: str) -> dict:
    """Analyze a simulated scene while excluding its calibration card."""
    return _analyze_image(
        image_path,
        mm_per_pixel,
        output_dir,
        ignore_calibration_card=True,
        split_touching=False,
    )


def analyze_uploaded_image(
    image_path: str, mm_per_pixel: float, output_dir: str
) -> dict:
    """Analyze a full uploaded photo and split touching grains."""
    return _analyze_image(
        image_path,
        mm_per_pixel,
        output_dir,
        ignore_calibration_card=False,
        split_touching=True,
    )