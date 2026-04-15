"""
cache.py
Frame storage with an optional disk persistence layer on top of Redis.

Key scheme (Redis):
  study:<id>:meta          → JSON {n_frames, cursor_frac}
  study:<id>:trans:<i>     → RGBA PNG bytes
  study:<id>:sag:<i>       → RGBA PNG bytes
  study:<id>:seg:<i>       → grayscale PNG bytes (0=bg, 255=artery)
  study:<id>:seg_status    → "none" | "running" | "ready" | "error"
  study:<id>:seg_job_id    → Celery task ID for segmentation
  study:<id>:job_id        → Celery task ID (for dedup)
  study:<id>:status        → "loading" | "ready" | "error"

Disk layout (when FRAME_STORE_DIR is set):
  <FRAME_STORE_DIR>/<safe_id>/meta.json
  <FRAME_STORE_DIR>/<safe_id>/trans/<frame_idx>.png
  <FRAME_STORE_DIR>/<safe_id>/sag/<frame_idx>.png
  <FRAME_STORE_DIR>/<safe_id>/seg/<frame_idx>.png

  <safe_id> is the cache_id with ':' replaced by '_' for filesystem safety.

Read priority: Redis → disk → (caller re-downloads from Orthanc)
Write: always write to both Redis and disk (if enabled).
"""

from __future__ import annotations

import json
from pathlib import Path
from django.core.cache import cache
from django.conf import settings

FRAME_TTL      = settings.FRAME_TTL
FRAME_STORE    = settings.FRAME_STORE_DIR   # Path or None


# ── Disk helpers ──────────────────────────────────────────────────────────────

def _safe_id(cache_id: str) -> str:
    """Convert cache_id to a filesystem-safe directory name."""
    return cache_id.replace(":", "_").replace("/", "_")


def _frame_path(cache_id: str, plane: str, frame_idx: int) -> Path:
    return FRAME_STORE / _safe_id(cache_id) / plane / f"{frame_idx}.png"


def _meta_path(cache_id: str) -> Path:
    return FRAME_STORE / _safe_id(cache_id) / "meta.json"


def _write_disk_frame(cache_id: str, plane: str, frame_idx: int, data: bytes):
    p = _frame_path(cache_id, plane, frame_idx)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def _read_disk_frame(cache_id: str, plane: str, frame_idx: int) -> bytes | None:
    p = _frame_path(cache_id, plane, frame_idx)
    return p.read_bytes() if p.exists() else None


def _write_disk_meta(cache_id: str, n_frames: int, cursor_frac: float):
    p = _meta_path(cache_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"n_frames": n_frames, "cursor_frac": cursor_frac}))


