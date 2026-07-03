"""The shared gateway to ffmpeg/ffprobe — call conventions in one place.

Conventions: stdin=DEVNULL + `-nostdin` (ffmpeg/ffprobe can read stdin
and eat lines of the calling script/loop), `-v error` (silence except errors),
check=True (tool error = CalledProcessError; `cli.main` turns it into a clean
message). Deliberate exceptions calling subprocess directly:
loudness analysis (`music.probe_track` — parses stderr, so no `-v error`)
and the rawvideo pipe for motion analysis (`motion._frame_pipe` — streaming Popen).
"""

import functools
import json
import subprocess
import sys
from pathlib import Path

# Hardware decode of inputs (input option — goes BEFORE -i). Best effort:
# when the accelerator can't handle a stream, ffmpeg falls back to software
# decode on its own. Matters for re-encodes reading 4K sources (select/trim/
# speed); NOT used in the montage xfade graph — dozens of simultaneous
# hardware decode sessions are the risk there, not the win.
HWACCEL = ["-hwaccel", "videotoolbox"] if sys.platform == "darwin" else []


@functools.lru_cache(maxsize=None)
def has_encoder(name: str) -> bool:
    """True when this ffmpeg build lists the encoder (`ffmpeg -encoders`)."""
    res = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                         stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if res.returncode != 0:
        return False
    return any(line.split()[1:2] == [name] for line in res.stdout.splitlines())


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
