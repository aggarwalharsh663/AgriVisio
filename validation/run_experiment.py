"""Run repeatable simulated scans through the production API and summarize results."""

import os
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from fastapi.testclient import TestClient

from backend import main as backend_main


SEEDS = (42, 43, 44, 45, 46)
LIGHTINGS = ("ring_light", "daylight", "tube_light")
N_GRAINS = 80
LABELS = ("good", "broken", "chalky", "discolored")
HIGH_ACCURACY_THRESHOLD = 0.9
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_scans() -> list[dict]:
    """Call the real scan endpoint while keeping generated files temporary."""
    rows = []
    with tempfile.TemporaryDirectory(prefix="agriscan_experiment_") as temp_dir:
        temp_root = Path(temp_dir)
        original_data_dir = backend_main.DATA_DIR
        original_database_path = backend_main.DATABASE_PATH
        original_mock_setting = os.environ.get("MOCK")
        backend_main.DATA_DIR = temp_root / "data"
        backend_main.DATA_DIR.mkdir()
        backend_main.DATABASE_PATH = temp_root / "agriscan.sqlite3"
        os.environ["MOCK"] = "0"

        try:
            with TestClient(backend_main.app) as client:
                for seed in SEEDS:
                    for lighting in LIGHTINGS:
                        response = client.post(
                            "/api/scan",
                            headers={"X-AgriScan-Token": backend_main.get_api_token()},
                            json={
                                "lighting": lighting,
                                "n_grains": N_GRAINS,
                                "seed": seed,
                                "sample_name": f"Experiment seed {seed}",
                                "variety": "Simulated experiment",
                            },
                        )
                        if response.status_code != 200:
                            raise RuntimeError(
                                "Scan failed for "
                                f"seed={seed}, lighting={lighting}: "
                                f"{response.status_code} {response.text}"
                            )

                        batch = response.json()
                        validation = batch["validation"]
                        row = {
                            "seed": seed,
                            "lighting": lighting,
                            "accuracy": validation["accuracy"],
                            "length_mae_mm": validation["length_mae_mm"],
                        }
                        for label in LABELS:
                            row[f"{label}_precision"] = validation["per_class"][
                                label
                            ]["precision"]
                            row[f"{label}_recall"] = validation["per_class"][
                                label
                            ]["recall"]
                        rows.append(row)
        finally:
            backend_main.DATA_DIR = original_data_dir
            backend_main.DATABASE_PATH = original_database_path
            if original_mock_setting is None:
                os.environ.pop("MOCK", None)
            else:
                os.environ["MOCK"] = original_mock_setting

    if len(rows) != len(SEEDS) * len(LIGHTINGS):
        raise RuntimeError(f"Expected 15 completed scans, got {len(rows)}")
    return rows


def _save_charts(results: pd.DataFrame, output_dir: Path) -> None:
    accuracy = results.groupby("lighting")["accuracy"].agg(["mean", "std"])
    accuracy["std"] = accuracy["std"].fillna(0)

    figure, axis = plt.subplots(figsize=(8, 5))
    axis.bar(
        accuracy.index,
        accuracy["mean"],
        yerr=accuracy["std"],
        capsize=5,
        color=["#5b8c5a", "#d5a84b", "#7696b3"],
    )
    axis.set(
        title="Simulated scan accuracy by lighting",
        xlabel="Simulated lighting",
        ylabel="Accuracy (mean ± sample standard deviation)",
        ylim=(0, 1.05),
    )
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    figure.savefig(output_dir / "accuracy_by_lighting.png", dpi=150)
    plt.close(figure)

    recall_columns = [f"{label}_recall" for label in LABELS]
    recall = results.groupby("lighting")[recall_columns].mean().reindex(LIGHTINGS)
    figure, axis = plt.subplots(figsize=(9, 5))
    recall.T.plot(kind="bar", ax=axis)
    axis.set(
        title="Simulated per-class recall by lighting",
        xlabel="Actual grain class",
        ylabel="Mean recall across five seeds",
        ylim=(0, 1.05),
    )
    axis.set_xticklabels(LABELS, rotation=0)
    axis.legend(title="Simulated lighting")
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    figure.savefig(output_dir / "per_class_recall.png", dpi=150)
    plt.close(figure)


def main() -> None:
    results_dir = PROJECT_ROOT / "validation"
    results_dir.mkdir(parents=True, exist_ok=True)
    data_dir = PROJECT_ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    results = pd.DataFrame(_run_scans())
    results_path = results_dir / "results.csv"
    results.to_csv(results_path, index=False, float_format="%.17g")
    _save_charts(results, data_dir)

    print("Experiment results (all 15 simulated scans):")
    print(
        results[["seed", "lighting", "accuracy", "length_mae_mm"]]
        .to_string(index=False, float_format=lambda value: f"{value:.17g}")
    )

    summary_metrics = [
        "accuracy",
        *(f"{label}_{metric}" for label in LABELS for metric in ("precision", "recall")),
        "length_mae_mm",
    ]
    summary_rows = []
    for lighting in LIGHTINGS:
        lighting_results = results.loc[results["lighting"] == lighting]
        for metric in summary_metrics:
            values = lighting_results[metric]
            summary_rows.append(
                {
                    "lighting": lighting,
                    "metric": metric,
                    "mean": values.mean(),
                    "std": values.std(ddof=0),
                }
            )
    summary = pd.DataFrame(summary_rows)
    print("\nMean and population standard deviation across five seeds:")
    print(
        summary.to_string(
            index=False, float_format=lambda value: f"{value:.17g}"
        )
    )

    if results["accuracy"].mean() >= HIGH_ACCURACY_THRESHOLD:
        print(
            "\nNote: Accuracy is very high; simulated data can be easier to "
            "classify than real-world grain samples."
        )
    print(f"\nSaved results: {results_path}")
    print(f"Saved charts: {data_dir / 'accuracy_by_lighting.png'}")
    print(f"             {data_dir / 'per_class_recall.png'}")


if __name__ == "__main__":
    main()
