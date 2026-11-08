"""Compare predicted grain labels and measurements with simulated ground truth."""

import json
import math
import sys
from pathlib import Path


LABELS = ["good", "broken", "chalky", "discolored"]
MATCH_DISTANCE_PX = 20.0


def compare(ground_truth_json_path, grains):
    """Compare predictions to one-to-one nearest ground-truth centre matches.

    Missed grains count as incorrect for accuracy and as false negatives for
    recall. Extra predictions count as false positives for precision. Measurement
    errors are averaged over matched pairs only; when there are no matches, they
    are ``None`` because a mean cannot be calculated.
    """
    with open(ground_truth_json_path, encoding="utf-8") as ground_truth_file:
        ground_truth = json.load(ground_truth_file)["grains"]

    for grain in ground_truth:
        _validate_grain(grain, truth=True)
    for grain in grains:
        _validate_grain(grain, truth=False)

    # Prefer nearby truth centres while allowing earlier choices to be reassigned.
    candidates = [[] for _ in grains]
    for predicted_index, prediction in enumerate(grains):
        for truth_index, truth in enumerate(ground_truth):
            distance = math.dist(prediction["center"], truth["center"])
            if distance < MATCH_DISTANCE_PX:
                candidates[predicted_index].append((distance, truth_index))
        candidates[predicted_index].sort()

    truth_to_prediction = {}

    def assign(predicted_index, visited_truth):
        for _, truth_index in candidates[predicted_index]:
            if truth_index in visited_truth:
                continue
            visited_truth.add(truth_index)
            previous_prediction = truth_to_prediction.get(truth_index)
            if previous_prediction is None or assign(
                previous_prediction, visited_truth
            ):
                truth_to_prediction[truth_index] = predicted_index
                return True
        return False

    for predicted_index in range(len(grains)):
        assign(predicted_index, set())

    matched_pairs = [
        (grains[predicted_index], ground_truth[truth_index])
        for truth_index, predicted_index in sorted(truth_to_prediction.items())
    ]
    matched_predictions = {
        predicted_index for predicted_index in truth_to_prediction.values()
    }
    matched_truth = set(truth_to_prediction)

    confusion = [[0 for _ in LABELS] for _ in LABELS]
    for prediction, truth in matched_pairs:
        actual_index = LABELS.index(truth["label"])
        predicted_index = LABELS.index(prediction["label"])
        confusion[actual_index][predicted_index] += 1

    true_positives = {
        label: confusion[index][index] for index, label in enumerate(LABELS)
    }
    predicted_counts = {label: 0 for label in LABELS}
    truth_counts = {label: 0 for label in LABELS}
    for prediction in grains:
        predicted_counts[prediction["label"]] += 1
    for truth in ground_truth:
        truth_counts[truth["label"]] += 1

    per_class = {}
    for label in LABELS:
        true_positive = true_positives[label]
        predicted = predicted_counts[label]
        actual = truth_counts[label]
        per_class[label] = {
            "precision": true_positive / predicted if predicted else 0.0,
            "recall": true_positive / actual if actual else 0.0,
        }

    correct = sum(true_positives.values())
    length_errors = [
        abs(prediction["length_mm"] - truth["true_length_mm"])
        for prediction, truth in matched_pairs
    ]
    width_errors = [
        abs(prediction["width_mm"] - truth["true_width_mm"])
        for prediction, truth in matched_pairs
    ]

    return {
        "accuracy": correct / len(ground_truth) if ground_truth else 0.0,
        "per_class": per_class,
        "confusion_matrix": confusion,
        "labels": LABELS.copy(),
        "missed_grains": len(ground_truth) - len(matched_truth),
        "extra_grains": len(grains) - len(matched_predictions),
        "length_mae_mm": (
            sum(length_errors) / len(length_errors) if length_errors else None
        ),
        "width_mae_mm": sum(width_errors) / len(width_errors) if width_errors else None,
    }


def _validate_grain(grain, truth):
    """Raise a clear error when a grain is missing required contract data."""
    label_key = "label"
    center_key = "center"
    length_key = "true_length_mm" if truth else "length_mm"
    width_key = "true_width_mm" if truth else "width_mm"
    required = (label_key, center_key, length_key, width_key)
    for key in required:
        if key not in grain:
            raise ValueError(f"Grain is missing required field: {key}")
    if grain[label_key] not in LABELS:
        raise ValueError(f"Unknown grain label: {grain[label_key]}")
    center = grain[center_key]
    if (
        not isinstance(center, (list, tuple))
        or len(center) != 2
        or not all(isinstance(value, (int, float)) for value in center)
    ):
        raise ValueError("Grain center must contain two numeric coordinates")
    for key in (length_key, width_key):
        if not isinstance(grain[key], (int, float)):
            raise ValueError(f"Grain field {key} must be numeric")


def main():
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from backend.grain_analysis import analyze_image
    from backend.simulator import make_scene

    output_dir = project_root / "data" / "validation"
    output_dir.mkdir(parents=True, exist_ok=True)
    scene = make_scene(
        seed=42,
        n_grains=80,
        lighting="ring_light",
        out_dir=str(output_dir),
    )
    analysis = analyze_image(
        scene["photo_path"],
        scene["mm_per_pixel"],
        str(output_dir / "analysis"),
    )
    results = compare(scene["ground_truth_path"], analysis["grains"])

    print("Per-class precision and recall")
    print(f"{'Label':<12} {'Precision':>10} {'Recall':>10}")
    for label in LABELS:
        metrics = results["per_class"][label]
        print(f"{label:<12} {metrics['precision']:>10.3f} {metrics['recall']:>10.3f}")
    print(f"Accuracy: {results['accuracy']:.3f}")
    print(
        f"Missed grains: {results['missed_grains']}; "
        f"extra grains: {results['extra_grains']}"
    )
    print(
        f"Length MAE (mm): {results['length_mae_mm']}; "
        f"width MAE (mm): {results['width_mae_mm']}"
    )
    print("Confusion matrix (rows: actual, columns: predicted):")
    print(f"{'':<12}" + "".join(f"{label:>12}" for label in LABELS))
    for label, row in zip(LABELS, results["confusion_matrix"]):
        print(f"{label:<12}" + "".join(f"{count:>12}" for count in row))

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axis = plt.subplots(figsize=(7, 6))
    image = axis.imshow(results["confusion_matrix"], cmap="Blues")
    axis.set(
        xticks=range(len(LABELS)),
        xticklabels=LABELS,
        yticks=range(len(LABELS)),
        yticklabels=LABELS,
        xlabel="Predicted label",
        ylabel="Actual label",
        title="Grain classification confusion matrix (SIMULATED)",
    )
    plt.setp(axis.get_xticklabels(), rotation=30, ha="right", rotation_mode="anchor")
    for row_index, row in enumerate(results["confusion_matrix"]):
        for column_index, count in enumerate(row):
            axis.text(column_index, row_index, str(count), ha="center", va="center")
    figure.colorbar(image, ax=axis)
    figure.tight_layout()
    chart_path = output_dir / "confusion_matrix.png"
    figure.savefig(chart_path, dpi=150)
    plt.close(figure)
    print(f"Saved confusion matrix: {chart_path}")


if __name__ == "__main__":
    main()
