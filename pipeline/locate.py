"""Mapping the montage timeline to source shots (and back). CLI: `shot locate`.

The timeline is built by `montage.build_timeline` — this module holds timecode
parsing and queries over the ready timeline. Everything read-only and pure
(no manifest/IO).
"""

import re
from pathlib import Path

_TC = re.compile(r"^\d+:\d{1,2}(\.\d+)?$")


def parse_timecode(s: str) -> float | None:
    """`M:SS`, `M:SS.s` or bare seconds -> seconds; other text -> None."""
    if _TC.match(s):
        m, sec = s.split(":")
        return int(m) * 60 + float(sec)
    try:
        return float(s)
    except ValueError:
        return None


def fmt_tc(sec: float) -> str:
    return f"{int(sec // 60)}:{sec % 60:04.1f}"


def locate_time(tl: list[dict], t: float, xfade: float) -> dict:
    """The clip playing at time t (montage timeline) + a possible transition to the next."""
    if not tl or t < 0 or t >= tl[-1]["end_s"]:
        return {"t": t, "clip": None}
    hit = next((r for r in tl if r["start_s"] <= t < r["end_s"]), None)
    nxt = tl[hit["index"] + 1] if hit and hit["index"] + 1 < len(tl) else None
    in_xfade = bool(nxt and t >= nxt["start_s"])  # crossfade overlap window
    return {"t": t, "clip": hit, "in_transition": in_xfade,
            "next": nxt if in_xfade else None}


def locate_match(tl: list[dict], q: str) -> list[dict]:
    """Reverse lookup: clips whose file/source/label matches the query."""
    ql = q.lower()
    out = []
    for r in tl:
        hay = " ".join(str(x) for x in (r.get("source"), r.get("label"),
                                        r.get("use"), r.get("select")) if x).lower()
        if ql in hay:
            out.append(r)
    return out


def src_name(r: dict | None) -> str:
    return Path(r["source"]).name if r and r.get("source") else "?"
