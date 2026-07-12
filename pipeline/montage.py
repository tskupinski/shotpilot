"""Splicing the select sequence into a film. CLI: `shot sequence` / `shot montage`.

Two paths: the default with crossfade transitions (`concat_xfade`, xfade chain
= re-encode of the whole) and a draft one without transitions (`concat`, concat
demuxer, stream copy). Clips are uniformly re-encoded at cut time (X264_ARGS) —
`check_uniform` guards that assumption for both paths (xfade also requires
matching fps/sizes); a mismatch is an error to fix (re-render the faulty clip),
not to mask. Audio is always dropped (`-an`): the splice is video-only, and a
mix of clips with/without audio breaks stream copy. Music comes as a separate,
cheap step AFTER the render (`shot music`, pipeline.music) — the mux does not
touch the video render.
"""

import datetime
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, ffmpeg, paths, schema
from .cut import X264_ARGS

SMOOTH_CACHE = paths.SMOOTH_CACHE

UNIFORM_KEYS = ("codec_name", "width", "height", "pix_fmt", "r_frame_rate")

# Selects are cut ONCE (cut.X264_ARGS, preset medium); the montage is re-rendered
# on every order iteration, so it trades a few % of file size for ~1.5-2× encode
# speed (preset fast). Same crf 18 — still above typical drone source bitrate.
MONTAGE_X264_ARGS = [
    "-c:v", "libx264", "-crf", "18", "-preset", "fast",
    "-pix_fmt", "yuv420p", "-movflags", "+faststart",
]


def draft_encoder() -> dict:
    """Encoder for `--draft` preview renders: hardware when a runtime test
    encode proves one works (ffmpeg.hw_encoder — VideoToolbox on macOS, VAAPI
    on Linux; ~2× faster than software here), otherwise the fastest reasonable
    software preset. Quality is for order/transition review, not publishing —
    the final render re-encodes with MONTAGE_X264_ARGS.

    Shape: `pre` = global/device args (before inputs), `suffix` = filter the
    graph output must pass through (hwupload for VAAPI), `args` = codec args,
    `label` = what the log names it."""
    hw = ffmpeg.hw_encoder()
    if hw:
        name, dev = hw
        if name == "h264_videotoolbox":
            return {"label": name, "pre": [], "suffix": None,
                    "args": ["-c:v", name, "-b:v", "50M", "-pix_fmt", "yuv420p",
                             "-movflags", "+faststart"]}
        if name == "h264_vaapi":
            return {"label": f"{name} ({dev})", "pre": ["-vaapi_device", dev],
                    "suffix": "format=nv12,hwupload",
                    "args": ["-c:v", name, "-qp", "23",
                             "-movflags", "+faststart"]}
    return {"label": "libx264 veryfast", "pre": [], "suffix": None,
            "args": ["-c:v", "libx264", "-crf", "23", "-preset", "veryfast",
                     "-pix_fmt", "yuv420p", "-movflags", "+faststart"]}

# Narrative roles of a clip (tags.role) — closed vocabulary; single source:
# pipeline/schemas/project.schema.json ($defs.tags), criteria in decision-rules.md.
ROLES = schema.tag_enum("role")

# Freshness states of the render/music (render_state/music_state) — compared
# as strings also in cli.py, hence constants instead of literals.
STATE_NONE = "none"          # no render / music
STATE_FRESH = "fresh"        # matches the current sequence and files
STATE_STALE = "stale"        # sequence/clips changed after the render
STATE_EXTERNAL = "external"  # ad-hoc render from --files, outside the manifest

# Shot types (tags.shot) — closed vocabulary; single source:
# pipeline/schemas/project.schema.json ($defs.tags), criteria in decision-rules.md.
SHOTS = schema.tag_enum("shot")


def stream_fields(path: Path) -> dict:
    """Raw video stream fields for the uniformity comparison (no fps conversion)."""
    data = ffmpeg.probe_json(
        ["-select_streams", "v:0",
         "-show_entries", "stream=" + ",".join(UNIFORM_KEYS) + ",duration",
         str(path)])
    return data["streams"][0]


def stream_fields_many(files: list[Path]) -> list[dict]:
    """stream_fields on a small thread pool — ffprobe is a subprocess, and the
    61 serial probes of a long cut's preflight cost ~9 s. Pool size respects
    config.parallel_jobs (jobs: 1 = serial, the slow-machine knob)."""
    jobs = min(config.parallel_jobs(min(8, os.cpu_count() or 4)), len(files))
    if jobs < 2:
        return [stream_fields(f) for f in files]
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        return list(ex.map(stream_fields, files))


