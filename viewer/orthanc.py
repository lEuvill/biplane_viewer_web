"""
orthanc.py
Orthanc PACS HTTP client — no Qt dependencies.
Ported from biplane_3d/orthanc_client.py.
"""

import os
import threading
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
import multiprocessing

import cv2
import numpy as np
import pydicom
import requests
from django.conf import settings


# ── Per-thread sessions ───────────────────────────────────────────────────────

_local = threading.local()

def _get_session() -> requests.Session:
    if not hasattr(_local, "session"):
        sess = requests.Session()
        sess.auth   = (settings.ORTHANC_USER, settings.ORTHANC_PASS)
        sess.verify = True
        _local.session = sess
    return _local.session


def _orhttp(method: str, path: str, timeout: int = 30, **kwargs):
    url  = settings.ORTHANC_URL.rstrip("/") + path
    resp = _get_session().request(method, url, timeout=timeout, **kwargs)
    resp.raise_for_status()
    return resp


# ── Patient name normalisation ────────────────────────────────────────────────

def _normalize_patient_query(raw: str) -> str:
    parts = [p.strip() for p in raw.replace("^", " ").split() if p.strip()]
    if not parts:
        return "*"
    def _wrap(s):
        if not s.startswith("*"): s = "*" + s
        if not s.endswith("*"):   s += "*"
        return s
    return "^".join(_wrap(p) for p in parts)


# ── Study / instance lookup ───────────────────────────────────────────────────

def orthanc_find_studies(patient_name: str) -> list:
    query = _normalize_patient_query(patient_name)
    body  = {"Level": "Study", "Query": {"PatientName": query}, "Expand": True}
    return _orhttp("POST", "/tools/find", json=body).json()


def orthanc_get_biplane_instances(orthanc_study_id: str) -> list:
    series_list   = _orhttp("GET", f"/studies/{orthanc_study_id}/series").json()
    all_instances = []
    for s in series_list:
        all_instances.extend(_orhttp("GET", f"/series/{s['ID']}/instances").json())

    biplane = [
        inst for inst in all_instances
        if _safe_int(inst.get("MainDicomTags", {}).get("NumberOfFrames", 0)) > 100
    ]

    if not biplane:
        raise ValueError("No biplane instances found (expected instances with >100 frames).")

    return sorted(biplane, key=lambda i: _safe_int(
        i.get("MainDicomTags", {}).get("InstanceNumber", 0)
    ))


def _safe_int(v):
    try:
        return int(v)
    except (ValueError, TypeError):
        return 0


def orthanc_fetch_frame_bgr(instance_id: str, frame_n: int) -> np.ndarray:
    for ep in [
        f"/instances/{instance_id}/frames/{frame_n}/preview",
        f"/instances/{instance_id}/frames/{frame_n}/image-uint8",
        f"/instances/{instance_id}/frames/{frame_n}/png",
    ]:
        try:
            resp = _orhttp("GET", ep)
            arr  = np.frombuffer(resp.content, dtype=np.uint8)
            bgr  = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if bgr is not None:
                return bgr
        except Exception:
            continue
    raise RuntimeError(f"Could not fetch preview frame from instance {instance_id}")


# ── DICOM download + decode ───────────────────────────────────────────────────

def _download_instance_bytes(inst_id: str, cancel_flag=None) -> bytes:
    url  = settings.ORTHANC_URL.rstrip("/") + f"/instances/{inst_id}/file"
    resp = _get_session().get(url, timeout=300, stream=True)
    resp.raise_for_status()
    buf = BytesIO()
    for chunk in resp.iter_content(chunk_size=512 * 1024):
        if cancel_flag and cancel_flag[0]:
            resp.close()
            raise RuntimeError("Cancelled")
        if chunk:
            buf.write(chunk)
    return buf.getvalue()


def load_frames_from_orthanc(instances, progress_cb=None, cancel_flag=None) -> tuple:
    """
    Download all instances (localhost — fast) then decode all frames in parallel.
    Returns (trans_frames, sag_frames, cursor_fracs).
    """
    from .frame_processor import decode_and_process

    # Phase 1: Download all instances (localhost HTTP, near-instant)
    dicom_data = [None] * len(instances)
    with ThreadPoolExecutor(max_workers=len(instances)) as pool:
        future_map = {
            pool.submit(_download_instance_bytes, inst["ID"], cancel_flag): i
            for i, inst in enumerate(instances)
        }
        for fut in as_completed(future_map):
            if cancel_flag and cancel_flag[0]:
                raise RuntimeError("Cancelled")
            i = future_map[fut]
            dicom_data[i] = fut.result()

    # Phase 2: Extract compressed frame bytes
    import pylibjpeg  # noqa — registers JPEG handler
    from pydicom.encaps import generate_pixel_data_frame

    all_jobs = []
    for data in dicom_data:
        ds = pydicom.dcmread(BytesIO(data))
        n  = int(getattr(ds, "NumberOfFrames", 1))
        r, c = int(ds.Rows), int(ds.Columns)
        for fb in generate_pixel_data_frame(ds.PixelData, n):
            all_jobs.append((fb, r, c))

    total = len(all_jobs)

    # Phase 3: Decode in parallel
    results   = [None] * total
    mp_ctx    = multiprocessing.get_context("spawn")
    n_workers = max(1, os.cpu_count() or 4)

    with ProcessPoolExecutor(max_workers=n_workers, mp_context=mp_ctx) as pool:
        future_map = {pool.submit(decode_and_process, job): i
                      for i, job in enumerate(all_jobs)}
        done = [0]
        for fut in as_completed(future_map):
            if cancel_flag and cancel_flag[0]:
                pool.shutdown(wait=False, cancel_futures=True)
                raise RuntimeError("Cancelled")
            i = future_map[fut]
            try:
                results[i] = fut.result()
            except Exception as e:
                print(f"Warning: frame {i} decode failed: {e}")
            done[0] += 1
            if progress_cb:
                progress_cb(done[0], total)

    trans, sag, raw_fracs = [], [], []
    for item in results:
        if item is None:
            continue
        t, s, frac = item
        trans.append(t)
        sag.append(s)
        raw_fracs.append(frac)

    arr = np.array(raw_fracs) if raw_fracs else np.array([0.5])
    vals, cnts = np.unique(np.round(arr, 2), return_counts=True)
    maj = float(vals[np.argmax(cnts)])
    return trans, sag, [maj] * len(trans)
