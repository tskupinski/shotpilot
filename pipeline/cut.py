"""Cutting segments into separate files (ffmpeg, re-encode). CLI: `vm select`."""

import sys
from pathlib import Path

from . import ffmpeg
from .probe import VideoInfo
from .segments import Segment

# Shared video encoding parameters (also in pipeline.pace) — uniform format for splicing.
X264_ARGS = [
    "-c:v", "libx264", "-crf", "18", "-preset", "medium",
    "-pix_fmt", "yuv420p", "-movflags", "+faststart",
]


def cut_range(info: VideoInfo, start: float, end: float, out: Path) -> Path:
    return ffmpeg.run_to([
        *ffmpeg.HWACCEL,
        "-ss", f"{start:.3f}", "-i", info.path,
        "-t", f"{end - start:.3f}",
        *X264_ARGS,
        *(["-c:a", "aac", "-b:a", "192k"] if info.has_audio else ["-an"]),
    ], out)


def cut_clips(info: VideoInfo, segments: list[Segment], clips_dir: Path) -> list[Path]:
    written = []
    for i, seg in enumerate(segments, 1):
        out = clips_dir / f"clip_{i:03d}.mp4"
        print(f"  clip {i}/{len(segments)}: {seg.start:.2f}s–{seg.end:.2f}s -> {out.name}", file=sys.stderr)
        written.append(cut_range(info, seg.start, seg.end, out))
    return written
