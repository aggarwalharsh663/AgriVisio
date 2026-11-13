import asyncio
import hashlib
import json
import os
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from statistics import mean, pstdev
import logging
from typing import Any
from uuid import uuid4

import cv2
import numpy as np
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.calibration import calibrate
from backend.database import get_batch as read_batch
from backend.database import list_batch_ids, list_batches, save_batch
from backend.fingerprint import compute_batch_fingerprint
from backend.grain_analysis import (
    _load_model,
    analyze_image,
    analyze_uploaded_image,
)
from backend.simulator import make_scene
from validation.validate import compare


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOGGER = logging.getLogger("agriscan")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
DATA_DIR = PROJECT_ROOT / "data"
DATABASE_PATH = PROJECT_ROOT / "agriscan.sqlite3"
DATA_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "batches").mkdir(parents=True, exist_ok=True)

_batch_lock = threading.RLock()
LABELS = ["good", "broken", "chalky", "discolored"]
UPLOAD_MAX_BYTES = 10 * 1024 * 1024       # 10 MB limit
UPLOAD_MAX_PIXELS = 20_000_000            # 20 Megapixel limit
ESTIMATED_MM_PER_PIXEL = 0.12


# Bootstrap Secret Management (R-02 / S-01 / S-02 / T-01)
_bootstrap_lock = threading.RLock()
_bootstrap_secrets: dict[str, dict[str, Any]] = {}
BOOTSTRAP_SECRET_TTL = float(os.getenv("AGRIVISIO_BOOTSTRAP_TTL") or os.getenv("AGRISCAN_BOOTSTRAP_TTL", "300.0"))  # Default 300s (5m) for browser mode
_last_console_printed_secret: str | None = None


def print_bootstrap_banner(secret: str, ttl: float = BOOTSTRAP_SECRET_TTL, reason: str = "") -> None:
    """Print the single-use bootstrap secret and direct URL to the server console (T-01)."""
    host = os.getenv("AGRIVISIO_HOST") or os.getenv("AGRISCAN_HOST", "127.0.0.1")
    port = os.getenv("AGRIVISIO_PORT") or os.getenv("AGRISCAN_PORT", "8000")
    url = f"http://{host}:{port}/?bootstrap={secret}"
    banner = [
        "",
        "=" * 70,
        " AGRIVISIO / AGRISCAN BROWSER AUTHENTICATION (T-01)",
        "=" * 70,
        f" One-time bootstrap secret: {secret}",
        f" Direct login URL:          {url}",
        f" Valid for {int(ttl)}s (single-use, auto-refreshed upon expiry/use).",
        "=" * 70,
        "",
    ]
    print("\n".join(banner), flush=True)
    if reason:
        LOGGER.info("Bootstrap secret refreshed (%s).", reason)
    else:
        LOGGER.info("Bootstrap secret initialized.")


def _purge_expired_secrets_locked(now: float | None = None) -> list[str]:
    """Purge expired and consumed secrets from in-memory store (Additional 3 / T-01)."""
    current = now if now is not None else time.time()
    stale_keys = [
        k for k, rec in _bootstrap_secrets.items()
        if rec.get("consumed") or (current - rec.get("created_at", 0) > rec.get("ttl", BOOTSTRAP_SECRET_TTL))
    ]
    for k in stale_keys:
        del _bootstrap_secrets[k]
    return stale_keys


def _get_active_bootstrap_secret_locked(now: float | None = None) -> tuple[str, float] | None:
    """Internal unlocked search for active secret; caller must hold _bootstrap_lock."""
    current = now if now is not None else time.time()
    _purge_expired_secrets_locked(current)
    for secret, rec in _bootstrap_secrets.items():
        if not rec.get("consumed"):
            remaining = rec.get("ttl", BOOTSTRAP_SECRET_TTL) - (current - rec.get("created_at", 0))
            if remaining > 0:
                return secret, remaining
    return None


def get_active_bootstrap_secret(now: float | None = None) -> tuple[str, float] | None:
    """Return an active unexpired, unconsumed secret and its remaining TTL, if any."""
    with _bootstrap_lock:
        return _get_active_bootstrap_secret_locked(now)


def ensure_active_bootstrap_secret(ttl: float = BOOTSTRAP_SECRET_TTL, print_console: bool = True, force_print: bool = False, reason: str = "") -> str:
    """Ensure at least one valid, unexpired bootstrap secret exists; if not, generate, register and print fresh (T-01)."""
    global _last_console_printed_secret
    now = time.time()
    with _bootstrap_lock:
        active = _get_active_bootstrap_secret_locked(now)
        if active is not None:
            secret = active[0]
        else:
            secret = secrets.token_hex(32)
            _bootstrap_secrets[secret] = {
                "created_at": now,
                "ttl": float(ttl),
                "consumed": False,
            }
    if print_console and (force_print or secret != _last_console_printed_secret):
        _last_console_printed_secret = secret
        print_bootstrap_banner(secret, ttl=ttl, reason=reason)
    return secret


