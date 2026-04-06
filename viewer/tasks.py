"""
tasks.py
Celery tasks for DICOM download + decode pipeline.
"""

from celery import shared_task
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .orthanc import orthanc_get_biplane_instances, load_frames_from_orthanc
from .cache   import store_frame, store_meta, set_status


def _push(job_id: str, msg: dict):
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f"progress_{job_id}",
        {"type": "progress.update", "message": msg},
    )


@shared_task(bind=True)
def load_study_task(self, study_id: str):
    """
    Full pipeline for one Orthanc study:
      1. Find biplane instances
      2. Download DICOM files (localhost — fast)
      3. Decode all frames on all available cores
      4. Store RGBA PNGs in Redis
      5. Push progress events over WebSocket
    """
    job_id = self.request.id
    set_status(study_id, "loading", job_id)

    try:
        _push(job_id, {"phase": "init", "msg": "Finding instances…"})

        instances = orthanc_get_biplane_instances(study_id)
        total = sum(
            int(i.get("MainDicomTags", {}).get("NumberOfFrames", 1))
            for i in instances
        )

        _push(job_id, {"phase": "decode", "done": 0, "total": total})

        def on_progress(done, total_frames):
            _push(job_id, {"phase": "decode", "done": done, "total": total_frames})

        trans_frames, sag_frames, cursor_fracs = load_frames_from_orthanc(
            instances,
            progress_cb=on_progress,
        )

        _push(job_id, {"phase": "storing", "msg": "Storing frames…"})
        for i, (t, s) in enumerate(zip(trans_frames, sag_frames)):
            store_frame(study_id, i, t, s)

        store_meta(study_id, len(trans_frames), cursor_fracs[0] if cursor_fracs else 0.5)
        set_status(study_id, "ready", job_id)

        _push(job_id, {
            "phase":       "complete",
            "n_frames":    len(trans_frames),
            "cursor_frac": cursor_fracs[0] if cursor_fracs else 0.5,
        })

    except Exception as exc:
        set_status(study_id, "error")
        _push(job_id, {"phase": "error", "msg": str(exc)})
        raise
