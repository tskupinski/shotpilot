"""HTML report + review.png verification sheet.

Thumbnails: a single set in thumbs/ keyed by timestamp (t<sec>.jpg) — each
extracted from 4K exactly once, shared by report.html and review.png.
"""

import html
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from . import ffmpeg
from .probe import VideoInfo
from .segments import Segment


def _thumb_for(video: Path, t: float, thumbs_dir: Path, width: int = 480) -> Path:
    """Thumbnail of the frame at t — extracted only if it does not exist yet."""
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    out = thumbs_dir / f"t{t:.1f}.jpg"
    if not out.exists():
        ffmpeg.extract_frame(video, t, out, width=width)
    return out


def _draw_score_axis(ax, score: np.ndarray, fps: float, threshold: float,
                     segments: list[Segment], duration: float, title: str) -> None:
    t = np.arange(len(score)) / fps
    ax.plot(t, score, lw=0.8, color="#444")
    ax.axhline(threshold, color="#d62728", ls="--", lw=1)
    for s in segments:
        ax.axvspan(s.start, s.end, color="#2ca02c", alpha=0.25)
    top = min(float(np.percentile(score, 99)) * 1.5, float(score.max())) if len(score) else 1
    ax.set_ylim(0, max(top, threshold * 2))
    ax.set_xlim(0, duration)
    ax.set_title(title, fontsize=10)


def make_review_sheet(
    info: VideoInfo,
    score: np.ndarray,
    threshold: float,
    segments: list[Segment],
    gaps: list[dict],
    out_dir: Path,
) -> Path:
    """A single PNG for judging the algorithm's decisions: plot + thumbnails of kept and rejected."""
    import cv2

    thumbs = out_dir / "thumbs"
    regions = sorted(
        [{"kind": "kept", "start": s.start, "end": s.end, "label": f"score {s.score:.2f}"}
         for s in segments]
        + [{"kind": "cut", "start": g["start"], "end": g["end"],
            "label": f"peak {g['peak_score']:.1f}"} for g in gaps],
        key=lambda r: r["start"],
    )

    ncols = 4
    nrows = max(1, -(-len(regions) // ncols))
    fig = plt.figure(figsize=(14, 3.5 + 2.2 * nrows), dpi=110)
    gs = fig.add_gridspec(nrows + 1, ncols, height_ratios=[1.6] + [1] * nrows, hspace=0.45)

    ax = fig.add_subplot(gs[0, :])
    _draw_score_axis(ax, score, info.fps, threshold, segments, info.duration,
                     f"{info.path.name} — threshold {threshold:.3f}, green = kept")

    for i, r in enumerate(regions):
        mid = round((r["start"] + r["end"]) / 2, 1)
        thumb = _thumb_for(info.path, mid, thumbs)
        img = cv2.cvtColor(cv2.imread(str(thumb)), cv2.COLOR_BGR2RGB)
        axi = fig.add_subplot(gs[1 + i // ncols, i % ncols])
        axi.imshow(img)
        axi.set_xticks([]), axi.set_yticks([])
        color = "#2ca02c" if r["kind"] == "kept" else "#d62728"
        for spine in axi.spines.values():
            spine.set_color(color), spine.set_linewidth(3)
        name = "KEPT" if r["kind"] == "kept" else "CUT"
        axi.set_title(f"{name} {r['start']:.1f}–{r['end']:.1f}s · {r['label']}",
                      fontsize=8, color=color)

    path = out_dir / "review.png"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def write_report(
    info: VideoInfo,
    score: np.ndarray,
    threshold: float,
    segments: list[Segment],
    out_dir: Path,
    clips_written: bool,
) -> Path:
    thumbs = out_dir / "thumbs"
    fig, ax = plt.subplots(figsize=(14, 4), dpi=110)
    _draw_score_axis(ax, score, info.fps, threshold, segments, info.duration,
                     "Camera motion smoothness over time")
    ax.set_xlabel("time [s]")
    ax.set_ylabel("acceleration RMS [px/frame²]")
    fig.tight_layout()
    fig.savefig(out_dir / "smoothness.png")
    plt.close(fig)

    rows = []
    for i, s in enumerate(segments, 1):
        mid = round((s.start + s.end) / 2, 1)
        thumb = _thumb_for(info.path, mid, thumbs)
        clip_cell = f"clips/clip_{i:03d}.mp4" if clips_written else "—"
        rows.append(f"""
        <tr>
          <td>{i}</td>
          <td><img src="thumbs/{thumb.name}" alt="segment {i}"></td>
          <td>{s.start:.2f} s – {s.end:.2f} s</td>
          <td>{s.duration:.2f} s</td>
          <td>{s.score:.3f}</td>
          <td>{clip_cell}</td>
        </tr>""")

    total = sum(s.duration for s in segments)
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Smoothness report — {html.escape(info.path.name)}</title>
<style>
  body {{ font-family: -apple-system, sans-serif; margin: 2rem auto; max-width: 1200px; color: #222; }}
  img {{ border-radius: 6px; display: block; }}
  table {{ border-collapse: collapse; margin-top: 1rem; }}
  th, td {{ padding: .5rem .9rem; border-bottom: 1px solid #ddd; text-align: left; vertical-align: middle; }}
  .meta {{ color: #666; }}
</style></head><body>
<h1>{html.escape(info.path.name)}</h1>
<p class="meta">{info.width}×{info.height} @ {info.fps:.2f} fps · {info.duration:.1f} s ·
threshold = {threshold:.3f} · segments found: {len(segments)} (total {total:.1f} s,
{100 * total / info.duration:.0f}% of footage)</p>
<img src="smoothness.png" style="width:100%" alt="smoothness plot">
<table>
<tr><th>#</th><th>preview</th><th>range</th><th>duration</th><th>score</th><th>clip</th></tr>
{"".join(rows) if rows else '<tr><td colspan="6">No segments meeting the criteria.</td></tr>'}
</table>
</body></html>
"""
    report = out_dir / "report.html"
    report.write_text(body)
    return report