def register_bootstrap_secret(secret: str, ttl: float = BOOTSTRAP_SECRET_TTL) -> None:
    """Register a one-time single-use bootstrap secret with bounded lifecycle."""
    if not secret or not isinstance(secret, str):
        return
    with _bootstrap_lock:
        _purge_expired_secrets_locked()
        _bootstrap_secrets[secret.strip()] = {
            "created_at": time.time(),
            "ttl": float(ttl),
            "consumed": False,
        }


def create_one_time_bootstrap_secret(ttl: float = BOOTSTRAP_SECRET_TTL) -> str:
    """Generate and register a one-time single-use bootstrap secret."""
    secret = secrets.token_hex(32)
    register_bootstrap_secret(secret, ttl=ttl)
    return secret


def consume_bootstrap_secret(secret: str) -> bool:
    """Validate and atomically consume a one-time bootstrap secret (S-01 / Additional 3 / T-01)."""
    if not secret or not isinstance(secret, str):
        return False
    now = time.time()
    clean_secret = secret.strip()
    consumed_ok = False
    with _bootstrap_lock:
        _purge_expired_secrets_locked(now)
        record = _bootstrap_secrets.get(clean_secret)
        if not record or record.get("consumed"):
            return False
        if now - record.get("created_at", 0) > record.get("ttl", BOOTSTRAP_SECRET_TTL):
            del _bootstrap_secrets[clean_secret]
            consumed_ok = False
        else:
            # Atomically consume and purge to prevent any replay
            record["consumed"] = True
            del _bootstrap_secrets[clean_secret]
            consumed_ok = True

    # Re-register a fresh secret whenever the current one is consumed or expired (T-01)
    if consumed_ok:
        ensure_active_bootstrap_secret(print_console=True, reason="previous secret consumed")
        return True
    else:
        ensure_active_bootstrap_secret(print_console=True, reason="previous secret expired")
        return False


# Register initial bootstrap secret from environment if present
_initial_secret = os.getenv("AGRIVISIO_BOOTSTRAP_SECRET") or os.getenv("AGRISCAN_BOOTSTRAP_SECRET")
if _initial_secret:
    register_bootstrap_secret(_initial_secret.strip())

# Authentication secret management
AUTH_TOKEN_FILE = DATA_DIR / ".auth_token"


def get_or_create_api_token() -> str:
    """Retrieve or generate secure authentication token (S-07)."""
    env_token = os.getenv("AGRIVISIO_API_TOKEN") or os.getenv("AGRISCAN_API_TOKEN")
    if env_token:
        return env_token.strip()
    path = AUTH_TOKEN_FILE
    if path.is_file():
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        try:
            stored = path.read_text(encoding="utf-8").strip()
            if stored:
                return stored
        except OSError:
            pass
    token = secrets.token_hex(32)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(token, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        pass
    return token


async def verify_api_token(
    request: Request,
    x_agrivisio_token: str | None = Header(None, alias="X-AgriVisio-Token"),
    x_agriscan_token: str | None = Header(None, alias="X-AgriScan-Token"),
    authorization: str | None = Header(None),
) -> str:
    """Verify local API token on protected operations.

    Query-token (?token=...) authentication is strictly removed (R-02).
    Only header tokens (X-AgriVisio-Token / X-AgriScan-Token / Bearer) or HttpOnly session cookie accepted.
    """

    expected = get_or_create_api_token()

    header_token = x_agrivisio_token or x_agriscan_token
    if header_token and secrets.compare_digest(header_token.strip(), expected):
        return expected

    if authorization and authorization.startswith("Bearer "):
        bearer = authorization[len("Bearer "):].strip()
        if secrets.compare_digest(bearer, expected):
            return expected

    cookie_token = request.cookies.get("agrivisio_session") or request.cookies.get("agriscan_session")
    if cookie_token and secrets.compare_digest(cookie_token.strip(), expected):
        return expected

    raise HTTPException(
        status_code=401,
        detail="Unauthorized: Valid authentication token required. Provide X-AgriVisio-Token, X-AgriScan-Token, Bearer token, or session cookie.",
    )


# Rate Limiting
_rate_limit_lock = threading.Lock()
_rate_limit_records: dict[str, list[float]] = {}
RATE_LIMIT_MAX_REQUESTS = 60
RATE_LIMIT_WINDOW_SECONDS = 60.0


def check_rate_limit(request: Request) -> None:
    client_ip = request.client.host if request.client else "127.0.0.1"
    now = time.time()
    with _rate_limit_lock:
        cutoff = now - RATE_LIMIT_WINDOW_SECONDS
        if len(_rate_limit_records) > 500:
            stale_ips = [ip for ip, rec in _rate_limit_records.items() if not rec or rec[-1] <= cutoff]
            for ip in stale_ips:
                del _rate_limit_records[ip]

        history = _rate_limit_records.get(client_ip, [])
        history = [t for t in history if t > cutoff]
        if len(history) >= RATE_LIMIT_MAX_REQUESTS:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: maximum {RATE_LIMIT_MAX_REQUESTS} requests per minute.",
            )
        history.append(now)
        _rate_limit_records[client_ip] = history


