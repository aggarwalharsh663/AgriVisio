"""Basic grain segmentation and measurements for simulated rice images."""

import cv2
import numpy as np


CARD_IGNORE_X = 970
BLUR_KERNEL_SIZE = (5, 5)
OPENING_KERNEL_SIZE = (3, 3)
MIN_GRAIN_AREA = 80
BRIGHT_VALUE_THRESHOLD = 200
LOW_SATURATION_THRESHOLD = 50


def segment_grains(image_bgr: np.ndarray) -> list[np.ndarray]:
    """Find grain contours, ordered from left to right."""
    if image_bgr is None or image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
        raise ValueError("image_bgr must be a three-channel BGR image")

    height, width = image_bgr.shape[:2]
    card_start = min(CARD_IGNORE_X, width)
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, BLUR_KERNEL_SIZE, 0)
    _, left_mask = cv2.threshold(
        blurred[:, :card_start], 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
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

    return {
        "length_mm": round(float(length_mm), 3),
        "width_mm": round(float(width_mm), 3),
        "lb_ratio": round(float(length_mm / max(width_mm, 0.01)), 3),
        "bbox": [int(x), int(y), int(box_width), int(box_height)],
        "center": [round(float(center_x), 2), round(float(center_y), 2)],
        "mean_rgb": [round(float(channel), 1) for channel in mean_rgb],
        "chalk_fraction": round(float(chalk_pixels.mean()), 4),
    }