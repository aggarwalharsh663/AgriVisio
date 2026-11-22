"""Image security validation and decompression bomb prevention for AgriVisio.

Inspects untrusted image headers before memory allocation and OpenCV decoding to prevent
decompression bombs, memory exhaustion, and denial-of-service attacks.
"""

from __future__ import annotations

import asyncio
import io
import struct
import warnings
from typing import Tuple

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError

# Security Resource Limits
UPLOAD_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
UPLOAD_MAX_PIXELS = 20_000_000        # 20 Megapixels
MAX_SINGLE_DIMENSION = 20_000         # Maximum width or height in pixels
ALLOWED_IMAGE_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})
CONCURRENT_DECODE_LIMIT = 4

# Async Semaphore for limiting concurrent decode/analysis operations
UPLOAD_CONCURRENCY_SEMAPHORE = asyncio.Semaphore(CONCURRENT_DECODE_LIMIT)


class ImageSecurityError(ValueError):
    """Base exception for image security and validation failures."""
    pass


class OversizedFileError(ImageSecurityError):
    """Raised when file content length exceeds maximum allowed byte size."""
    pass


class DecompressionBombError(ImageSecurityError):
    """Raised when declared dimensions or total pixels exceed safe decompression thresholds."""
    pass


class UnsupportedFormatError(ImageSecurityError):
    """Raised when the actual container or codec format is not among allowed formats."""
    pass


class CorruptImageError(ImageSecurityError):
    """Raised when the image header is truncated, invalid, or unidentifiable."""
    pass


def validate_image_header(
    raw_bytes: bytes,
    max_bytes: int = UPLOAD_MAX_BYTES,
    max_pixels: int = UPLOAD_MAX_PIXELS,
    allowed_formats: frozenset[str] = ALLOWED_IMAGE_FORMATS,
) -> Tuple[int, int, str]:
    """Validate image bytes before decompression to prevent memory exhaustion and DoS.

    Inspects container and codec headers using Pillow without decoding pixel data into memory.
    Enforces maximum file size, allowable image formats (JPEG, PNG, WebP), dimension boundaries,
    and catches Pillow DecompressionBombWarning / DecompressionBombError.

    Returns:
        tuple of (width, height, format_name)

    Raises:
        OversizedFileError: If raw bytes exceed max_bytes.
        DecompressionBombError: If declared image dimensions or total pixels exceed max_pixels.
        UnsupportedFormatError: If image is not an allowed format (e.g. GIF, TIFF, BMP).
        CorruptImageError: If headers are truncated, malformed, or unidentifiable.
    """
    if len(raw_bytes) > max_bytes:
        raise OversizedFileError("Photo must be 10 MB or smaller.")

    if not raw_bytes or len(raw_bytes) < 12:
        raise CorruptImageError("Choose a valid JPEG, PNG, or WebP photo.")

    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        warnings.simplefilter("error", Image.DecompressionBombError)
        old_limit = Image.MAX_IMAGE_PIXELS
        try:
            Image.MAX_IMAGE_PIXELS = max_pixels
            try:
                # Open only inspects the file header; pixel data is NOT decoded into memory
                with Image.open(io.BytesIO(raw_bytes)) as im:
                    fmt = im.format
                    width, height = im.size
            except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
                raise DecompressionBombError(
                    "Photo dimensions must be 20 megapixels or smaller."
                ) from exc
            except (UnidentifiedImageError, OSError, SyntaxError, struct.error, ValueError) as exc:
                raise CorruptImageError("Choose a valid JPEG, PNG, or WebP photo.") from exc
            except Exception as exc:
                raise CorruptImageError("Choose a valid JPEG, PNG, or WebP photo.") from exc
        finally:
            Image.MAX_IMAGE_PIXELS = old_limit

    if fmt is None or fmt.upper() not in allowed_formats:
        raise UnsupportedFormatError("Choose a valid JPEG, PNG, or WebP photo.")

    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise CorruptImageError("Choose a valid JPEG, PNG, or WebP photo.")

    if width > MAX_SINGLE_DIMENSION or height > MAX_SINGLE_DIMENSION:
        raise DecompressionBombError("Photo dimensions must be 20 megapixels or smaller.")

    total_pixels = width * height
    if total_pixels > max_pixels:
        raise DecompressionBombError("Photo dimensions must be 20 megapixels or smaller.")

    return width, height, fmt.upper()


def safe_decode_image(
    raw_bytes: bytes,
    max_bytes: int = UPLOAD_MAX_BYTES,
    max_pixels: int = UPLOAD_MAX_PIXELS,
) -> np.ndarray:
    """Safely decode an image using OpenCV ONLY after pre-validation has succeeded.

    Guarantees that untrusted bytes never undergo expensive OpenCV bitmap decompression
    until dimensions, formats, and decompression bomb checks have passed.
    """
    # 1. Inspect and validate header without pixel decompression
    width, height, fmt = validate_image_header(raw_bytes, max_bytes=max_bytes, max_pixels=max_pixels)

    # 2. Decode using OpenCV
    decoded = cv2.imdecode(np.frombuffer(raw_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        raise CorruptImageError("Choose a valid JPEG, PNG, or WebP photo.")

    # 3. Post-decode defense-in-depth dimension check
    dec_height, dec_width = decoded.shape[:2]
    if dec_width * dec_height > max_pixels:
        raise DecompressionBombError("Photo dimensions must be 20 megapixels or smaller.")

    return decoded
