import hashlib
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from statistics import mean, pstdev
from uuid import uuid4

import cv2
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.calibration import calibrate
from backend.simulator import make_scene


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
_batch_lock = threading.Lock()


class ScanRequest(BaseModel):
    lighting: str = Field(pattern="^(ring_light|daylight|tube_light)$")
    n_grains: int = Field(ge=8, le=100)
    seed: int | None = None
    sample_name: str = Field(min_length=1, max_length=80)
    variety: str = Field(min_length=1, max_length=80)


def _next_batch_id() -> str:
    date_part = datetime.now().strftime("%Y%m%d")
    with _batch_lock:
        existing = [
            path.name
            for path in (DATA_DIR / "batches").glob(f"AS-{date_part}-*")
        ]
        sequence = max(
            (int(name.rsplit("-", 1)[-1]) for name in existing), default=0
        ) + 1
        return f"AS-{date_part}-{sequence:03d}"


def _mock_grains(ground_truth: dict) -> list[dict]:
    grains = []
    for source in ground_truth["grains"]:
        length = float(source["true_length_mm"])
        width = float(source["true_width_mm"])
        label = source["label"]
        grains.append(
            {
                "id": source["id"],
                "bbox": [0, 0, 0, 0],
                "center": source["center"],
                "length_mm": length,
                "width_mm": width,
                "lb_ratio": round(length / max(width, 0.01), 2),
                "mean_rgb": [228, 221, 201],
                "chalk_fraction": 0.65 if label == "chalky" else 0.04,
                "label": label,
                "confidence": 0.96,
                "low_confidence": False,
                "reason": "Simulated reference label used in mock mode.",
            }
        )
    return grains


def _make_batch(request: ScanRequest) -> dict:
    batch_id = _next_batch_id()
    batch_dir = DATA_DIR / "batches" / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    scene = make_scene(
        seed=(
            request.seed
            if request.seed is not None
            else int(uuid4().int % 2_000_000_000)
        ),
        n_grains=request.n_grains,
        lighting=request.lighting,
        out_dir=str(batch_dir),
    )
    truth = json.loads(
        Path(scene["ground_truth_path"]).read_text(encoding="utf-8")
    )
    calibrated = calibrate(
        scene["photo_path"],
        scene["card_patches"],
        scene["card_true_rgb"],
        str(batch_dir / "calibrated.jpg"),
    )
    annotated = cv2.imread(calibrated["corrected_path"])
    grains = _mock_grains(truth)
    label_colors = {
        "good": (80, 170, 90),
        "broken": (45, 145, 220),
        "chalky": (210, 135, 85),
        "discolored": (90, 95, 205),
    }
    for grain in grains:
        x, y = (int(value) for value in grain["center"])
        cv2.circle(annotated, (x, y), 20, label_colors[grain["label"]], 3)
    cv2.imwrite(str(batch_dir / "annotated.jpg"), annotated)

    labels = ["good", "broken", "chalky", "discolored"]
    counts = {
        label: sum(grain["label"] == label for grain in grains)
        for label in labels
    }
    total = len(grains)
    lengths = [grain["length_mm"] for grain in grains]
    widths = [grain["width_mm"] for grain in grains]
    percent = {
        label: round(count / total * 100, 1) if total else 0
        for label, count in counts.items()
    }
    base_url = f"/files/batches/{batch_id}"
    vector = [percent[label] / 100 for label in labels]
    batch = {
        "batch_id": batch_id,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sample_name": request.sample_name.strip(),
        "variety": request.variety.strip(),
        "lighting": request.lighting,
        "simulated": True,
        "images": {
            "raw": f"{base_url}/photo.jpg",
            "calibrated": f"{base_url}/calibrated.jpg",
            "annotated": f"{base_url}/annotated.jpg",
        },
        "calibration": {
            "patch_error_before": calibrated["patch_error_before"],
            "patch_error_after": calibrated["patch_error_after"],
        },
        "grains": grains,
        "summary": {
            "total_grains": total,
            "counts": counts,
            "percent": percent,
            "mean_length_mm": round(mean(lengths), 2) if lengths else 0,
            "std_length_mm": round(pstdev(lengths), 2) if len(lengths) > 1 else 0,
            "mean_width_mm": round(mean(widths), 2) if widths else 0,
            "mean_lb_ratio": round(mean(g["lb_ratio"] for g in grains), 2)
            if grains
            else 0,
            "mean_confidence": 0.96 if grains else 0,
            "low_confidence_count": 0,
        },
        "fingerprint": {
            "vector": vector,
            "hash": hashlib.sha256(json.dumps(vector).encode()).hexdigest(),
        },
        "validation": {
            "accuracy": 1.0 if grains else 0,
            "per_class": {
                label: {
                    "precision": 1.0 if counts[label] else 0,
                    "recall": 1.0 if counts[label] else 0,
                }
                for label in labels
            },
            "confusion_matrix": [
                [counts[label] if row == column else 0 for column, _ in enumerate(labels)]
                for row, label in enumerate(labels)
            ],
            "labels": labels,
            "length_mae_mm": 0,
        },
    }
    (batch_dir / "batch.json").write_text(json.dumps(batch, indent=2), encoding="utf-8")
    return batch

app = FastAPI(title="Agri Scan (SIMULATED HARDWARE DEMO)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/scan")
def scan(request: ScanRequest) -> dict:
    if os.getenv("MOCK", "0") != "1":
        raise HTTPException(
            status_code=503,
            detail="Mock mode is not enabled. Set MOCK=1 to run the simulated demo.",
        )
    try:
        return _make_batch(request)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"The simulated scan could not be completed: {exc}",
        ) from exc


@app.get("/api/batches")
def batches() -> list[dict]:
    results = []
    for path in sorted(
        (DATA_DIR / "batches").glob("AS-*/batch.json"), reverse=True
    ):
        try:
            results.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return results


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str) -> dict:
    path = DATA_DIR / "batches" / batch_id / "batch.json"
    if not path.is_file() or Path(batch_id).name != batch_id:
        raise HTTPException(status_code=404, detail="Batch not found")
    return json.loads(path.read_text(encoding="utf-8"))


app.mount("/files", StaticFiles(directory=DATA_DIR), name="files")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
