"""Montage sequence lint: diversity and narrative structure. CLI: `vm sequence`.

The warning schema (types + fields) and its rendering into messages (WARN_FMT)
live TOGETHER in this file — they must not drift apart. Numeric thresholds
below; criteria are described in docs/decision-rules.md ("Montage — ordering").
"""

import json
from collections import Counter
from pathlib import Path

from .montage import effective_pace, use_duration

# Diversity/structure lint thresholds — criteria described in docs/decision-rules.md.
SHOT_OVERUSE_SHARE = 0.4   # one shot type > 40% of the tagged sequence
SHOT_OVERUSE_MIN = 6       # ...with at least this many tagged entries
SCENE_OVERUSE_COUNT = 3    # the same scene this many times in the whole sequence
LIGHT_RUN_LEN = 4          # this many consecutive clips with the same light
MONOTONY_LEN = 4           # this many consecutive clips without pace contrast...
MONOTONY_RATIO = 1.5       # ...when max/min of effective pace < this
DYNAMIC_PCT_S = 4.0        # effective pace considered dynamic
BREATH_PCT_S = 2.5         # effective pace considered a breather
BREATH_AFTER = 3           # after this many dynamic in a row a breather is due


WARN_FMT = {
    "untagged": lambda w: f"untagged: {w['file']}",
    "rejected_clip": lambda w: (f"clip {w['at'][0]} is a REJECTED select "
                                f"({Path(w['file']).name}) — remove it from the sequence or `vm tag --unreject`"),
    "adjacent_same_shot": lambda w: (f"clips {w['at'][0]} and {w['at'][1]} next "
                                     f"to each other with the same shot={w['value']}"),
    "adjacent_same_scene": lambda w: (f"clips {w['at'][0]} and {w['at'][1]} next "
                                      f"to each other with the same scene={w['value']}"),
    "weak_hook": lambda w: (f"weak opening: {Path(w['file']).name} "
                            f"(★{w['stars'] or '?'}, no role=hook) — "
                            f"the hook should be ★4–5 with a clear subject"),
    "weak_closer": lambda w: (f"weak closer: {Path(w['file']).name} — "
                              f"the finale: role=final, panorama or golden-hour"),
    "shot_overuse": lambda w: (f"shot={w['value']} dominates: {w['count']} clips "
                               f"({w['pct']}% of the sequence)"),
    "scene_overuse": lambda w: f"scene={w['value']} as many as {w['count']}× in the sequence",
    "light_run": lambda w: (f"clips {w['at'][0]}–{w['at'][1]}: a run of the same "
                            f"light ({w['value']})"),
    "tempo_monotony": lambda w: (f"clips {w['at'][0]}–{w['at'][1]}: monotonous "
                                 f"effective pace ({w['paces'][0]:g}–"
                                 f"{w['paces'][-1]:g} %/s without contrast)"),
    "missing_breath": lambda w: (f"clips {w['at'][0]}–{w['at'][1]}: a run of "
                                 f"dynamic clips with no breather after it"),
    "duration_off_target": lambda w: (f"duration {w['total_s']} s vs target "
                                      f"{w['target_s']:g} s (Δ {w['delta_s']:+g} s)"),
}


def format_warning(w: dict) -> str:
    fmt = WARN_FMT.get(w["type"], lambda w: json.dumps(w, ensure_ascii=False))
    return fmt(w)


