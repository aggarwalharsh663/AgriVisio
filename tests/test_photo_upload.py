import json
from pathlib import Path

import cv2
import numpy as np
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from backend import main
from backend.simulator import make_scene


def test_uploaded_photo_is_analyzed_with_an_estimated_scale(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    database_path = tmp_path / "agriscan.sqlite3"
    source_dir = tmp_path / "source"
    data_dir.mkdir()
    scene = make_scene(
        seed=1701,
        n_grains=24,
        lighting="ring_light",
        out_dir=str(source_dir),
    )
    photo_bytes = Path(scene["photo_path"]).read_bytes()

    monkeypatch.setattr(main, "DATA_DIR", data_dir)
    monkeypatch.setattr(main, "DATABASE_PATH", database_path)
    files_mount = next(
        route for route in main.app.routes if getattr(route, "path", None) == "/files"
    )
    monkeypatch.setattr(files_mount, "app", StaticFiles(directory=data_dir))

    with TestClient(main.app) as client:
        response = client.post(
            "/api/scan/upload",
            data={
                "sample_name": "Uploaded sample",
                "variety": "Basmati",
                "lighting": "ring_light",
            },
            files={"image": ("rice-photo.jpg", photo_bytes, "image/jpeg")},
        )

        assert response.status_code == 200, response.text
        batch = response.json()
        assert batch["simulated"] is True
        assert set(batch) == {
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
        assert batch["images"]["raw"].endswith("/uploaded-photo.jpg")
        assert batch["calibration"] == {
            "patch_error_before": None,
            "patch_error_after": None,
        }
        assert batch["summary"]["total_grains"] > 0
        assert batch["validation"]["accuracy"] is None
        assert "precision" in batch["validation"]["per_class"]["good"]
        assert all(
            reason.startswith("Estimated size; assumed scale 0.12 mm/pixel")
            for reason in (grain["reason"] for grain in batch["grains"])
        )
        assert all(client.get(path).status_code == 200 for path in batch["images"].values())
        assert json.loads(
            (data_dir / batch["batch_id"] / "batch.json").read_text()
        ) == batch


def test_uploaded_photo_rejects_invalid_image(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(main, "DATA_DIR", data_dir)
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "agriscan.sqlite3")

    with TestClient(main.app) as client:
        response = client.post(
            "/api/scan/upload",
            data={
                "sample_name": "Invalid sample",
                "variety": "Demo",
                "lighting": "daylight",
            },
            files={"image": ("not-a-photo.jpg", b"not an image", "image/jpeg")},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "Choose a valid JPEG, PNG, or WebP photo."


def test_uploaded_photo_detects_dense_grains_across_the_full_frame(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(main, "DATA_DIR", data_dir)
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "agriscan.sqlite3")
    files_mount = next(
        route for route in main.app.routes if getattr(route, "path", None) == "/files"
    )
    monkeypatch.setattr(files_mount, "app", StaticFiles(directory=data_dir))

    image = np.full((720, 1200, 3), 24, dtype=np.uint8)
    rng = np.random.default_rng(73)
    for row in range(14):
        for column in range(20):
            center = (35 + column * 52 + (row % 2) * 18, 25 + row * 31)
            angle = int(rng.integers(0, 180))
            cv2.ellipse(image, center, (26, 8), angle, 0, 360, (228, 222, 200), -1)
    success, encoded = cv2.imencode(".jpg", image)
    assert success

    with TestClient(main.app) as client:
        response = client.post(
            "/api/scan/upload",
            data={
                "sample_name": "Dense rice sample",
                "variety": "Basmati",
                "lighting": "daylight",
            },
            files={"image": ("dense-rice.jpg", encoded.tobytes(), "image/jpeg")},
        )

    assert response.status_code == 200, response.text
    batch = response.json()
    assert batch["summary"]["total_grains"] >= 200
    assert batch["summary"]["total_grains"] == len(batch["grains"])
    assert any(grain["center"][0] > 970 for grain in batch["grains"])


def test_uploaded_photo_counts_dense_167_grain_sample(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(main, "DATA_DIR", data_dir)
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "agriscan.sqlite3")
    image = np.full((720, 1200, 3), 24, dtype=np.uint8)
    rng = np.random.default_rng(73)
    for index in range(167):
        row, column = divmod(index, 21)
        center = (30 + column * 45 + (row % 2) * 22, 20 + row * 24)
        angle = int(rng.integers(0, 180))
        cv2.ellipse(image, center, (24, 7), angle, 0, 360, (228, 222, 200), -1)

    success, encoded = cv2.imencode(".jpg", image)
    assert success

    with TestClient(main.app) as client:
        response = client.post(
            "/api/scan/upload",
            data={
                "sample_name": "167-grain sample",
                "variety": "Basmati",
                "lighting": "daylight",
            },
            files={"image": ("167-grains.jpg", encoded.tobytes(), "image/jpeg")},
        )

    assert response.status_code == 200, response.text
    batch = response.json()
    detected = batch["summary"]["total_grains"]
    assert detected == len(batch["grains"])
    assert 160 <= detected <= 170


def test_uploaded_blue_tray_photo_uses_calibrated_scale(tmp_path, monkeypatch):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    monkeypatch.setattr(main, 'DATA_DIR', data_dir)
    monkeypatch.setattr(main, 'DATABASE_PATH', tmp_path / 'agriscan.sqlite3')

    # Blue tray: B channel high (~240), R channel low (~60)
    image = np.zeros((1000, 800, 3), dtype=np.uint8)
    image[:, :] = [240, 140, 60]  # B, G, R
    for i in range(20):
        cx = 150 + (i % 4) * 150
        cy = 150 + (i // 4) * 150
        cv2.ellipse(image, (cx, cy), (28, 8), 30 * i, 0, 360, (220, 220, 210), -1)

    success, encoded = cv2.imencode('.jpg', image)
    assert success

    with TestClient(main.app) as client:
        response = client.post(
            '/api/scan/upload',
            data={
                'sample_name': 'Scanner Bed Blue Tray',
                'variety': '1121 Steam',
                'lighting': 'daylight',
            },
            files={'image': ('blue-tray.jpg', encoded.tobytes(), 'image/jpeg')},
        )

    assert response.status_code == 200, response.text
    batch = response.json()
    assert batch['summary']['total_grains'] == 20
    assert any('Calibrated scanner bed scale' in g['reason'] for g in batch['grains'])