class ScanRequest(BaseModel):
    lighting: str = Field(pattern="^(ring_light|daylight|tube_light)$")
    n_grains: int = Field(ge=8, le=200)
    seed: int | None = None
    sample_name: str = Field(min_length=1, max_length=80)
    variety: str = Field(min_length=1, max_length=80)


def _next_batch_id() -> str:
    """Defensively generate next batch ID, handling non-integer folders safely."""
    date_part = datetime.now().strftime("%Y%m%d")
    prefix = f"AS-{date_part}-"
    with _batch_lock:
        existing = set()
        if DATA_DIR.is_dir():
            for p in DATA_DIR.glob(f"{prefix}*"):
                if p.is_dir():
                    existing.add(p.name)
            batches_dir = DATA_DIR / "batches"
            if batches_dir.is_dir():
                for p in batches_dir.glob(f"{prefix}*"):
                    if p.is_dir():
                        existing.add(p.name)
        if DATABASE_PATH.is_file():
            try:
                existing.update(list_batch_ids(DATABASE_PATH, prefix))
            except Exception:
                pass

        sequences = []
        for name in existing:
            if not name.startswith(prefix):
                continue
            suffix = name[len(prefix):]
            if suffix.isdigit():
                sequences.append(int(suffix))
        sequence = max(sequences, default=0) + 1
        return f"{prefix}{sequence:03d}"


def _create_batch_directory() -> tuple[str, Path]:
    """Collision-safe batch directory creation."""
    with _batch_lock:
        for _ in range(50):
            batch_id = _next_batch_id()
            batch_dir = DATA_DIR / "batches" / batch_id
            if not batch_dir.exists():
                batch_dir.mkdir(parents=True, exist_ok=False)
                return batch_id, batch_dir
        batch_id = f"AS-{datetime.now().strftime('%Y%m%d')}-{uuid4().hex[:6]}"
        batch_dir = DATA_DIR / "batches" / batch_id
        batch_dir.mkdir(parents=True, exist_ok=True)
        return batch_id, batch_dir


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
                "reason": "SIMULATED MOCK DATA — Not a genuine physical measurement.",
            }
        )
    return grains


def is_mock_mode_enabled() -> bool:
    """Determine if mock mode is explicitly and safely permitted.

    Mock mode is strictly OFF by default (R-07). It is only permitted when
    AGRISCAN_ENV=development is explicitly configured AND MOCK=1.
    If AGRISCAN_ENV=production, mock mode is strictly prohibited.
    """
    env = (os.getenv("AGRIVISIO_ENV") or os.getenv("AGRISCAN_ENV", "")).strip().lower()
    mock_flag = os.getenv("MOCK", "0").strip().lower()
    if mock_flag in ("1", "true", "yes"):
        if env == "production":
            raise HTTPException(
                status_code=403,
                detail="Security Violation: Mock simulation mode is strictly prohibited in production environments.",
            )
        if env != "development":
            raise HTTPException(
                status_code=403,
                detail="Mock simulation mode requires explicit development environment (set AGRISCAN_ENV=development and MOCK=1).",
            )
        return True
    return False

