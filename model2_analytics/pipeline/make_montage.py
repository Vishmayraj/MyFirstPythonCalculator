"""Create visual montages of eval + live screenshots for easy viewing."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2

BASE = Path(__file__).resolve().parent.parent.parent / "demo_results"


def make_montage(src_dir: Path, out_name: str, cols: int = 4, max_imgs: int = 16):
    imgs = sorted(src_dir.glob("*.jpg"))[:max_imgs]
    if not imgs:
        print(f"No images in {src_dir}")
        return
    # Load and resize
    loaded = []
    for p in imgs:
        img = cv2.imread(str(p))
        if img is None:
            continue
        h, w = img.shape[:2]
        # Square-ish tiles
        tile_w = 480
        tile_h = int(tile_w * h / w)
        img = cv2.resize(img, (tile_w, tile_h))
        loaded.append(img)

    rows = (len(loaded) + cols - 1) // cols
    if not loaded:
        print(f"No readable images in {src_dir}")
        return
    tile_h = loaded[0].shape[0]
    tile_w = loaded[0].shape[1]
    canvas = np.zeros((rows * tile_h, cols * tile_w, 3), dtype=np.uint8)

    for i, img in enumerate(loaded):
        r, c = divmod(i, cols)
        canvas[r * tile_h:(r + 1) * tile_h, c * tile_w:(c + 1) * tile_w] = img

    out_path = BASE / out_name
    cv2.imwrite(str(out_path), canvas)
    print(f"Montage saved: {out_path} ({len(loaded)} tiles)")


import numpy as np

if __name__ == "__main__":
    make_montage(BASE / "eval_screenshots", "montage_eval_results.jpg")
    make_montage(BASE / "live_screenshots", "montage_live_results.jpg")
    print("Done!")