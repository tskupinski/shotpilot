"""Color grading — non-destructive, applied at montage render time. CLI: `shot grade`.

A grade is a DECISION in the manifest, not a file: the montage re-encodes
everything anyway (crossfades), so per-clip grade filters ride along for free.
Three layers composed per clip, in order:

1. normalize (per source, manifest "sources"): log -> Rec.709 input LUT
   (e.g. DJI D-Log), so corrections and stats see normal-contrast footage;
2. correct (per select, "grade"): exposure / temperature / saturation /
   contrast deviations so clips from different lights match;
3. look (per cut, cuts.<name>.grade): ONE creative look for the whole film —
   a built-in preset (LOOKS) or a user .cube from luts/.

Every non-empty chain ends in format=yuv420p: several of these filters work
in RGB internally and xfade requires uniform pixel formats on all inputs.
Because the chain is applied downstream of the smooth-cache clips inside the
render graph, grade changes never invalidate output/smooth-cache/.

Decision criteria (when to correct, thresholds, look subtlety):
docs/decision-rules.md, the "Grading" section.
"""

import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

from . import ffmpeg, paths

LUTS_DIR = Path("luts")

# Correction parameters: stored per select as DEVIATIONS from neutral
# (a key set back to its neutral value is removed; empty dict = no entry).
# The schema ($defs.correction) enforces the same ranges on save.
NEUTRAL = {"exposure": 0.0, "temperature": 6500.0,
           "saturation": 1.0, "contrast": 1.0}
RANGES = {"exposure": (-2.0, 2.0), "temperature": (3000.0, 10000.0),
          "saturation": (0.0, 2.0), "contrast": (0.5, 1.5)}

# Built-in looks for drone landscape footage — filter strings live here
# (implementation), selection criteria in decision-rules.md ("Grading").
LOOKS = {
    # warm sunny lift — golden-hour footage that came out flatter than it felt
    "golden": "colortemperature=temperature=5800:pl=0.6,"
              "eq=saturation=1.1:contrast=1.03",
    # cinematic teal shadows / warm highlights
    "teal-orange": "colorbalance=rs=-0.05:bs=0.06:rm=0.02:bm=-0.02:"
                   "rh=0.05:bh=-0.05,eq=saturation=1.08",
    # gentle S-curve contrast + chroma — hazy midday footage
    "punch": "curves=all='0/0 0.25/0.21 0.5/0.5 0.75/0.79 1/1',"
             "eq=saturation=1.12",
    # faded-film lifted blacks — calm/ambient cuts
    "matte": "curves=all='0/0.05 0.5/0.5 1/0.97',eq=saturation=0.9:contrast=0.98",
    # cool desaturated moody — fog/overcast films
    "nordic": "colortemperature=temperature=7200:pl=0.6,"
              "eq=saturation=0.85:contrast=1.05",
}


# ------------------------------------------------------------- filter builders

def validate_correction(grade: dict) -> list[str]:
    """Range/name check of a correction dict; returns error strings (empty = OK)."""
    errs = []
    for k, v in grade.items():
        if k not in RANGES:
            errs.append(f"unknown grade parameter: {k}")
        elif not isinstance(v, (int, float)) or isinstance(v, bool):
            errs.append(f"{k}: expected a number, got {v!r}")
        else:
            lo, hi = RANGES[k]
            if not lo <= v <= hi:
                errs.append(f"{k}={v:g} out of range {lo:g}–{hi:g}")
    return errs


def _escape_level(s: str, chars: str) -> str:
    s = s.replace("\\", "\\\\")
    for ch in chars:
        s = s.replace(ch, "\\" + ch)
    return s


def escape_filter_path(path: str | Path) -> str:
    """A file path as a filter argument: ffmpeg parses the graph twice
    (filter args, then the graph itself), so special characters (: ' [ ] , ;)
    need two escaping levels — the classic lut3d footgun."""
    return _escape_level(_escape_level(str(path), "':"), "'[],;")


def correction_filter(grade: dict | None) -> str | None:
    """Per-select corrections -> filter string (None when neutral/absent)."""
    if not grade:
        return None
    parts = []
    if grade.get("exposure"):
        parts.append(f"exposure=exposure={grade['exposure']:g}")
    temp = grade.get("temperature")
    if temp and temp != NEUTRAL["temperature"]:
        parts.append(f"colortemperature=temperature={temp:g}:pl=0.6")
    eq = [f"{k}={grade[k]:g}" for k in ("contrast", "saturation")
          if grade.get(k) is not None and grade[k] != NEUTRAL[k]]
    if eq:
        parts.append("eq=" + ":".join(eq))
    return ",".join(parts) or None


