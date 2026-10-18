import json

import cv2
import numpy as np
import pytest

from backend.grain_analysis import (
    FEATURE_NAMES,
    analyze_image,
    measure_grain,
    segment_grains,
)
from backend.simulator import LIGHTING, make_scene


@pytest.mark.parametrize("lighting", LIGHTING)
def test_simulated_grain_count_and_mean_length(tmp_path, lighting):
    scene = make_scene(
        seed=17,
        n_grains=80,
        lighting=lighting,
        out_dir=str(tmp_path / lighting),
    )
    image = cv2.imread(scene["photo_path"])
    truth = json.loads(
        (tmp_path / lighting / "ground_truth.json").read_text(encoding="utf-8")
    )["grains"]

    contours = segment_grains(image)
    measured = [
        measure_grain(contour, image, scene["mm_per_pixel"])
        for contour in contours
    ]

    assert abs(len(measured) - len(truth)) / len(truth) <= 0.05
    detected_centers = np.array([grain["center"] for grain in measured])
    true_centers = np.array([grain["center"] for grain in truth])
    distances = np.linalg.norm(
        detected_centers[:, None, :] - true_centers[None, :, :], axis=2
    )
    candidate_matches = sorted(
        (distances[detected_id, true_id], detected_id, true_id)
        for detected_id in range(len(measured))
        for true_id in range(len(truth))
    )
    matched_detected = set()
    matched_truth = set()
    absolute_length_errors = []
    for _, detected_id, true_id in candidate_matches:
        if detected_id in matched_detected or true_id in matched_truth:
            continue
        matched_detected.add(detected_id)
        matched_truth.add(true_id)
        absolute_length_errors.append(
            abs(measured[detected_id]["length_mm"] - truth[true_id]["true_length_mm"])
        )
    assert np.mean(absolute_length_errors) < 0.3


def test_measurement_includes_rgb_and_chalk_fraction():
    image = np.zeros((40, 60, 3), dtype=np.uint8)
    cv2.ellipse(image, (30, 20), (16, 5), 0, 0, 360, (245, 245, 245), -1)
    contours = segment_grains(image)

    assert len(contours) == 1
    result = measure_grain(contours[0], image, mm_per_pixel=0.1)
    assert set(result) == {
        "length_mm",
        "width_mm",
        "lb_ratio",
        "bbox",
        "center",
        "mean_rgb",
        "chalk_fraction",
    }
    assert result["mean_rgb"] == pytest.approx([245, 245, 245], abs=1)
    assert 0 <= result["chalk_fraction"] <= 1


def test_segmentation_ignores_card_and_tiny_blobs_and_sorts_left_to_right():
    image = np.full((160, 1280, 3), 22, dtype=np.uint8)
    for center_x in (500, 100, 300):
        cv2.ellipse(image, (center_x, 80), (24, 7), 20, 0, 360, (225, 220, 205), -1)
    cv2.circle(image, (600, 40), 2, (255, 255, 255), -1)
    cv2.rectangle(image, (970, 0), (1279, 159), (255, 255, 255), -1)

    contours = segment_grains(image)
    centers_x = [cv2.minAreaRect(contour)[0][0] for contour in contours]

    assert len(contours) == 3
    assert centers_x == sorted(centers_x)


def test_analyze_image_returns_contract_keys_and_saves_annotation(tmp_path):
    scene = make_scene(seed=17, n_grains=24, lighting="ring_light", out_dir=str(tmp_path))
    result = analyze_image(scene["photo_path"], scene["mm_per_pixel"], str(tmp_path / "out"))

    assert set(result) == {"annotated_image_path", "grains"}
    assert cv2.imread(result["annotated_image_path"]) is not None
    for grain in result["grains"]:
        assert set(grain) == {
            "id",
            "bbox",
            "center",
            "length_mm",
            "width_mm",
            "lb_ratio",
            "mean_rgb",
            "chalk_fraction",
            "label",
            "confidence",
            "low_confidence",
            "reason",
        }
        assert grain["label"] in {"good", "broken", "chalky", "discolored"}
        assert grain["low_confidence"] == (grain["confidence"] < 0.6)
        assert grain["reason"]


def test_analyze_image_handles_zero_grains(tmp_path):
    scene = make_scene(seed=17, n_grains=0, lighting="ring_light", out_dir=str(tmp_path))

    result = analyze_image(scene["photo_path"], scene["mm_per_pixel"], str(tmp_path / "empty"))

    assert result["grains"] == []
    assert cv2.imread(result["annotated_image_path"]) is not None


def test_classifier_uses_documented_feature_order():
    from backend.grain_analysis import feature_vector

    measured = {
        "length_mm": 5.9,
        "width_mm": 1.9,
        "lb_ratio": 3.1,
        "chalk_fraction": 0.2,
        "mean_rgb": [210, 220, 230],
    }

    assert len(FEATURE_NAMES) == 8
    assert feature_vector(measured, saturation=12.0) == [
        5.9, 1.9, 3.1, 0.2, 210.0, 220.0, 230.0, 12.0
    ]
