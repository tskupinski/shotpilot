"""The shared gateway to ffmpeg/ffprobe — call conventions in one place.

Conventions: stdin=DEVNULL + `-nostdin` (ffmpeg/ffprobe can read stdin
and eat lines of the calling script/loop), `-v error` (silence except errors),
check=True (tool error = CalledProcessError; `cli.main` turns it into a clean
message). Deliberate exceptions calling subprocess directly:
loudness analysis (`music.probe_track` — parses stderr, so no `-v error`)
and the rawvideo pipe for motion analysis (`motion._frame_pipe` — streaming Popen).
"""

import json
import subprocess
from pathlib import Path


def run(args: list, capture: bool = False) -> subprocess.CompletedProcess:
    """`ffmpeg -nostdin -y -v error *args`; capture=True collects stdout (bytes)."""
    return subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-v", "error", *map(str, args)],
        check=True, stdin=subprocess.DEVNULL, capture_output=capture)


def run_to(args: list, out: Path) -> Path:
    """run() rendering to `out` atomically (tmp file + rename): an interrupted
    render leaves NO partial file that a later run/agent could mistake for done."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".part" + out.suffix)
    run([*args, tmp])
    tmp.replace(out)
    return out


def probe_json(args: list) -> dict:
    """`ffprobe -v error *args -of json` -> parsed dict."""
    res = subprocess.run(
        ["ffprobe", "-v", "error", *map(str, args), "-of", "json"],
        check=True, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    return json.loads(res.stdout)


def duration(path: Path) -> float:
    """File (container) duration in seconds — the only implementation."""
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        check=True, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    return float(res.stdout.strip())


def extract_frame(src: Path, at_s: float, out: Path,
                  width: int | None = None, vf: str | None = None) -> Path:
    """One frame of `src` at `at_s` -> `out`; `width` scales (height auto,
    even), `vf` is a filter applied BEFORE scaling."""
    filters = [f for f in (vf, f"scale={width}:-2" if width else None) if f]
    args = ["-ss", f"{at_s:.3f}", "-i", src]
    if filters:
        args += ["-vf", ",".join(filters)]
    run([*args, "-frames:v", "1", out])
    return out
