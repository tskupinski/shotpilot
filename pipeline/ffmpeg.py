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
import time
from pathlib import Path

from . import config


@functools.lru_cache(maxsize=None)
def vaapi_device() -> str | None:
    """First working VAAPI render node (runtime device-init probe, ~0.1 s once
    per process) — an encoder merely LISTED by the build is not enough (this
    machine's ffmpeg lists h264_nvenc with no NVIDIA GPU in sight)."""
    for dev in sorted(Path("/dev/dri").glob("renderD*")):
        res = subprocess.run(
            ["ffmpeg", "-v", "error", "-init_hw_device", f"vaapi=va:{dev}",
             "-f", "lavfi", "-i", "nullsrc=s=64x64:d=0.04", "-f", "null", "-"],
            stdin=subprocess.DEVNULL, capture_output=True)
        if res.returncode == 0:
            return str(dev)
    return None


def hwaccel_args() -> list[str]:
    """Hardware decode of inputs (input options — go BEFORE -i). Best effort:
    when the accelerator can't handle a stream, ffmpeg falls back to software
    decode on its own. Matters for re-encodes reading 4K sources (select/trim/
    speed); NOT used in the montage xfade graph — dozens of simultaneous
    hardware decode sessions are the risk there, not the win.
    Opt-out: `shot config --hwaccel off` (or env SHOT_HWACCEL=off)."""
    if not config.hwaccel_enabled():
        return []
    if sys.platform == "darwin":
        return ["-hwaccel", "videotoolbox"]
    dev = vaapi_device()
    if dev:
        return ["-hwaccel", "vaapi", "-hwaccel_device", dev]
    return []


@functools.lru_cache(maxsize=None)
def hw_encoder() -> tuple[str, str] | None:
    """(encoder, device) after a real test encode, or None — the only proof an
    encoder works is encoding with it. Used by montage.draft_encoder()."""
    if not config.hwaccel_enabled():
        return None
    candidates = []
    if sys.platform == "darwin":
        candidates.append(("h264_videotoolbox", []))
    dev = vaapi_device() if sys.platform != "darwin" else None
    if dev:
        candidates.append(("h264_vaapi",
                           ["-vaapi_device", dev, "-vf", "format=nv12,hwupload"]))
    for name, extra in candidates:
        res = subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi",
             "-i", "testsrc2=size=320x240:rate=30:duration=0.1",
             *extra, "-c:v", name, "-f", "null", "-"],
            stdin=subprocess.DEVNULL, capture_output=True)
        if res.returncode == 0:
            return name, dev or ""
    return None


@functools.lru_cache(maxsize=None)
def has_encoder(name: str) -> bool:
    """True when this ffmpeg build lists the encoder (`ffmpeg -encoders`)."""
    res = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                         stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if res.returncode != 0:
        return False
    return any(line.split()[1:2] == [name] for line in res.stdout.splitlines())


@functools.lru_cache(maxsize=None)
def has_filter(name: str) -> bool:
    """True when this ffmpeg build lists the filter (`ffmpeg -filters`)."""
    res = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                         stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if res.returncode != 0:
        return False
    return any(line.split()[1:2] == [name] for line in res.stdout.splitlines())


def run(args: list, capture: bool = False) -> subprocess.CompletedProcess:
    """`ffmpeg -nostdin -y -v error *args`; capture=True collects stdout (bytes)."""
    return subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-v", "error", *map(str, args)],
        check=True, stdin=subprocess.DEVNULL, capture_output=capture)


PROGRESS_EVERY_S = 30.0  # wall-clock spacing of progress lines on stderr


def _run_with_progress(args: list, total_s: float, label: str) -> None:
    """run() equivalent that reports render progress: `-progress pipe:1`
    key=value blocks -> one stderr line every PROGRESS_EVERY_S. Renders
    shorter than the interval print nothing — short jobs stay quiet."""
    proc = subprocess.Popen(
        ["ffmpeg", "-nostdin", "-y", "-v", "error",
         "-progress", "pipe:1", *map(str, args)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True)
    next_at = time.monotonic() + PROGRESS_EVERY_S
    for line in proc.stdout:
        if not line.startswith("out_time_us="):
            continue
        try:
            done_s = int(line.split("=", 1)[1]) / 1e6
        except ValueError:  # "N/A" before the first frame lands
            continue
        if time.monotonic() >= next_at:
            pct = min(100.0, 100.0 * done_s / total_s) if total_s else 0.0
            print(f"  {label}: {pct:.0f}% ({done_s:.0f}/{total_s:.0f} s)",
                  file=sys.stderr, flush=True)
            next_at = time.monotonic() + PROGRESS_EVERY_S
    rc = proc.wait()
    if rc:
        raise subprocess.CalledProcessError(rc, proc.args)


def run_to(args: list, out: Path, progress_total_s: float | None = None,
           progress_label: str = "render") -> Path:
    """run() rendering to `out` atomically (tmp file + rename): an interrupted
    render leaves NO partial file that a later run/agent could mistake for done.
    `progress_total_s` = expected output duration; when set, a progress line
    lands on stderr every PROGRESS_EVERY_S (long renders stop being a black box)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".part" + out.suffix)
    if progress_total_s is not None:
        _run_with_progress([*args, tmp], progress_total_s, progress_label)
    else:
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