def look_filter(cut_grade: dict | None) -> str | None:
    """The cut's look ({"look": preset} or {"lut": path}) -> filter string."""
    if not cut_grade:
        return None
    if cut_grade.get("look"):
        if cut_grade["look"] not in LOOKS:
            raise ValueError(f"unknown look in the manifest: {cut_grade['look']} "
                             f"(available: {', '.join(LOOKS)})")
        return LOOKS[cut_grade["look"]]
    if cut_grade.get("lut"):
        return "lut3d=" + escape_filter_path(cut_grade["lut"])
    return None


def normalize_filter(source_grade: dict | None) -> str | None:
    """The source's normalize layer ({"input_lut": path}) -> lut3d string."""
    if not source_grade or not source_grade.get("input_lut"):
        return None
    return "lut3d=" + escape_filter_path(source_grade["input_lut"])


def filters_in(expr: str) -> list[str]:
    """Filter names used in a chain expression (for has_filter availability checks)."""
    return re.findall(r"(?:^|,)([a-z0-9_]+)(?==)", expr)


def missing_filters(expr: str) -> list[str]:
    """Filters of the expression this ffmpeg build lacks (exposure/
    colortemperature need ffmpeg >= 5.0) — a clean refusal instead of a
    cryptic graph error at render time."""
    return [f for f in filters_in(expr) if not ffmpeg.has_filter(f)]


def chain_for_use(use: str, selects: list[dict], cut_grade: dict | None,
                  sources: dict | None) -> str | None:
    """The complete grade chain of one spliced file: normalize -> correct -> look.

    `use` may be a speed variant — it maps to its select via
    `montage.resolve_use` (variants are pure setpts retimes, so the select's
    corrections apply unchanged). None = the clip renders ungraded.
    """
    from . import montage  # local import: montage.render_state imports grade
    entry = montage.resolve_use(str(use), selects)
    parts = []
    if entry:
        src = (sources or {}).get(entry["source"])
        parts += [f for f in (normalize_filter(src),
                              correction_filter(entry.get("grade"))) if f]
    lf = look_filter(cut_grade)
    if lf:
        parts.append(lf)
    if not parts:
        return None
    return ",".join(parts) + ",format=yuv420p"


def grade_snapshot(files: list, selects: list[dict], cut_grade: dict | None,
                   sources: dict | None) -> dict | None:
    """What the render record stores for freshness: the exact per-clip chains
    plus mtimes of every referenced .cube (editing a LUT file = stale render).
    None when no clip is graded — ungraded projects stay snapshot-free."""
    from . import montage  # as in chain_for_use
    chains = [chain_for_use(str(f), selects, cut_grade, sources) for f in files]
    if not any(chains):
        return None
    lut_files = set()
    if cut_grade and cut_grade.get("lut"):
        lut_files.add(cut_grade["lut"])
    for f in files:
        entry = montage.resolve_use(str(f), selects)
        if entry:
            src = (sources or {}).get(entry["source"]) or {}
            if src.get("input_lut"):
                lut_files.add(src["input_lut"])
    luts = {p: (round(Path(p).stat().st_mtime, 3) if Path(p).exists() else None)
            for p in sorted(lut_files)}
    look = None
    if cut_grade:
        look = cut_grade.get("look") or (
            f"lut:{cut_grade['lut']}" if cut_grade.get("lut") else None)
    return {"clips": chains, "look": look, "luts": luts}


def looks_catalog() -> dict:
    """Built-in looks + user .cube files found in luts/ (for --list-looks)."""
    return {"looks": dict(LOOKS),
            "luts": sorted(str(p) for p in LUTS_DIR.glob("*.cube"))}


def check_lut_file(path: Path) -> str | None:
    """Plausibility check of a .cube before it lands in the manifest;
    returns an error string or None."""
    if not path.exists():
        return f"file does not exist: {path}"
    try:
        head = path.read_text(errors="replace")[:4096]
    except OSError as e:
        return f"cannot read {path}: {e}"
    if "LUT_3D_SIZE" not in head:
        return f"{path} does not look like a 3D LUT (no LUT_3D_SIZE header)"
    return None


# ------------------------------------------------------------- color analysis

# Width frames are downscaled to for color stats (color is low-frequency —
# 320 px is plenty and decodes fast even from 4K).
ANALYSIS_WIDTH = 320
SAMPLE_FPS = 1.0  # one frame per second of footage

# Range-tolerant clipping thresholds (limited-range yuv sources don't reach 0/1).
CLIP_HIGH = 0.98
CLIP_LOW = 0.02


