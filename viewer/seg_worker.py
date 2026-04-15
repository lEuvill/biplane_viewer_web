"""
seg_worker.py
Standalone nnUNet v2 inference script — runs in the GPU Python venv.
Called as a subprocess by segmentation.py.

Two-pass pipeline per frame:
  Pass 1 — Artery model  → binary artery mask  (0/255)
  Pass 2 — Lumen model   → 3-class mask applied inside artery area:
             0=background, 1=lumen, 255=plaque (encoded as 128/255 so
             cv2 can write a single grayscale PNG)

Output files per frame (all in --output-dir):
  <name>.png        artery binary mask  (0 | 255)
  <name>_lumen.png  lumen binary mask   (0 | 255)
  <name>_plaque.png plaque binary mask  (0 | 255)

stdout:
    TOTAL:<n>
    PROGRESS:<i>/<n>   (one per frame, after both passes)
    DONE
"""

import argparse
from pathlib import Path

import numpy as np
import cv2

ARTERY_MODEL_DIR = (
    r"D:\Vasolab\Butterfly_Training\Model_Arteries"
    r"\nnUnet_results\Dataset004_Bulb-52k"
    r"\nnUNetTrainer__nnUNetPlans__2d"
)

LUMEN_MODEL_DIR = (
    r"D:\Vasolab\Butterfly_Training\Model_Lumen"
    r"\nnUnet_results\Dataset025_ALL"
    r"\nnUNetTrainerPlaque__nnUNetPlans__2d"
)

# spacing for 2D NaturalImage2DIO models (z=999 placeholder)
IMG_PROPS = {"spacing": [999.0, 1.0, 1.0]}


def load_gray(png_path: Path) -> np.ndarray:
    """Read PNG as uint8 grayscale; fall back to imdecode for non-ASCII paths."""
    img = cv2.imread(str(png_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        with open(png_path, "rb") as f:
            raw = np.frombuffer(f.read(), dtype=np.uint8)
        img = cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise RuntimeError(f"Could not read {png_path}")
    return img


def make_predictor(device, model_dir, folds, checkpoint="checkpoint_best.pth"):
    from nnunetv2.inference.predict_from_raw_data import nnUNetPredictor
    p = nnUNetPredictor(
        tile_step_size=0.5,
        use_gaussian=True,
        use_mirroring=True,
        perform_everything_on_device=True,
        device=device,
        verbose=False,
        verbose_preprocessing=False,
        allow_tqdm=False,
    )
    p.initialize_from_trained_model_folder(
        model_dir,
        use_folds=folds,
        checkpoint_name=checkpoint,
    )
    return p


def predict(predictor, gray: np.ndarray) -> np.ndarray:
    """Run inference on a (H, W) uint8 grayscale image. Returns (H, W) int array."""
    img_arr = gray[np.newaxis, np.newaxis].astype(np.float32)  # (1, 1, H, W)
    seg = predictor.predict_single_npy_array(
        img_arr,
        IMG_PROPS,
        segmentation_previous_stage=None,
        output_file_truncated=None,
        save_or_return_probabilities=False,
    )
    return np.squeeze(seg)  # (H, W)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir",  required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    input_dir  = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    pngs = sorted(input_dir.glob("*.png"))
    total = len(pngs)
    if total == 0:
        print("TOTAL:0", flush=True)
        print("DONE",    flush=True)
        return

    print(f"TOTAL:{total}", flush=True)

    import torch
    device = torch.device("cuda", 0) if torch.cuda.is_available() else torch.device("cpu")

    # Load both models once — amortises startup cost over all frames
    artery_pred = make_predictor(device, ARTERY_MODEL_DIR, folds=("all",))
    lumen_pred  = make_predictor(device, LUMEN_MODEL_DIR,  folds=(0,))

    for i, png_path in enumerate(pngs):
        gray = load_gray(png_path)

        # ── Pass 1: artery segmentation ──────────────────────────────────
        artery_seg  = predict(artery_pred, gray)           # 0=bg, 1=artery
        artery_mask = (artery_seg == 1).astype(np.uint8)   # binary, same size as gray

        # ── Pass 2: lumen/plaque — only within the artery area ───────────
        # Zero out pixels outside the artery to focus the model
        masked_gray = gray * artery_mask
        lumen_seg   = predict(lumen_pred, masked_gray)     # 0=bg, 1=lumen, 2=plaque

        # Apply artery mask: discard any predictions outside artery
        lumen_seg   = lumen_seg * artery_mask

        # ── Write masks ───────────────────────────────────────────────────
        cv2.imwrite(str(output_dir / png_path.name),
                    artery_mask.astype(np.uint8) * 255)

        cv2.imwrite(str(output_dir / (png_path.stem + "_lumen.png")),
                    (lumen_seg == 1).astype(np.uint8) * 255)

        cv2.imwrite(str(output_dir / (png_path.stem + "_plaque.png")),
                    (lumen_seg == 2).astype(np.uint8) * 255)

        print(f"PROGRESS:{i + 1}/{total}", flush=True)

    print("DONE", flush=True)


if __name__ == "__main__":
    main()
