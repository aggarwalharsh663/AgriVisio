"""Generate simulated rice-grain photos and ground truth."""

import json
from pathlib import Path

import cv2
import numpy as np


W, H = 1280, 960
MM_PER_PIXEL = 0.12

CARD_COLORS = [
    (255, 255, 255),
    (200, 200, 200),
    (128, 128, 128),
    (30, 30, 30),
    (200, 30, 30),
    (30, 160, 50),
    (30, 60, 200),
    (230, 210, 40),
    (40, 190, 200),
    (190, 50, 160),
    (230, 120, 30),
    (110, 70, 40),
]

LIGHTING = {
    "ring_light": {"gain": (1.00, 1.00, 1.00), "gamma": 1.00, "bright": 0, "noise": 3},
    "daylight": {"gain": (0.92, 1.00, 1.12), "gamma": 1.10, "bright": -10, "noise": 4},
    "tube_light": {"gain": (0.85, 1.05, 0.90), "gamma": 0.90, "bright": 10, "noise": 5},
}

BASE_COLOR = {
    "good": (232, 226, 205),
    "broken": (228, 222, 200),
    "chalky": (250, 249, 244),
    "discolored": (205, 170, 105),
}


_yy, _xx = np.mgrid[0:H, 0:W]
_VIGNETTE = (
    1.0 - 0.25 * (((_xx - W / 2) / (W / 2)) ** 2 + ((_yy - H / 2) / (H / 2)) ** 2)
)[..., None].astype(np.float32)


def _draw_grain(img, cx, cy, length, width, angle, label, rng):
    color = np.array(BASE_COLOR[label], dtype=np.float32)
    color += rng.normal(0, 4, 3)
    pad = int(np.ceil(max(length, width) / 2 + max(width / 4, 1) * 3 + 2))
    x0 = max(0, int(cx) - pad)
    x1 = min(W, int(cx) + pad + 1)
    y0 = max(0, int(cy) - pad)
    y1 = min(H, int(cy) + pad + 1)

    sub_w = x1 - x0
    sub_h = y1 - y0
    local_cx = int(cx) - x0
    local_cy = int(cy) - y0

    mask = np.zeros((sub_h, sub_w), np.uint8)
    cv2.ellipse(
        mask,
        (local_cx, local_cy),
        (int(length / 2), int(width / 2)),
        angle,
        0,
        360,
        255,
        -1,
    )
    dist = cv2.GaussianBlur(mask, (0, 0), max(width / 4, 1)).astype(np.float32) / 255
    shade = 0.80 + 0.25 * dist
    layer = np.empty((sub_h, sub_w, 3), np.float32)
    for channel in range(3):
        layer[..., channel] = color[channel] * shade
    if label == "chalky":
        cv2.ellipse(
            layer,
            (local_cx, local_cy),
            (int(length / 3), int(width / 3.2)),
            angle,
            0,
            360,
            (255, 255, 252),
            -1,
        )
    hit = mask > 0
    img_sub = img[y0:y1, x0:x1]
    img_sub[hit] = layer[hit]


def _build_scene(n_grains, rng):
    img = np.full((H, W, 3), 22, np.float32)
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
        cx = rng.uniform(70, W - 340)
        cy = rng.uniform(50, H - 50)
        angle = rng.uniform(0, 180)
        radius = length / 2 + 6
        if any(
            (cx - px) ** 2 + (cy - py) ** 2 < (radius + other_radius) ** 2
            for px, py, other_radius in placed
        ):
            continue
        placed.append((cx, cy, radius))
        _draw_grain(img, cx, cy, length, width, angle, label, rng)
        grains.append(
            {
                "id": len(grains) + 1,
                "label": label,
                "true_length_mm": round(float(length_mm), 2),
                "true_width_mm": round(float(width_mm), 2),
                "center": [round(float(cx), 1), round(float(cy), 1)],
            }
        )
    return img, grains


def _draw_card(img):
    patches = []
    x0, y0, size, gap = W - 310, 80, 90, 8
    for index, color in enumerate(CARD_COLORS):
        row, column = divmod(index, 3)
        x = x0 + column * (size + gap)
        y = y0 + row * (size + gap)
        img[y : y + size, x : x + size] = color
        patches.append([x, y, size])
    return patches


def _apply_lighting(ideal, params, rng):
    img = ideal / 255.0
    img = img * np.array(params["gain"], np.float32)[None, None, :]
    gamma = params["gamma"]
    if gamma != 1.0:
        img = np.clip(img, 0, 1) ** gamma
    else:
        img = np.clip(img, 0, 1)
    img = img * _VIGNETTE
    img = img * 255 + (params["bright"] + rng.normal(0, params["noise"], img.shape))
    return np.clip(img, 0, 255).astype(np.uint8)


def make_scene(seed: int, n_grains: int, lighting: str, out_dir: str) -> dict:
    """Save one simulated scene and return its image and ground-truth metadata."""
    if lighting not in LIGHTING:
        raise ValueError(f"lighting must be one of: {', '.join(LIGHTING)}")
    if n_grains < 0:
        raise ValueError("n_grains must be non-negative")

    output_dir = Path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    ideal, grains = _build_scene(n_grains, rng)
    card_patches = _draw_card(ideal)
    photo = _apply_lighting(ideal, LIGHTING[lighting], np.random.default_rng(seed + 7))

    photo_path = output_dir / "photo.jpg"
    if not cv2.imwrite(str(photo_path), cv2.cvtColor(photo, cv2.COLOR_RGB2BGR)):
        raise OSError(f"Could not write simulated photo to {photo_path}")
    ground_truth_path = output_dir / "ground_truth.json"
    ground_truth_path.write_text(
        json.dumps({"grains": grains}, indent=2),
        encoding="utf-8",
    )
    return {
        "photo_path": str(photo_path),
        "ground_truth_path": str(ground_truth_path),
        "mm_per_pixel": MM_PER_PIXEL,
        "card_patches": card_patches,
        "card_true_rgb": [list(color) for color in CARD_COLORS],
    }