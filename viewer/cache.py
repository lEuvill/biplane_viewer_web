"""
cache.py
Redis helpers for frame storage and study metadata.

Key scheme:
  study:<id>:meta          → JSON {n_frames, cursor_frac}
  study:<id>:trans:<i>     → RGBA PNG bytes
  study:<id>:sag:<i>       → RGBA PNG bytes
  study:<id>:job_id        → Celery task ID (for dedup)
  study:<id>:status        → "loading" | "ready" | "error"
"""

from __future__ import annotations

import json
from django.core.cache import cache
from django.conf import settings

FRAME_TTL = settings.FRAME_TTL


def store_frame(study_id: str, frame_idx: int, trans_png: bytes, sag_png: bytes):
    cache.set(f"study:{study_id}:trans:{frame_idx}", trans_png, timeout=FRAME_TTL)
    cache.set(f"study:{study_id}:sag:{frame_idx}",   sag_png,   timeout=FRAME_TTL)


def get_frame(study_id: str, frame_idx: int, plane: str) -> bytes | None:
    return cache.get(f"study:{study_id}:{plane}:{frame_idx}")


def store_meta(study_id: str, n_frames: int, cursor_frac: float):
    cache.set(f"study:{study_id}:meta",
              json.dumps({"n_frames": n_frames, "cursor_frac": cursor_frac}),
              timeout=FRAME_TTL)


def get_meta(study_id: str) -> dict | None:
    raw = cache.get(f"study:{study_id}:meta")
    return json.loads(raw) if raw else None


def set_status(study_id: str, status: str, job_id: str = ""):
    cache.set(f"study:{study_id}:status", status, timeout=FRAME_TTL)
    if job_id:
        cache.set(f"study:{study_id}:job_id", job_id, timeout=FRAME_TTL)
        # Reverse lookup: job_id → study_id (so the WS consumer can find it)
        cache.set(f"job:{job_id}:study_id", study_id, timeout=FRAME_TTL)


def get_study_id_for_job(job_id: str) -> str | None:
    return cache.get(f"job:{job_id}:study_id")


def get_status(study_id: str) -> dict:
    status = cache.get(f"study:{study_id}:status") or "none"
    job_id = cache.get(f"study:{study_id}:job_id") or ""
    meta   = get_meta(study_id)
    return {"status": status, "job_id": job_id, "meta": meta}
