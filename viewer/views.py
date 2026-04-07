"""
views.py
HTTP views for the biplane web viewer.
"""

import json
from django.shortcuts import render
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.csrf import ensure_csrf_cookie

from .orthanc import orthanc_find_studies, orthanc_fetch_frame_bgr
from .cache   import get_frame, get_status, get_meta, set_status
from .tasks   import load_study_task
from .models  import SharedStudy

import cv2
import numpy as np


# ── Pages ─────────────────────────────────────────────────────────────────────

@ensure_csrf_cookie
def search_page(request):
    return render(request, "viewer/search.html")


def viewer_page(request, study_id):
    cache_id = request.GET.get("cache_id", study_id)
    meta     = get_meta(cache_id)
    job_id   = request.GET.get("job_id", "")

    # Cache expired but we have a stored share record — auto-trigger re-download
    if not meta and not job_id:
        try:
            shared = SharedStudy.objects.get(cache_id=cache_id)
            task   = load_study_task.delay(study_id, shared.instance_ids, cache_id)
            set_status(cache_id, "loading", task.id)
            job_id = task.id
        except SharedStudy.DoesNotExist:
            pass   # unknown cache_id — viewer.js will redirect to search

    return render(request, "viewer/viewer.html", {
        "study_id":    study_id,
        "cache_id":    cache_id,
        "n_frames":    meta["n_frames"]    if meta else 0,
        "cursor_frac": meta["cursor_frac"] if meta else 0.5,
        "job_id":      job_id,
    })


# ── API ───────────────────────────────────────────────────────────────────────

@require_POST
def api_search(request):
    body = json.loads(request.body)
    name = body.get("patient_name", "").strip()
    if not name:
        return JsonResponse({"error": "patient_name required"}, status=400)
    try:
        studies = orthanc_find_studies(name)
        results = []
        for s in studies:
            tags  = s.get("PatientMainDicomTags", {})
            stags = s.get("MainDicomTags", {})
            results.append({
                "id":           s["ID"],
                "patient_name": tags.get("PatientName", "Unknown"),
                "patient_id":   tags.get("PatientID", ""),
                "study_date":   stags.get("StudyDate", ""),
                "description":  stags.get("StudyDescription", ""),
            })
        return JsonResponse({"studies": results})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@require_POST
def api_load_study(request, study_id):
    try:
        body         = json.loads(request.body or b"{}")
        instance_ids = body.get("instance_ids", [])   # list of Orthanc instance IDs

        # Build a cache key that includes selected instances so different
        # selections are treated as separate loads
        import hashlib
        sel_key = hashlib.md5(
            ",".join(sorted(instance_ids)).encode()
        ).hexdigest()[:8] if instance_ids else "all"
        cache_id = f"{study_id}:{sel_key}"

        status = get_status(cache_id)

        if status["status"] == "ready":
            return JsonResponse({"status": "ready", "job_id": status["job_id"],
                                 "meta": status["meta"], "cache_id": cache_id})

        if status["status"] == "loading" and status["job_id"]:
            from celery.result import AsyncResult
            ar = AsyncResult(status["job_id"])
            if ar.state not in ("FAILURE", "SUCCESS", "REVOKED"):
                return JsonResponse({"status": "loading", "job_id": status["job_id"],
                                     "cache_id": cache_id})
            # Task is no longer running (failed/revoked) — redispatch

        # Persist instance selection so shared links can auto-reload after cache expiry
        SharedStudy.objects.update_or_create(
            cache_id=cache_id,
            defaults={"study_id": study_id, "instance_ids": instance_ids},
        )

        task = load_study_task.delay(study_id, instance_ids, cache_id)
        set_status(cache_id, "loading", task.id)
        return JsonResponse({"status": "loading", "job_id": task.id,
                             "cache_id": cache_id})

    except Exception as e:
        import traceback; traceback.print_exc()
        return JsonResponse({"error": str(e)}, status=500)


@require_GET
def api_study_status(request, study_id):
    cache_id = request.GET.get("cache_id", study_id)
    return JsonResponse(get_status(cache_id))


@require_GET
def api_frame(request, study_id, frame_idx, plane):
    if plane not in ("trans", "sag"):
        return HttpResponse(status=400)
    cache_id = request.GET.get("cache_id", study_id)
    data = get_frame(cache_id, int(frame_idx), plane)
    if data is None:
        return HttpResponse(status=404)
    return HttpResponse(data, content_type="image/png")


@require_GET
def api_preview(request, study_id, plane):
    """
    Fetch a preview JPEG of frame 0 for a study.
    plane: 'trans' (top half) or 'sag' (bottom half)
    """
    if plane not in ("trans", "sag"):
        return HttpResponse(status=400)
    try:
        from .orthanc import orthanc_get_biplane_instances
        instances = orthanc_get_biplane_instances(study_id)
        bgr  = orthanc_fetch_frame_bgr(instances[0]["ID"], 0)
        mid  = bgr.shape[0] // 2
        half = bgr[:mid] if plane == "trans" else bgr[mid:]
        half = cv2.resize(half, (160, 160))
        _, buf = cv2.imencode(".jpg", half, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return HttpResponse(buf.tobytes(), content_type="image/jpeg")
    except Exception:
        return HttpResponse(status=404)


@require_GET
def api_instances(request, study_id):
    """Return biplane instances for a study as JSON."""
    try:
        from .orthanc import orthanc_get_biplane_instances
        instances = orthanc_get_biplane_instances(study_id)
        result = []
        for idx, inst in enumerate(instances):
            tags = inst.get("MainDicomTags", {})
            result.append({
                "id":        inst["ID"],
                "n_frames":  tags.get("NumberOfFrames", "?"),
                "inst_num":  tags.get("InstanceNumber", str(idx + 1)),
                "idx":       idx,
            })
        return JsonResponse({"instances": result})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
