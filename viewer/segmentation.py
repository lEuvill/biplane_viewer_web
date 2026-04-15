"""
segmentation.py
Orchestrates nnUNet inference for a cached study.

Reads transverse frame PNGs from the cache, writes them to a temp dir,
runs seg_worker.py in the GPU Python venv as a subprocess, reads back
mask PNGs, and stores them in the cache.
"""

import subprocess
import tempfile
import os
from pathlib import Path
from typing import Callable

from .cache import get_frame, store_seg_mask

# Path to the GPU Python interpreter (CUDA-enabled venv)
GPU_PYTHON = r"D:\Vasolab\VasolabsPlaque\venv\Scripts\python.exe"

SEG_WORKER = str(Path(__file__).parent / "seg_worker.py")


def run_segmentation(
    cache_id: str,
    n_frames: int,
    progress_cb: Callable[[int, int], None] | None = None,
):
    """
    Run nnUNet segmentation on all transverse frames of a study.

    Reads frames from the local cache, invokes seg_worker.py in the GPU venv
    as a subprocess (streaming per-frame progress), then stores the resulting
    binary mask PNGs back into the cache.

    progress_cb(done, total) is called after each frame completes.
    Raises RuntimeError if the worker exits with a non-zero status.
    """
    with tempfile.TemporaryDirectory() as tmp:
        input_dir  = Path(tmp) / "input"
        output_dir = Path(tmp) / "output"
        input_dir.mkdir()

        # ── Write cached frames to temp input dir ──────────────────────────
        for i in range(n_frames):
            png_bytes = get_frame(cache_id, i, "trans")
            if png_bytes is None:
                raise RuntimeError(
                    f"Frame {i} not found in cache for study '{cache_id}'. "
                    "Ensure the study is fully loaded before running segmentation."
                )
            (input_dir / f"{i:05d}.png").write_bytes(png_bytes)

        # ── Launch GPU subprocess ───────────────────────────────────────────
        proc = subprocess.Popen(
            [
                GPU_PYTHON,
                SEG_WORKER,
                "--input-dir",  str(input_dir),
                "--output-dir", str(output_dir),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        total_seen = n_frames
        done = 0

        for line in proc.stdout:
            line = line.strip()
            if line.startswith("TOTAL:"):
                try:
                    total_seen = int(line.split(":", 1)[1])
                except ValueError:
                    pass
            elif line.startswith("PROGRESS:"):
                try:
                    parts = line.split(":", 1)[1].split("/")
                    done = int(parts[0])
                    if progress_cb:
                        progress_cb(done, total_seen)
                except (ValueError, IndexError):
                    pass
            elif line == "DONE":
                pass

        proc.wait()
        stderr_out = proc.stderr.read()
        if proc.returncode != 0:
            raise RuntimeError(
                f"seg_worker.py exited with code {proc.returncode}.\n"
                f"stderr:\n{stderr_out[-2000:]}"
            )

        # ── Read mask PNGs and store in cache ───────────────────────────────
        for i in range(n_frames):
            stem = f"{i:05d}"
            for plane, filename in (
                ("seg",    f"{stem}.png"),
                ("lumen",  f"{stem}_lumen.png"),
                ("plaque", f"{stem}_plaque.png"),
            ):
                p = output_dir / filename
                if not p.exists():
                    raise RuntimeError(
                        f"Segmentation mask '{filename}' not found in output dir."
                    )
                store_seg_mask(cache_id, i, p.read_bytes(), plane=plane)
