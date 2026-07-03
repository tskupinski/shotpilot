"""Music for the assembled film. CLI: `vm music`.

Three operations: track generation (Stable Audio via the Stability API, key
in env STABILITY_API_KEY), track analysis for the agent (`probe_track` — duration,
loudness, energy curve from ebur128; the agent cannot hear, so it must see)
and mux onto the finished montage (`apply_music` — video stream copy, seconds
instead of a re-encode; iterating on music does not touch the render).

Film longer than the track: `loop=True` loops the track list with acrossfade;
multiple tracks (multi-part generation) are joined with the same acrossfade.
Rules for prompt choice and loop vs parts: docs/decision-rules.md ("Music").
"""

import json
import math
import os
import re
import subprocess
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from . import ffmpeg, paths

API_URL = "https://api.stability.ai/v2beta/audio/stable-audio-2/text-to-audio"
BALANCE_URL = "https://api.stability.ai/v1/user/balance"
MODEL = "stable-audio-2.5"
MAX_DURATION_S = 190          # provider's limit for a single generation
CREDITS_PER_GENERATION = 20   # cost is FLAT, independent of duration (measured 2026-06)
MUSIC_DIR = paths.MUSIC
LOOP_XFADE_S = 4.0            # audio crossfade between parts/repetitions

DEFAULT_FADE_IN_S = 1.0
DEFAULT_FADE_OUT_S = 3.0
DEFAULT_LUFS = -14            # integrated loudness (YouTube standard)


# ---------------------------------------------------------------- generation

def _multipart(fields: dict) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    lines = []
    for k, v in fields.items():
        lines += [f"--{boundary}",
                  f'Content-Disposition: form-data; name="{k}"', "", str(v)]
    lines += [f"--{boundary}--", ""]
    return "\r\n".join(lines).encode(), f"multipart/form-data; boundary={boundary}"


def slug_for(prompt: str, out_dir: Path = MUSIC_DIR) -> Path:
    words = re.findall(r"[a-z0-9]+", prompt.lower().encode("ascii", "ignore").decode())
    base = "-".join(words[:5]) or "track"
    out = out_dir / f"{base}.mp3"
    n = 2
    while out.exists():
        out = out_dir / f"{base}-{n}.mp3"
        n += 1
    return out


