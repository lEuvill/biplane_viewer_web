"""
views.py
HTTP views for the biplane web viewer.
"""

import json
from django.shortcuts import render
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST, require_GET

from .orthanc import orthanc_find_studies, orthanc_fetch_frame_bgr
from .cache   import get_frame, get_status, get_meta, set_status
from .tasks   import load_study_task

import cv2
import numpy as np


# ── Pages ─────────────────────────────────────────────────────────────────────

def search_page(request):
    return render(request, "viewer/search.html")


def viewer_page(request, study_id):
    meta = get_meta(study_id)
    return render(request, "viewer/viewer.html", {
        "study_id":    study_id,
        "n_frames":    meta["n_frames"]    if meta else 0,
        "cursor_frac": meta["cursor_frac"] if meta else 0.5,
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
    status = get_status(study_id)

    if status["status"] == "ready":
        return JsonResponse({"status": "ready", "job_id": status["job_id"],
                             "meta": status["meta"]})

    if status["status"] == "loading" and status["job_id"]:
        return JsonResponse({"status": "loading", "job_id": status["job_id"]})

    task = load_study_task.delay(study_id)
    set_status(study_id, "loading", task.id)
    return JsonResponse({"status": "loading", "job_id": task.id})


@require_GET
def api_study_status(request, study_id):
    return JsonResponse(get_status(study_id))


@require_GET
def api_frame(request, study_id, frame_idx, plane):
    if plane not in ("trans", "sag"):
        return HttpResponse(status=400)
    data = get_frame(study_id, int(frame_idx), plane)
    if data is None:
        return HttpResponse(status=404)
    return HttpResponse(data, content_type="image/png")


@require_GET
def api_preview(request, study_id):
    """Fetch a preview JPEG of frame 0 from Orthanc for the study browser."""
    try:
        from .orthanc import orthanc_get_biplane_instances
        instances = orthanc_get_biplane_instances(study_id)
        bgr = orthanc_fetch_frame_bgr(instances[0]["ID"], 0)
        bgr = cv2.resize(bgr, (200, 200))
        _, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return HttpResponse(buf.tobytes(), content_type="image/jpeg")
    except Exception:
        return HttpResponse(status=404)
