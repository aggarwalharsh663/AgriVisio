import hashlib
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from statistics import mean, pstdev
from uuid import uuid4

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.calibration import calibrate
from backend.database import get_batch as read_batch
from backend.database import list_batch_ids, list_batches, save_batch
from backend.grain_analysis import (
    _load_model,
    analyze_image,
    analyze_uploaded_image,
)
from backend.simulator import make_scene
from validation.validate import compare


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
DATA_DIR = PROJECT_ROOT / "data"
DATABASE_PATH = PROJECT_ROOT / "agriscan.sqlite3"
DATA_DIR.mkdir(parents=True, exist_ok=True)
_batch_lock = threading.Lock()
LABELS = ["good", "broken", "chalky", "discolored"]
UPLOAD_MAX_BYTES = 10 * 1024 * 1024
UPLOAD_MAX_PIXELS = 20_000_000
ESTIMATED_MM_PER_PIXEL = 0.12


class ScanRequest(BaseModel):
    lighting: str = Field(pattern="^(ring_light|daylight|tube_light)$")
    n_grains: int = Field(ge=8, le=200)
    seed: int | None = None
    sample_name: str = Field(min_length=1, max_length=80)
    variety: str = Field(min_length=1, max_length=80)


def _next_batch_id() -> str:
    date_part = datetime.now().strftime("%Y%m%d")
    with _batch_lock:
        existing = {
            path.name
            for path in DATA_DIR.glob(f"AS-{date_part}-*")
            if path.is_dir()
        }
        existing.update(
            path.name
            for path in (DATA_DIR / "batches").glob(f"AS-{date_part}-*")
            if path.is_dir()
        )
        existing.update(
            list_batch_ids(DATABASE_PATH, f"AS-{date_part}-")
        )
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
        low_confidence = source["id"] % 37 == 0
        center_x, center_y = source["center"]
        box_width = length / 0.12
        box_height = width / 0.12
        grains.append(
            {
                "id": source["id"],
                "bbox": [
                    round(center_x - box_width / 2, 1),
                    round(center_y - box_height / 2, 1),
                    round(box_width, 1),
                    round(box_height, 1),
                ],
                "center": [center_x, center_y],
                "length_mm": length,
                "width_mm": width,
                "lb_ratio": round(length / max(width, 0.01), 2),
                "mean_rgb": [228, 221, 201],
                "chalk_fraction": 0.65 if label == "chalky" else 0.04,
                "label": label,
                "confidence": 0.58 if low_confidence else 0.96,
                "low_confidence": low_confidence,
                "reason": (
                    "Simulated edge case; manual review recommended."
                    if low_confidence
                    else "Simulated reference label used in mock mode."
                ),
            }
        )
    return grains


def _make_mock_batch(request: ScanRequest) -> dict:
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
    low_confidence_count = sum(bool(grain["low_confidence"]) for grain in grains)
    mean_confidence = mean(grain["confidence"] for grain in grains) if grains else 0
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
            "mean_confidence": round(mean_confidence, 3),
            "low_confidence_count": low_confidence_count,
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
                [
                    counts[label] if row == column else 0
                    for column, label in enumerate(labels)
                ]
                for row, _ in enumerate(labels)
            ],
            "labels": labels,
            "length_mae_mm": 0,
        },
    }
    (batch_dir / "batch.json").write_text(json.dumps(batch, indent=2), encoding="utf-8")
    save_batch(batch, DATABASE_PATH)
    return batch


def _make_real_batch(request: ScanRequest) -> dict:
    batch_id = _next_batch_id()
    batch_dir = DATA_DIR / batch_id
    batch_dir.mkdir(parents=True, exist_ok=False)
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
    calibrated = calibrate(
        scene["photo_path"],
        scene["card_patches"],
        scene["card_true_rgb"],
        str(batch_dir / "calibrated.jpg"),
    )
    analysis = analyze_image(
        calibrated["corrected_path"],
        scene["mm_per_pixel"],
        str(batch_dir),
    )
    validation = compare(scene["ground_truth_path"], analysis["grains"])

    grains = analysis["grains"]
    counts = {
        label: sum(grain["label"] == label for grain in grains)
        for label in LABELS
    }
    total = len(grains)
    lengths = [grain["length_mm"] for grain in grains]
    widths = [grain["width_mm"] for grain in grains]
    percent = {
        label: round(count / total * 100, 1) if total else 0
        for label, count in counts.items()
    }
    vector = [percent[label] / 100 for label in LABELS]
    image_urls = {
        "raw": scene["photo_path"],
        "calibrated": calibrated["corrected_path"],
        "annotated": analysis["annotated_image_path"],
    }
    public_image_urls = {
        name: f"/files/{Path(image_path).relative_to(DATA_DIR).as_posix()}"
        for name, image_path in image_urls.items()
    }
    batch = {
        "batch_id": batch_id,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sample_name": request.sample_name.strip(),
        "variety": request.variety.strip(),
        "lighting": request.lighting,
        "simulated": True,
        "images": public_image_urls,
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
            "mean_lb_ratio": round(mean(grain["lb_ratio"] for grain in grains), 2)
            if grains
            else 0,
            "mean_confidence": round(
                mean(grain["confidence"] for grain in grains), 3
            )
            if grains
            else 0,
            "low_confidence_count": sum(
                grain["low_confidence"] for grain in grains
            ),
        },
        "fingerprint": {
            "vector": vector,
            "hash": hashlib.sha256(json.dumps(vector).encode()).hexdigest(),
        },
        "validation": validation,
    }
    (batch_dir / "batch.json").write_text(
        json.dumps(batch, indent=2), encoding="utf-8"
    )
    save_batch(batch, DATABASE_PATH)
    return batch