def _make_mock_batch(request: ScanRequest) -> dict:
    # Enforce strict mock mode guard (R-07)
    if not is_mock_mode_enabled():
        raise HTTPException(
            status_code=403,
            detail="Mock simulation mode requires explicit development environment (set AGRISCAN_ENV=development and MOCK=1).",
        )

    batch_id, batch_dir = _create_batch_directory()
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

    counts = {
        label: sum(grain["label"] == label for grain in grains)
        for label in LABELS
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

    summary = {
        "total_grains": total,
        "counts": counts,
        "percent": percent,
        "mean_length_mm": round(mean(lengths), 2) if lengths else 0,
        "std_length_mm": round(pstdev(lengths), 2) if len(lengths) > 1 else 0,
        "mean_width_mm": round(mean(widths), 2) if widths else 0,
        "mean_lb_ratio": round(mean(g["lb_ratio"] for g in grains), 2) if grains else 0,
        "mean_confidence": round(mean_confidence, 3),
        "low_confidence_count": low_confidence_count,
    }

    calibration_meta = {
        "calibration_status": "SIMULATED",
        "mm_per_pixel": ESTIMATED_MM_PER_PIXEL,
        "patch_error_before": calibrated["patch_error_before"],
        "patch_error_after": calibrated["patch_error_after"],
    }

    fingerprint = compute_batch_fingerprint(
        counts=counts,
        summary=summary,
        calibration=calibration_meta,
        labels=LABELS,
    )

    batch = {
        "batch_id": batch_id,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sample_name": request.sample_name.strip(),
        "variety": request.variety.strip(),
        "lighting": request.lighting,
        "simulated": True,
        "is_mock": True,
        "measurement_status": "SIMULATED",
        "watermark": "MOCK DATA — NOT A REAL MEASUREMENT",
        "images": {
            "raw": f"{base_url}/photo.jpg",
            "calibrated": f"{base_url}/calibrated.jpg",
            "annotated": f"{base_url}/annotated.jpg",
        },
        "calibration": calibration_meta,
        "grains": grains,
        "summary": summary,
        "fingerprint": fingerprint,
        "validation": {
            "accuracy": None,
            "per_class": {
                label: {"precision": None, "recall": None}
                for label in LABELS
            },
            "confusion_matrix": [[0 for _ in LABELS] for _ in LABELS],
            "labels": LABELS,
            "length_mae_mm": None,
            "note": "MOCK DATA — NOT A REAL MEASUREMENT",
        },
    }
    (batch_dir / "batch.json").write_text(json.dumps(batch, indent=2), encoding="utf-8")
    save_batch(batch, DATABASE_PATH)
    return batch


def _make_real_batch(request: ScanRequest) -> dict:
    batch_id, batch_dir = _create_batch_directory()
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
    base_url = f"/files/batches/{batch_id}"
    public_image_urls = {
        "raw": f"{base_url}/photo.jpg",
        "calibrated": f"{base_url}/calibrated.jpg",
        "annotated": f"{base_url}/annotated.jpg",
    }

    summary = {
        "total_grains": total,
        "counts": counts,
        "percent": percent,
        "mean_length_mm": round(mean(lengths), 2) if lengths else 0,
        "std_length_mm": round(pstdev(lengths), 2) if len(lengths) > 1 else 0,
        "mean_width_mm": round(mean(widths), 2) if widths else 0,
        "mean_lb_ratio": round(mean(grain["lb_ratio"] for grain in grains), 2) if grains else 0,
        "mean_confidence": round(mean(grain["confidence"] for grain in grains), 3) if grains else 0,
        "low_confidence_count": sum(grain["low_confidence"] for grain in grains),
    }

    calibration_meta = {
        "calibration_status": "CHAMBER_CALIBRATED",
        "mm_per_pixel": round(scene["mm_per_pixel"], 5),
        "calibration_reference_type": "12_PATCH_COLOR_TARGET",
        "patch_error_before": calibrated["patch_error_before"],
        "patch_error_after": calibrated["patch_error_after"],
    }

    photo_bytes = Path(scene["photo_path"]).read_bytes() if Path(scene["photo_path"]).is_file() else None
    fingerprint = compute_batch_fingerprint(
        counts=counts,
        summary=summary,
        calibration=calibration_meta,
        image_bytes=photo_bytes,
        labels=LABELS,
    )

    batch = {
        "batch_id": batch_id,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sample_name": request.sample_name.strip(),
        "variety": request.variety.strip(),
        "lighting": request.lighting,
        "simulated": True,
        "is_mock": False,
        "measurement_status": "SIMULATED_CHAMBER",
        "images": public_image_urls,
        "calibration": calibration_meta,
        "grains": grains,
        "summary": summary,
        "fingerprint": fingerprint,
        "validation": validation,
    }
    (batch_dir / "batch.json").write_text(json.dumps(batch, indent=2), encoding="utf-8")
    save_batch(batch, DATABASE_PATH)
    return batch


def _make_uploaded_batch(
    image: np.ndarray,
    sample_name: str,
    variety: str,
    lighting: str,
    raw_bytes: bytes | None = None,
    calibration_mm_per_pixel: float | None = None,
    reference_length_mm: float | None = None,
    reference_pixels: float | None = None,
) -> dict:
    """Analyze an uploaded photo using physical calibration workflow (F-06)."""
    batch_id, batch_dir = _create_batch_directory()
    raw_path = batch_dir / "uploaded-photo.jpg"
    calibrated_path = batch_dir / "calibrated.jpg"
    if raw_bytes is not None:
        raw_path.write_bytes(raw_bytes)
        calibrated_path.write_bytes(raw_bytes)
    else:
        if not cv2.imwrite(str(raw_path), image) or not cv2.imwrite(str(calibrated_path), image):
            raise OSError("The uploaded photo could not be saved.")

    # Physical Calibration Workflow:
    # Completely remove tray color and sample/variety name guessing
    calibration_status = "UNCALIBRATED"
    reference_type = "NONE"
    ref_length = None

    if reference_length_mm is not None and reference_pixels is not None:
        if reference_length_mm <= 0 or reference_pixels <= 0:
            raise ValueError("Reference dimensions must be greater than zero.")
        scale_mm_per_pixel = float(reference_length_mm) / float(reference_pixels)
        calibration_status = "CALIBRATED"
        reference_type = "PHYSICAL_REFERENCE_OBJECT"
        ref_length = float(reference_length_mm)
        estimate_note = f"Calibrated with physical reference ({ref_length:.1f} mm): {scale_mm_per_pixel:.4f} mm/pixel. "
    elif calibration_mm_per_pixel is not None:
        if calibration_mm_per_pixel <= 0:
            raise ValueError("calibration_mm_per_pixel must be greater than zero.")
        scale_mm_per_pixel = float(calibration_mm_per_pixel)
        calibration_status = "CALIBRATED"
        reference_type = "DIRECT_SCALE_INPUT"
        estimate_note = f"Calibrated scale: {scale_mm_per_pixel:.4f} mm/pixel. "
    else:
        scale_mm_per_pixel = ESTIMATED_MM_PER_PIXEL
        calibration_status = "UNCALIBRATED"
        reference_type = "DEFAULT_ESTIMATE"
        estimate_note = "Estimated size; assumed scale 0.12 mm/pixel (not calibrated). "

    analysis = analyze_uploaded_image(str(calibrated_path), scale_mm_per_pixel, str(batch_dir))
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
    base_url = f"/files/batches/{batch_id}"

    summary = {
        "total_grains": total,
        "counts": counts,
        "percent": percent,
        "mean_length_mm": round(mean(lengths), 2) if lengths else 0,
        "std_length_mm": round(pstdev(lengths), 2) if len(lengths) > 1 else 0,
        "mean_width_mm": round(mean(widths), 2) if widths else 0,
        "mean_lb_ratio": round(mean(grain["lb_ratio"] for grain in grains), 2) if grains else 0,
        "mean_confidence": round(mean(grain["confidence"] for grain in grains), 3) if grains else 0,
        "low_confidence_count": sum(grain["low_confidence"] for grain in grains),
    }

    calibration_meta = {
        "calibration_status": calibration_status,
        "mm_per_pixel": round(scale_mm_per_pixel, 5),
        "calibration_reference_type": reference_type,
        "calibration_reference_length_mm": ref_length,
        "calibration_timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "measurement_source": "UPLOADED_IMAGE",
        "patch_error_before": None,
        "patch_error_after": None,
    }

    fingerprint = compute_batch_fingerprint(
        counts=counts,
        summary=summary,
        calibration=calibration_meta,
        image_bytes=raw_bytes,
        labels=LABELS,
    )

    batch = {
        "batch_id": batch_id,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sample_name": sample_name.strip(),
        "variety": variety.strip(),
        "lighting": lighting,
        "simulated": False,  # Uploaded real photographs are NOT simulated
        "is_mock": False,
        "measurement_status": "REAL_IMAGE_MEASUREMENT",
        "images": {
            "raw": f"{base_url}/uploaded-photo.jpg",
            "calibrated": f"{base_url}/calibrated.jpg",
            "annotated": f"{base_url}/annotated.jpg",
        },
        "calibration": calibration_meta,
        "grains": grains,
        "summary": summary,
        "fingerprint": fingerprint,
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
    # Write to standardized batch directory
    (batch_dir / "batch.json").write_text(json.dumps(batch, indent=2), encoding="utf-8")
    # Backward compatibility: write copy to DATA_DIR / batch_id
    legacy_dir = DATA_DIR / batch_id
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "batch.json").write_text(json.dumps(batch, indent=2), encoding="utf-8")

    save_batch(batch, DATABASE_PATH)
    return batch


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        _load_model()
    except Exception:
        pass

    # Print a fresh, one-time bootstrap URL or secret to server console at startup (T-01)
    ensure_active_bootstrap_secret(print_console=True, reason="server startup")

    # Validate custom CORS configuration at startup (S-06)
    raw_cors = os.getenv("AGRIVISIO_ALLOWED_ORIGINS") or os.getenv("AGRISCAN_ALLOWED_ORIGINS")
    if raw_cors:
        parse_and_validate_allowed_origins(raw_cors)

    stop_event = asyncio.Event()

    async def _auto_refresh_loop():
        while not stop_event.is_set():
            try:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=2.0)
                    break
                except asyncio.TimeoutError:
                    pass
                if stop_event.is_set():
                    break
                active = get_active_bootstrap_secret()
                if active is None:
                    ensure_active_bootstrap_secret(print_console=True, reason="previous secret expired")
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    loop_task = asyncio.create_task(_auto_refresh_loop())
    try:
        yield
    finally:
        stop_event.set()
        loop_task.cancel()
        try:
            await loop_task
        except (asyncio.CancelledError, Exception):
            pass


