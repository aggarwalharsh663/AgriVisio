"""Basic grain segmentation and measurements for simulated rice images."""

from functools import lru_cache
from pathlib import Path

import cv2
import joblib
import numpy as np


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
MODEL_PATH = Path(__file__).with_name("model.joblib")


def _measure_grain_details(
    contour: np.ndarray, image: np.ndarray, mm_per_pixel: float
) -> tuple[dict, float]:
    """Measure one grain and return the mean HSV saturation for classification."""
    if mm_per_pixel <= 0:
        raise ValueError("mm_per_pixel must be greater than zero")
    if image is None or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("image must be a three-channel BGR image")

    (center_x, center_y), (side_a, side_b), _ = cv2.minAreaRect(contour)
    length_mm = max(side_a, side_b) * mm_per_pixel
    width_mm = min(side_a, side_b) * mm_per_pixel
    x, y, box_width, box_height = cv2.boundingRect(contour)

    contour_mask = np.zeros(image.shape[:2], dtype=np.uint8)
    cv2.drawContours(contour_mask, [contour], -1, 255, thickness=cv2.FILLED)
    grain_pixels = contour_mask[y : y + box_height, x : x + box_width] > 0
    grain_bgr = image[y : y + box_height, x : x + box_width][grain_pixels]
    if grain_bgr.size == 0:
        raise ValueError("contour does not contain any image pixels")

    grain_hsv = cv2.cvtColor(
        grain_bgr.reshape(-1, 1, 3), cv2.COLOR_BGR2HSV
    ).reshape(-1, 3)
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


def segment_grains(image_bgr: np.ndarray) -> list[np.ndarray]:
    """Find grain contours, ordered from left to right."""
    if image_bgr is None or image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
        raise ValueError("image_bgr must be a three-channel BGR image")

    height, width = image_bgr.shape[:2]
    card_start = min(CARD_IGNORE_X, width)
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

    opening_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, OPENING_KERNEL_SIZE
    )
    opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, opening_kernel)
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
    """Load and cache the trained classifier once per process."""
    if not MODEL_PATH.is_file():
        raise FileNotFoundError(
            f"Classifier model not found at {MODEL_PATH}; "
            "run python -m backend.train_classifier first"
        )
    bundle = joblib.load(MODEL_PATH)
    if bundle.get("feature_names") != list(FEATURE_NAMES):
        raise ValueError(f"Classifier features do not match {MODEL_PATH}")
    return bundle


def _reason(label: str, measurement: dict, batch_median: float, model) -> str:
    ratio = measurement["length_mm"] / batch_median * 100
    importance_order = np.argsort(model.feature_importances_)[::-1][:2]
    important_features = [
        FEATURE_LABELS[FEATURE_NAMES[index]] for index in importance_order
    ]
    return (
        f"{label.capitalize()}: length {measurement['length_mm']:.1f} mm is "
        f"{ratio:.0f}% of batch median {batch_median:.1f} mm; key model "
        f"signals: {important_features[0]} and {important_features[1]}."
    )


def analyze_image(image_path: str, mm_per_pixel: float, output_dir: str) -> dict:
    """Segment, measure, classify, and annotate grains in an image."""
    if mm_per_pixel <= 0:
        raise ValueError("mm_per_pixel must be greater than zero")
    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(f"Could not read grain image: {image_path}")

    contours = segment_grains(image)
    details = [
        _measure_grain_details(contour, image, mm_per_pixel)
        for contour in contours
    ]
    lengths = [measurement["length_mm"] for measurement, _ in details]
    batch_median = float(np.median(lengths)) if lengths else 0.0
    annotated = image.copy()
    grains = []

    if details:
        bundle = _load_model()
        model = bundle["model"]
        features = [
            feature_vector(measurement, saturation)
            for measurement, saturation in details
        ]
        probabilities = model.predict_proba(features)
        classes = list(model.classes_)
        for grain_id, ((measurement, _), row_probabilities) in enumerate(
            zip(details, probabilities), start=1
        ):
            best_index = int(np.argmax(row_probabilities))
            label = str(classes[best_index])
            confidence = float(row_probabilities[best_index])
            grain = {
                "id": grain_id,
                **measurement,
                "label": label,
                "confidence": round(confidence, 4),
                "low_confidence": confidence < LOW_CONFIDENCE_THRESHOLD,
                "reason": _reason(label, measurement, batch_median, model),
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