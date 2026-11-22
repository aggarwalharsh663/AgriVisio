"""Hardware scanner integration for AgriVisio (WIA / Flatbed scanner)."""

from __future__ import annotations

import io
import logging
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Tuple

import cv2
import numpy as np
from PIL import Image

LOGGER = logging.getLogger("agrivisio_hardware_scanner")

ESTIMATED_MM_PER_PIXEL = 0.12


def extract_dpi_from_image(
    raw_bytes: bytes | None = None,
    file_path: str | Path | None = None,
) -> tuple[float, float] | None:
    """Extract and validate horizontal and vertical DPI from image metadata.

    Supports EXIF (tags 0x011A, 0x011B, 0x0128), JFIF density, and standard image formats
    (JPEG, PNG, TIFF). Validates that DPI values are positive, finite numbers within
    physical hardware bounds (10 to 24000 DPI). Rejects missing, zero, negative, or
    corrupted metadata by returning None.
    """
    if raw_bytes is None and file_path is None:
        return None

    try:
        if raw_bytes is not None:
            im = Image.open(io.BytesIO(raw_bytes))
        else:
            im = Image.open(str(file_path))

        xdpi = None
        ydpi = None
        img_format = getattr(im, "format", "")

        # 1. Check explicit JFIF density (standard for JPEGs)
        jfif_density = im.info.get("jfif_density")
        jfif_unit = im.info.get("jfif_unit")
        if jfif_density and len(jfif_density) == 2:
            try:
                jx, jy = float(jfif_density[0]), float(jfif_density[1])
                if np.isfinite(jx) and np.isfinite(jy) and jx > 0 and jy > 0:
                    if jfif_unit == 1:  # dots per inch
                        xdpi, ydpi = jx, jy
                    elif jfif_unit == 2:  # dots per cm -> convert to inches
                        xdpi, ydpi = jx * 2.54, jy * 2.54
            except (ValueError, TypeError):
                pass

        # 2. Check explicit EXIF tags (0x011A=XResolution, 0x011B=YResolution, 0x0128=ResolutionUnit)
        if xdpi is None or ydpi is None:
            try:
                exif = im.getexif()
                if exif and (0x011A in exif and 0x011B in exif):
                    x_res = exif.get(0x011A)
                    y_res = exif.get(0x011B)
                    unit = exif.get(0x0128, 2)  # default 2 = inches
                    if x_res is not None and y_res is not None:
                        ex_x = float(x_res[0] / x_res[1] if isinstance(x_res, tuple) else x_res)
                        ex_y = float(y_res[0] / y_res[1] if isinstance(y_res, tuple) else y_res)
                        if np.isfinite(ex_x) and np.isfinite(ex_y) and ex_x > 0 and ex_y > 0:
                            if unit == 2:  # inches
                                xdpi, ydpi = ex_x, ex_y
                            elif unit == 3:  # cm -> inches
                                xdpi, ydpi = ex_x * 2.54, ex_y * 2.54
            except Exception:
                pass

        # 3. For non-JPEG formats (e.g. PNG with pHYs chunk, TIFF), check standard PIL info DPI
        # For JPEG, Pillow synthesizes a fake default (72, 72) when EXIF has no resolution tags;
        # thus JPEG must only be accepted if backed by explicit JFIF density or EXIF resolution.
        if (xdpi is None or ydpi is None) and img_format != "JPEG":
            dpi_info = im.info.get("dpi")
            if dpi_info and len(dpi_info) == 2:
                try:
                    rx, ry = float(dpi_info[0]), float(dpi_info[1])
                    if np.isfinite(rx) and np.isfinite(ry) and rx > 0 and ry > 0:
                        xdpi, ydpi = rx, ry
                except (ValueError, TypeError):
                    pass

        if xdpi is None or ydpi is None:
            return None

        # Hardware plausibility check: scanners operate between 25 and 12000 DPI
        if not (10.0 <= xdpi <= 24000.0 and 10.0 <= ydpi <= 24000.0):
            return None

        # Round near-integers (e.g. PNG 299.9994 -> 300.0)
        final_x = round(xdpi) if abs(xdpi - round(xdpi)) < 0.05 else round(xdpi, 2)
        final_y = round(ydpi) if abs(ydpi - round(ydpi)) < 0.05 else round(ydpi, 2)
        return float(final_x), float(final_y)

    except Exception:
        return None