app = FastAPI(title="AgriVisio - Precision Optical Grain Assessment", lifespan=lifespan)

# Restrict CORS to trusted local origins (S-06)
DEFAULT_TRUSTED_ORIGINS = [
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://127.0.0.1",
    "http://localhost",
    "app://agrivisio",
    "app://agriscan",
]


def parse_and_validate_allowed_origins(raw_origins: str | None = None) -> list[str]:
    """Parse, validate, and filter custom CORS origins from AGRIVISIO_ALLOWED_ORIGINS / AGRISCAN_ALLOWED_ORIGINS (S-06).

    - Outright rejects wildcard '*' or any wildcard patterns to prevent arbitrary cross-origin access.
    - Drops empty values and duplicate origins.
    - Fails loudly with logging when invalid or wildcard values are supplied.
    """
    raw = raw_origins if raw_origins is not None else (os.getenv("AGRIVISIO_ALLOWED_ORIGINS") or os.getenv("AGRISCAN_ALLOWED_ORIGINS", ""))
    trusted = list(DEFAULT_TRUSTED_ORIGINS)
    seen = set(trusted)

    if raw:
        for item in raw.split(","):
            clean = item.strip()
            if not clean:
                continue
            if clean == "*" or "*" in clean:
                LOGGER.error(
                    "CORS configuration error: rejected wildcard origin '%s' in AGRISCAN_ALLOWED_ORIGINS. "
                    "Wildcard CORS origins are strictly prohibited to prevent cross-origin data theft.",
                    clean,
                )
                continue
            if not (clean.startswith("http://") or clean.startswith("https://") or clean.startswith("app://")):
                LOGGER.error(
                    "CORS configuration error: rejected invalid origin '%s' in AGRISCAN_ALLOWED_ORIGINS. "
                    "Origins must specify protocol (http://, https://, or app://).",
                    clean,
                )
                continue
            if clean in seen:
                LOGGER.info("Dropped duplicate CORS origin: %s", clean)
                continue
            seen.add(clean)
            trusted.append(clean)
            LOGGER.info("Configured custom CORS allowed origin: %s", clean)
    return trusted