def generate_track(prompt: str, duration_s: float, out: Path) -> Path:
    """A single Stable Audio generation -> mp3. Costs money (~$0.20) — call after approval."""
    key = os.environ.get("STABILITY_API_KEY")
    if not key:
        raise RuntimeError("no key in env STABILITY_API_KEY — create an account and generate "
                           "one at https://platform.stability.ai/account/keys")
    if not 1 <= duration_s <= MAX_DURATION_S:
        raise ValueError(f"duration {duration_s:g} s outside the range 1–{MAX_DURATION_S} s "
                         f"(single-generation limit); for a longer film: --loop when muxing "
                         f"or several parts (vm music TRACK1 TRACK2 ...)")
    body, ctype = _multipart({"prompt": prompt, "duration": int(round(duration_s)),
                              "output_format": "mp3", "model": MODEL})
    req = urllib.request.Request(API_URL, data=body, headers={
        "authorization": f"Bearer {key}", "accept": "audio/*", "content-type": ctype,
        # Cloudflare in front of api.stability.ai rejects urllib's default UA (403)
        "user-agent": "shotpilot-vm/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            audio = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise RuntimeError(f"Stability API HTTP {e.code}: {detail}") from e
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(audio)
    return out


def account_balance() -> int | None:
    """Stability credit balance; None when the key / network is missing (the balance
    is informational extra, not a blocker — errors are silenced)."""
    key = os.environ.get("STABILITY_API_KEY")
    if not key:
        return None
    req = urllib.request.Request(BALANCE_URL, headers={
        "authorization": f"Bearer {key}", "user-agent": "shotpilot-vm/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return int(json.loads(resp.read())["credits"])
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ analysis

def audio_duration(path: Path) -> float:
    return ffmpeg.duration(path)


_EBUR_LINE = re.compile(r"t:\s*([0-9.]+)\s+TARGET:\s*-?\d+ LUFS\s+M:\s*(-?[0-9.]+)")
_EBUR_SUMMARY = re.compile(r"^\s+(I|LRA):\s+(-?[0-9.]+)\s+(?:LUFS|LU)\s*$", re.M)


def probe_track(path: Path, window_s: float = 5.0) -> dict:
    """Duration, integrated loudness and energy curve (window_s windows, LUFS momentary)."""
    dur = audio_duration(path)
    res = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path),
                          "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True, check=True,
                         stdin=subprocess.DEVNULL)
    buckets: dict[int, list[float]] = {}
    for t, m in _EBUR_LINE.findall(res.stderr):
        buckets.setdefault(int(float(t) // window_s), []).append(float(m))
    energy = [{"t0": round(b * window_s, 1),
               "t1": round(min((b + 1) * window_s, dur), 1),
               "lufs": round(sum(v) / len(v), 1)}
              for b, v in sorted(buckets.items())]
    summary = dict(_EBUR_SUMMARY.findall(res.stderr))
    return {"file": str(path), "duration_s": round(dur, 2),
            "integrated_lufs": float(summary["I"]) if "I" in summary else None,
            "lra_lu": float(summary["LRA"]) if "LRA" in summary else None,
            "energy": energy}


# ---------------------------------------------------------------------- mux

def _joined_s(durs: list[float], xfade: float) -> float:
    return sum(durs) - (len(durs) - 1) * xfade


def apply_music(video: Path, tracks: list[Path], out: Path,
                fade_in: float = DEFAULT_FADE_IN_S,
                fade_out: float = DEFAULT_FADE_OUT_S,
                lufs: float = DEFAULT_LUFS, loop: bool = False) -> dict:
    """Mux music onto the film: video stream copy, audio acrossfade/loop + fade + loudnorm.

    Fades go AFTER loudnorm (single-pass loudnorm would boost them back up);
    aresample because loudnorm leaves 192 kHz.
    """
    vdur = ffmpeg.duration(video)
    durs = [audio_duration(t) for t in tracks]
    xfade = min(LOOP_XFADE_S, min(durs) / 2) if (loop or len(tracks) > 1) else 0.0

    seq, sdurs = list(tracks), list(durs)
    if loop and _joined_s(sdurs, xfade) < vdur:
        # repeat the whole list in order until it covers the film's length
        need = math.ceil((vdur - xfade) / (_joined_s(durs, xfade) - xfade))
        for i in range(len(tracks), need * len(tracks)):
            seq.append(tracks[i % len(tracks)])
            sdurs.append(durs[i % len(tracks)])

    joined = _joined_s(sdurs, xfade)
    audio_end = min(joined, vdur)
    cur = "[1:a]"
    parts = []
    for i in range(2, len(seq) + 1):
        parts.append(f"{cur}[{i}:a]acrossfade=d={xfade:.3f}[a{i}]")
        cur = f"[a{i}]"
    chain = (f"{cur}atrim=0:{vdur:.3f},loudnorm=I={lufs:g}:TP=-1.5:LRA=11,"
             f"afade=t=in:d={fade_in:.3f},"
             f"afade=t=out:st={max(0.0, audio_end - fade_out):.3f}:d={fade_out:.3f},"
             f"aresample=48000[aout]")
    parts.append(chain)

    inputs = ["-i", str(video)]
    for t in seq:
        inputs += ["-i", str(t)]
    ffmpeg.run_to([*inputs, "-filter_complex", ";".join(parts),
                   "-map", "0:v", "-map", "[aout]", "-c:v", "copy",
                   "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                   "-t", f"{vdur:.3f}"], out)
    return {"out": str(out), "video_s": round(vdur, 2),
            "audio_s": round(audio_end, 2), "parts": len(seq),
            "looped": len(seq) > len(tracks), "xfade_s": round(xfade, 2),
            "gap_s": round(max(0.0, vdur - joined), 2)}
