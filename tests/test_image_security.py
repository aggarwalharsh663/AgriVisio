"""Tests for image security, decompression bomb prevention, and pre-decode resource validation.

Verifies that untrusted image bytes are validated at the container and header level before
any memory-intensive OpenCV decoding takes place, preventing DoS and memory exhaustion.
"""

import io
import struct
import zlib
import cv2
import numpy as np
import pytest
from PIL import Image
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from backend import main
from backend.image_security import (
    UPLOAD_MAX_BYTES,
    UPLOAD_MAX_PIXELS,
    DecompressionBombError,
    OversizedFileError,
    UnsupportedFormatError,
    CorruptImageError,
    validate_image_header,
    safe_decode_image,
)
from backend.ipc_worker import handle_scan_upload


@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    batches_dir = data_dir / "batches"
    batches_dir.mkdir(parents=True, exist_ok=True)
    database_path = tmp_path / "agrivisio.sqlite3"

    monkeypatch.setattr(main, "DATA_DIR", data_dir)
    monkeypatch.setattr(main, "DATABASE_PATH", database_path)
    monkeypatch.setattr(main, "AUTH_TOKEN_FILE", tmp_path / "auth_token.txt")
    files_mount = next(
        route for route in main.app.routes if getattr(route, "path", None) == "/files"
    )
    monkeypatch.setattr(files_mount, "app", StaticFiles(directory=data_dir))

    token = main.get_or_create_api_token()
    client = TestClient(main.app)
    client.headers["X-AgriVisio-Token"] = token
    yield client
    client.close()


def _make_bomb_png(width: int = 10000, height: int = 10000) -> bytes:
    """Construct a lightweight PNG (approx 109 bytes) declaring excessive dimensions."""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr_crc = struct.pack(">I", zlib.crc32(b"IHDR" + ihdr_data))
    ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + ihdr_crc
    raw_scanlines = b"\x00" * (1 + width * 3)
    comp = zlib.compress(raw_scanlines)
    idat = (
        struct.pack(">I", len(comp))
        + b"IDAT"
        + comp
        + struct.pack(">I", zlib.crc32(b"IDAT" + comp))
    )
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND"))
    return sig + ihdr + idat + iend