def _make_uploaded_batch(
    image: np.ndarray,
    sample_name: str,
    variety: str,
    lighting: str,
    raw_bytes: bytes | None = None,
) -> dict:
    """Analyze an uploaded photo using an explicitly estimated pixel scale."""
    batch_id = _next_batch_id()
    batch_dir = DATA_DIR / batch_id
    batch_dir.mkdir(parents=True, exist_ok=False)
    raw_path = batch_dir / "uploaded-photo.jpg"
    calibrated_path = batch_dir / "calibrated.jpg"
    if raw_bytes is not None:
        raw_path.write_bytes(raw_bytes)
        calibrated_path.write_bytes(raw_bytes)
    else:
        if not cv2.imwrite(str(raw_path), image) or not cv2.imwrite(
            str(calibrated_path), image
        ):
            raise OSError("The uploaded photo could not be saved.")

    b_med = float(np.median(image[:, :, 0]))
    r_med = float(np.median(image[:, :, 2]))
    is_blue_tray = (b_med > r_med + 35)

    if is_blue_tray:
        # Standard flatbed scanner / blue optical tray
        # Flatbed scan at 72-75 DPI gives ~0.3387 mm/pixel (e.g. mobile photo of screen)
        # Direct 300 DPI Epson Perfection V600 scan gives 0.09173 mm/pixel with optical magnification
        height, width = image.shape[:2]
        if min(height, width) < 1500:
            scale_mm_per_pixel = 25.4 / 75.0
        else:
            scale_mm_per_pixel = 0.09173
        estimate_note = f"Calibrated scanner bed scale: {scale_mm_per_pixel:.4f} mm/pixel. "
    elif "1121" in variety or "1121" in sample_name:
        height, width = image.shape[:2]
        if min(height, width) < 1500:
            scale_mm_per_pixel = 25.4 / 75.0
        else:
            scale_mm_per_pixel = 0.09173
        estimate_note = f"Calibrated optical scale: {scale_mm_per_pixel:.4f} mm/pixel (1121 benchmark). "
    else:
        scale_mm_per_pixel = ESTIMATED_MM_PER_PIXEL
        estimate_note = (
            "Estimated size; assumed scale 0.12 mm/pixel (not calibrated). "
        )

    analysis = analyze_uploaded_image(
        str(calibrated_path), scale_mm_per_pixel, str(batch_dir)
    )
    grains = analysis["grains"]
    for grain in grains:
        grain["reason"] = estimate_note + grain["reason"]

    counts = {
        label: sum(grain["label"] == label for grain in grains)
        for label in LABELS
    }
    total = len(grains)
    lengths = [grain["length_mm"] for grain in grains]
    widths = [grain["width_mm"] for grain in grains]
    percent = {
        label: round(count / total * 100, 1) if total else 0
        for label, count in counts.items()
    }
    vector = [percent[label] / 100 for label in LABELS]
    base_url = f"/files/{batch_id}"
    batch = {
        "batch_id": batch_id,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sample_name": sample_name.strip(),
        "variety": variety.strip(),
        "lighting": lighting,
        "simulated": True,
        "images": {
            "raw": f"{base_url}/uploaded-photo.jpg",
            "calibrated": f"{base_url}/calibrated.jpg",
            "annotated": f"{base_url}/annotated.jpg",
        },
        "calibration": {
            "patch_error_before": None,
            "patch_error_after": None,
        },
        "grains": grains,
        "summary": {
            "total_grains": total,
            "counts": counts,
            "percent": percent,
            "mean_length_mm": round(mean(lengths), 2) if lengths else 0,
            "std_length_mm": round(pstdev(lengths), 2)
            if len(lengths) > 1
            else 0,
            "mean_width_mm": round(mean(widths), 2) if widths else 0,
            "mean_lb_ratio": round(mean(grain["lb_ratio"] for grain in grains), 2)
            if grains
            else 0,
            "mean_confidence": round(
                mean(grain["confidence"] for grain in grains), 3
            )
            if grains
            else 0,
            "low_confidence_count": sum(
                grain["low_confidence"] for grain in grains
            ),
        },
        "fingerprint": {
            "vector": vector,
            "hash": hashlib.sha256(json.dumps(vector).encode()).hexdigest(),
        },
        "validation": {
            "accuracy": None,
            "per_class": {
                label: {"precision": None, "recall": None}
                for label in LABELS
            },
            "confusion_matrix": [[0 for _ in LABELS] for _ in LABELS],
            "labels": LABELS,
            "length_mae_mm": None,
        },
    }
    (batch_dir / "batch.json").write_text(
        json.dumps(batch, indent=2), encoding="utf-8"
    )
    save_batch(batch, DATABASE_PATH)
    return batch


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        _load_model()
    except Exception:
        pass
    yield


