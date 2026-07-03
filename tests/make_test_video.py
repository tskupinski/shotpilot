"""Generates a synthetic test video: smooth pans + shaky fragments.

Expected pipeline result: 3 smooth segments (~0-8s, ~12-22s, ~25-30s).
Usage: .venv/bin/python tests/make_test_video.py input/test_synthetic.mp4
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

FPS = 30
W, H = 1280, 720
DURATION = 30  # s
# (start_s, end_s, is_shaky)
PHASES = [(0, 8, False), (8, 12, True), (12, 22, False), (22, 25, True), (25, 30, False)]


def main(out_path: Path) -> None:
    rng = np.random.default_rng(42)
    # Large textured "map" that the camera window travels across.
    world = rng.integers(0, 255, (2200, 3600), dtype=np.uint8)
    world = cv2.GaussianBlur(world, (0, 0), 3)
    world = cv2.applyColorMap(world, cv2.COLORMAP_VIRIDIS)

    n_frames = DURATION * FPS
    max_x, max_y = world.shape[1] - W, world.shape[0] - H
    x, y = 200.0, 200.0
    vx, vy = 4.0, 1.5

    tmp = Path(tempfile.mkstemp(suffix=".mp4")[1])
    writer = cv2.VideoWriter(str(tmp), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    for i in range(n_frames):
        t = i / FPS
        shaky = any(a <= t < b and s for a, b, s in PHASES)
        if shaky:
            jx, jy = rng.normal(0, 9, 2)  # abrupt position jitter
        else:
            jx = jy = 0.0
        x = float(np.clip(x + vx, 0, max_x))
        y = float(np.clip(y + vy, 0, max_y))
        if x in (0.0, float(max_x)):
            vx = -vx
        if y in (0.0, float(max_y)):
            vy = -vy
        cx = int(np.clip(x + jx, 0, max_x))
        cy = int(np.clip(y + jy, 0, max_y))
        writer.write(world[cy:cy + H, cx:cx + W])
    writer.release()

    # Re-encode to h264 so the file behaves like typical drone footage.
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(tmp),
         "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", str(out_path)],
        check=True,
    )
    tmp.unlink()
    print(f"OK: {out_path}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
