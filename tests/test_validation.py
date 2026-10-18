import json

import pytest

from validation.validate import LABELS, compare


def _write_truth(tmp_path, grains):
    path = tmp_path / "ground_truth.json"
    path.write_text(json.dumps({"grains": grains}), encoding="utf-8")
    return path


def _truth(label, center, length=6.0, width=2.0):
    return {
        "label": label,
        "center": center,
        "true_length_mm": length,
        "true_width_mm": width,
    }


def _prediction(label, center, length=6.0, width=2.0):
    return {
        "label": label,
        "center": center,
        "length_mm": length,
        "width_mm": width,
    }


def test_compare_hand_calculable_matches_and_classification(tmp_path):
    path = _write_truth(
        tmp_path,
        [
            _truth("good", [0, 0], length=6, width=2),
            _truth("broken", [30, 0], length=4, width=1),
            _truth("chalky", [60, 0], length=5, width=2),
        ],
    )
    predictions = [
        _prediction("good", [1, 0], length=7, width=2.5),
        _prediction("good", [31, 0], length=3, width=1.5),
        _prediction("discolored", [61, 0], length=6, width=1),
    ]

    result = compare(path, predictions)

    assert result["labels"] == LABELS
    assert result["confusion_matrix"] == [
        [1, 0, 0, 0],
        [1, 0, 0, 0],
        [0, 0, 0, 1],
        [0, 0, 0, 0],
    ]
    assert result["accuracy"] == pytest.approx(1 / 3)
    assert result["per_class"] == {
        "good": {"precision": pytest.approx(1 / 2), "recall": 1.0},
        "broken": {"precision": 0.0, "recall": 0.0},
        "chalky": {"precision": 0.0, "recall": 0.0},
        "discolored": {"precision": 0.0, "recall": 0.0},
    }
    assert result["missed_grains"] == 0
    assert result["extra_grains"] == 0
    assert result["length_mae_mm"] == pytest.approx(1.0)
    assert result["width_mae_mm"] == pytest.approx(2 / 3)


def test_compare_counts_missed_and_extra_and_excludes_unmatched_errors(tmp_path):
    path = _write_truth(
        tmp_path,
        [
            _truth("good", [0, 0]),
            _truth("broken", [100, 0]),
        ],
    )
    predictions = [
        _prediction("good", [19.99, 0], length=8, width=4),
        _prediction("chalky", [19.99, 0]),
        _prediction("broken", [120, 0]),
    ]

    result = compare(path, predictions)

    assert result["accuracy"] == pytest.approx(0.5)
    assert result["confusion_matrix"][0][0] == 1
    assert result["missed_grains"] == 1
    assert result["extra_grains"] == 2
    assert result["per_class"]["chalky"]["precision"] == 0
    assert result["per_class"]["broken"]["recall"] == 0
    assert result["length_mae_mm"] == pytest.approx(2)
    assert result["width_mae_mm"] == pytest.approx(2)


def test_match_distance_is_strictly_less_than_twenty_pixels(tmp_path):
    path = _write_truth(tmp_path, [_truth("good", [0, 0])])

    result = compare(path, [_prediction("good", [20, 0])])

    assert result["missed_grains"] == 1
    assert result["extra_grains"] == 1
    assert result["accuracy"] == 0
    assert result["length_mae_mm"] is None
    assert result["width_mae_mm"] is None


def test_empty_grain_lists_return_zero_accuracy_and_empty_matrix(tmp_path):
    path = _write_truth(tmp_path, [])

    result = compare(path, [])

    assert result["accuracy"] == 0
    assert result["confusion_matrix"] == [[0, 0, 0, 0] for _ in LABELS]
    assert result["missed_grains"] == 0
    assert result["extra_grains"] == 0
    assert result["length_mae_mm"] is None
    assert result["width_mae_mm"] is None


def test_compare_rejects_an_unknown_label(tmp_path):
    path = _write_truth(tmp_path, [_truth("other", [0, 0])])

    with pytest.raises(ValueError, match="Unknown grain label"):
        compare(path, [])
