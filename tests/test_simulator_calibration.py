import json

import cv2
import pytest

from backend.calibration import calibrate
from backend.simulator import LIGHTING, make_scene


@pytest.mark.parametrize("lighting", LIGHTING)
def test_scene_files_ground_truth_and_calibration_improve_patches(tmp_path, lighting):
    scene = make_scene(seed=17, n_grains=24, lighting=lighting, out_dir=str(tmp_path))

    photo_path = tmp_path / "photo.jpg"
    ground_truth_path = tmp_path / "ground_truth.json"
    corrected_path = tmp_path / "corrected.jpg"
    assert photo_path.is_file()
    assert ground_truth_path.is_file()
    assert cv2.imread(str(photo_path)) is not None

    ground_truth = json.loads(ground_truth_path.read_text(encoding="utf-8"))
    assert set(ground_truth) == {"grains"}
    assert len(ground_truth["grains"]) == 24
    for grain in ground_truth["grains"]:
        assert set(grain) == {
            "id",
            "label",
            "true_length_mm",
            "true_width_mm",
            "center",
        }
        assert grain["label"] in {"good", "broken", "chalky", "discolored"}

    result = calibrate(
        scene["photo_path"],
        scene["card_patches"],
        scene["card_true_rgb"],
        str(corrected_path),
    )
    assert corrected_path.is_file()
    assert cv2.imread(str(corrected_path)) is not None
    assert result["patch_error_after"] < result["patch_error_before"]
