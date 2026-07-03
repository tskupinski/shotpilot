"""Screen pace assessment of a clip and speed-up (setpts). CLI: `vm pace` / `vm speed`.

Pace = median on-screen motion in % of frame width per second, broken down into
translation (pan/tilt), dolly (forward flight = zoom) and rotation. The
recommended speed multiplier is mechanical (target/measured) — the decision
whether to apply it (e.g. moody shots stay slow) belongs to the agent/user.
"""

from pathlib import Path

import numpy as np

from . import ffmpeg
from .cut import X264_ARGS
from .motion import ANALYSIS_WIDTH, FrameMotion
from .probe import VideoInfo


def measure_pace(motions: list[FrameMotion], fps: float) -> dict:
    """Screen-pace components in % of frame width / s (medians)."""
    to_pct = fps / ANALYSIS_WIDTH * 100
    arm = ANALYSIS_WIDTH / 2  # arm: zoom/rotation converted to frame-edge motion
    dx = np.array([m.dx for m in motions])
    dy = np.array([m.dy for m in motions])
    ds = np.array([m.ds for m in motions])
    da = np.array([m.da for m in motions])
    trans = float(np.median(np.hypot(dx, dy))) * to_pct
    dolly = float(np.median(np.abs(ds))) * arm * to_pct
    rot = float(np.median(np.abs(da))) * arm * to_pct
    return {
        "trans_pct_s": round(trans, 2),
        "dolly_pct_s": round(dolly, 2),
        "rot_pct_s": round(rot, 2),
        "total_pct_s": round(trans + dolly + rot, 2),
    }


def pace_profile(motions: list[FrameMotion], fps: float,
                 window_s: float = 2.0) -> dict:
    """Pace profile in non-overlapping time windows + "dull" stretches.

    Dull = a window with pace < 50% of the clip median, reported only from 2 windows
    in a row (a single dip is usually a maneuver turn, not a dull stretch). Times
    are on the axis of the input motions (for the source's motion.csv = source time).
    """
    if not motions:
        return {"window_s": window_s, "median_pct_s": 0.0,
                "windows": [], "boring": []}
    t0 = motions[0].t
    windows, bucket, edge = [], [], t0 + window_s
    min_frames = max(1, int(0.5 * window_s * fps))

    def flush(start: float, end: float) -> None:
        if len(bucket) >= min_frames:
            windows.append({"t0": round(start, 1), "t1": round(end, 1),
                            "total_pct_s": measure_pace(bucket, fps)["total_pct_s"]})

    for m in motions:
        if m.t >= edge:
            flush(edge - window_s, edge)
            bucket, edge = [], edge + window_s
        bucket.append(m)
    flush(edge - window_s, min(edge, motions[-1].t))

    median = float(np.median([w["total_pct_s"] for w in windows])) if windows else 0.0
    boring, run = [], []
    for w in windows + [None]:
        if w is not None and median > 0 and w["total_pct_s"] < 0.5 * median:
            run.append(w)
        else:
            if len(run) >= 2:
                boring.append({"from": run[0]["t0"], "to": run[-1]["t1"]})
            run = []
    return {"window_s": window_s, "median_pct_s": round(median, 2),
            "windows": windows, "boring": boring}


def classify(total: float, slow_below: float, fast_above: float) -> str:
    if total < slow_below:
        return "slow"
    if total > fast_above:
        return "fast"
    return "good"


def recommend_speed(total: float, target: float, max_speed: float) -> float:
    """Multiplier bringing the pace to the target, rounded to 0.25, in [1, max]."""
    if total <= 0:
        return 1.0
    raw = min(max(target / total, 1.0), max_speed)
    return round(raw * 4) / 4


def apply_speed(info: VideoInfo, speed: float, out: Path) -> Path:
    """Re-encode with setpts; audio dropped (footage is edited with music)."""
    return ffmpeg.run_to([
        "-i", info.path,
        "-vf", f"setpts=PTS/{speed:g}",
        "-an", *X264_ARGS,
    ], out)


def profile_with_src_axis(profile: dict, offset: float) -> dict:
    """Profile windows on dual axes: clip time + source time (for vm trim)."""
    profile["windows"] = [
        {"t0": round(w["t0"] - offset, 1), "t1": round(w["t1"] - offset, 1),
         "t0_src": w["t0"], "t1_src": w["t1"],
         "total_pct_s": w["total_pct_s"]} for w in profile["windows"]]
    profile["boring"] = [
        {"from": round(b["from"] - offset, 1), "to": round(b["to"] - offset, 1),
         "from_src": b["from"], "to_src": b["to"]} for b in profile["boring"]]
    return profile


def format_result(r: dict) -> str:
    """Pace measurement result (dict from cli.cmd_pace) as human-readable text."""
    if "error" in r:
        return f"{r['file']}: ERROR {r['error']}"
    lines = [f"{Path(r['file']).name}: {r['pace']['total_pct_s']} %/s "
             f"(trans {r['pace']['trans_pct_s']} + dolly {r['pace']['dolly_pct_s']} "
             f"+ rot {r['pace']['rot_pct_s']}) -> {r['classification']}, "
             f"recommendation x{r['recommended_speed']:g}"]
    prof = r.get("profile")
    if prof:
        lines.append(f"  profile (windows {prof['window_s']:g} s, "
                     f"median {prof['median_pct_s']} %/s):")
        for w in prof["windows"]:
            src = (f" (src {w['t0_src']:g}–{w['t1_src']:g})"
                   if w["t0_src"] != w["t0"] else "")
            lines.append(f"    {w['t0']:g}–{w['t1']:g} s{src}: {w['total_pct_s']}")
        for b in prof["boring"]:
            src = (f" (src {b['from_src']:g}–{b['to_src']:g})"
                   if b["from_src"] != b["from"] else "")
            lines.append(f"    DULL: {b['from']:g}–{b['to']:g} s{src} — "
                         f"pace < 50% of clip median")
    return "\n".join(lines)