trusted_origins = parse_and_validate_allowed_origins()

app.add_middleware(
    CORSMiddleware,
    allow_origins=trusted_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_security_and_session_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    path = request.url.path
    if path == "/" or path.endswith((".html", ".css", ".js")):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        # S-01: Static resource requests must NEVER issue authentication cookies or grant sessions.
    return response


_last_heartbeat_time = time.time()
_has_received_heartbeat = False


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "service": "AgriVisio Precision Optical Grain Assessment"}


class BootstrapRequest(BaseModel):
    bootstrap_secret: str = Field(min_length=16, max_length=128)


@app.post("/api/auth/bootstrap")
def exchange_bootstrap_secret(req: BootstrapRequest, request: Request, response: Response):
    """Exchange a single-use bootstrap secret for an authenticated session (R-02).

    Prevents arbitrary loopback callers from obtaining tokens. Secret expires in 30s
    and cannot be reused.
    """
    secret = req.bootstrap_secret.strip()
    if not consume_bootstrap_secret(secret):
        raise HTTPException(
            status_code=401,
            detail="Invalid, expired, or previously consumed bootstrap secret.",
        )

    token = get_or_create_api_token()
    is_secure = request.url.scheme == "https"
    response.set_cookie(
        key="agrivisio_session",
        value=token,
        httponly=True,
        samesite="lax",
        secure=is_secure,
        path="/",
        max_age=86400,
    )
    response.set_cookie(
        key="agriscan_session",
        value=token,
        httponly=True,
        samesite="lax",
        secure=is_secure,
        path="/",
        max_age=86400,
    )
    # S-01 / S-02: Never disclose the master token in HTTP response bodies
    return {"status": "authenticated"}


@app.get("/api/auth/status")
def auth_status(request: Request):
    """Check whether the current caller possesses a valid authenticated session (T-01)."""
    expected = get_or_create_api_token()
    cookie_token = request.cookies.get("agrivisio_session") or request.cookies.get("agriscan_session")
    if cookie_token and secrets.compare_digest(cookie_token.strip(), expected):
        return {"authenticated": True}
    token_header = request.headers.get("x-agrivisio-token") or request.headers.get("x-agriscan-token")
    if token_header and secrets.compare_digest(token_header.strip(), expected):
        return {"authenticated": True}
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        if secrets.compare_digest(auth_header[7:].strip(), expected):
            return {"authenticated": True}
    return {"authenticated": False}


@app.post("/api/auth/logout")
def logout(response: Response):
    """Terminate the authenticated session and clear the session cookie (R-02)."""
    response.delete_cookie(
        key="agrivisio_session",
        path="/",
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        key="agriscan_session",
        path="/",
        httponly=True,
        samesite="lax",
    )
    return {"status": "logged_out"}


