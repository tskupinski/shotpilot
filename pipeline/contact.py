"""Contact sheet: grid of frames every N seconds for judging appeal. CLI: `shot sheet`.

`shot scan` builds the sheet from frames of its own decoding pass (render_grid);
standalone `shot sheet` extracts frames with ffmpeg (with an mtime cache).
"""

import sys
import tempfile
from pathlib import Path

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import numpy as np

from . import ffmpeg, paths
from .probe import probe

THUMB_WIDTH = 420


def render_grid(
    frames_bgr: list[np.ndarray],
    times: list[float],
    title: str,
    out_path: Path,
    cols: int = 5,
    labels: list[str] | None = None,
) -> Path:
    """Draws a grid of frames (BGR) with timestamps to a PNG.

    `labels` replaces the default t=..s titles (grade previews label frames
    with clip name + RAW/GRADED state instead of times)."""
    if not frames_bgr:
        raise ValueError("no frames for the sheet")
    h, w = frames_bgr[0].shape[:2]
    rows = -(-len(frames_bgr) // cols)
    fig = plt.figure(figsize=(2.9 * cols, 2.9 * (h / w) * rows + 0.5), dpi=110)
    fig.suptitle(title, fontsize=11, y=1.0)
    for i, (frame, t) in enumerate(zip(frames_bgr, times)):
        ax = fig.add_subplot(rows, cols, i + 1)
        ax.imshow(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        ax.set_xticks([]), ax.set_yticks([])
        # scan passes raw frame timestamps (e.g. 1.96029) — display rounded, the
        # labels are meant to be typed back into `shot frames` / `shot select`
        ax.set_title(labels[i] if labels else f"t={round(t, 1):g}s", fontsize=8)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    return out_path


def sheet_fresh(video: Path) -> bool:
    out = paths.video_dir(video.stem) / "contact.png"
    return out.exists() and out.stat().st_mtime > video.stat().st_mtime


def make_contact_sheet(video: Path, interval: float = 2.0, cols: int = 5,
                       force: bool = False) -> Path:
    out = paths.video_dir(video.stem) / "contact.png"
    if not force and sheet_fresh(video):
        print(f"  contact.png from cache: {out}", file=sys.stderr)
        return out

    with tempfile.TemporaryDirectory() as tmp:
        # A single decoder pass instead of seeking per frame.
        ffmpeg.run(["-i", video,
                    "-vf", f"fps=1/{interval},scale={THUMB_WIDTH}:-2",
                    f"{tmp}/f_%04d.jpg"])
        files = sorted(Path(tmp).glob("f_*.jpg"))
        if not files:
            raise RuntimeError(f"ffmpeg extracted no frames from {video}")
        frames = [cv2.imread(str(f)) for f in files]
        times = [i * interval for i in range(len(frames))]
        return render_grid(frames, times, f"{video.name} — one frame every {interval:g} s",
                           out, cols)