def _read_disk_meta(cache_id: str) -> dict | None:
    p = _meta_path(cache_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def store_frame(cache_id: str, frame_idx: int, trans_png: bytes, sag_png: bytes):
    cache.set(f"study:{cache_id}:trans:{frame_idx}", trans_png, timeout=FRAME_TTL)
    cache.set(f"study:{cache_id}:sag:{frame_idx}",   sag_png,   timeout=FRAME_TTL)
    if FRAME_STORE:
        _write_disk_frame(cache_id, "trans", frame_idx, trans_png)
        _write_disk_frame(cache_id, "sag",   frame_idx, sag_png)


def get_frame(cache_id: str, frame_idx: int, plane: str) -> bytes | None:
    # 1. Redis
    data = cache.get(f"study:{cache_id}:{plane}:{frame_idx}")
    if data is not None:
        return data

    # 2. Disk fallback
    if FRAME_STORE:
        data = _read_disk_frame(cache_id, plane, frame_idx)
        if data is not None:
            # Re-warm Redis so next request is fast
            cache.set(f"study:{cache_id}:{plane}:{frame_idx}", data, timeout=FRAME_TTL)
            return data

    return None


def store_meta(cache_id: str, n_frames: int, cursor_frac: float):
    cache.set(f"study:{cache_id}:meta",
              json.dumps({"n_frames": n_frames, "cursor_frac": cursor_frac}),
              timeout=FRAME_TTL)
    if FRAME_STORE:
        _write_disk_meta(cache_id, n_frames, cursor_frac)


def get_meta(cache_id: str) -> dict | None:
    # 1. Redis
    raw = cache.get(f"study:{cache_id}:meta")
    if raw:
        return json.loads(raw)

    # 2. Disk fallback
    if FRAME_STORE:
        meta = _read_disk_meta(cache_id)
        if meta:
            # Re-warm Redis
            cache.set(f"study:{cache_id}:meta",
                      json.dumps(meta), timeout=FRAME_TTL)
            return meta

    return None


def set_status(cache_id: str, status: str, job_id: str = ""):
    cache.set(f"study:{cache_id}:status", status, timeout=FRAME_TTL)
    if job_id:
        cache.set(f"study:{cache_id}:job_id", job_id, timeout=FRAME_TTL)
        cache.set(f"job:{job_id}:study_id", cache_id, timeout=FRAME_TTL)


def store_seg_mask(cache_id: str, frame_idx: int, mask_png: bytes,
                   plane: str = "seg"):
    """plane: 'seg' (artery), 'lumen', or 'plaque'"""
    cache.set(f"study:{cache_id}:{plane}:{frame_idx}", mask_png, timeout=FRAME_TTL)
    if FRAME_STORE:
        _write_disk_frame(cache_id, plane, frame_idx, mask_png)


def get_seg_mask(cache_id: str, frame_idx: int,
                 plane: str = "seg") -> bytes | None:
    """plane: 'seg' (artery), 'lumen', or 'plaque'"""
    data = cache.get(f"study:{cache_id}:{plane}:{frame_idx}")
    if data is not None:
        return data
    if FRAME_STORE:
        data = _read_disk_frame(cache_id, plane, frame_idx)
        if data is not None:
            cache.set(f"study:{cache_id}:{plane}:{frame_idx}", data, timeout=FRAME_TTL)
            return data
    return None


def set_seg_status(cache_id: str, status: str, job_id: str = ""):
    cache.set(f"study:{cache_id}:seg_status",  status, timeout=FRAME_TTL)
    if job_id:
        cache.set(f"study:{cache_id}:seg_job_id", job_id, timeout=FRAME_TTL)
        cache.set(f"job:{job_id}:study_id", cache_id, timeout=FRAME_TTL)


def _seg_complete(cache_id: str) -> bool:
    """
    True only if all three mask planes exist for both frame 0 and the last frame.
    Uses the study meta to determine n_frames; falls back to checking frame 0 only
    if meta is unavailable.
    """
    meta = _read_disk_meta(cache_id) if FRAME_STORE else None
    if meta is None:
        raw = cache.get(f"study:{cache_id}:meta")
        if raw:
            import json as _json
            meta = _json.loads(raw)

    n_frames = meta["n_frames"] if meta else 1
    check_frames = {0, max(0, n_frames - 1)}   # first + last

    for frame_idx in check_frames:
        for plane in ("seg", "lumen", "plaque"):
            if cache.get(f"study:{cache_id}:{plane}:{frame_idx}") is not None:
                continue
            if FRAME_STORE and _read_disk_frame(cache_id, plane, frame_idx) is not None:
                continue
            return False
    return True


def get_seg_status(cache_id: str) -> dict:
    status = cache.get(f"study:{cache_id}:seg_status") or "none"
    job_id = cache.get(f"study:{cache_id}:seg_job_id") or ""

    # If Redis says ready, verify all planes are actually present at first+last frame
    if status == "ready" and not _seg_complete(cache_id):
        status = "none"

    # Disk recovery: Redis expired but masks exist on disk
    if status == "none" and FRAME_STORE and _seg_complete(cache_id):
        status = "ready"

    return {"status": status, "job_id": job_id}


def get_study_id_for_job(job_id: str) -> str | None:
    return cache.get(f"job:{job_id}:study_id")


def get_status(cache_id: str) -> dict:
    status = cache.get(f"study:{cache_id}:status") or "none"
    job_id = cache.get(f"study:{cache_id}:job_id") or ""
    meta   = get_meta(cache_id)

    # If disk has the frames but Redis status expired, report ready
    if status == "none" and FRAME_STORE and _read_disk_meta(cache_id):
        status = "ready"

    return {"status": status, "job_id": job_id, "meta": meta}
