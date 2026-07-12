"""Frame-by-frame global camera motion estimation (OpenCV).

Decoding and scaling are done by ffmpeg (rawvideo pipe) — multithreaded and
without pushing full 4K frames through Python.
"""

import csv
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import paths

import cv2
import numpy as np

# Width the frames are downscaled to for analysis.
ANALYSIS_WIDTH = 640
MIN_FEATURES = 30  # below this many tracked points we consider tracking failed

FEATURE_PARAMS = dict(maxCorners=400, qualityLevel=0.01, minDistance=12, blockSize=7)
LK_PARAMS = dict(
    winSize=(21, 21),
    maxLevel=3,
    criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
)


@dataclass
class FrameMotion:
    frame: int
    t: float
    dx: float
    dy: float
    da: float  # rotation [rad]
    ds: float  # scale change (zoom), 0 = none
    n_features: int
    tracking_failed: bool


def _estimate_pair(prev_gray: np.ndarray, gray: np.ndarray) -> tuple[float, float, float, float, int]:
    """Global prev->curr transform: (dx, dy, da, ds, n_inliers)."""
    pts = cv2.goodFeaturesToTrack(prev_gray, **FEATURE_PARAMS)
    if pts is None or len(pts) < MIN_FEATURES:
        return 0.0, 0.0, 0.0, 0.0, 0

    nxt, status, _ = cv2.calcOpticalFlowPyrLK(prev_gray, gray, pts, None, **LK_PARAMS)
    if nxt is None:
        return 0.0, 0.0, 0.0, 0.0, 0

    ok = status.ravel() == 1
    src, dst = pts[ok], nxt[ok]
    if len(src) < MIN_FEATURES:
        return 0.0, 0.0, 0.0, 0.0, int(len(src))

    matrix, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=3.0)
    if matrix is None:
        return 0.0, 0.0, 0.0, 0.0, int(len(src))

    dx, dy = float(matrix[0, 2]), float(matrix[1, 2])
    da = float(np.arctan2(matrix[1, 0], matrix[0, 0]))
    scale = float(np.hypot(matrix[0, 0], matrix[1, 0]))
    n_inliers = int(inliers.sum()) if inliers is not None else int(len(src))
    return dx, dy, da, scale - 1.0, n_inliers


def _frame_pipe(video_path: Path, width: int, height: int):
    """Generator of BGR frames at analysis resolution — decode+scale in ffmpeg."""
    frame_bytes = width * height * 3
    proc = subprocess.Popen(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(video_path),
         "-vf", f"scale={width}:{height}",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE, bufsize=frame_bytes * 4,
    )
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            yield np.frombuffer(buf, np.uint8).reshape(height, width, 3)
    finally:
        proc.stdout.close()
        proc.wait()


def analyze_motion(
    video_path: Path,
    fps: float,
    frame_sink: Callable[[int, np.ndarray], None] | None = None,
) -> list[FrameMotion]:
    """frame_sink(idx, frame_bgr) receives every analysis frame (e.g. for the contact sheet)."""
    from .probe import probe  # local import: avoids an import cycle

    info = probe(video_path)
    width = min(ANALYSIS_WIDTH, info.width)
    height = max(2, round(info.height * width / info.width / 2) * 2)
    total = info.n_frames

    motions: list[FrameMotion] = []
    prev_gray = None
    for idx, frame in enumerate(_frame_pipe(video_path, width, height)):
        if frame_sink is not None:
            frame_sink(idx, frame)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        if prev_gray is None:
            motions.append(FrameMotion(idx, idx / fps, 0.0, 0.0, 0.0, 0.0, 0, False))
        else:
            dx, dy, da, ds, n = _estimate_pair(prev_gray, gray)
            motions.append(FrameMotion(idx, idx / fps, dx, dy, da, ds, n, n < MIN_FEATURES))

        prev_gray = gray
        if total and (idx + 1) % 250 == 0:
            # the file name keeps parallel batch scans (shot scan --jobs) readable
            print(f"  {video_path.name}: motion analysis {idx + 1}/{total} frames "
                  f"({100 * (idx + 1) // total}%)", file=sys.stderr)

    if not motions:
        raise RuntimeError(f"ffmpeg decoded no frames from {video_path}")
    return motions


def jitter_test(
    motions: list[FrameMotion],
    start: float = 0.0,
    end: float | None = None,
    deadband: float = 0.02,
) -> dict:
    """Distinguishes jitter from smooth maneuvers: jitter = frequent velocity sign flips.

    Smooth motion (even fast/variable) keeps a constant dx/dy sign; shaking oscillates.
    """
    sel = [m for m in motions if m.t >= start and (end is None or m.t < end)]
    if len(sel) < 4:
        return {"error": "too few frames in range"}
    span = sel[-1].t - sel[0].t or 1e-9

    def flips(values: list[float]) -> int:
        signs = [v > 0 for v in values if abs(v) > deadband]
        return sum(1 for a, b in zip(signs, signs[1:]) if a != b)

    fx = flips([m.dx for m in sel])
    fy = flips([m.dy for m in sel])
    flips_per_s = (fx + fy) / span
    return {
        "from": round(sel[0].t, 2), "to": round(sel[-1].t, 2),
        "frames": len(sel),
        "mean_abs_dx": round(float(np.mean([abs(m.dx) for m in sel])), 3),
        "mean_abs_dy": round(float(np.mean([abs(m.dy) for m in sel])), 3),
        "sign_flips_x": fx, "sign_flips_y": fy,
        "flips_per_s": round(flips_per_s, 2),
        "verdict": "jitter" if flips_per_s > 3.0 else "smooth-maneuver",
    }


def cache_fresh(video_path: Path) -> bool:
    """Whether motion.csv in output/<stem>/ is newer than the video file."""
    csv_path = paths.video_dir(video_path.stem) / "motion.csv"
    return csv_path.exists() and csv_path.stat().st_mtime > video_path.stat().st_mtime


def load_or_analyze(
    video_path: Path,
    fps: float,
    force: bool = False,
    frame_sink: Callable[[int, np.ndarray], None] | None = None,
) -> list[FrameMotion]:
    """Returns motion from cache (output/<stem>/motion.csv) if newer than the video.

    Note: on a cache hit frame_sink receives no frames (there is no decoding).
    """
    csv_path = paths.video_dir(video_path.stem) / "motion.csv"
    if not force and cache_fresh(video_path):
        print(f"  motion.csv from cache: {csv_path}", file=sys.stderr)
        return load_csv(csv_path)
    motions = analyze_motion(video_path, fps, frame_sink=frame_sink)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    save_csv(motions, csv_path)
    return motions


def save_csv(motions: list[FrameMotion], path: Path) -> None:
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["frame", "t", "dx", "dy", "da", "ds", "n_features", "tracking_failed"])
        for m in motions:
            writer.writerow([m.frame, f"{m.t:.4f}", f"{m.dx:.4f}", f"{m.dy:.4f}",
                             f"{m.da:.6f}", f"{m.ds:.6f}", m.n_features, int(m.tracking_failed)])


def load_csv(path: Path) -> list[FrameMotion]:
    motions = []
    with path.open() as f:
        for row in csv.DictReader(f):
            motions.append(FrameMotion(
                frame=int(row["frame"]), t=float(row["t"]),
                dx=float(row["dx"]), dy=float(row["dy"]),
                da=float(row["da"]), ds=float(row["ds"]),
                n_features=int(row["n_features"]),
                tracking_failed=bool(int(row["tracking_failed"])),
            ))
    return motions