@app.post("/api/heartbeat")
def record_heartbeat():
    global _last_heartbeat_time, _has_received_heartbeat
    _last_heartbeat_time = time.time()
    _has_received_heartbeat = True
    return {"status": "alive"}


@app.get("/api/heartbeat/status")
def get_heartbeat_status():
    seconds_since = round(time.time() - _last_heartbeat_time, 2)
    is_active = _has_received_heartbeat and (seconds_since < 30.0)
    return {
        "active": is_active,
        "seconds_since_last": seconds_since,
    }


@app.post("/api/scan")
def scan(
    request_data: ScanRequest,
    req: Request,
    auth: str = Depends(verify_api_token),
):
    check_rate_limit(req)
    try:
        if is_mock_mode_enabled():
            return _make_mock_batch(request_data)
        return _make_real_batch(request_data)
    except HTTPException:
        raise
    except Exception as exc:
        LOGGER.exception("Scan processing error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": "scan_failed",
                "detail": "The scan could not be completed. Please try again.",
            },
        )


@app.post("/api/scan/upload")
async def scan_uploaded_photo(
    req: Request,
    image: UploadFile = File(...),
    sample_name: str = Form(...),
    variety: str = Form(...),
    lighting: str = Form(...),
    calibration_mm_per_pixel: float | None = Form(None),
    reference_length_mm: float | None = Form(None),
    reference_pixels: float | None = Form(None),
    auth: str = Depends(verify_api_token),
):
    check_rate_limit(req)
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
            decoded,
            sample_name,
            variety,
            lighting,
            raw_bytes=contents,
            calibration_mm_per_pixel=calibration_mm_per_pixel,
            reference_length_mm=reference_length_mm,
            reference_pixels=reference_pixels,
        )
    except HTTPException:
        raise
    except Exception as exc:
        LOGGER.exception("Uploaded photo analysis error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": "scan_failed",
                "detail": "The uploaded photo could not be analyzed. Please check the image and try again.",
            },
        )
    finally:
        await image.close()


def _get_sorted_batch_list() -> list[dict]:
    results = {}
    try:
        db_batches = list_batches(DATABASE_PATH)
    except Exception:
        db_batches = []
    for batch in db_batches:
        if isinstance(batch, dict) and batch.get("batch_id") and batch.get("created_at") is not None:
            results[batch["batch_id"]] = batch

    legacy_directory = DATA_DIR / "batches"
    for path in legacy_directory.glob("AS-*/batch.json"):
        try:
            batch = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(batch, dict) or "batch_id" not in batch or "created_at" not in batch:
                continue
            results.setdefault(batch["batch_id"], batch)
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    return sorted(
        results.values(),
        key=lambda b: (b.get("created_at") if isinstance(b, dict) and b.get("created_at") is not None else ""),
        reverse=True,
    )


@app.get("/api/batches")
def batches(
    page: int | None = Query(None, ge=1),
    page_size: int | None = Query(None, ge=1, le=100),
    auth: Any = Depends(verify_api_token),
) -> Any:
    all_batches = _get_sorted_batch_list()
    is_p_int = isinstance(page, int)
    is_ps_int = isinstance(page_size, int)
    if is_p_int or is_ps_int:
        p = page if is_p_int else 1
        ps = page_size if is_ps_int else 20
        start = (p - 1) * ps
        end = start + ps
        return {
            "items": all_batches[start:end],
            "total": len(all_batches),
            "page": p,
            "page_size": ps,
            "total_pages": (len(all_batches) + ps - 1) // ps if all_batches else 1,
        }
    return all_batches[:100]


