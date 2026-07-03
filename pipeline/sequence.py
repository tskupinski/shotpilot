"""Montage sequence preview: the view, casting pool, drop candidates.

Pure presentation + casting heuristics on manifest data (dicts come in as
parameters — the module does not read the manifest itself). CLI: `vm sequence`
(no arguments).
"""

from pathlib import Path

from . import lint, montage


def tags_compact(entry: dict) -> str:
    t = entry.get("tags")
    if not t:
        return "[untagged]"
    role = f" +{t['role']}" if t.get("role") else ""
    return f"[{t.get('scene', '?')}/{t.get('shot', '?')}/{t.get('light', '?')}{role}]"


def _round1(v: float | None) -> float | None:
    return round(v, 1) if v else None


def drop_candidates(seq: list[dict], by_file: dict) -> list[dict]:
    """Drop candidates when the target is exceeded: tag twins + lowest ★."""
    reasons = {}
    twins = {}
    for e in seq:
        t = (by_file.get(e["select"]) or {}).get("tags") or {}
        if t.get("scene") and t.get("shot"):
            twins.setdefault((t["scene"], t["shot"]), []).append(e["select"])
    for files in twins.values():
        if len(files) > 1:
            ranked = sorted(files, key=lambda f: by_file[f].get("stars") or 0)
            for f in ranked[:-1]:
                reasons[f] = (f"tag twin (scene+shot) "
                              f"of {Path(ranked[-1]).name}")
    by_stars = sorted(seq, key=lambda e: by_file.get(e["select"], {}).get("stars") or 0)
    for e in by_stars:
        if len(reasons) >= 5:
            break
        reasons.setdefault(e["select"], "lowest ★")
    out = []
    for e in seq:
        f = e["select"]
        if f in reasons:
            sel = by_file.get(f, {})
            out.append({"file": f, "stars": sel.get("stars"),
                        "duration_s": _round1(montage.use_duration(sel, e["use"])),
                        "reason": reasons[f]})
    return sorted(out, key=lambda c: c["stars"] or 0)[:5]


def sequence_view(mont: dict, selects: list[dict]) -> tuple[dict, str]:
    by_file = {s["file"]: s for s in selects}
    seq = mont.get("sequence", [])
    target_s = mont.get("target_s")
    rows, total = [], 0.0
    for i, e in enumerate(seq, 1):
        sel = by_file.get(e["select"], {})
        dur = montage.use_duration(sel, e["use"])
        total += dur or 0
        rows.append({"n": i, "select": e["select"], "use": e["use"],
                     "duration_s": _round1(dur), "stars": sel.get("stars"),
                     "tags": sel.get("tags")})
    warnings = lint.lint_sequence(seq, selects, target_s)
    in_seq = {e["select"] for e in seq}
    unused = [{"file": s["file"], "stars": s.get("stars"),
               "duration_s": _round1(montage.use_duration(s, s["file"])),
               "tags": s.get("tags")}
              for s in selects if s["file"] not in in_seq and not s.get("reject")]
    rejected = [{"file": s["file"], "stars": s.get("stars")}
                for s in selects if s.get("reject")]
    payload = {"sequence": rows, "total_s": round(total, 1), "target_s": target_s,
               "notes": mont.get("notes"),
               "unused": unused, "rejected": rejected, "warnings": warnings}
    if target_s and total > target_s:
        payload["drop_candidates"] = drop_candidates(seq, by_file)
    if not rows:
        human = "empty sequence — `vm sequence FILE...`"
        if target_s:
            human += f"\nMontage target: {target_s:g} s"
        return payload, human
    lines = [f"  {r['n']:>3}. {Path(r['use']).name}  "
             f"{r['duration_s'] if r['duration_s'] is not None else '?'} s  "
             f"{tags_compact({'tags': r['tags']})}" for r in rows]
    if target_s:
        delta = payload["total_s"] - target_s
        lines.append(f"Total: {payload['total_s']} s / target {target_s:g} s "
                     f"(Δ {delta:+.1f} s), clips: {len(rows)}")
    else:
        lines.append(f"Total: {payload['total_s']} s, clips: {len(rows)}")
    if payload["notes"]:
        lines.append(f"Cut note: {payload['notes']}")
    if unused:
        lines.append(f"Outside the sequence (casting pool): {len(unused)}")
        for u in unused:
            stars = "★" * (u["stars"] or 0)
            lines.append(f"  {stars:<5} {Path(u['file']).name}  "
                         f"{u['duration_s'] or '?'} s  "
                         f"{tags_compact({'tags': u['tags']})}")
    if rejected:
        lines.append(f"Rejected (out of casting): {len(rejected)}")
        for r in rejected:
            lines.append(f"  ✗ {Path(r['file']).name}")
    for c in payload.get("drop_candidates", []):
        stars = "★" * (c["stars"] or 0)
        lines.append(f"  DROP? {stars:<5} {Path(c['file']).name}  "
                     f"{c['duration_s'] or '?'} s — {c['reason']}")
    for w in warnings:
        lines.append(f"  WARNING: {lint.format_warning(w)}")
    return payload, "\n".join(lines)