def fps_value(rate: str | None) -> float:
    """'30000/1001' -> 29.97; tolerates plain numbers and empty/zero values."""
    if not rate:
        return 0.0
    if "/" in rate:
        num, den = rate.split("/", 1)
        return float(num) / float(den) if float(den) else 0.0
    return float(rate)


def target_fps(files: list[Path], fields: list[dict] | None = None) -> str:
    """Highest r_frame_rate in the set — normalizing upward loses no frames.

    `fields` = precomputed `stream_fields` per file (one ffprobe pass shared by
    the caller instead of every helper probing the same files again).
    """
    fields = fields or stream_fields_many(files)
    return max((x.get("r_frame_rate") for x in fields),
               key=fps_value, default="30000/1001")


def check_uniform(files: list[Path], keys: tuple[str, ...] = UNIFORM_KEYS,
                  fields: list[dict] | None = None) -> list[dict]:
    """Compares clips against the first; returns a list of mismatches (empty = OK).

    `keys` narrows the checked fields: the xfade path (re-encode) normalizes fps
    on the fly, so it skips `r_frame_rate`; the concat path (stream copy) requires
    full uniformity, because it does not recompute frames. `fields` as in `target_fps`.
    """
    fields = fields or stream_fields_many(files)
    ref = fields[0]
    mismatches = []
    for f, cur in zip(files[1:], fields[1:]):
        for key in keys:
            if cur.get(key) != ref.get(key):
                mismatches.append({"file": str(f), "field": key,
                                   "value": cur.get(key), "expected": ref.get(key)})
    return mismatches


def concat(files: list[Path], out: Path) -> Path:
    """Splice via concat demuxer without re-encode; the file list stays alongside as an artifact."""
    out.parent.mkdir(parents=True, exist_ok=True)
    lst = out.with_suffix(".concat.txt")
    lines = "\n".join(
        "file '{}'".format(str(f.resolve()).replace("'", "'\\''")) for f in files)
    lst.write_text(lines + "\n")
    return ffmpeg.run_to(["-f", "concat", "-safe", "0", "-i", lst,
                          "-c:v", "copy", "-an", "-movflags", "+faststart"], out)


def smooth_clip(src: Path, fps: str, duration_s: float | None = None) -> Path:
    """Version of the clip motion-interpolated to `fps` — rendered ONCE, cached.

    Conversion PER CLIP (one ffmpeg, a few GB) instead of `minterpolate` on many
    4K inputs at once inside the xfade chain — the latter allocates ~28 GB and
    dies with OOM. The cache in `output/smooth-cache/` is reused across versions
    (short/standard/long), invalidated by source mtime and by a MISMATCHED fps
    (an entry smoothed to a different target must not pass as fresh); writes are
    atomic (.tmp → replace), so an interrupted render does not leave a corrupted
    entry later treated as fresh.
    """
    SMOOTH_CACHE.mkdir(parents=True, exist_ok=True)
    dst = SMOOTH_CACHE / src.name
    if (dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime
            and stream_fields(dst).get("r_frame_rate") == fps):
        return dst
    return ffmpeg.run_to(
        ["-i", src,
         "-vf", f"minterpolate=fps={fps}:mi_mode=mci:me_mode=bidir:vsbmc=1",
         "-an", *X264_ARGS],
        dst, progress_total_s=duration_s, progress_label=f"smooth {src.name}")


def clip_starts(durations: list[float], xfade: float) -> list[float]:
    """Start of each clip on the final timeline: each contributes `dur - xfade`,
    because the transition overlays its last `xfade` s onto the start of the next.

    The only source of the timeline formula — used by both the render
    (`concat_xfade`: xfade transition offsets) and the timeline (`build_timeline`
    for `shot locate`). A divergence of the two would mean locate points at
    different clips than the ones actually playing.
    """
    starts, t = [], 0.0
    for d in durations:
        starts.append(t)
        t += d - xfade
    return starts


