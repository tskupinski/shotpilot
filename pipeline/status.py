"""Project status aggregation: inputs, selects, cuts, totals, publish.

The single source of the `shot status` payload — shared by the CLI
(`shot status`, cli.py) and the read-only web UI (`shot ui`, webui.py).
Pure view over the manifest + per-video summaries; writes nothing.
"""

import json
from pathlib import Path

from . import config, manifest, montage, paths, sequence

VIDEO_EXT = {".mp4", ".mov", ".mts", ".avi", ".mkv"}


def build_status(data: dict | None = None) -> dict:
    """The `shot status --json` payload (also the base of the web UI's API)."""
    if data is None:
        data = manifest.load()
    inputs = []
    input_dir = config.input_dir()
    for f in sorted(input_dir.iterdir()) if input_dir.exists() else []:
        if f.suffix.lower() not in VIDEO_EXT:
            continue
        summary_path = paths.video_dir(f.stem) / "summary.json"
        entry = {"file": str(f), "stem": f.stem, "analyzed": summary_path.exists()}
        if summary_path.exists():
            s = json.loads(summary_path.read_text())
            entry.update(duration=s["video"]["duration"],
                         kept_pct=s["stats"]["kept_pct"],
                         warnings=s["warnings"])
        inputs.append(entry)

    selects = data["selects"]
    by_file = {s["file"]: s for s in selects}
    cuts = {}
    for name in manifest.cut_names(data):
        cut = manifest.get_cut(data, name)
        cseq = cut.get("sequence", [])
        cseq_s = round(sum(montage.use_duration(by_file.get(e["select"], {}),
                                                e["use"]) or 0 for e in cseq), 1)
        cuts[name] = {"sequence_len": len(cseq), "sequence_s": cseq_s,
                      "target_s": cut.get("target_s"),
                      "notes": cut.get("notes"),
                      "render": montage.render_state(cut, data),
                      "music": {"tracks": len(cut.get("music", {}).get("tracks", [])),
                                "applied": montage.music_state(cut, data)}}
    return {
        "input_dir": str(input_dir),
        "inputs": inputs,
        "selects": selects,
        "totals": {
            "inputs": len(inputs),
            "analyzed": sum(1 for i in inputs if i["analyzed"]),
            "selects": len(selects),
            "tagged": sum(1 for s in selects if s.get("tags")),
            "rejected": sum(1 for s in selects if s.get("reject")),
            "speed_variants": sum(len(s.get("speed_variants", {})) for s in selects),
            "selects_total_s": round(sum(s["range"][1] - s["range"][0]
                                         for s in selects if s.get("range")), 1),
        },
        "cuts": cuts,
        "publish": data.get("publish"),
    }


def format_status(payload: dict) -> str:
    """The human lines of `shot status` (the payload rendered for the terminal)."""
    input_dir = payload["input_dir"]
    inputs, selects, cuts = payload["inputs"], payload["selects"], payload["cuts"]
    loc = f" ({input_dir}/)" if input_dir != "input" else ""
    lines = [f"Inputs{loc}: {payload['totals']['analyzed']}/{payload['totals']['inputs']} analyzed"]
    for i in inputs:
        mark = "✓" if i["analyzed"] else "·"
        warn = f"  WARNINGS: {len(i['warnings'])}" if i.get("warnings") else ""
        lines.append(f"  {mark} {i['file']}{warn}")
    rej = payload["totals"]["rejected"]
    lines.append(f"Selects: {len(selects)} (total {payload['totals']['selects_total_s']} s), "
                 f"speed variants: {payload['totals']['speed_variants']}"
                 + (f", rejected: {rej}" if rej else ""))
    for s in selects:
        stars = "★" * (s.get("stars") or 0)
        var = ", ".join(f"x{k}" for k in s.get("speed_variants", {})) or "—"
        pace_v = f"{s['pace_pct_s']} %/s" if s.get("pace_pct_s") else "?"
        mark = "✗ " if s.get("reject") else ""
        lines.append(f"  {mark}{stars:<5} {Path(s['file']).name}  [{s['range'][0]}–{s['range'][1]}s] "
                     f"pace {pace_v}, variants: {var}  {sequence.tags_compact(s)}")
        if s.get("notes"):
            lines.append(f"        {s['notes']}")
    for name, c in cuts.items():
        if not c["sequence_len"]:
            continue
        r = c["render"]
        reason = f" ({r['reason']})" if r.get("reason") else ""
        target = c["target_s"]
        seq_disp = (f"{c['sequence_s']} s / target {target:g} s" if target
                    else f"{c['sequence_s']} s")
        label = "Montage" if name == "main" else f"Montage [{name}]"
        draft = " (draft)" if r.get("draft") else ""
        lines.append(f"{label}: {c['sequence_len']} clips in sequence ({seq_disp}), "
                     f"render: {r['state']}{draft}{reason}")
        if c["notes"]:
            lines.append(f"  note: {c['notes']}")
        mus = c["music"]
        if mus["tracks"] or mus["applied"]["state"] != montage.STATE_NONE:
            a = mus["applied"]
            reason = f" ({a['reason']})" if a.get("reason") else ""
            lines.append(f"  music: tracks {mus['tracks']}, "
                         f"applied: {a['state']}{reason}")
    pub = payload["publish"]
    if pub:
        th = pub.get("thumbnail")
        th_disp = (f"✓ ({Path(th['source']).name} @ {th['at_s']:g}s)" if th else "—")
        desc_disp = "✓" if pub.get("description_file") else "—"
        if not pub.get("description_file") and (paths.PUBLISH / "description.txt").exists():
            desc_disp = "— (file exists, no manifest entry — see `shot publish`)"
        lines.append(f"Publishing: title {'✓' if pub.get('title') else '—'}, "
                     f"description {desc_disp}, "
                     f"thumbnail {th_disp}")
    return "\n".join(lines)
