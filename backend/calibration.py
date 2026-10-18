"""Calibrate simulated image colours from the reference card."""

from pathlib import Path

import cv2
import numpy as np


def _patch_means(img_rgb, card_patches):
    means = []
    for x, y, size in card_patches:
        patch = img_rgb[y + 15 : y + size - 15, x + 15 : x + size - 15]
        if patch.size == 0:
            raise ValueError(f"Card patch at ({x}, {y}) is too small or outside the image")
        means.append(patch.reshape(-1, 3).mean(axis=0))
    return np.asarray(means, dtype=np.float32)


def _features(rgb):
    rgb = np.asarray(rgb, dtype=np.float32).reshape(-1, 3)
    return np.column_stack((rgb, np.ones(len(rgb), dtype=np.float32)))


def _patch_error(measured, true_rgb):
    return float(np.linalg.norm(measured - true_rgb, axis=1).mean())


def calibrate(photo_path, card_patches, card_true_rgb, out_path):
    """Correct an image with an affine RGB fit and report mean patch distances."""
    bgr = cv2.imread(str(photo_path))
    if bgr is None:
        raise FileNotFoundError(f"Could not read photo: {photo_path}")
    if len(card_patches) != len(card_true_rgb) or len(card_patches) < 4:
        raise ValueError("At least four matching card patches and true RGB values are required")

    image_rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    true_rgb = np.asarray(card_true_rgb, dtype=np.float32)
    if true_rgb.shape != (len(card_patches), 3):
        raise ValueError("card_true_rgb must contain one RGB triplet per card patch")

    measured = _patch_means(image_rgb, card_patches)
    matrix, *_ = np.linalg.lstsq(_features(measured), true_rgb, rcond=None)
    patch_error_before = _patch_error(measured, true_rgb)

    corrected_rgb = np.clip(_features(image_rgb) @ matrix, 0, 255).reshape(image_rgb.shape)
    corrected_rgb = corrected_rgb.astype(np.uint8)
    corrected_measured = _patch_means(corrected_rgb, card_patches)
    patch_error_after = _patch_error(corrected_measured, true_rgb)

    corrected_path = Path(out_path)
    corrected_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(
        str(corrected_path),
        cv2.cvtColor(corrected_rgb, cv2.COLOR_RGB2BGR),
    ):
        raise OSError(f"Could not write calibrated photo to {corrected_path}")

    return {
        "corrected_path": str(corrected_path),
        "patch_error_before": patch_error_before,
        "patch_error_after": patch_error_after,
    }