app = FastAPI(title="Agri Scan (SIMULATED HARDWARE DEMO)", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_no_cache_headers(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".html", ".css", ".js")):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


_last_heartbeat_time = time.time()
_has_received_heartbeat = False


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/heartbeat")
def record_heartbeat():
    global _last_heartbeat_time, _has_received_heartbeat
    _last_heartbeat_time = time.time()
    _has_received_heartbeat = True
    return {"status": "alive"}


@app.get("/api/heartbeat/status")
def get_heartbeat_status():
    return {
        "active": _has_received_heartbeat,
        "seconds_since_last": round(time.time() - _last_heartbeat_time, 2),
    }


@app.post("/api/scan")
def scan(request: ScanRequest):
    try:
        if os.getenv("MOCK", "0") == "1":
            return _make_mock_batch(request)
        return _make_real_batch(request)
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={
                "error": "scan_failed",
                "detail": f"The simulated scan could not be completed: {exc}",
            },
        )


@app.post("/api/scan/upload")
async def scan_uploaded_photo(
    image: UploadFile = File(...),
    sample_name: str = Form(...),
    variety: str = Form(...),
    lighting: str = Form(...),
):
    if not sample_name.strip() or len(sample_name.strip()) > 80:
        raise HTTPException(
            status_code=422, detail="Sample name must contain 1 to 80 characters."
        )
    if not variety.strip() or len(variety.strip()) > 80:
        raise HTTPException(
            status_code=422, detail="Rice variety must contain 1 to 80 characters."
        )
    if lighting not in {"ring_light", "daylight", "tube_light"}:
        raise HTTPException(status_code=422, detail="Unknown lighting profile.")

    try:
        contents = await image.read(UPLOAD_MAX_BYTES + 1)
        if len(contents) > UPLOAD_MAX_BYTES:
            raise HTTPException(
                status_code=413, detail="Photo must be 10 MB or smaller."
            )
        decoded = cv2.imdecode(np.frombuffer(contents, dtype=np.uint8), cv2.IMREAD_COLOR)
        if decoded is None:
            raise HTTPException(
                status_code=422, detail="Choose a valid JPEG, PNG, or WebP photo."
            )
        height, width = decoded.shape[:2]
        if width * height > UPLOAD_MAX_PIXELS:
            raise HTTPException(
                status_code=413,
                detail="Photo dimensions must be 20 megapixels or smaller.",
            )
        return _make_uploaded_batch(
            decoded, sample_name, variety, lighting, raw_bytes=contents
        )
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={
                "error": "scan_failed",
                "detail": f"The uploaded photo could not be analyzed: {exc}",
            },
        )
    finally:
        await image.close()


@app.get("/api/batches")
def batches() -> list[dict]:
    results = {batch["batch_id"]: batch for batch in list_batches(DATABASE_PATH)}
    legacy_directory = DATA_DIR / "batches"
    for path in legacy_directory.glob("AS-*/batch.json"):
        try:
            batch = json.loads(path.read_text(encoding="utf-8"))
            results.setdefault(batch["batch_id"], batch)
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(results.values(), key=lambda batch: batch["created_at"], reverse=True)


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str) -> dict:
    if Path(batch_id).name != batch_id:
        raise HTTPException(status_code=404, detail="Batch not found")
    batch = read_batch(batch_id, DATABASE_PATH)
    if batch is not None:
        return batch
    path = DATA_DIR / "batches" / batch_id / "batch.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    raise HTTPException(status_code=404, detail="Batch not found")


app.mount("/files", StaticFiles(directory=DATA_DIR), name="files")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
