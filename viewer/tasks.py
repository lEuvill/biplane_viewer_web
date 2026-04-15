"""
tasks.py
Celery tasks for DICOM download + decode pipeline.
"""

from celery import shared_task
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .orthanc import orthanc_get_biplane_instances, orthanc_get_instances_by_ids, load_frames_from_orthanc
from .cache   import store_frame, store_meta, set_status, get_meta, set_seg_status, get_seg_status


def _push(job_id: str, msg: dict):
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f"progress_{job_id}",
        {"type": "progress.update", "message": msg},
    )


@shared_task(bind=True)
def load_study_task(self, study_id: str, instance_ids: list = None, cache_id: str = None, swapped: bool = False):
    """
    Full pipeline for one Orthanc study:
      1. Find biplane instances (all, or only selected instance_ids)
      2. Download DICOM files (localhost — fast)
      3. Decode all frames on all available cores
      4. Store RGBA PNGs in Redis under cache_id
      5. Push progress events over WebSocket
    """
    job_id   = self.request.id
    cache_id = cache_id or study_id
    set_status(cache_id, "loading", job_id)

    try:
        _push(job_id, {"phase": "init", "msg": "Finding instances…"})

        if instance_ids:
            instances = orthanc_get_instances_by_ids(instance_ids)
        else:
            instances = orthanc_get_biplane_instances(study_id)

        _push(job_id, {"phase": "download", "done": 0, "total": 0})

        def on_download(bytes_done, bytes_total):
            _push(job_id, {"phase": "download",
                            "done": bytes_done, "total": bytes_total})

        def on_progress(done, total_frames):
            _push(job_id, {"phase": "decode", "done": done, "total": total_frames})

        trans_frames, sag_frames, cursor_fracs = load_frames_from_orthanc(
            instances,
            progress_cb=on_progress,
            download_cb=on_download,
            swapped=swapped,
        )

        _push(job_id, {"phase": "storing", "msg": "Storing frames…"})
        for i, (t, s) in enumerate(zip(trans_frames, sag_frames)):
            store_frame(cache_id, i, t, s)

        store_meta(cache_id, len(trans_frames), cursor_fracs[0] if cursor_fracs else 0.5)
        set_status(cache_id, "ready", job_id)

        _push(job_id, {
            "phase":       "complete",
            "n_frames":    len(trans_frames),
            "cursor_frac": cursor_fracs[0] if cursor_fracs else 0.5,
        })

    except Exception as exc:
        set_status(cache_id, "error")
        _push(job_id, {"phase": "error", "msg": str(exc)})
        raise


def _load_run_segmentation():
    """
    Load run_segmentation directly from the source file on disk, bypassing
    Python's sys.modules cache.  This means changes to segmentation.py are
    always picked up without restarting the Celery worker.
    """
    import importlib.util
    from pathlib import Path
    seg_path = Path(__file__).parent / "segmentation.py"
    spec = importlib.util.spec_from_file_location("_segmentation_fresh", seg_path)
    mod  = importlib.util.module_from_spec(spec)
    # Set the package so relative imports (from .cache import …) resolve correctly
    mod.__package__ = "viewer"
    spec.loader.exec_module(mod)
    return mod.run_segmentation


@shared_task(bind=True)
def segment_study_task(self, cache_id: str):
    """
    Run nnUNet segmentation on all transverse frames of a loaded study.
    Stores per-frame binary mask PNGs in the cache under the same cache_id.
    Pushes progress events over WebSocket using the same job_id mechanism.
    """
    run_segmentation = _load_run_segmentation()

    job_id = self.request.id
    set_seg_status(cache_id, "running", job_id)

    try:
        meta = get_meta(cache_id)
        if not meta:
            raise RuntimeError(
                f"Study '{cache_id}' not found in cache. Load the study first."
            )
        n_frames = meta["n_frames"]

        _push(job_id, {"phase": "seg_start", "total": n_frames})

        def on_progress(done, total):
            _push(job_id, {"phase": "seg_progress", "done": done, "total": total})

        run_segmentation(cache_id, n_frames, progress_cb=on_progress)

        set_seg_status(cache_id, "ready", job_id)
        _push(job_id, {"phase": "seg_complete", "n_frames": n_frames})

    except Exception as exc:
        set_seg_status(cache_id, "error")
        _push(job_id, {"phase": "seg_error", "msg": str(exc)})
        raise
