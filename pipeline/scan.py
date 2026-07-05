"""Orchestration of a single video's smoothness scan (logic of the former __main__.py)."""

import json
import sys
from pathlib import Path

import numpy as np

from . import contact, cut, motion, paths, report, schema, segments as seg
from .probe import probe

SHEET_INTERVAL_S = 2.0


def build_warnings(stats: dict, n_segments: int) -> list[str]:
    warnings = []
    if n_segments == 0:
        warnings.append("No segments found — the threshold may be too low or the footage genuinely shaky; check review.png and try --threshold with a higher value.")
    if stats["tracking_failed_pct"] > 20:
        warnings.append(f"Tracking failed for {stats['tracking_failed_pct']:.0f}% of frames — result unreliable (motion blur / low texture / night).")
    if n_segments and stats["kept_pct"] > 95:
        warnings.append("Kept >95% of the footage — with gimbal footage this is usually correct; if in doubt verify review.png or `shot jitter`.")
    if n_segments and stats["kept_pct"] < 30:
        warnings.append("Kept <30% of the footage — the threshold may be too strict; consider a higher --threshold.")
    return warnings


def scan_video(
    video: Path,
    threshold: str = "auto",
    min_clip: float = 2.5,
    margin: float = 0.3,
    do_cut: bool = False,
    force: bool = False,
) -> dict:
    """Smoothness analysis + artifacts; returns a summary (also written to summary.json)."""
    info = probe(video)
    out_dir = paths.video_dir(video.stem)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"{info.path.name}: {info.width}x{info.height} @ {info.fps:.2f} fps, "
          f"{info.duration:.1f} s", file=sys.stderr)

    # Contact sheet from frames of the same decoding pass (zero second decode).
    sheet_every = max(1, int(SHEET_INTERVAL_S * info.fps))
    sheet_frames: list = []

    def collect(idx: int, frame) -> None:
        if idx % sheet_every == 0:
            sheet_frames.append((idx / info.fps, frame.copy()))

    motions = motion.load_or_analyze(video, info.fps, force=force, frame_sink=collect)

    sheet_path = out_dir / "contact.png"
    if sheet_frames:
        contact.render_grid([f for _, f in sheet_frames], [t for t, _ in sheet_frames],
                            f"{video.name} — one frame every {SHEET_INTERVAL_S:g} s", sheet_path)
    elif not contact.sheet_fresh(video):
        # Motion cache hit but the sheet is missing/stale — separate (rare) extraction.
        contact.make_contact_sheet(video, SHEET_INTERVAL_S)

    score = seg.smoothness_score(motions, info.fps)
    thr = seg.auto_threshold(score) if threshold == "auto" else float(threshold)
    found = seg.find_segments(score, info.fps, thr, min_clip_s=min_clip, margin_s=margin)
    seg.save_json(found, thr, out_dir / "segments.json")
    gaps = seg.find_gaps(found, score, info.fps, info.duration)
    kept = sum(s.duration for s in found)

    clips: list[Path] = []
    if found and do_cut:
        print("Cutting segments (re-encode) ...", file=sys.stderr)
        clips = cut.cut_clips(info, found, out_dir / "clips")

    report_path = report.write_report(info, score, thr, found, out_dir,
                                      clips_written=bool(clips))
    review_path = report.make_review_sheet(info, score, thr, found, gaps, out_dir)

    stats = {
        "n_segments": len(found),
        "kept_s": round(kept, 2),
        "kept_pct": round(100 * kept / info.duration, 1),
        "tracking_failed_pct": round(
            100 * sum(m.tracking_failed for m in motions[1:]) / max(len(motions) - 1, 1), 1),
        "score_p50": round(float(np.percentile(score, 50)), 4),
        "score_p90": round(float(np.percentile(score, 90)), 4),
    }
    summary = {
        "video": {"path": str(info.path), "width": info.width, "height": info.height,
                  "fps": round(info.fps, 3), "duration": round(info.duration, 2)},
        "params": {"min_clip": min_clip, "margin": margin,
                   "threshold_mode": "auto" if threshold == "auto" else "manual",
                   "threshold": round(thr, 4), "cut": do_cut},
        "stats": stats,
        "segments": [
            {"index": i, "start": s.start, "end": s.end,
             "duration": round(s.duration, 3), "score": s.score,
             "clip": str(clips[i - 1]) if clips else None}
            for i, s in enumerate(found, 1)
        ],
        "rejected": gaps,
        "artifacts": {
            "summary": str(out_dir / "summary.json"),
            "review_sheet": str(review_path),
            "contact_sheet": str(sheet_path),
            "report": str(report_path),
            "segments_json": str(out_dir / "segments.json"),
            "motion_csv": str(out_dir / "motion.csv"),
        },
        "warnings": build_warnings(stats, len(found)),
    }
    schema.check(summary, "summary", str(out_dir / "summary.json"))
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    for w in summary["warnings"]:
        print(f"WARNING: {w}", file=sys.stderr)
    return summary
