"""Smoothness scoring and detection of smooth segments."""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .motion import FrameMotion

# Rotation and zoom live on a different scale than pixel translation — the weights
# bring them to a comparable order of magnitude (rad -> px on an arm of ~half the analysis width).
ROTATION_WEIGHT = 320.0
ZOOM_WEIGHT = 320.0
SCORE_WINDOW_S = 0.5          # rolling-RMS window of acceleration
HYSTERESIS_RATIO = 0.6        # exit threshold from "rough" = 0.6 * entry threshold
ABS_THRESHOLD_FLOOR = 0.35    # px/frame^2 — below this we do not treat motion as rough
AUTO_PERCENTILE = 75          # base of the adaptive threshold
AUTO_MARGIN = 1.6             # threshold = percentile * margin
MERGE_GAP_S = 0.5             # segments closer together than this get merged


@dataclass
class Segment:
    start: float
    end: float
    score: float  # mean smoothness score (lower = smoother)

    @property
    def duration(self) -> float:
        return self.end - self.start


def smoothness_score(motions: list[FrameMotion], fps: float) -> np.ndarray:
    """Per-frame score: rolling RMS of global-motion acceleration (lower = smoother)."""
    velocity = np.array([
        [m.dx, m.dy, m.da * ROTATION_WEIGHT, m.ds * ZOOM_WEIGHT] for m in motions
    ])
    accel = np.zeros_like(velocity)
    accel[1:] = np.diff(velocity, axis=0)
    accel_mag = np.linalg.norm(accel, axis=1)

    # window may not exceed the signal — np.convolve(mode="same") returns the
    # kernel's length for clips shorter than the window (sub-second accidental shots)
    window = min(max(3, int(SCORE_WINDOW_S * fps)), max(1, len(accel_mag)))
    kernel = np.ones(window) / window
    score = np.sqrt(np.convolve(accel_mag**2, kernel, mode="same"))

    # Failed tracking = no confidence in the measurement, treat it as rough.
    failed = np.array([m.tracking_failed for m in motions])
    failed[0] = False  # the first frame has no pair, that is not a tracking failure
    score[failed] = max(score.max(), ABS_THRESHOLD_FLOOR * 10)
    return score


def auto_threshold(score: np.ndarray) -> float:
    return max(float(np.percentile(score, AUTO_PERCENTILE)) * AUTO_MARGIN, ABS_THRESHOLD_FLOOR)


def find_segments(
    score: np.ndarray,
    fps: float,
    threshold: float,
    min_clip_s: float = 2.5,
    margin_s: float = 0.3,
) -> list[Segment]:
    """Classification with hysteresis -> merging -> margin -> length filter."""
    enter_rough = threshold
    exit_rough = threshold * HYSTERESIS_RATIO

    smooth = np.zeros(len(score), dtype=bool)
    rough = score[0] >= enter_rough
    for i, s in enumerate(score):
        if rough and s < exit_rough:
            rough = False
        elif not rough and s >= enter_rough:
            rough = True
        smooth[i] = not rough

    # Boundary indices of contiguous "smooth" blocks.
    padded = np.concatenate([[False], smooth, [False]])
    edges = np.flatnonzero(np.diff(padded.astype(int)))
    raw = [(int(edges[i]), int(edges[i + 1])) for i in range(0, len(edges), 2)]

    # Merge blocks separated by a short gap.
    merged: list[list[int]] = []
    max_gap = int(MERGE_GAP_S * fps)
    for a, b in raw:
        if merged and a - merged[-1][1] <= max_gap:
            merged[-1][1] = b
        else:
            merged.append([a, b])

    margin_f = int(margin_s * fps)
    min_frames = int(min_clip_s * fps)
    segments = []
    for a, b in merged:
        a, b = a + margin_f, b - margin_f
        if b - a < min_frames:
            continue
        segments.append(Segment(
            start=round(a / fps, 3),
            end=round(b / fps, 3),
            score=round(float(score[a:b].mean()), 4),
        ))
    return segments


def find_gaps(
    segments: list[Segment],
    score: np.ndarray,
    fps: float,
    duration: float,
) -> list[dict]:
    """Rejected intervals between segments, with peak score as the rationale."""
    bounds = [0.0] + [t for s in segments for t in (s.start, s.end)] + [duration]
    gaps = []
    for a, b in zip(bounds[::2], bounds[1::2]):
        if b - a < 0.2:  # skip residual gaps at the boundaries
            continue
        fa, fb = int(a * fps), max(int(a * fps) + 1, int(b * fps))
        gaps.append({
            "start": round(a, 3),
            "end": round(b, 3),
            "duration": round(b - a, 3),
            "peak_score": round(float(score[fa:fb].max()), 4),
        })
    return gaps


def save_json(segments: list[Segment], threshold: float, path: Path) -> None:
    payload = {
        "threshold": round(threshold, 4),
        "segments": [
            {"start": s.start, "end": s.end, "duration": round(s.duration, 3), "score": s.score}
            for s in segments
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