# 1. Normal JPEG upload
def test_normal_jpeg_upload(auth_client):
    buf = io.BytesIO()
    Image.new("RGB", (400, 400), color=(220, 220, 220)).save(buf, format="JPEG")
    res = auth_client.post(
        "/api/scan/upload",
        files={"image": ("test.jpg", buf.getvalue(), "image/jpeg")},
        data={"sample_name": "Valid JPEG", "variety": "Basmati", "lighting": "ring_light"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["sample_name"] == "Valid JPEG"


# 2. Normal PNG upload
def test_normal_png_upload(auth_client):
    buf = io.BytesIO()
    Image.new("RGB", (300, 300), color=(210, 215, 210)).save(buf, format="PNG")
    res = auth_client.post(
        "/api/scan/upload",
        files={"image": ("test.png", buf.getvalue(), "image/png")},
        data={"sample_name": "Valid PNG", "variety": "IR64", "lighting": "daylight"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["sample_name"] == "Valid PNG"


# 3. Normal WebP upload
def test_normal_webp_upload(auth_client):
    buf = io.BytesIO()
    Image.new("RGB", (300, 300), color=(230, 220, 210)).save(buf, format="WEBP")
    res = auth_client.post(
        "/api/scan/upload",
        files={"image": ("test.webp", buf.getvalue(), "image/webp")},
        data={"sample_name": "Valid WebP", "variety": "Pusa 1121", "lighting": "tube_light"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["sample_name"] == "Valid WebP"


# 4. File exceeding 10 MB rejected before OpenCV decode
def test_file_exceeding_10mb_rejected_before_decode(auth_client, monkeypatch):
    oversized = b"0" * (UPLOAD_MAX_BYTES + 1024)

    # Monkeypatch cv2.imdecode to ensure it is NEVER called
    def fail_imdecode(*args, **kwargs):
        pytest.fail("cv2.imdecode should never be invoked for an oversized upload!")

    monkeypatch.setattr(cv2, "imdecode", fail_imdecode)

    res = auth_client.post(
        "/api/scan/upload",
        files={"image": ("oversized.jpg", oversized, "image/jpeg")},
        data={"sample_name": "Too Big", "variety": "Basmati", "lighting": "ring_light"},
    )
    assert res.status_code == 413
    assert "10 MB or smaller" in res.text


# 5. Image exceeding 20 megapixels rejected before OpenCV decode
def test_image_exceeding_20_megapixels_rejected_before_decode(auth_client, monkeypatch):
    # Create 5000 x 4500 = 22.5 megapixels JPEG (header says 22.5 MP)
    buf = io.BytesIO()
    Image.new("RGB", (5000, 4500), color=(200, 200, 200)).save(buf, format="JPEG")
    data = buf.getvalue()

    # Verify cv2.imdecode is never called
    def fail_imdecode(*args, **kwargs):
        pytest.fail("cv2.imdecode should never be invoked for a >20MP image!")

    monkeypatch.setattr(cv2, "imdecode", fail_imdecode)

    res = auth_client.post(
        "/api/scan/upload",
        files={"image": ("large_dimensions.jpg", data, "image/jpeg")},
        data={"sample_name": "Too Many Pixels", "variety": "Basmati", "lighting": "ring_light"},
    )
    assert res.status_code == 413
    assert "20 megapixels or smaller" in res.text


# 6. Small compressed file with excessive declared dimensions (Decompression Bomb)
def test_small_compressed_decompression_bomb_rejected_before_decode(auth_client, monkeypatch):
    # 10,000 x 10,000 = 100 megapixels, but file is only ~109 bytes!
    bomb_bytes = _make_bomb_png(10000, 10000)
    assert len(bomb_bytes) < 1024, "Decompression bomb should be tiny in bytes"

    # Verify cv2.imdecode is never called
    def fail_imdecode(*args, **kwargs):
        pytest.fail("cv2.imdecode should NEVER be invoked on a decompression bomb!")

    monkeypatch.setattr(cv2, "imdecode", fail_imdecode)

    res = auth_client.post(
        "/api/scan/upload",
        files={"image": ("bomb.png", bomb_bytes, "image/png")},
        data={"sample_name": "Decompression Bomb", "variety": "Basmati", "lighting": "ring_light"},
    )
    assert res.status_code == 413
    assert "20 megapixels or smaller" in res.text


# 7. Corrupt image header rejected before OpenCV decode
def test_corrupt_image_header_rejected_before_decode(auth_client, monkeypatch):
    corrupt_bytes = b"RIFF\x00\x00\x00\x00WEBPVP8 \x00\x00\x00\x00corrupted_payload_data"

    def fail_imdecode(*args, **kwargs):
        pytest.fail("cv2.imdecode should never be invoked on a corrupted header!")

    monkeypatch.setattr(cv2, "imdecode", fail_imdecode)

    res = auth_client.post(
        "/api/scan/upload",
        files={"image": ("corrupt.jpg", corrupt_bytes, "image/jpeg")},
        data={"sample_name": "Corrupt Header", "variety": "Basmati", "lighting": "ring_light"},
    )
    assert res.status_code == 422
    assert "Choose a valid JPEG, PNG, or WebP photo." in res.text


# 8. Unsupported image formats rejected before OpenCV decode
@pytest.mark.parametrize("fmt,ext", [("GIF", "gif"), ("BMP", "bmp"), ("TIFF", "tiff")])
def test_unsupported_image_formats_rejected_before_decode(auth_client, monkeypatch, fmt, ext):
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), color=(200, 200, 200)).save(buf, format=fmt)
    unsupported_bytes = buf.getvalue()

    def fail_imdecode(*args, **kwargs):
        pytest.fail(f"cv2.imdecode should not be called on unsupported format {fmt}!")

    monkeypatch.setattr(cv2, "imdecode", fail_imdecode)

    # Note: client disguises it as .jpg and image/jpeg, but header inspection must reject it
    res = auth_client.post(
        "/api/scan/upload",
        files={"image": (f"disguised.{ext}", unsupported_bytes, "image/jpeg")},
        data={"sample_name": "Unsupported Format", "variety": "Basmati", "lighting": "ring_light"},
    )
    assert res.status_code == 422
    assert "Choose a valid JPEG, PNG, or WebP photo." in res.text


# 9. Truncated image rejected safely
def test_truncated_image_rejected_safely(auth_client):
    buf = io.BytesIO()
    Image.new("RGB", (200, 200), color=(200, 200, 200)).save(buf, format="JPEG")
    # Take first 24 bytes only (incomplete/truncated JPEG stream)
    truncated = buf.getvalue()[:24]

    res = auth_client.post(
        "/api/scan/upload",
        files={"image": ("truncated.jpg", truncated, "image/jpeg")},
        data={"sample_name": "Truncated", "variety": "Basmati", "lighting": "ring_light"},
    )
    assert res.status_code == 422
    assert "Choose a valid JPEG, PNG, or WebP photo." in res.text


# 10. Valid image just below configured limits succeeds
def test_valid_image_just_below_configured_limits_succeeds(auth_client):
    # 4000 x 4900 = 19.6 megapixels (under 20 MP limit)
    buf = io.BytesIO()
    Image.new("RGB", (4000, 4900), color=(220, 220, 220)).save(buf, format="JPEG", quality=80)
    data = buf.getvalue()
    assert len(data) < UPLOAD_MAX_BYTES

    # Pre-validation direct check
    w, h, fmt = validate_image_header(data)
    assert (w, h) == (4000, 4900)
    assert fmt == "JPEG"

    # API upload succeeds without DecompressionBombWarning/Error
    res = auth_client.post(
        "/api/scan/upload",
        files={"image": ("near_limit.jpg", data, "image/jpeg")},
        data={"sample_name": "Near Limit", "variety": "Basmati", "lighting": "ring_light"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["sample_name"] == "Near Limit"


# 11. IPC worker decompression bomb and oversized rejection
def test_ipc_worker_decompression_bomb_and_oversized(tmp_path, monkeypatch):
    bomb_file = tmp_path / "bomb.png"
    bomb_file.write_bytes(_make_bomb_png(10000, 10000))

    # Ensure cv2.imdecode is never called in ipc_worker
    def fail_imdecode(*args, **kwargs):
        pytest.fail("cv2.imdecode should never be called for bomb in ipc_worker!")

    monkeypatch.setattr(cv2, "imdecode", fail_imdecode)

    with pytest.raises(ValueError, match="20 megapixels or smaller"):
        handle_scan_upload({
            "file_path": str(bomb_file),
            "sample_name": "IPC Bomb",
            "variety": "Basmati",
            "lighting": "ring_light",
        })