def _sample_frames(video: Path, vf_pre: str | None,
                   sample_fps: float) -> list[np.ndarray]:
    """BGR frames at analysis size via an ffmpeg rawvideo pipe (decode+scale
    +optional normalize LUT outside Python) — the motion.py decode approach,
    but sampled at `sample_fps` instead of every frame."""
    from .probe import probe  # local import: avoids an import cycle
    info = probe(video)
    width = min(ANALYSIS_WIDTH, info.width)
    height = max(2, round(info.height * width / info.width / 2) * 2)
    filters = [f for f in (vf_pre, f"fps={sample_fps:g}",
                           f"scale={width}:{height}") if f]
    frame_bytes = width * height * 3
    proc = subprocess.Popen(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(video),
         "-vf", ",".join(filters), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE, bufsize=frame_bytes * 4)
    frames = []
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            frames.append(np.frombuffer(buf, np.uint8).reshape(height, width, 3))
    finally:
        proc.stdout.close()
        proc.wait()
    return frames


def analyze_color(video: Path, normalize: str | None = None,
                  sample_fps: float = SAMPLE_FPS) -> dict:
    """Color stats of a clip: luma level/spread, saturation, cast, clipping.

    `normalize` = the source's input LUT (lut3d chain), applied in the decode —
    the stats must describe the image the CORRECT layer sees, not raw log.
    """
    frames = _sample_frames(video, normalize, sample_fps)
    if not frames:
        raise RuntimeError(f"ffmpeg decoded no frames from {video}")
    arr = np.stack(frames).astype(np.float32) / 255.0  # (N, H, W, 3) BGR
    b, g, r = arr[..., 0], arr[..., 1], arr[..., 2]
    y = 0.0722 * b + 0.7152 * g + 0.2126 * r  # Rec.709 luma
    mx, mn = arr.max(axis=-1), arr.min(axis=-1)
    sat = np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0.0)  # HSV S
    r_mean, g_mean, b_mean = (float(c.mean()) for c in (r, g, b))
    cast_strength = abs(r_mean - b_mean)
    cast = ("neutral" if cast_strength < 0.01
            else "warm" if r_mean > b_mean else "cool")
    stats = {
        "mean_luma": round(float(y.mean()), 3),
        "luma_p5": round(float(np.percentile(y, 5)), 3),
        "luma_p95": round(float(np.percentile(y, 95)), 3),
        "mean_sat": round(float(sat.mean()), 3),
        "rgb_means": [round(r_mean, 3), round(g_mean, 3), round(b_mean, 3)],
        "cast": cast,
        "cast_strength": round(cast_strength, 3),
        "clip_high_pct": round(float((y > CLIP_HIGH).mean() * 100), 2),
        "clip_low_pct": round(float((y < CLIP_LOW).mean() * 100), 2),
    }
    return {"file": str(video), "frames_sampled": len(frames),
            "normalize": normalize, "stats": stats,
            "analyzed_at": datetime.datetime.now().isoformat(timespec="seconds")}


def load_or_analyze_color(video: Path, normalize: str | None = None,
                          force: bool = False) -> dict:
    """Stats from cache (output/<stem>/color.json) when newer than the video
    AND measured through the same normalize LUT — otherwise re-analyzed."""
    out = paths.video_dir(video.stem) / "color.json"
    if (not force and out.exists()
            and out.stat().st_mtime > video.stat().st_mtime):
        data = json.loads(out.read_text())
        if data.get("normalize") == normalize:
            print(f"  color.json from cache: {out}", file=sys.stderr)
            return data
    data = analyze_color(video, normalize)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return data


# ------------------------------------------------------------------- preview

PREVIEW_WIDTH = 480


def render_preview(clips: list[tuple[Path, str | None]], out: Path,
                   frames: int = 3, width: int = PREVIEW_WIDTH) -> Path:
    """Before/after grid PNG: per clip a RAW row and (when a chain exists)
    a GRADED row of the same frames — the agent judges the grade via Read.

    Frames are extracted with the grade chain as `vf` (the render applies the
    IDENTICAL string), so the preview shows exactly what the montage will bake.
    """
    import tempfile

    import cv2

    from .contact import render_grid
    grid, labels = [], []
    with tempfile.TemporaryDirectory() as tmp:
        for i, (f, chain) in enumerate(clips):
            dur = ffmpeg.duration(f)
            times = [(j + 1) * dur / (frames + 1) for j in range(frames)]
            name = Path(f).stem
            rows = [("RAW", None)] + ([("GRADED", chain)] if chain else [])
            if not chain:
                rows = [("RAW (no grade)", None)]
            for state, vf in rows:
                for j, t in enumerate(times):
                    p = Path(tmp) / f"c{i}_{state[:1]}{j}.jpg"
                    ffmpeg.extract_frame(f, t, p, width=width, vf=vf)
                    grid.append(cv2.imread(str(p)))
                    labels.append(f"{name[:24]} {state} t={round(t, 1):g}s")
        return render_grid(grid, [0.0] * len(grid),
                           "grade preview — RAW vs GRADED (render applies the "
                           "same chain)", out, cols=frames, labels=labels)