@app.get("/api/batches/{batch_id}")
def get_batch(
    batch_id: str,
    auth: Any = Depends(verify_api_token),
) -> dict:
    if Path(batch_id).name != batch_id or "/" in batch_id or "\\" in batch_id or ".." in batch_id:
        raise HTTPException(status_code=404, detail="Batch not found")
    try:
        batch = read_batch(batch_id, DATABASE_PATH)
    except (OSError, json.JSONDecodeError, KeyError):
        raise HTTPException(status_code=404, detail="Batch not found")
    if batch is not None:
        if isinstance(batch, dict) and batch.get("batch_id"):
            return batch
        raise HTTPException(status_code=404, detail="Batch not found")
    path = DATA_DIR / "batches" / batch_id / "batch.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise HTTPException(status_code=404, detail="Batch not found")
            return data
        except (OSError, json.JSONDecodeError, KeyError):
            raise HTTPException(status_code=404, detail="Batch not found")
    legacy_path = DATA_DIR / batch_id / "batch.json"
    if legacy_path.is_file():
        try:
            data = json.loads(legacy_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise HTTPException(status_code=404, detail="Batch not found")
            return data
        except (OSError, json.JSONDecodeError, KeyError):
            raise HTTPException(status_code=404, detail="Batch not found")
    raise HTTPException(status_code=404, detail="Batch not found")


@app.get("/api/batches/{batch_id}/export")
def export_batch(
    batch_id: str,
    auth: Any = Depends(verify_api_token),
):
    batch = get_batch(batch_id, auth=auth)
    if batch.get("is_mock"):
        raise HTTPException(
            status_code=400,
            detail="Mock batches cannot be exported as genuine grading certificates or measurement reports.",
        )
    safe_batch_id = "".join(c for c in batch_id if c.isalnum() or c in "-_")
    return JSONResponse(
        content=batch,
        headers={"Content-Disposition": f'attachment; filename="AgriVisio-Report-{safe_batch_id}.json"'},
    )


class ProtectedDataFilesApp(StaticFiles):
    """Secure, authenticated, narrowly scoped file-serving app (R-01)."""

    ALLOWED_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".json")
    BLOCKED_EXTENSIONS = (
        ".sqlite3", ".db", ".auth_token", ".py", ".pyc", ".joblib",
        ".env", ".key", ".pem", ".log", ".bat", ".cmd", ".ps1", ".sh",
        ".webm", ".mp4", ".exe", ".dll", ".ini",
    )

    def __init__(self, directory: Path):
        super().__init__(directory=str(directory), check_dir=False)

    async def get_response(self, path: str, scope):
        import urllib.parse
        from starlette.requests import Request

        # 1. Authenticate every /files request (R-01)
        req = Request(scope)
        expected = get_or_create_api_token()
        token_header = req.headers.get("x-agrivisio-token") or req.headers.get("x-agriscan-token")
        auth_header = req.headers.get("authorization")
        cookie_token = req.cookies.get("agrivisio_session") or req.cookies.get("agriscan_session")

        authenticated = False
        if token_header and secrets.compare_digest(token_header.strip(), expected):
            authenticated = True
        elif auth_header and auth_header.startswith("Bearer "):
            bearer = auth_header[7:].strip()
            if secrets.compare_digest(bearer, expected):
                authenticated = True
        elif cookie_token and secrets.compare_digest(cookie_token.strip(), expected):
            authenticated = True

        if not authenticated:
            return Response("Unauthorized", status_code=401)

        # Dynamically follow DATA_DIR
        self.directory = str(DATA_DIR)

        raw_url_path = req.url.path
        decoded = urllib.parse.unquote(path)

        # 2. Strict traversal & encoding checks
        if (
            ".." in raw_url_path
            or "\0" in decoded
            or "\0" in path
        ):
            return Response("Access Denied", status_code=403)

        norm_parts = [p for p in decoded.replace("\\", "/").split("/") if p and p != "."]
        if any(p == ".." or p.startswith("..") or ":" in p for p in norm_parts):
            return Response("Access Denied", status_code=403)

        lower = decoded.lower()
        if lower.endswith(self.BLOCKED_EXTENSIONS):
            return Response("Access Denied", status_code=403)

        # 3. Narrow scoping (Additional 2): only allow valid batch files (.jpg, .jpeg, .png, .webp, .json)
        if not lower.endswith(self.ALLOWED_EXTENSIONS):
            return Response("Not Found", status_code=404)

        # Enforce strict batch directory scope: batches/<valid-batch-id>/... or legacy AS-.../...
        is_scoped = False
        if norm_parts[0] == "batches" and len(norm_parts) >= 3:
            batch_id = norm_parts[1]
            if re.match(r"^[\w-]+$", batch_id):
                is_scoped = True
        elif norm_parts[0].startswith("AS-") and len(norm_parts) >= 2:
            batch_id = norm_parts[0]
            if re.match(r"^AS-[\w-]+$", batch_id):
                is_scoped = True

        if not is_scoped:
            return Response("Access Denied: Unscoped batch file access", status_code=403)

        # 4. Containment and symlink escape verification
        base_dir = Path(self.directory).resolve()
        try:
            target_path = (base_dir / Path(*norm_parts)).resolve()
        except Exception:
            return Response("Access Denied", status_code=403)

        if not str(target_path).startswith(str(base_dir) + os.path.sep) and target_path != base_dir:
            return Response("Access Denied", status_code=403)

        if not target_path.is_file():
            return Response("Not Found", status_code=404)

        ext = target_path.suffix.lower()
        media_type = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".json": "application/json",
        }.get(ext, "application/octet-stream")

        headers = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Cache-Control": "private, max-age=86400",
        }
        return FileResponse(target_path, media_type=media_type, headers=headers)


app.mount("/files", ProtectedDataFilesApp(directory=DATA_DIR), name="files")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


def get_api_token() -> str:
    """Helper for testing and local integration."""
    return get_or_create_api_token()
