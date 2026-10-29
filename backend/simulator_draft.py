"""
Agri Scan: Hardware Simulator (SIMULATED imaging stage)

Stands in for the camera + lighting box until real hardware is ready.
It generates synthetic top-down photos of rice grains on a dark tray plus a
printed reference card, photographed under different lighting conditions.
It also knows the TRUE label of every grain, so the pipeline can be tested.

Usage:
    python agriscan_hardware_simulator.py generate  --out sim_data --grains 120 --seed 1
    python agriscan_hardware_simulator.py calib-demo --out sim_data

IMPORTANT (be honest in the demo): everything produced here is SYNTHETIC.
Do not report accuracy measured on it as real-world accuracy, and do not
train the final model only on it.
"""
import argparse
import json
import os

import cv2
import numpy as np

W, H = 1280, 960                 # image size in pixels
MM_PER_PIXEL = 0.12              # simulated camera scale (known ground truth)

# 12 reference patches (same colours as the printed card in the reference specification)
CARD_COLORS = [
    (255, 255, 255), (200, 200, 200), (128, 128, 128), (30, 30, 30),
    (200, 30, 30), (30, 160, 50), (30, 60, 200), (230, 210, 40),
    (40, 190, 200), (190, 50, 160), (230, 120, 30), (110, 70, 40),
]

# Lighting presets: per-channel gain (R,G,B), gamma, brightness, noise sigma
LIGHTING = {
    "ring_light": dict(gain=(1.00, 1.00, 1.00), gamma=1.00, bright=0, noise=3),
    "daylight":   dict(gain=(0.92, 1.00, 1.12), gamma=1.10, bright=-10, noise=4),
    "tube_light": dict(gain=(0.85, 1.05, 0.90), gamma=0.90, bright=10, noise=5),
}

# Base grain appearance (RGB) per class
BASE_COLOR = {
    "good": (232, 226, 205),
    "broken": (228, 222, 200),
    "chalky": (250, 249, 244),
    "discolored": (205, 170, 105),
}


def draw_grain(img, truth_img, cx, cy, length, width, angle, label, rng):
    """Draw one rice grain (ellipse with shading) and record its true label."""
    color = np.array(BASE_COLOR[label], dtype=np.float32)
    color += rng.normal(0, 4, 3)
    layer = np.zeros((H, W, 3), np.float32)
    mask = np.zeros((H, W), np.uint8)
    cv2.ellipse(mask, (int(cx), int(cy)), (int(length / 2), int(width / 2)),
                angle, 0, 360, 255, -1)
    # soft shading: brighter in the middle, darker at the edges
    dist = cv2.GaussianBlur(mask, (0, 0), max(width / 4, 1)).astype(np.float32) / 255
    shade = 0.80 + 0.25 * dist
    for c in range(3):
        layer[..., c] = color[c] * shade
    if label == "chalky":   # opaque white patch in the middle
        cv2.ellipse(layer, (int(cx), int(cy)),
                    (int(length / 3), int(width / 3.2)), angle, 0, 360,
                    (255, 255, 252), -1)
    m = mask > 0
    img[m] = layer[m]
    cv2.ellipse(truth_img, (int(cx), int(cy)), (int(length / 2), int(width / 2)),
                angle, 0, 360, int(truth_img.max() + 1), -1)
    return mask


def build_scene(n_grains, rng):
    """Ideal (perfectly lit) scene. Returns image, truth list."""
    img = np.full((H, W, 3), 22, np.float32)            # dark tray
    truth_img = np.zeros((H, W), np.int32)
    grains = []
    placed = []
    classes = ["good"] * 70 + ["broken"] * 12 + ["chalky"] * 10 + ["discolored"] * 8
    tries = 0
    while len(grains) < n_grains and tries < n_grains * 200:
        tries += 1
        label = classes[rng.integers(len(classes))]
        length_mm = rng.normal(5.9, 0.35)
        width_mm = rng.normal(1.9, 0.12)
        if label == "broken":
            length_mm *= rng.uniform(0.35, 0.65)
        length = length_mm / MM_PER_PIXEL
        width = width_mm / MM_PER_PIXEL
        cx = rng.uniform(70, W - 340)       # leave right strip for the card
        cy = rng.uniform(50, H - 50)
        angle = rng.uniform(0, 180)
        r = length / 2 + 6
        if any((cx - px) ** 2 + (cy - py) ** 2 < (r + pr) ** 2 for px, py, pr in placed):
            continue                          # keep grains from overlapping
        placed.append((cx, cy, r))
        draw_grain(img, truth_img, cx, cy, length, width, angle, label, rng)
        grains.append(dict(id=len(grains) + 1, label=label,
                           true_length_mm=round(float(length_mm), 2),
                           true_width_mm=round(float(width_mm), 2),
                           center=[round(float(cx), 1), round(float(cy), 1)]))
    return img, grains