def concat_xfade(files: list[Path], out: Path, duration: float,
                 smooth: bool = False, fields: list[dict] | None = None,
                 encode: dict | None = None,
                 vf: list[str | None] | None = None) -> Path:
    """Splice with crossfade transitions (xfade chain, re-encode of the whole).

    A re-encode happens anyway, so we normalize every input to a common fps
    (the highest in the set — `target_fps`) and a common timebase before it
    enters xfade. Thanks to this, mixed source frame rates (different camera
    settings) don't block the crossfade, and normalizing upward duplicates
    frames instead of dropping them.

    `smooth=True`: clips with an fps OTHER than the target are FIRST smoothed
    one by one (`smooth_clip` — motion interpolation into the cache), and only
    then enter the xfade chain like regular clips at the target. This removes
    the judder from frame duplication (23.976→29.97) WITHOUT putting
    minterpolate into the big graph (which allocated tens of GB → OOM). The
    montage itself then has the same memory profile as the non-smooth version.

    `fields` as in `target_fps`; `encode` overrides the output encoder
    (default MONTAGE_X264_ARGS; `draft_encoder()` for preview renders —
    its `pre` args go before the inputs and its `suffix` filter caps the
    graph, e.g. hwupload for a VAAPI encode of the software filter output).

    `vf` = per-clip grade chains (grade.chain_for_use, index-aligned with
    `files`; None entries render ungraded). Applied AFTER the fps/timebase
    normalization and downstream of the smooth-cache clips, so grade changes
    never invalidate output/smooth-cache/.
    """
    enc = encode or {"pre": [], "suffix": None, "args": MONTAGE_X264_ARGS}
    if len(files) < 2:
        chain = vf[0] if vf and vf[0] else None
        if chain:  # a single graded clip: concat() is stream copy, so encode
            if enc["suffix"]:
                chain = f"{chain},{enc['suffix']}"
            return ffmpeg.run_to([*enc["pre"], "-i", files[0], "-vf", chain,
                                  "-an", *enc["args"]], out)
        return concat(files, out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or stream_fields_many(files)
    durs = [float(x["duration"]) for x in fields]
    fps = target_fps(files, fields)

    work = list(files)
    if smooth:
        todo = [i for i in range(len(files)) if fields[i].get("r_frame_rate") != fps]
        for n, i in enumerate(todo, 1):
            print(f"  smoothing {n}/{len(todo)}: {files[i].name} ...",
                  file=sys.stderr, flush=True)
            work[i] = smooth_clip(files[i], fps, durs[i] or None)

    # unify fps + timebase of each input -> [n0], [n1], ... (no minterpolate);
    # each grade chain ends in format=yuv420p, so graded and ungraded pads
    # enter xfade with a uniform pixel format
    parts = [f"[{i}:v]fps={fps},settb=AVTB"
             + (f",{vf[i]}" if vf and vf[i] else "") + f"[n{i}]"
             for i in range(len(work))]
    starts = clip_starts(durs, duration)
    for i in range(1, len(work)):
        src = "[n0]" if i == 1 else f"[v{i - 1}]"
        parts.append(f"{src}[n{i}]xfade=transition=fade:"
                     f"duration={duration:.3f}:offset={starts[i]:.3f}[v{i}]")
    inputs = [a for f in work for a in ("-i", str(f))]
    map_label = f"[v{len(work) - 1}]"
    if enc["suffix"]:  # hardware encoders take the graph output via hwupload
        parts.append(f"{map_label}{enc['suffix']}[vhw]")
        map_label = "[vhw]"
    expected = sum(durs) - (len(files) - 1) * duration
    return ffmpeg.run_to([*enc["pre"], *inputs,
                          "-filter_complex", ";".join(parts),
                          "-map", map_label, "-an", *enc["args"]], out,
                         progress_total_s=expected,
                         progress_label="montage render")


def resolve_use(path: str, selects: list[dict]) -> dict | None:
    """Maps a spliced file (x1 select or a _x* variant) to its manifest select entry."""
    for s in selects:
        if s["file"] == path or path in s.get("speed_variants", {}).values():
            return s
    return None


def use_speed(entry: dict, use: str) -> float | None:
    """Multiplier of the spliced file: 1.0 for the x1 select, speed_variants key for a variant."""
    if use == entry.get("file"):
        return 1.0
    for k, v in entry.get("speed_variants", {}).items():
        if v == use:
            return float(k)
    return None


def use_duration(entry: dict, use: str) -> float | None:
    """Duration of a sequence clip from the manifest (range / multiplier) — no ffprobe."""
    if not entry.get("range"):
        return None
    speed = use_speed(entry, use)
    if speed is None:
        return None
    return (entry["range"][1] - entry["range"][0]) / speed


def effective_pace(entry: dict, use: str) -> float | None:
    """On-screen pace of the spliced file: pace_pct_s × variant multiplier."""
    pace, speed = entry.get("pace_pct_s"), use_speed(entry, use)
    if not pace or not speed:
        return None
    return round(pace * speed, 2)


def build_timeline(montage: dict, selects: list[dict], xfade: float) -> list[dict]:
    """Montage timeline: start/end of every sequence clip on the final axis.

    The axis is computed by `clip_starts` — exactly the formula the render
    (`concat_xfade`) uses to set transition offsets; adjacent clips overlap in
    the window [end_i - xfade, end_i]. Clip durations: with a FRESH render, the
    measured times from its record (`clip_durations_s` — axis matches the file
    down to milliseconds), otherwise from the manifest (`use_duration` —
    range/multiplier, no ffprobe; the difference vs a future render is
    sub-second).

    Clips outside the manifest (an external montage given as an explicit file
    list) resolve via fallback: duration from ffprobe, label = file name,
    source = the file itself.
    """
    seq = montage.get("sequence", [])
    rdurs = (montage.get("render") or {}).get("clip_durations_s")
    if not (rdurs and len(rdurs) == len(seq)
            and render_state(montage)["state"] == STATE_FRESH):
        rdurs = None
    entries = [resolve_use(e["use"], selects) for e in seq]
    durs = []
    for i, (e, entry) in enumerate(zip(seq, entries)):
        dur = rdurs[i] if rdurs else (use_duration(entry, e["use"]) if entry else None)
        if dur is None:  # file outside the manifest -> ffprobe
            p = Path(e["use"])
            dur = float(stream_fields(p).get("duration", 0)) if p.exists() else 0.0
        durs.append(dur)
    starts = clip_starts(durs, xfade)
    rows = []
    for i, (e, entry, dur, start) in enumerate(zip(seq, entries, durs, starts)):
        use = e["use"]
        rows.append({"index": i, "select": e.get("select"), "use": use,
                     "source": entry.get("source") if entry else use,
                     "label": entry.get("label") if entry else Path(use).stem,
                     "speed": use_speed(entry, use) if entry else None,
                     "range": entry.get("range") if entry else None,
                     "start_s": round(start, 3), "end_s": round(start + dur, 3),
                     "dur_s": round(dur, 3)})
    return rows


def render_state(montage: dict, data: dict | None = None) -> dict:
    """Whether the last render matches the sequence, the current clip files
    and the current grade decisions (`data` = a manifest already in the
    caller's hand — saves a re-load)."""
    render = montage.get("render")
    if not render:
        return {"state": STATE_NONE}
    current = [e["use"] for e in montage.get("sequence", [])]
    if current != render.get("files"):
        return {"state": STATE_STALE, "reason": "sequence changed after render"}
    rendered_at = datetime.datetime.fromisoformat(render["rendered_at"]).timestamp()
    for f in current:
        p = Path(f)
        if not p.exists():
            return {"state": STATE_STALE, "reason": f"missing file: {f}"}
        if p.stat().st_mtime > rendered_at:
            return {"state": STATE_STALE, "reason": f"clip changed after render: {f}"}
    # grade freshness: what the render baked in (render.grade snapshot,
    # None = ungraded) vs the manifest now; the snapshot embeds LUT mtimes,
    # so editing a .cube also lands here
    from . import grade, manifest  # local imports: no cycle at module level
    if data is None:
        data = manifest.load()
    snapshot = grade.grade_snapshot(current, data.get("selects", []),
                                    montage.get("grade"), data.get("sources"))
    if snapshot != render.get("grade"):
        return {"state": STATE_STALE, "reason": "grade changed after render"}
    state = {"state": STATE_FRESH}
    if render.get("draft"):
        state["draft"] = True  # fresh, but preview quality (--draft encode)
    return state


def music_state(montage: dict, data: dict | None = None) -> dict:
    """Whether the applied music (the cut's music.applied) matches the last render."""
    applied = montage.get("music", {}).get("applied")
    if not applied:
        return {"state": STATE_NONE}
    render = montage.get("render")
    if not render:
        return {"state": STATE_STALE, "reason": "no montage render"}
    if render_state(montage, data)["state"] != STATE_FRESH:
        return {"state": STATE_STALE, "reason": "montage render is stale"}
    if applied.get("render_rendered_at") != render.get("rendered_at"):
        return {"state": STATE_STALE,
                "reason": "montage re-rendered after music was applied"}
    if not Path(applied["out"]).exists():
        return {"state": STATE_STALE, "reason": f"missing file: {applied['out']}"}
    return {"state": STATE_FRESH, "out": applied["out"]}
