from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import cv2
import numpy as np

from backend.main import (
    DATA_DIR,
    DATABASE_PATH,
    ScanRequest,
    _load_model,
    _make_mock_batch,
    _make_real_batch,
    _make_uploaded_batch,
    batches,
    get_batch,
    is_mock_mode_enabled,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
LOGGER = logging.getLogger("agriscan_ipc_worker")


def handle_health(data: dict) -> dict:
    return {"status": "ok"}


def handle_heartbeat(data: dict) -> dict:
    return {"status": "alive"}


def handle_scan(data: dict) -> dict:
    req = ScanRequest(**data)
    if is_mock_mode_enabled():
        return _make_mock_batch(req)
    return _make_real_batch(req)


def handle_scan_upload(data: dict) -> dict:
    file_path = data.get("file_path")
    sample_name = data.get("sample_name", "").strip()
    variety = data.get("variety", "").strip()
    lighting = data.get("lighting", "ring_light")

    if not sample_name or len(sample_name) > 80:
        raise ValueError("Sample name must contain 1 to 80 characters.")
    if not variety or len(variety) > 80:
        raise ValueError("Rice variety must contain 1 to 80 characters.")
    if lighting not in {"ring_light", "daylight", "tube_light"}:
        raise ValueError("Unknown lighting profile.")

    if not file_path or not os.path.isfile(file_path):
        raise FileNotFoundError(f"Uploaded file path not found: {file_path}")

    image = cv2.imread(file_path)
    if image is None:
        raise ValueError("Choose a valid JPEG, PNG, or WebP photo.")

    height, width = image.shape[:2]
    if width * height > 20_000_000:
        raise ValueError("Photo dimensions must be 20 megapixels or smaller.")

    raw_bytes = Path(file_path).read_bytes() if os.path.isfile(file_path) else None
    return _make_uploaded_batch(image, sample_name, variety, lighting, raw_bytes=raw_bytes)


def _try_scan_wia():
    """Attempt to acquire an image from a WIA-compatible scanner (Epson)."""
    import subprocess
    import tempfile
    import uuid

    # Use PowerShell + WIA COM to scan securely with parameterized output path
    scan_id = uuid.uuid4().hex
    temp_path = os.path.abspath(os.path.join(tempfile.gettempdir(), f"agriscan_hw_{scan_id}.jpg"))

    ps_script = '''
param([string]$outputPath)
if ([string]::IsNullOrWhiteSpace($outputPath)) {
    Write-Error "Invalid output path"
    exit 1
}
Add-Type -AssemblyName System.Runtime.InteropServices
$deviceManager = New-Object -ComObject WIA.DeviceManager
$devices = $deviceManager.DeviceInfos
if ($devices.Count -eq 0) {
    Write-Error "No scanner found"
    exit 1
}
$scanner = $null
for ($i = 1; $i -le $devices.Count; $i++) {
    $dev = $devices.Item($i)
    if ($dev.Type -eq 1) {
        $scanner = $dev
        break
    }
}
if ($scanner -eq $null) {
    Write-Error "No scanner found"
    exit 1
}
$device = $scanner.Connect()
$item = $device.Items.Item(1)

foreach ($prop in $item.Properties) {
    if ($prop.PropertyID -eq 6147) { $prop.Value = 300 }
    if ($prop.PropertyID -eq 6148) { $prop.Value = 300 }
    if ($prop.PropertyID -eq 6146) { $prop.Value = 1 }
}

$imageFile = $item.Transfer("{B96B3CAE-0728-11D3-9D7B-0000F81EF32E}")
if (Test-Path $outputPath) { Remove-Item $outputPath -Force }
$imageFile.SaveFile($outputPath)
Write-Output "SCAN_OK"
'''
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", "& { " + ps_script + " }", "-outputPath", temp_path],
        capture_output=True, text=True, timeout=90
    )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "No scanner found" in stderr or "No scanner found" in result.stdout:
            raise RuntimeError("No scanner found")
        raise RuntimeError(f"Scanner error: {stderr or result.stdout}")

    if not os.path.isfile(temp_path):
        raise RuntimeError("No scanner found")

    return temp_path


def handle_scan_hardware(data: dict) -> dict:
    """Acquire image from hardware scanner and run grain analysis."""
    sample_name = data.get("sample_name", "").strip()
    variety = data.get("variety", "").strip()
    lighting = data.get("lighting", "ring_light")

    if not sample_name or len(sample_name) > 80:
        raise ValueError("Sample name must contain 1 to 80 characters.")
    if not variety or len(variety) > 80:
        raise ValueError("Rice variety must contain 1 to 80 characters.")

    # Try to scan from hardware
    scanned_path = _try_scan_wia()

    try:
        image = cv2.imread(scanned_path)
        if image is None:
            raise ValueError("Scanner returned an invalid image.")

        raw_bytes = Path(scanned_path).read_bytes()
        return _make_uploaded_batch(image, sample_name, variety, lighting, raw_bytes=raw_bytes)
    finally:
        try:
            if os.path.isfile(scanned_path):
                os.unlink(scanned_path)
        except OSError:
            pass


def handle_list_batches(data: dict) -> list[dict]:
    return batches()


def handle_get_batch(data: dict) -> dict:
    batch_id = data.get("batch_id")
    if not batch_id:
        raise ValueError("batch_id is required.")
    return get_batch(batch_id)


def handle_shutdown(data: dict) -> dict:
    """Handle graceful shutdown requested by Electron parent process (R-06)."""
    LOGGER.info("Graceful shutdown signal received from Electron parent.")
    sys.exit(0)


HANDLERS = {
    "health": handle_health,
    "heartbeat": handle_heartbeat,
    "scan": handle_scan,
    "scan_upload": handle_scan_upload,
    "scan_hardware": handle_scan_hardware,
    "list_batches": handle_list_batches,
    "get_batch": handle_get_batch,
    "shutdown": handle_shutdown,
}


def main():
    try:
        _load_model()
    except Exception as exc:
        LOGGER.warning("Could not pre-load classifier model: %s", exc)

    # Signal portless readiness to Electron parent process
    print(json.dumps({"event": "ready", "app": "agriscan"}), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req_id = None
        try:
            req = json.loads(line)
            req_id = req.get("id")
            action = req.get("action")
            data = req.get("data") or {}

            handler = HANDLERS.get(action)
            if not handler:
                resp = {"id": req_id, "ok": False, "error": f"Unknown action: {action}"}
            else:
                result = handler(data)
                resp = {"id": req_id, "ok": True, "result": result}
        except Exception as e:
            LOGGER.exception("Error processing request: %s", e)
            resp = {"id": req_id, "ok": False, "error": str(e)}

        print(json.dumps(resp), flush=True)


if __name__ == "__main__":
    main()
