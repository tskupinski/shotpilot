"""Video metadata via ffprobe."""

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from . import ffmpeg


@dataclass
class VideoInfo:
    path: Path
    width: int
    height: int
    fps: float
    duration: float
    n_frames: int
    has_audio: bool


def probe(path: Path) -> VideoInfo:
    data = ffmpeg.probe_json(["-show_streams", "-show_format", str(path)])

    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    has_audio = any(s["codec_type"] == "audio" for s in data["streams"])

    fps = float(Fraction(video.get("avg_frame_rate") or video["r_frame_rate"]))
    duration = float(video.get("duration") or data["format"]["duration"])
    n_frames = int(video.get("nb_frames") or round(duration * fps))

    return VideoInfo(
        path=path,
        width=int(video["width"]),
        height=int(video["height"]),
        fps=fps,
        duration=duration,
        n_frames=n_frames,
        has_audio=has_audio,
    )
