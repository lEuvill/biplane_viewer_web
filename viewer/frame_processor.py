"""
frame_processor.py
Subprocess-safe frame decoder — NO Qt / Django / PyVista imports.
Called inside ProcessPoolExecutor workers.
"""

import numpy as np
import cv2
import pylibjpeg

PROC_W = 300
PROC_H = 300
DARK_CUTOFF = 15


def decode_and_process(args):
    """
    Decode one JPEG-Lossless compressed DICOM frame.

    args = (frame_bytes, rows, cols)
    Returns (trans_rgba bytes, sag_rgba bytes, cursor_frac float)
    where each rgba bytes is a PNG-encoded 300×300 RGBA image
    (dark pixels have alpha=0 for Three.js transparency).
    """
    frame_bytes, rows, cols = args

    arr = pylibjpeg.decode(frame_bytes)
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)
    arr = arr.reshape(rows, cols, -1).astype(np.uint8)
    bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)

    mid      = bgr.shape[0] // 2
    sag_half = bgr[mid:]

    # Cursor detection
    b = sag_half[:, :, 0].astype(np.int32)
    g = sag_half[:, :, 1].astype(np.int32)
    r = sag_half[:, :, 2].astype(np.int32)
    col_cnts = ((b > 140) & (g > 140) & (r < 100)).sum(axis=0)
    best_x   = int(col_cnts.argmax())
    frac     = best_x / sag_half.shape[1] if sag_half.shape[1] > 0 else 0.5

    trans_gray = cv2.resize(cv2.cvtColor(bgr[:mid], cv2.COLOR_BGR2GRAY),
                            (PROC_W, PROC_H), interpolation=cv2.INTER_AREA)
    sag_gray   = cv2.resize(cv2.cvtColor(sag_half, cv2.COLOR_BGR2GRAY),
                            (PROC_W, PROC_H), interpolation=cv2.INTER_AREA)

    trans_png = _to_rgba_png(_norm(trans_gray))
    sag_png   = _to_rgba_png(_norm(sag_gray))

    return trans_png, sag_png, frac


def _norm(gray):
    """Per-frame min-max normalisation to full 0-255 range."""
    mn, mx = int(gray.min()), int(gray.max())
    if mx <= 0 or mx == mn:
        return gray
    return ((gray.astype(np.float32) - mn) / (mx - mn) * 255).astype(np.uint8)


def _to_rgba_png(gray):
    """Convert grayscale array to RGBA PNG bytes with dark pixels transparent."""
    rgba = np.zeros((PROC_H, PROC_W, 4), dtype=np.uint8)
    rgba[:, :, :3] = gray[:, :, np.newaxis]
    rgba[:, :,  3] = np.where(gray > DARK_CUTOFF, 255, 0).astype(np.uint8)
    _, buf = cv2.imencode(".png", rgba)
    return buf.tobytes()
