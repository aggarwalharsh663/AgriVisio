"""Compare simulated lighting drift before and after colour calibration."""

import sys
from pathlib import Path

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.calibration import calibrate
from backend.simulator import make_scene


LIGHTINGS = ("ring_light", "daylight", "tube_light")
SEED = 42
N_GRAINS = 120


def _read_rgb(image_path: str) -> np.ndarray:
    image_bgr = cv2.imread(image_path)
    if image_bgr is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")
    return cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)


def _grain_mask(raw_rgb: np.ndarray) -> np.ndarray:
    # The simulator keeps the colour card on the right and the tray dark.
    grain_area = raw_rgb[:, : raw_rgb.shape[1] - 330]
    return np.max(grain_area, axis=2) > 65


def main() -> None:
    output_dir = PROJECT_ROOT / "data"
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_images = []
    corrected_images = []
    before_means = []
    after_means = []

    for lighting in LIGHTINGS:
        scene_dir = output_dir / "lighting_test" / lighting
        scene = make_scene(
            seed=SEED,
            n_grains=N_GRAINS,
            lighting=lighting,
            out_dir=str(scene_dir),
        )
        corrected = calibrate(
            photo_path=scene["photo_path"],
            card_patches=scene["card_patches"],
            card_true_rgb=scene["card_true_rgb"],
            out_path=str(scene_dir / "corrected.png"),
        )

        raw_rgb = _read_rgb(scene["photo_path"])
        corrected_rgb = _read_rgb(corrected["corrected_path"])
        mask = _grain_mask(raw_rgb)
        if not np.any(mask):
            raise ValueError(f"No simulated grain pixels found for {lighting}")

        raw_images.append(raw_rgb)
        corrected_images.append(corrected_rgb)
        before_means.append(raw_rgb[:, : mask.shape[1]][mask].mean(axis=0))
        after_means.append(corrected_rgb[:, : mask.shape[1]][mask].mean(axis=0))

    before_std = np.std(np.asarray(before_means), axis=0)
    after_std = np.std(np.asarray(after_means), axis=0)

    figure, axis = plt.subplots(figsize=(8, 5))
    positions = np.arange(3)
    bar_width = 0.36
    axis.bar(positions - bar_width / 2, before_std, bar_width, label="Before calibration")
    axis.bar(positions + bar_width / 2, after_std, bar_width, label="After calibration")
    axis.set_xticks(positions, ("Red", "Green", "Blue"))
    axis.set_ylabel("Standard deviation across lightings (RGB levels)")
    axis.set_title("Lighting drift (SIMULATED)")
    axis.legend()
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    figure.savefig(output_dir / "lighting_chart.png", dpi=150)
    plt.close(figure)

    figure, axes = plt.subplots(2, len(LIGHTINGS), figsize=(12, 6))
    for column, lighting in enumerate(LIGHTINGS):
        axes[0, column].imshow(raw_images[column])
        axes[0, column].set_title(f"{lighting.replace('_', ' ').title()} - raw")
        axes[1, column].imshow(corrected_images[column])
        axes[1, column].set_title(f"{lighting.replace('_', ' ').title()} - corrected")
    for axis in axes.flat:
        axis.axis("off")
    figure.suptitle("Simulated lighting comparison")
    figure.tight_layout()
    figure.savefig(output_dir / "lighting_grid.png", dpi=120)
    plt.close(figure)

    print("Lighting test complete.")
    print(f"Chart: {output_dir / 'lighting_chart.png'}")
    print(f"Grid:  {output_dir / 'lighting_grid.png'}")
    print(f"RGB standard deviation before: {before_std.round(2).tolist()}")
    print(f"RGB standard deviation after:  {after_std.round(2).tolist()}")


if __name__ == "__main__":
    main()