def calculate_scanner_scale(
    dpi_x: float,
    dpi_y: float | None = None,
    current_shape: tuple[int, int] | None = None,
    original_shape: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Calculate the physical scale (mm/pixel) and metadata for scanner DPI.

    Handles isotropic, anisotropic, and resized scanner images.
    """
    if dpi_y is None:
        dpi_y = dpi_x

    if dpi_x <= 0 or dpi_y <= 0 or not np.isfinite(dpi_x) or not np.isfinite(dpi_y):
        raise ValueError("Scanner DPI must be a positive finite number.")

    eff_dpi_x = float(dpi_x)
    eff_dpi_y = float(dpi_y)

    # Adjust effective DPI if image was resized after acquisition
    if original_shape is not None and current_shape is not None:
        h_orig, w_orig = original_shape[:2]
        h_curr, w_curr = current_shape[:2]
        if w_orig > 0 and h_orig > 0:
            eff_dpi_x = eff_dpi_x * (float(w_curr) / float(w_orig))
            eff_dpi_y = eff_dpi_y * (float(h_curr) / float(h_orig))

    scale_x = 25.4 / eff_dpi_x
    scale_y = 25.4 / eff_dpi_y

    is_anisotropic = abs(eff_dpi_x - eff_dpi_y) >= 0.5

    if not is_anisotropic:
        confirmed_dpi = round(eff_dpi_x, 1)
        if 298.0 <= confirmed_dpi <= 302.0:
            reference_type = "FLATBED_SCANNER_300DPI"
            scanner_dpi: Any = 300
            scale_val: Any = round(25.4 / confirmed_dpi, 5)
        elif 596.0 <= confirmed_dpi <= 604.0:
            reference_type = "FLATBED_SCANNER_600DPI"
            scanner_dpi = 600
            scale_val = round(25.4 / confirmed_dpi, 5)
        elif 148.0 <= confirmed_dpi <= 152.0:
            reference_type = "FLATBED_SCANNER_150DPI"
            scanner_dpi = 150
            scale_val = round(25.4 / confirmed_dpi, 5)
        else:
            reference_type = f"FLATBED_SCANNER_{int(round(confirmed_dpi))}DPI"
            scanner_dpi = int(round(confirmed_dpi))
            scale_val = round(scale_x, 5)

        return {
            "calibration_status": "CALIBRATED",
            "calibration_reference_type": reference_type,
            "mm_per_pixel": scale_val,
            "scale_x": scale_x,
            "scale_y": scale_y,
            "scanner_dpi": scanner_dpi,
            "is_anisotropic": False,
        }
    else:
        # Anisotropic scaling: horizontal and vertical DPI differ
        dpi_x_int = int(round(eff_dpi_x))
        dpi_y_int = int(round(eff_dpi_y))
        reference_type = f"FLATBED_SCANNER_ANISOTROPIC_{dpi_x_int}x{dpi_y_int}DPI"
        scanner_dpi = (dpi_x_int, dpi_y_int)
        scale_val = (round(scale_x, 5), round(scale_y, 5))

        return {
            "calibration_status": "CALIBRATED",
            "calibration_reference_type": reference_type,
            "mm_per_pixel": scale_val,
            "scale_x": scale_x,
            "scale_y": scale_y,
            "scanner_dpi": scanner_dpi,
            "is_anisotropic": True,
        }


def resolve_scanner_calibration(
    raw_bytes: bytes | None = None,
    image: np.ndarray | None = None,
    explicit_dpi: float | tuple[float, float] | None = None,
    explicit_scale: float | None = None,
    original_shape: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Resolve physical calibration for flatbed scanner scans.

    If 300 DPI (or another valid DPI) is confirmed from image metadata or scanner driver,
    returns CALIBRATED with verified scale. If scanner resolution cannot be verified,
    returns UNCALIBRATED with estimated scale 0.12 mm/pixel without falsely claiming calibration.
    """
    # 1. Direct explicit scale override (if caller supplied calibrated mm_per_pixel)
    if explicit_scale is not None:
        try:
            s = float(explicit_scale)
            if s > 0 and np.isfinite(s):
                scale_disp = s if s < 0.0001 else round(s, 5)
                return {
                    "calibration_status": "CALIBRATED",
                    "calibration_reference_type": "DIRECT_SCALE_INPUT",
                    "mm_per_pixel": scale_disp,
                    "scale_x": s,
                    "scale_y": s,
                    "scanner_dpi": None,
                    "is_anisotropic": False,
                }
        except (ValueError, TypeError):
            pass

    # 2. Explicit DPI provided by hardware driver or API parameter
    if explicit_dpi is not None:
        try:
            if isinstance(explicit_dpi, (tuple, list)) and len(explicit_dpi) == 2:
                dx, dy = float(explicit_dpi[0]), float(explicit_dpi[1])
                if dx > 0 and dy > 0 and np.isfinite(dx) and np.isfinite(dy):
                    current_shape = image.shape[:2] if image is not None else None
                    return calculate_scanner_scale(dx, dy, current_shape, original_shape)
            else:
                d = float(explicit_dpi)  # type: ignore
                if d > 0 and np.isfinite(d):
                    current_shape = image.shape[:2] if image is not None else None
                    return calculate_scanner_scale(d, d, current_shape, original_shape)
        except (ValueError, TypeError):
            pass

    # 3. Detect DPI embedded in raw image bytes metadata
    if raw_bytes:
        detected_dpi = extract_dpi_from_image(raw_bytes=raw_bytes)
        if detected_dpi is not None:
            dx, dy = detected_dpi
            current_shape = image.shape[:2] if image is not None else None
            return calculate_scanner_scale(dx, dy, current_shape, original_shape)

    # 4. Scanner resolution cannot be verified -> fail safe, do not falsely claim calibration
    return {
        "calibration_status": "UNCALIBRATED",
        "calibration_reference_type": "DEFAULT_ESTIMATE",
        "mm_per_pixel": ESTIMATED_MM_PER_PIXEL,
        "scale_x": ESTIMATED_MM_PER_PIXEL,
        "scale_y": ESTIMATED_MM_PER_PIXEL,
        "scanner_dpi": None,
        "is_anisotropic": False,
    }


class ScannerError(Exception):
    """Base exception for hardware scanning operations."""
    pass


class ScannerNotFoundError(ScannerError):
    """Raised when no compatible hardware scanner is detected."""
    pass


class ScannerOperationError(ScannerError):
    """Raised when the scanner operation encounters a driver or hardware fault."""
    pass


class ScannerTimeoutError(ScannerError):
    """Raised when the scanner operation times out."""
    pass


class HardwareScanUnsupportedError(ScannerError):
    """Raised when direct hardware scanning is unsupported in the current platform or environment."""
    pass


_last_wia_readback_dpi: tuple[float, float] | None = None


class ScannedImagePath(str):
    """String path subclass holding scan-associated WIA readback DPI metadata."""
    readback_dpi: Tuple[float, float] | None = None


def is_hardware_scan_supported() -> bool:
    """Check if the current host environment supports direct WIA hardware scanning."""
    if sys.platform != "win32":
        return False
    flag = os.getenv("AGRIVISIO_HARDWARE_SCAN_ENABLED", "1").strip().lower()
    return flag not in ("0", "false", "no", "disabled")


def _try_scan_wia(timeout: float = 90.0) -> str:
    """Attempt to acquire an image from a WIA-compatible scanner (Epson / Flatbed).

    Returns path to temporary scanned image carrying scan-associated readback DPI.
    Caller is responsible for cleaning up the temporary file.
    """
    if not is_hardware_scan_supported():
        raise HardwareScanUnsupportedError(
            "Direct hardware scanning is only supported on Windows with WIA device support."
        )

    scan_id = uuid.uuid4().hex
    temp_path = os.path.abspath(os.path.join(tempfile.gettempdir(), f"agrivisio_hw_{scan_id}.jpg"))

    ps_script = """
param([string]$outputPath)
if ([string]::IsNullOrWhiteSpace($outputPath)) {
    $outputPath = $env:AGRIVISIO_SCAN_OUTPUT
}
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

$actualXRes = 300
$actualYRes = 300
foreach ($prop in $item.Properties) {
    if ($prop.PropertyID -eq 6147) { $actualXRes = [int]$prop.Value }
    if ($prop.PropertyID -eq 6148) { $actualYRes = [int]$prop.Value }
}
Write-Output "SCAN_OK:XRES=${actualXRes}:YRES=${actualYRes}"
"""
    env = os.environ.copy()
    env["AGRIVISIO_SCAN_OUTPUT"] = temp_path
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", "& { " + ps_script + " }"],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        LOGGER.error("Hardware scan timed out after %s seconds.", timeout)
        if os.path.isfile(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        raise ScannerTimeoutError(f"Scanner acquisition timed out after {timeout} seconds.") from exc
    except Exception as exc:
        LOGGER.error("Failed to execute scanner PowerShell process: %s", exc)
        if os.path.isfile(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        raise ScannerOperationError(f"Scanner process execution failed: {exc}") from exc

    scan_readback_dpi: Tuple[float, float] | None = None
    for line in result.stdout.splitlines():
        line = line.strip()
        if "SCAN_OK:XRES=" in line:
            import re
            m = re.search(r"SCAN_OK:XRES=(\d+):YRES=(\d+)", line)
            if m:
                scan_readback_dpi = (float(m.group(1)), float(m.group(2)))

    global _last_wia_readback_dpi
    _last_wia_readback_dpi = scan_readback_dpi

    if result.returncode != 0:
        if os.path.isfile(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        combined_output = f"{result.stdout} {result.stderr}".strip()
        if "No scanner found" in combined_output or "0 devices" in combined_output:
            raise ScannerNotFoundError("No scanner found. Please verify that the flatbed scanner is powered on and connected.")
        raise ScannerOperationError(f"Scanner hardware error: {result.stderr.strip() or result.stdout.strip()}")

    if not os.path.isfile(temp_path):
        raise ScannerNotFoundError("No scanner found or image file was not created by WIA driver.")

    result_path = ScannedImagePath(temp_path)
    result_path.readback_dpi = scan_readback_dpi
    return result_path


def acquire_hardware_scan(timeout: float = 90.0) -> Tuple[np.ndarray, bytes]:
    """Acquire an image from attached hardware scanner, returning (image_bgr, raw_bytes).

    Guarantees cleanup of temporary files created during acquisition. Ensures image
    bytes carry the confirmed scanner DPI metadata and standard JPEG format.
    """
    scanned_path = _try_scan_wia(timeout=timeout)
    readback_dpi = getattr(scanned_path, "readback_dpi", _last_wia_readback_dpi)
    try:
        image = cv2.imread(scanned_path)
        if image is None:
            raise ScannerOperationError("Scanner device returned an invalid or corrupt image file.")

        raw_bytes = Path(scanned_path).read_bytes()
        # If scanner returned non-JPEG (e.g. BMP), normalize to JPEG while preserving scan DPI
        is_jpeg = raw_bytes.startswith(b"\xff\xd8\xff")
        embedded_dpi = extract_dpi_from_image(raw_bytes=raw_bytes)
        effective_dpi = embedded_dpi or readback_dpi

        if not is_jpeg or embedded_dpi is None:
            buf = io.BytesIO()
            pil_img = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
            if effective_dpi is not None:
                pil_img.save(buf, format="JPEG", dpi=effective_dpi)
            else:
                pil_img.save(buf, format="JPEG")
            raw_bytes = buf.getvalue()

        return image, raw_bytes
    finally:
        try:
            if os.path.isfile(scanned_path):
                os.unlink(scanned_path)
        except OSError:
            pass
