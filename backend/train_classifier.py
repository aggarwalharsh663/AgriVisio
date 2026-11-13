"""Train and evaluate the grain classifier on grouped simulated scenes."""

import argparse
import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv2
import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import GroupShuffleSplit

from backend.calibration import calibrate
from backend.grain_analysis import (
    FEATURE_NAMES,
    _measure_grain_details,
    feature_vector,
    segment_grains,
)
from backend.simulator import LIGHTING, make_scene


LABELS = ["good", "broken", "chalky", "discolored"]
MAX_MATCH_DISTANCE_PX = 30.0


def _match_scene(
    image: np.ndarray,
    ground_truth: list[dict],
    mm_per_pixel: float,
) -> tuple[list[list[float]], list[str]]:
    """Match detected contours to unique nearest ground-truth centres."""
    detections = [
        _measure_grain_details(contour, image, mm_per_pixel)
        for contour in segment_grains(image)
    ]
    if not detections or not ground_truth:
        return [], []

    detected_centres = np.asarray(
        [measurement["center"] for measurement, _ in detections], dtype=float
    )
    true_centres = np.asarray(
        [grain["center"] for grain in ground_truth], dtype=float
    )
    distances = np.linalg.norm(
        detected_centres[:, None, :] - true_centres[None, :, :], axis=2
    )
    candidate_matches = sorted(
        (float(distances[detection_id, truth_id]), detection_id, truth_id)
        for detection_id in range(len(detections))
        for truth_id in range(len(ground_truth))
    )

    used_detections = set()
    used_truth = set()
    features = []
    labels = []
    for distance, detection_id, truth_id in candidate_matches:
        if distance > MAX_MATCH_DISTANCE_PX:
            break
        if detection_id in used_detections or truth_id in used_truth:
            continue
        used_detections.add(detection_id)
        used_truth.add(truth_id)
        measurement, saturation = detections[detection_id]
        features.append(feature_vector(measurement, saturation))
        labels.append(ground_truth[truth_id]["label"])
    return features, labels


def _collect_training_data(
    scene_count: int, grains_per_scene: int, first_seed: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    feature_rows = []
    labels = []
    groups = []

    with tempfile.TemporaryDirectory(prefix="agriscan-training-") as temporary:
        for seed_offset in range(scene_count):
            seed = first_seed + seed_offset
            for lighting in LIGHTING:
                scene_dir = Path(temporary) / f"seed-{seed}" / lighting
                scene = make_scene(
                    seed=seed,
                    n_grains=grains_per_scene,
                    lighting=lighting,
                    out_dir=str(scene_dir),
                )
                calibrated_path = scene_dir / "calibrated.jpg"
                calibrate(
                    scene["photo_path"],
                    scene["card_patches"],
                    scene["card_true_rgb"],
                    str(calibrated_path),
                )
                image = cv2.imread(str(calibrated_path))
                if image is None:
                    raise OSError(f"Could not read calibrated scene: {calibrated_path}")
                ground_truth = json.loads(
                    Path(scene["ground_truth_path"]).read_text(encoding="utf-8")
                )["grains"]
                scene_features, scene_labels = _match_scene(
                    image, ground_truth, scene["mm_per_pixel"]
                )
                feature_rows.extend(scene_features)
                labels.extend(scene_labels)
                groups.extend([seed] * len(scene_labels))
                print(
                    f"Collected seed {seed}, {lighting}: "
                    f"{len(scene_labels)}/{len(ground_truth)} matched grains"
                )

    return (
        np.asarray(feature_rows, dtype=np.float32),
        np.asarray(labels),
        np.asarray(groups),
    )


def train_classifier(
    scene_count: int = 20,
    grains_per_scene: int = 80,
    seed: int = 1729,
    model_path: Path | None = None,
) -> dict:
    """Train on 20 distinct seeds across all lighting modes and report holdout metrics."""
    if scene_count < 5:
        raise ValueError("scene_count must be at least 5 for a scene-grouped split")
    if grains_per_scene <= 0:
        raise ValueError("grains_per_scene must be positive")

    features, labels, groups = _collect_training_data(
        scene_count, grains_per_scene, seed
    )
    if len(labels) == 0:
        raise ValueError("No grains were matched; classifier training cannot continue")
    if set(labels) != set(LABELS):
        raise ValueError(f"Training data is missing classes: {set(LABELS) - set(labels)}")

    splitter = GroupShuffleSplit(
        n_splits=1, test_size=0.2, random_state=seed
    )
    train_indices, test_indices = next(
        splitter.split(features, labels, groups=groups)
    )
    training_model = RandomForestClassifier(
        n_estimators=300,
        class_weight="balanced_subsample",
        min_samples_leaf=2,
        random_state=seed,
        n_jobs=-1,
    )
    training_model.fit(features[train_indices], labels[train_indices])
    predictions = training_model.predict(features[test_indices])
    accuracy = float(accuracy_score(labels[test_indices], predictions))
    report = classification_report(
        labels[test_indices],
        predictions,
        labels=LABELS,
        output_dict=True,
        zero_division=0,
    )
    matrix = confusion_matrix(labels[test_indices], predictions, labels=LABELS)
    print(f"Scenes: {scene_count * len(LIGHTING)} ({scene_count} seeds × all lightings)")
    print(f"Matched training grains: {len(labels)}")
    print(f"Held-out scene-group accuracy (simulated data): {accuracy:.4f}")
    print("Per-class precision / recall (simulated data):")
    for label in LABELS:
        print(
            f"  {label}: precision={report[label]['precision']:.4f}, "
            f"recall={report[label]['recall']:.4f}"
        )
    print(f"Confusion matrix (labels={LABELS}):\n{matrix}")

    final_model = RandomForestClassifier(
        n_estimators=300,
        class_weight="balanced_subsample",
        min_samples_leaf=2,
        random_state=seed,
        n_jobs=-1,
    )
    final_model.fit(features, labels)
    destination = model_path or Path(__file__).with_name("model.joblib")
    destination.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": final_model,
            "feature_names": list(FEATURE_NAMES),
            "training_scenes": scene_count * len(LIGHTING),
            "training_seeds": scene_count,
        },
        destination,
    )
    print(f"Saved trained classifier to {destination}")
    return {
        "accuracy": accuracy,
        "report": report,
        "confusion_matrix": matrix,
        "labels": LABELS,
        "model_path": destination,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenes", type=int, default=20, help="Number of distinct seeds")
    parser.add_argument("--grains-per-scene", type=int, default=80)
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("model.joblib"))
    args = parser.parse_args()
    train_classifier(
        scene_count=args.scenes,
        grains_per_scene=args.grains_per_scene,
        seed=args.seed,
        model_path=args.output,
    )


if __name__ == "__main__":
    main()