def _monotone_stretches(vals: list[float]) -> list[tuple[int, int]]:
    """Maximal stretches ≥ MONOTONY_LEN where max/min < MONOTONY_RATIO."""
    out, i = [], 0
    while i < len(vals):
        lo = hi = vals[i]
        j = i
        while j + 1 < len(vals):
            nlo, nhi = min(lo, vals[j + 1]), max(hi, vals[j + 1])
            if nlo <= 0 or nhi / nlo >= MONOTONY_RATIO:
                break
            lo, hi, j = nlo, nhi, j + 1
        if j - i + 1 >= MONOTONY_LEN:
            out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def lint_sequence(entries: list[dict], selects: list[dict],
                  target_s: float | None = None) -> list[dict]:
    """Mechanical diversity and narrative-structure warnings (non-blocking).

    Criteria and thresholds described in docs/decision-rules.md; entries without
    tags / pace_pct_s don't participate in the checks that require them (old
    manifests keep working unchanged). `at` positions are 1-based like the
    preview numbering.
    """
    by_file = {s["file"]: s for s in selects}
    sels = [by_file.get(e["select"]) or {} for e in entries]
    tags = [s.get("tags") or None for s in sels]
    effs = [effective_pace(s, e["use"]) for s, e in zip(sels, entries)]
    n = len(entries)
    warnings = []

    # selects marked as rejected should not enter the montage
    for i, s in enumerate(sels):
        if s.get("reject"):
            warnings.append({"type": "rejected_clip", "at": [i + 1],
                             "file": entries[i]["select"]})

    # missing tags + adjacency (unchanged from the original lint)
    prev_tags = None
    for i, t in enumerate(tags):
        if not t:
            warnings.append({"type": "untagged", "file": entries[i]["select"]})
            prev_tags = None
            continue
        if prev_tags:
            for dim, wtype in (("shot", "adjacent_same_shot"),
                               ("scene", "adjacent_same_scene")):
                if t.get(dim) and t.get(dim) == prev_tags.get(dim):
                    warnings.append({"type": wtype, "at": [i, i + 1],
                                     "value": t[dim]})
        prev_tags = t

    if not entries:
        return warnings

    # structure: the opening and the closer
    first_t = tags[0] or {}
    if first_t.get("role") != "hook" and (sels[0].get("stars") or 0) < 4:
        warnings.append({"type": "weak_hook", "file": entries[0]["select"],
                         "stars": sels[0].get("stars"),
                         "role": first_t.get("role")})
    last_t = tags[-1] or {}
    if (last_t.get("role") != "final" and last_t.get("shot") != "panorama"
            and last_t.get("light") != "golden-hour"):
        warnings.append({"type": "weak_closer", "file": entries[-1]["select"],
                         "tags": tags[-1]})

    # global distribution: shot-type dominance and scene repeats
    tagged = [t for t in tags if t]
    if len(tagged) >= SHOT_OVERUSE_MIN:
        for value, cnt in Counter(t["shot"] for t in tagged if t.get("shot")).items():
            if cnt / len(tagged) > SHOT_OVERUSE_SHARE:
                warnings.append({"type": "shot_overuse", "value": value,
                                 "count": cnt,
                                 "pct": round(100 * cnt / len(tagged))})
    for value, cnt in Counter(t["scene"] for t in tagged if t.get("scene")).items():
        if cnt >= SCENE_OVERUSE_COUNT:
            warnings.append({"type": "scene_overuse", "value": value, "count": cnt})

    # runs of the same light
    i = 0
    while i < n:
        light = (tags[i] or {}).get("light")
        j = i
        while light and j + 1 < n and (tags[j + 1] or {}).get("light") == light:
            j += 1
        if light and j - i + 1 >= LIGHT_RUN_LEN:
            warnings.append({"type": "light_run", "value": light,
                             "at": [i + 1, j + 1]})
        i = j + 1

    # effective-pace monotony (measured stretches; a missing measurement breaks one)
    i = 0
    while i < n:
        if effs[i] is None:
            i += 1
            continue
        j = i
        while j + 1 < n and effs[j + 1] is not None:
            j += 1
        for a, b in _monotone_stretches(effs[i:j + 1]):
            warnings.append({"type": "tempo_monotony",
                             "at": [i + a + 1, i + b + 1],
                             "paces": effs[i + a:i + b + 1]})
        i = j + 1

    # no breather after a run of dynamic clips
    run_start, run_len = 0, 0
    for idx in range(n):
        eff = effs[idx]
        if eff is not None and eff >= DYNAMIC_PCT_S:
            if run_len == 0:
                run_start = idx
            run_len += 1
            continue
        if run_len >= BREATH_AFTER:
            role = (tags[idx] or {}).get("role")
            is_breath = role == "breather" or (eff is not None and eff < BREATH_PCT_S)
            if not is_breath and (eff is not None or role is not None):
                warnings.append({"type": "missing_breath",
                                 "at": [run_start + 1, idx]})
        run_len = 0

    # total vs target (durations of the files actually spliced)
    if target_s:
        total = sum(use_duration(s, e["use"]) or 0
                    for s, e in zip(sels, entries))
        delta = total - target_s
        if abs(delta) > max(10, 0.1 * target_s):
            warnings.append({"type": "duration_off_target",
                             "total_s": round(total, 1),
                             "target_s": target_s,
                             "delta_s": round(delta, 1)})
    return warnings
