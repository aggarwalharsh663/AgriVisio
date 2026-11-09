"""Reproducible evaluation and validation script for AgriScan grain classification model.

Evaluates the trained model against simulated ground-truth scenes across standard
lighting presets, producing class-level precision, recall, confusion matrices,
and length error metrics. Reconciles the four agronomic classes:
good, broken, chalky, discolored.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.grain_analysis import (
    MODEL_PATH,
    analyze_image,
    verify_model_integrity,
    _load_model,
)
from backend.simulator import make_scene
from backend.calibration import calibrate
from validation.validate import compare, LABELS


def run_model_validation(
    seeds: list[int] | None = None,
    lightings: list[str] | None = None,
    n_grains: int = 60,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Execute reproducible model validation across seeds and lighting environments."""
    # 1. Verify model integrity first
    print("Verifying model integrity against manifest...")
    manifest = verify_model_integrity()
    print(f"Model integrity verified: SHA-256 = {manifest.get('sha256')[:16]}...")

    model = _load_model()
    classes = getattr(model, "classes_", LABELS)
    print(f"Loaded classifier classes ({len(classes)}): {list(classes)}")

    seeds = seeds or [42, 43, 44]
    lightings = lightings or ["ring_light", "daylight", "tube_light"]
    eval_dir = output_dir or (PROJECT_ROOT / "data" / "validation_run")
    eval_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []

    for seed in seeds:
        for lighting in lightings:
            scene_dir = eval_dir / f"scene_{seed}_{lighting}"
            scene_dir.mkdir(parents=True, exist_ok=True)

            # Generate synthetic chamber scene
            scene = make_scene(
                seed=seed,
                n_grains=n_grains,
                lighting=lighting,
                out_dir=str(scene_dir),
            )

            # Calibrate
            calibrated_path = str(scene_dir / "calibrated.jpg")
            calibrate(
                photo_path=scene["photo_path"],
                card_patches=scene["card_patches"],
                card_true_rgb=scene["card_true_rgb"],
                out_path=calibrated_path,
            )

            # Analyze
            analysis = analyze_image(
                image_path=calibrated_path,
                mm_per_pixel=scene["mm_per_pixel"],
                output_dir=str(scene_dir),
            )

            # Validate against ground truth
            comparison = compare(
                ground_truth_json_path=scene["ground_truth_path"],
                grains=analysis["grains"],
            )

            results.append({
                "seed": seed,
                "lighting": lighting,
                "accuracy": comparison["accuracy"],
                "length_mae_mm": comparison["length_mae_mm"],
                "per_class": comparison["per_class"],
                "confusion_matrix": comparison["confusion_matrix"],
            })

    # Aggregate metrics
    accuracies = [r["accuracy"] for r in results]
    mean_accuracy = float(np.mean(accuracies))
    std_accuracy = float(np.std(accuracies))
    mean_mae = float(np.mean([r["length_mae_mm"] for r in results if r["length_mae_mm"] is not None]))

    # Per-class summary
    per_class_summary = {}
    for lbl in LABELS:
        precisions = [r["per_class"][lbl]["precision"] for r in results if r["per_class"][lbl]["precision"] is not None]
        recalls = [r["per_class"][lbl]["recall"] for r in results if r["per_class"][lbl]["recall"] is not None]
        per_class_summary[lbl] = {
            "mean_precision": float(np.mean(precisions)) if precisions else 0.0,
            "mean_recall": float(np.mean(recalls)) if recalls else 0.0,
        }

    summary = {
        "model_sha256": manifest.get("sha256"),
        "classes": list(classes),
        "total_scans": len(results),
        "mean_accuracy": round(mean_accuracy, 4),
        "std_accuracy": round(std_accuracy, 4),
        "mean_length_mae_mm": round(mean_mae, 4),
        "per_class": per_class_summary,
    }

    print("\n" + "=" * 60)
    print("AGRISCAN MODEL VALIDATION SUMMARY")
    print("=" * 60)
    print(f"Evaluated Scans:      {summary['total_scans']}")
    print(f"Recognized Classes:   {', '.join(summary['classes'])} (4 classes)")
    print(f"Overall Mean Accuracy:{summary['mean_accuracy']:.1%} (+/- {summary['std_accuracy']:.1%})")
    print(f"Mean Length MAE:      {summary['mean_length_mae_mm']:.3f} mm")
    print("\nPer-Class Breakdown:")
    for lbl, m in per_class_summary.items():
        print(f"  - {lbl:12s}: Precision = {m['mean_precision']:.1%}, Recall = {m['mean_recall']:.1%}")
    print("=" * 60)

    # Save summary report
    summary_file = eval_dir / "validation_summary.json"
    summary_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Validation summary saved to {summary_file}")

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AgriScan Model Validation")
    parser.add_argument("--grains", type=int, default=50, help="Grains per scan")
    args = parser.parse_args()
    run_model_validation(n_grains=args.grains)
