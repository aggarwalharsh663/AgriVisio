"""Deterministic cryptographic batch fingerprinting for AgriScan.

Produces reproducible, collision-resistant fingerprints capturing morphometrics,
class distribution, calibration metadata, and raw image identity.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


FINGERPRINT_SCHEMA_VERSION = "v1"


def compute_batch_fingerprint(
    counts: dict[str, int],
    summary: dict[str, Any],
    calibration: dict[str, Any] | None = None,
    image_bytes: bytes | None = None,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    """Generate a deterministic multi-factor cryptographic fingerprint of a batch.

    Args:
        counts: Mapping of quality labels to grain counts.
        summary: Aggregated morphometric summary statistics.
        calibration: Calibration parameters and status.
        image_bytes: Optional raw image bytes to bind image identity.
        labels: Ordered list of recognized grain classes.

    Returns:
        Dict with 'vector' (normalized class distribution) and 'hash' (SHA-256).
    """
    ordered_labels = labels or ["good", "broken", "chalky", "discolored"]
    total = sum(counts.get(lbl, 0) for lbl in ordered_labels)

    # 1. Normalized vector (distribution across ordered labels)
    vector = [
        round((counts.get(lbl, 0) / total), 4) if total > 0 else 0.0
        for lbl in ordered_labels
    ]

    # 2. Deterministic payload dictionary
    canonical_payload: dict[str, Any] = {
        "schema_version": FINGERPRINT_SCHEMA_VERSION,
        "labels": ordered_labels,
        "class_counts": {lbl: int(counts.get(lbl, 0)) for lbl in ordered_labels},
        "vector": vector,
        "total_grains": int(summary.get("total_grains", total)),
        "mean_length_mm": round(float(summary.get("mean_length_mm", 0.0)), 3),
        "mean_width_mm": round(float(summary.get("mean_width_mm", 0.0)), 3),
        "mean_lb_ratio": round(float(summary.get("mean_lb_ratio", 0.0)), 3),
        "std_length_mm": round(float(summary.get("std_length_mm", 0.0)), 3),
        "calibration_status": (calibration or {}).get("calibration_status", "UNKNOWN"),
        "mm_per_pixel": round(float((calibration or {}).get("mm_per_pixel", 0.0)), 5)
        if (calibration or {}).get("mm_per_pixel") is not None
        else None,
    }

    if image_bytes is not None:
        canonical_payload["image_sha256"] = hashlib.sha256(image_bytes).hexdigest()

    # Deterministic JSON serialization with sorted keys
    canonical_json = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":"))
    content_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    return {
        "vector": vector,
        "hash": content_hash,
    }