def draw_card(img):
    """Reference card: 12 patches in a 3x4 grid on the right of the image."""
    patches = []
    x0, y0, s, gap = W - 310, 80, 90, 8
    for i, col in enumerate(CARD_COLORS):
        r, c = divmod(i, 3)
        x, y = x0 + c * (s + gap), y0 + r * (s + gap)
        img[y:y + s, x:x + s] = col
        patches.append((x, y, s))
    return patches


def apply_lighting(ideal, params, rng):
    """Simulate a different lighting condition + camera noise."""
    img = ideal / 255.0
    img = img * np.array(params["gain"], np.float32)[None, None, :]
    img = np.clip(img, 0, 1) ** params["gamma"]
    # vignette: darker corners
    yy, xx = np.mgrid[0:H, 0:W]
    v = 1 - 0.25 * (((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
    img = img * v[..., None]
    img = img * 255 + params["bright"] + rng.normal(0, params["noise"], img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)


def generate(out, n_grains, seed):
    os.makedirs(out, exist_ok=True)
    rng = np.random.default_rng(seed)
    ideal, grains = build_scene(n_grains, rng)
    patches = draw_card(ideal)
    meta = dict(mm_per_pixel=MM_PER_PIXEL, grains=grains,
                card_patches=patches, card_true_rgb=CARD_COLORS,
                note="SYNTHETIC DATA from agriscan_hardware_simulator.py")
    for name, params in LIGHTING.items():
        photo = apply_lighting(ideal, params, np.random.default_rng(seed + 7))
        cv2.imwrite(os.path.join(out, f"rice_{name}.jpg"),
                    cv2.cvtColor(photo, cv2.COLOR_RGB2BGR))
    with open(os.path.join(out, "ground_truth.json"), "w") as f:
        json.dump(meta, f, indent=2)
    counts = {k: sum(g["label"] == k for g in grains) for k in BASE_COLOR}
    print(f"Saved 3 simulated photos + ground_truth.json to {out}/")
    print(f"Grains placed: {len(grains)}  {counts}")


# ---------------- self-calibration demo (simple, for the simulator) -------
def patch_means(img_rgb, patches):
    return np.array([img_rgb[y + 15:y + s - 15, x + 15:x + s - 15].reshape(-1, 3).mean(0)
                     for x, y, s in patches])


def _features(rgb):
    """RGB plus a constant (bias) term, so the fit can also remove an offset."""
    rgb = np.asarray(rgb, np.float32).reshape(-1, 3)
    return np.hstack([rgb, np.ones((len(rgb), 1), np.float32)])


def fit_correction(measured, true):
    """Least-squares 4x3 affine colour correction: measured RGB -> true RGB."""
    M, *_ = np.linalg.lstsq(_features(measured), true, rcond=None)
    return M


def rice_mean_rgb(img_rgb):
    """Mean RGB of the rice (bright pixels) on the left side, excluding the card."""
    left = img_rgb[:, : W - 330].astype(np.float32)
    gray = left.mean(2)
    m = gray > 100
    return left[m].mean(0)


def calib_demo(out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    meta = json.load(open(os.path.join(out, "ground_truth.json")))
    patches, true = meta["card_patches"], np.array(meta["card_true_rgb"], np.float32)
    before, after = {}, {}
    for name in LIGHTING:
        bgr = cv2.imread(os.path.join(out, f"rice_{name}.jpg"))
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
        M = fit_correction(patch_means(rgb, patches), true)
        corrected = np.clip(_features(rgb) @ M, 0, 255).reshape(rgb.shape)
        before[name] = rice_mean_rgb(rgb)
        after[name] = rice_mean_rgb(corrected)
        cv2.imwrite(os.path.join(out, f"corrected_{name}.jpg"),
                    cv2.cvtColor(corrected.astype(np.uint8), cv2.COLOR_RGB2BGR))
    sd_b = np.std(list(before.values()), axis=0)
    sd_a = np.std(list(after.values()), axis=0)
    fig, ax = plt.subplots(figsize=(6, 4))
    x = np.arange(3)
    ax.bar(x - 0.18, sd_b, 0.36, label="Without calibration", color="#c0504d")
    ax.bar(x + 0.18, sd_a, 0.36, label="With calibration", color="#2e7d32")
    ax.set_xticks(x, ["Red", "Green", "Blue"])
    ax.set_ylabel("Std. dev. of rice colour across 3 lightings")
    ax.set_title("Lighting drift of measured rice colour (SIMULATED)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(out, "calibration_robustness.png"), dpi=150)
    print("Std dev across lightings (R,G,B):")
    print("  before:", np.round(sd_b, 2))
    print("  after: ", np.round(sd_a, 2))
    print(f"Saved chart + corrected photos in {out}/")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["generate", "calib-demo"])
    ap.add_argument("--out", default="sim_data")
    ap.add_argument("--grains", type=int, default=120)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    generate(a.out, a.grains, a.seed) if a.cmd == "generate" else calib_demo(a.out)
