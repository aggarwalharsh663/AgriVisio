import sqlite3

import pytest
from fastapi.testclient import TestClient
from fastapi.staticfiles import StaticFiles

from backend import main
from backend.database import get_batch


BATCH_KEYS = {
    "batch_id",
    "created_at",
    "sample_name",
    "variety",
    "lighting",
    "simulated",
    "images",
    "calibration",
    "grains",
    "summary",
    "fingerprint",
    "validation",
}
GRAIN_KEYS = {
    "id",
    "bbox",
    "center",
    "length_mm",
    "width_mm",
    "lb_ratio",
    "mean_rgb",
    "chalk_fraction",
    "label",
    "confidence",
    "low_confidence",
    "reason",
}
SUMMARY_KEYS = {
    "total_grains",
    "counts",
    "percent",
    "mean_length_mm",
    "std_length_mm",
    "mean_width_mm",
    "mean_lb_ratio",
    "mean_confidence",
    "low_confidence_count",
}


@pytest.mark.parametrize("lighting", ["ring_light", "daylight", "tube_light"])
def test_real_scan_pipeline_returns_contract_and_saves_to_sqlite(
    tmp_path, monkeypatch, lighting
):
    data_dir = tmp_path / "data"
    database_path = tmp_path / "agriscan.sqlite3"
    monkeypatch.setattr(main, "DATA_DIR", data_dir)
    monkeypatch.setattr(main, "DATABASE_PATH", database_path)
    monkeypatch.delenv("MOCK", raising=False)
    data_dir.mkdir(parents=True)
    files_mount = next(
        route for route in main.app.routes if getattr(route, "path", None) == "/files"
    )
    monkeypatch.setattr(files_mount, "app", StaticFiles(directory=data_dir))

    with TestClient(main.app) as client:
        response = client.post(
            "/api/scan",
            json={
                "lighting": lighting,
                "n_grains": 80,
                "seed": 1701,
                "sample_name": "E2E simulated sample",
                "variety": "Demo",
            },
        )

        assert response.status_code == 200, response.text
        batch = response.json()
        assert set(batch) == BATCH_KEYS
        assert batch["simulated"] is True
        assert batch["lighting"] == lighting
        assert 80 <= batch["summary"]["total_grains"] <= 150
        assert set(batch["summary"]) == SUMMARY_KEYS
        assert set(batch["images"]) == {"raw", "calibrated", "annotated"}
        assert set(batch["calibration"]) == {
            "patch_error_before",
            "patch_error_after",
        }
        assert set(batch["fingerprint"]) == {"vector", "hash"}
        assert set(batch["validation"]) >= {
            "accuracy",
            "per_class",
            "confusion_matrix",
            "labels",
            "length_mae_mm",
        }
        assert all(url.startswith("/files/") for url in batch["images"].values())
        assert all(set(grain) == GRAIN_KEYS for grain in batch["grains"])
        assert all(
            client.get(url).status_code == 200
            for url in batch["images"].values()
        )

        assert get_batch(batch["batch_id"], database_path) == batch
        assert client.get(f"/api/batches/{batch['batch_id']}").json() == batch
        assert any(
            saved["batch_id"] == batch["batch_id"]
            for saved in client.get("/api/batches").json()
        )

    with sqlite3.connect(database_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM batches").fetchone()[0]
    assert count == 1


def test_scan_pipeline_failure_returns_clear_json_error(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "agriscan.sqlite3")
    monkeypatch.delenv("MOCK", raising=False)

    def fail_scene(*args, **kwargs):
        if args or kwargs:
            raise OSError("simulator disk write failed")
        raise OSError("simulator disk write failed")

    monkeypatch.setattr(main, "make_scene", fail_scene)
    with TestClient(main.app) as client:
        response = client.post(
            "/api/scan",
            json={
                "lighting": "ring_light",
                "n_grains": 80,
                "seed": 1701,
                "sample_name": "Failure sample",
                "variety": "Demo",
            },
        )

    assert response.status_code == 500
    assert response.json() == {
        "error": "scan_failed",
        "detail": "The simulated scan could not be completed: simulator disk write failed",
    }
