"""shot — agent-first CLI for Shotpilot.

Conventions (all subcommands):
- `--json`: JSON result on stdout; logs always on stderr
- multiple files natively (batch) — no shell loops
- exit != 0 on error; in batch mode one file's error does not stop the rest
  (aggregate exit 1 + "error" field in that file's result)
"""

import argparse
import datetime
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import (config, contact, manifest,
               montage as montage_mod, motion, music as music_mod,
               ffmpeg, locate as locate_mod, pace as pace_mod, paths,
               publish as publish_mod, scan as scan_mod,
               schema as schema_mod, sequence as sequence_mod)
from .cut import cut_range
from .probe import probe

SELECTS_DIR = paths.SELECTS
VIDEO_EXT = {".mp4", ".mov", ".mts", ".avi", ".mkv"}
KEBAB = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*$")


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def emit(payload, as_json: bool, human: str | None = None) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif human:
        print(human)


def _batch(files: list[Path], fn) -> tuple[list[dict], int]:
    """Runs fn(file) per file; errors do not stop the batch."""
    results, rc = [], 0
    for f in files:
        if not f.exists():
            results.append({"file": str(f), "error": "file does not exist"})
            rc = 1
            continue
        try:
            results.append(fn(f))
        except Exception as e:  # noqa: BLE001 — the batch must survive to the end
            log(f"ERROR [{f}]: {e}")
            results.append({"file": str(f), "error": str(e)})
            rc = 1
    return results, rc


# --------------------------------------------------------------- subcommands

def cmd_status(args) -> int:
    inputs = []
    input_dir = config.input_dir()
    for f in sorted(input_dir.iterdir()) if input_dir.exists() else []:
        if f.suffix.lower() not in VIDEO_EXT:
            continue
        summary_path = paths.video_dir(f.stem) / "summary.json"
        entry = {"file": str(f), "analyzed": summary_path.exists()}
        if summary_path.exists():
            s = json.loads(summary_path.read_text())
            entry.update(duration=s["video"]["duration"],
                         kept_pct=s["stats"]["kept_pct"],
                         warnings=s["warnings"])
        inputs.append(entry)

    data = manifest.load()
    selects = data["selects"]
    by_file = {s["file"]: s for s in selects}
    cuts = {}
    for name in manifest.cut_names(data):
        cut = manifest.get_cut(data, name)
        cseq = cut.get("sequence", [])
        cseq_s = round(sum(montage_mod.use_duration(by_file.get(e["select"], {}),
                                                    e["use"]) or 0 for e in cseq), 1)
        cuts[name] = {"sequence_len": len(cseq), "sequence_s": cseq_s,
                      "target_s": cut.get("target_s"),
                      "notes": cut.get("notes"),
                      "render": montage_mod.render_state(cut),
                      "music": {"tracks": len(cut.get("music", {}).get("tracks", [])),
                                "applied": montage_mod.music_state(cut)}}
    payload = {
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
    loc = f" ({input_dir}/)" if str(input_dir) != "input" else ""
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
                     f"pace {pace_v}, variants: {var}  {sequence_mod.tags_compact(s)}")
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
        if mus["tracks"] or mus["applied"]["state"] != montage_mod.STATE_NONE:
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
    emit(payload, args.json, "\n".join(lines))
    return 0


def _append_note(entry: dict, note: str) -> str:
    return (entry.get("notes", "") + "; " if entry.get("notes") else "") + note


def cmd_scan(args) -> int:
    results, rc = _batch(args.files, lambda f: scan_mod.scan_video(
        f, threshold=args.threshold, min_clip=args.min_clip,
        margin=args.margin, do_cut=args.cut, force=args.force))
    human = "\n".join(
        f"{r['video']['path']}: {r['stats']['n_segments']} seg., "
        f"{r['stats']['kept_pct']}% kept, threshold {r['params']['threshold']}"
        if "error" not in r else f"{r['file']}: ERROR {r['error']}"
        for r in results)
    emit({"results": results}, args.json, human)
    return rc


def cmd_sheet(args) -> int:
    def one(f: Path) -> dict:
        out = contact.make_contact_sheet(f, args.interval, args.cols, force=args.force)
        return {"file": str(f), "sheet": str(out)}
    results, rc = _batch(args.files, one)
    emit({"results": results}, args.json,
         "\n".join(r.get("sheet", f"{r['file']}: ERROR") for r in results))
    return rc


def cmd_frames(args) -> int:
    if not args.file.exists():
        log(f"file does not exist: {args.file}")
        return 1
    out_dir = paths.video_dir(args.file.stem) / "frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for t in args.times:
        out = out_dir / f"t{t:g}.jpg"
        ffmpeg.extract_frame(args.file, t, out, width=args.width)
        written.append(str(out))
    emit({"file": str(args.file), "frames": written}, args.json, "\n".join(written))
    return 0


def cmd_jitter(args) -> int:
    if not args.file.exists():
        log(f"file does not exist: {args.file}")
        return 1
    info = probe(args.file)
    motions = motion.load_or_analyze(args.file, info.fps)
    result = motion.jitter_test(motions, start=args.start, end=args.end)
    result = {"file": str(args.file), **result}
    if "error" in result:
        emit(result, args.json, f"ERROR: {result['error']}")
        return 1
    emit(result, args.json,
         f"{args.file.name} [{result['from']}–{result['to']}s]: "
         f"{result['sign_flips_x']}/{result['sign_flips_y']} sign flips x/y "
         f"({result['flips_per_s']}/s) -> {result['verdict']}")
    return 0


def _select_one(file: Path, start: float, end: float, label: str,
                stars: int | None = None, note: str | None = None,
                out: Path | None = None) -> dict:
    """Cut one select + manifest entry; raises on a bad input (batch-friendly)."""
    if not file.exists():
        raise ValueError(f"file does not exist: {file}")
    if end <= start:
        raise ValueError("end must be > start")
    info = probe(file)
    out = out or SELECTS_DIR / f"{file.stem}_{label}.mp4"
    log(f"Cutting {start:g}–{end:g}s -> {out} ...")
    cut_range(info, start, end, out)
    entry = {
        "file": str(out),
        "source": str(file),
        "range": [start, end],
        "label": label,
    }
    if stars is not None:
        entry["stars"] = stars
    if note:
        entry["notes"] = note
    manifest.upsert_select(entry)
    return manifest.find(out)


def _select_plan(args) -> int:
    """Batch cutting from a JSONL plan (one decision per line: file, start, end,
    label, optionally stars/note/out). Already-cut selects with a matching range
    are SKIPPED — an interrupted run is resumed by re-running the same command."""
    if args.label or args.stars is not None or args.note or args.out:
        log("--plan does not combine with --label/--stars/--note/--out "
            "(those fields live in the plan lines)")
        return 1
    lines = [ln for ln in args.plan.read_text().splitlines() if ln.strip()]
    results, rc, done, skipped = [], 0, 0, 0
    for i, ln in enumerate(lines, 1):
        try:
            e = json.loads(ln)
            file, label = Path(e["file"]), e["label"]
            start, end = float(e["start"]), float(e["end"])
            out = Path(e["out"]) if e.get("out") else SELECTS_DIR / f"{file.stem}_{label}.mp4"
            existing = manifest.find(out)
            if (out.exists() and not args.force
                    and existing and existing.get("range") == [start, end]):
                log(f"skip (already cut): {out}")
                results.append({**existing, "skipped": True})
                skipped += 1
                continue
            results.append(_select_one(file, start, end, label,
                                       e.get("stars"), e.get("note"), out))
            done += 1
        except Exception as ex:  # noqa: BLE001 — the batch must survive to the end
            log(f"ERROR [plan line {i}]: {ex}")
            results.append({"plan_line": i, "error": str(ex)})
            rc = 1
    emit({"results": results},
         args.json, f"selects: {done} cut, {skipped} skipped, "
                    f"{len(lines) - done - skipped} failed")
    return rc


def cmd_select(args) -> int:
    if args.plan:
        return _select_plan(args)
    if args.file is None or args.start is None or args.end is None or not args.label:
        log("usage: shot select FILE START END --label X  (or: shot select --plan PLAN.jsonl)")
        return 1
    try:
        found = _select_one(args.file, args.start, args.end, args.label,
                            args.stars, args.note, args.out)
    except ValueError as e:
        log(str(e))
        return 1
    emit({**found, "manifest": str(manifest.MANIFEST_PATH)},
         args.json, found["file"])
    return 0


def _pace_motions(f: Path, info) -> tuple[list, float]:
    """Motion for a clip + timeline offset relative to the source.

    From the source range per the manifest (no decoding; source timestamps,
    offset = range start), otherwise file analysis (own timestamps, offset 0).
    """
    entry = manifest.find(f)
    if entry and entry.get("source") and entry.get("range"):
        src_csv = paths.video_dir(Path(entry["source"]).stem) / "motion.csv"
        if src_csv.exists():
            a, b = entry["range"]
            log(f"  motion from source motion.csv ({entry['source']}, {a}–{b}s)")
            return [m for m in motion.load_csv(src_csv) if a <= m.t < b], a
    return motion.load_or_analyze(f, info.fps), 0.0


def cmd_pace(args) -> int:
    files = list(args.files)
    if args.selects:
        files += [Path(s["file"]) for s in manifest.load()["selects"]]
    if not files:
        log("provide files or --selects")
        return 1

    def one(f: Path) -> dict:
        info = probe(f)
        motions, offset = _pace_motions(f, info)
        p = pace_mod.measure_pace(motions, info.fps)
        result = {
            "file": str(f), "duration": round(info.duration, 2), "pace": p,
            "classification": pace_mod.classify(p["total_pct_s"], args.slow_below, args.fast_above),
            "recommended_speed": pace_mod.recommend_speed(p["total_pct_s"], args.target, args.max_speed),
        }
        if args.profile:
            result["profile"] = pace_mod.profile_with_src_axis(
                pace_mod.pace_profile(motions, info.fps, args.window), offset)
        manifest.update_fields(f, pace_pct_s=p["total_pct_s"])
        return result

    results, rc = _batch(files, one)
    human = "\n".join(pace_mod.format_result(r) for r in results)
    emit({"results": results,
          "bands": {"slow_below": args.slow_below, "fast_above": args.fast_above,
                    "target": args.target}},
         args.json, human)
    return rc


def cmd_speed(args) -> int:
    if not args.file.exists():
        log(f"file does not exist: {args.file}")
        return 1
    if args.factor <= 0:
        log("factor must be > 0")
        return 1
    if re.search(r"_x[\d.]+$", args.file.stem):
        log(f"{args.file.name} is already a speed variant — render from the original "
            f"(multiplier stacking and double re-encode)")
        return 1
    info = probe(args.file)
    out = args.out or args.file.with_name(f"{args.file.stem}_x{args.factor:g}.mp4")
    log(f"Rendering x{args.factor:g} -> {out} ...")
    pace_mod.apply_speed(info, args.factor, out)
    manifest.update_fields(args.file, speed_variants={f"{args.factor:g}": str(out)})
    emit({"file": str(args.file), "speed": args.factor, "out": str(out),
          "duration": round(info.duration / args.factor, 2)},
         args.json, str(out))
    return 0


def cmd_tag(args) -> int:
    """Content tags + select metadata (batch) — instead of manual manifest edits."""
    tags = {k: getattr(args, k) for k in ("scene", "shot", "light", "role")
            if getattr(args, k)}
    for k, v in tags.items():
        if not KEBAB.match(v):
            log(f"--{k} must be kebab-case: {v}")
            return 1
    if args.note is not None and args.append_note:
        log("--note and --append-note are mutually exclusive")
        return 1
    if args.reject and args.unreject:
        log("--reject and --unreject are mutually exclusive")
        return 1
    if (not tags and args.note is None and not args.append_note and args.stars is None
            and not args.reject and not args.unreject):
        log("specify what to set: --scene/--shot/--light/--role/--note/--append-note/"
            "--stars/--reject/--unreject")
        return 1

    def one(f: Path) -> dict:
        entry = manifest.find(f)
        if entry is None:
            raise ValueError("not in the manifest — run shot select first")
        fields = {}
        if tags:
            fields["tags"] = {**(entry.get("tags") or {}), **tags}
        if args.note is not None:
            fields["notes"] = args.note
        if args.append_note:
            fields["notes"] = _append_note(entry, args.append_note)
        if args.stars is not None:
            fields["stars"] = args.stars
        if args.reject:
            fields["reject"] = True
        if args.unreject:
            fields["reject"] = False
        manifest.update_fields(f, **fields)
        return manifest.find(f)

    results, rc = _batch(args.files, one)
    human = "\n".join(
        f"{Path(r['file']).name}: {sequence_mod.tags_compact(r)}" if "error" not in r
        else f"{r['file']}: ERROR {r['error']}"
        for r in results)
    emit({"results": results}, args.json, human)
    return rc


def cmd_trim(args) -> int:
    """Trims a select: fresh re-cut from the SOURCE (no generation loss), same path."""
    f = args.file
    entry = manifest.find(f)
    if entry is None or not entry.get("source") or not entry.get("range"):
        log(f"{f}: not in the manifest (with source/range) — trim works on selects")
        return 1
    if args.end <= args.start:
        log("end must be > start")
        return 1
    src = Path(entry["source"])
    if not src.exists():
        log(f"source does not exist: {src}")
        return 1
    info = probe(src)
    if args.end > info.duration + 0.05:
        log(f"range beyond the source ({info.duration:.1f} s)")
        return 1
    old = entry["range"]
    if not (old[0] <= args.start and args.end <= old[1]):
        log(f"WARNING: new range extends beyond the old one [{old[0]:g}–{old[1]:g}] — "
            f"a re-cut, not a narrowing")
    data = manifest.load()
    shared = [name for name in manifest.cut_names(data)
              if any(e["select"] == entry["file"]
                     for e in manifest.get_cut(data, name).get("sequence", []))]
    if len(shared) > 1:
        log(f"WARNING: the select appears in {len(shared)} cuts ({', '.join(shared)}) — "
            f"trim will change it in ALL of them (renders become stale). "
            f"For a single-version variant: a separate select from the source "
            f"(shot select SOURCE A B --label ...)")
    log(f"Re-cut from source {args.start:g}–{args.end:g}s -> {f} ...")
    cut_range(info, args.start, args.end, f)

    fields = {"range": [args.start, args.end],
              "range_history": entry.get("range_history", []) + [old]}
    if args.note:
        fields["notes"] = _append_note(entry, args.note)
    manifest.update_fields(f, **fields)

    variants = entry.get("speed_variants", {})
    dropped, refreshed = [], []
    if args.drop_variants:
        for v in variants.values():
            Path(v).unlink(missing_ok=True)
            dropped.append(v)
        manifest.set_speed_variants(f, {})
    elif variants:
        sel_info = probe(f)
        for k, v in variants.items():
            log(f"Refreshing variant x{k} -> {v} ...")
            pace_mod.apply_speed(sel_info, float(k), Path(v))
            refreshed.append(v)

    sel_info = probe(f)
    motions, _ = _pace_motions(f, sel_info)
    pace_v = None
    if motions:
        pace_v = pace_mod.measure_pace(motions, sel_info.fps)["total_pct_s"]
        manifest.update_fields(f, pace_pct_s=pace_v)

    emit({**manifest.find(f), "variants_refreshed": refreshed,
          "variants_dropped": dropped},
         args.json,
         f"{f} [{args.start:g}–{args.end:g}s]"
         + (f", pace {pace_v} %/s" if pace_v else "")
         + (f", refreshed variants: {len(refreshed)}" if refreshed else "")
         + (f", dropped variants: {len(dropped)}" if dropped else ""))
    return 0


def cmd_sequence(args) -> int:
    """Sets/shows the cut's ordering (default: main); arguments = files to splice."""
    if not KEBAB.match(args.cut):
        log("cut name: kebab-case (a-z, 0-9, hyphens)")
        return 1
    if args.note is not None and args.append_note:
        log("--note and --append-note are mutually exclusive")
        return 1

    def save_note() -> None:
        if args.note is not None:
            manifest.set_cut_notes(args.note, cut=args.cut)
        elif args.append_note:
            manifest.set_cut_notes(args.append_note, cut=args.cut, append=True)

    if args.clear:
        manifest.set_sequence([], cut=args.cut)
        save_note()
        emit({"sequence": [], "total_s": 0, "warnings": []}, args.json,
             "sequence cleared")
        return 0
    if args.target is not None:
        if args.target < 0:
            log("--target cannot be negative")
            return 1
        manifest.set_target(args.target or None, cut=args.cut)
    if args.files:
        selects = manifest.load()["selects"]
        entries, errors, seen = [], [], set()
        for f in args.files:
            sf = str(f)
            if not f.exists():
                errors.append(f"{sf}: file does not exist")
                continue
            sel = montage_mod.resolve_use(sf, selects)
            if sel is None:
                errors.append(f"{sf}: does not match any select in the manifest")
                continue
            if sel["file"] in seen:
                errors.append(f"{sf}: select {sel['file']} used more than once")
                continue
            seen.add(sel["file"])
            entries.append({"select": sel["file"], "use": sf})
        if errors:
            for e in errors:
                log(e)
            log("nothing saved")
            return 1
        manifest.set_sequence(entries, cut=args.cut)
    save_note()
    data = manifest.load()
    payload, human = sequence_mod.sequence_view(manifest.get_cut(data, args.cut),
                                                data["selects"])
    payload["cut"] = args.cut
    emit(payload, args.json, human)
    return 0


def cmd_montage(args) -> int:
    """Splices the film. Without --files: manifest sequence -> output/cuts/<cut>.mp4
    (render recorded).

    With --files CLIP...: renders an explicit list to --out (external version) — does
    NOT touch the manifest (separate short/long cuts/variants; timeline via
    `shot locate --files`).
    """
    if args.xfade < 0:
        log("--xfade cannot be negative")
        return 1
    external = bool(args.files)
    mont = {}
    if external:
        if not args.out:
            log("--files requires --out (so the cut's render is not overwritten)")
            return 1
        files = list(args.files)
    else:
        mont = manifest.get_cut(manifest.load(), args.cut)
        seq = mont.get("sequence", [])
        if not seq:
            log(f"cut \"{args.cut}\" sequence is empty — `shot sequence --cut {args.cut} "
                f"FILE...` or provide `--files CLIP...`")
            return 1
        files = [Path(e["use"]) for e in seq]
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        for m in missing:
            log(f"missing file: {m}")
        return 1
    xfade = args.xfade if len(files) > 1 else 0.0
    smooth = args.smooth and xfade > 0
    draft = args.draft and xfade > 0
    out = args.out or paths.cut_render(args.cut)
    # Identical render already recorded and fresh -> skip (before any ffprobe).
    # Matching a non-draft record also satisfies a --draft request (the final
    # render is strictly better); the reverse never skips.
    rec = mont.get("render") or {}
    if (not external and not args.force
            and montage_mod.render_state(mont)["state"] == montage_mod.STATE_FRESH
            and rec.get("out") == str(out)
            and rec.get("xfade_s") == xfade
            and bool(rec.get("smooth")) == smooth
            and (not rec.get("draft") or draft)):
        emit({"out": rec["out"], "clips": rec["clips"], "xfade": xfade,
              "smooth": bool(rec.get("smooth")), "draft": bool(rec.get("draft")),
              "duration": rec.get("duration_s"), "expected": rec.get("expected_s"),
              "skipped": True, "render_state": montage_mod.STATE_FRESH},
             args.json,
             f"render up to date: {rec['out']} ({rec['clips']} clips, "
             f"{rec.get('duration_s')} s) — `--force` re-renders")
        return 0
    # xfade re-encodes and normalizes fps on the fly -> skip r_frame_rate in the check;
    # concat (--xfade 0) is stream copy, so it requires full uniformity (fps too).
    fields = [montage_mod.stream_fields(f) for f in files]
    check_keys = (montage_mod.UNIFORM_KEYS if xfade == 0
                  else tuple(k for k in montage_mod.UNIFORM_KEYS if k != "r_frame_rate"))
    mismatches = montage_mod.check_uniform(files, check_keys, fields)
    if mismatches:
        for mm in mismatches:
            log(f"non-uniform clip: {mm['file']} — {mm['field']}={mm['value']} "
                f"(expected {mm['expected']})")
        log("fix by re-rendering the clip (shot select / shot speed) — do not force the splice")
        return 1
    durs = [float(x.get("duration", 0)) for x in fields]
    if xfade > 0:
        too_short = [(f, d) for f, d in zip(files, durs) if d <= 2 * xfade]
        if too_short:
            for f, d in too_short:
                log(f"clip too short for transitions: {f} ({d:.2f} s ≤ 2×{xfade:g} s)")
            log("shorten the transition (--xfade) or lengthen/replace the clip")
            return 1
    expected = sum(durs) - (len(files) - 1) * xfade
    if args.smooth and xfade == 0:
        log("--smooth skipped: --xfade 0 is stream copy (no re-encode, "
            "no interpolation)")
    if args.draft and xfade == 0:
        log("--draft skipped: --xfade 0 is stream copy already (no re-encode)")
    if xfade > 0:
        encode = montage_mod.draft_args() if draft else None
        if smooth:
            log(f"Splicing {len(files)} clips with crossfade {xfade:g} s + motion "
                f"interpolation (--smooth) -> {out} (re-encode + minterpolate on clips "
                f"with a different fps — MUCH slower, expect hours for long footage) ...")
        elif draft:
            log(f"Splicing {len(files)} clips with crossfade {xfade:g} s -> {out} "
                f"(DRAFT encode: {encode[1]} — preview quality, re-render without "
                f"--draft before music/publish) ...")
        else:
            log(f"Splicing {len(files)} clips with crossfade {xfade:g} s -> {out} "
                f"(re-encode, ~{expected * 4 / 60:.0f} min) ...")
        montage_mod.concat_xfade(files, out, xfade, smooth=smooth,
                                 fields=fields, encode=encode)
    else:
        log(f"Splicing {len(files)} clips (hard cuts, no re-encode) -> {out} ...")
        montage_mod.concat(files, out)
    out_info = probe(out)
    tol = max(0.5, len(files) / out_info.fps)
    if abs(out_info.duration - expected) > tol:
        # set the result aside under another name: a cut render is never silently bad
        rejected = out.with_suffix(".rejected.mp4")
        out.replace(rejected)
        log(f"splice duration {out_info.duration:.2f} s deviates from the expected "
            f"{expected:.2f} s (tolerance {tol:.2f} s)")
        emit({"ok": False, "rejected": str(rejected),
              "duration": round(out_info.duration, 2),
              "expected": round(expected, 2), "tolerance": round(tol, 2)},
             args.json,
             f"render rejected -> {rejected} ({out_info.duration:.2f} s vs "
             f"expected {expected:.2f} s)")
        return 1
    if not external:
        manifest.record_render({"out": str(out), "clips": len(files),
                                "xfade_s": xfade, "smooth": smooth, "draft": draft,
                                "expected_s": round(expected, 2),
                                "duration_s": round(out_info.duration, 2),
                                "clip_durations_s": [round(d, 3) for d in durs],
                                "files": [str(f) for f in files]},
                               cut=args.cut)
    human = (f"{out} ({len(files)} clips, {out_info.duration:.1f} s, "
             f"crossfade {xfade:g} s{', smooth' if smooth else ''}"
             f"{', DRAFT' if draft else ''})" if xfade > 0 else
             f"{out} ({len(files)} clips, {out_info.duration:.1f} s, hard cuts)")
    emit({"out": str(out), "clips": len(files), "xfade": xfade, "smooth": smooth,
          "draft": draft,
          "duration": round(out_info.duration, 2), "expected": round(expected, 2),
          "render_state": (montage_mod.STATE_EXTERNAL if external
                           else montage_mod.STATE_FRESH)},
         args.json, human)
    return 0


def cmd_smooth(args) -> int:
    """Warms the motion-interpolation cache (output/smooth-cache/) BEFORE the render.

    Cache mechanics: docs/decision-rules.md ("Mixed frame rates and --smooth").
    Without arguments: clips of the current sequence with fps other than target.
    """
    if args.files:
        files = list(args.files)
    else:
        seq = manifest.get_cut(manifest.load(), args.cut).get("sequence", [])
        if not seq:
            log(f"cut \"{args.cut}\" sequence is empty — provide CLIP... "
                f"or set `shot sequence --cut {args.cut}`")
            return 1
        files = [Path(e["use"]) for e in seq]
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        for m in missing:
            log(f"missing file: {m}")
        return 1
    fields = [montage_mod.stream_fields(f) for f in files]
    fps = args.fps or montage_mod.target_fps(files, fields)
    todo = [f for f, x in zip(files, fields)
            if x.get("r_frame_rate") != fps]
    smoothed = []
    for n, f in enumerate(todo, 1):
        log(f"  smoothing {n}/{len(todo)}: {f.name} ...")
        smoothed.append(str(montage_mod.smooth_clip(f, fps)))
    human = (f"smooth cache ready: {len(smoothed)} clips to fps {fps} "
             f"({len(files) - len(todo)} already on target)")
    emit({"target_fps": fps, "smoothed": smoothed,
          "on_target": len(files) - len(todo)}, args.json, human)
    return 0


def cmd_locate(args) -> int:
    """Maps montage time to source file (and back); read-only, crossfade timeline."""
    data = manifest.load()
    if args.files:  # external montage: timeline from an explicit file list, not manifest
        missing = [str(f) for f in args.files if not f.exists()]
        if missing:
            for m in missing:
                log(f"missing file: {m}")
            return 1
        mont = {"sequence": [{"select": str(f), "use": str(f)} for f in args.files]}
        state = {"state": montage_mod.STATE_EXTERNAL,
                 "reason": f"{len(args.files)} files from --files"}
    else:
        mont = manifest.get_cut(data, args.cut)
        if not mont.get("sequence"):
            log(f"cut \"{args.cut}\" sequence is empty — `shot sequence --cut {args.cut} "
                f"FILE...` or provide `--files FILE...`")
            return 1
        state = montage_mod.render_state(mont)
    render = mont.get("render") or {}
    xfade = args.xfade if args.xfade is not None else render.get("xfade_s")
    if xfade is None:
        xfade = 1.0
    tl = montage_mod.build_timeline(mont, data["selects"], xfade)
    film_s = tl[-1]["end_s"] if tl else 0.0
    if state["state"] not in (montage_mod.STATE_FRESH, montage_mod.STATE_EXTERNAL):
        log(f"note: render {state['state']} ({state.get('reason','')}) — "
            f"timeline computed from the CURRENT sequence (= next render)")

    # without arguments: full timeline
    if not args.queries:
        payload = {"xfade_s": xfade, "film_s": round(film_s, 1),
                   "clips": len(tl), "render": state, "timeline": tl}
        lines = [f"Montage timeline ({len(tl)} clips, {locate_mod.fmt_tc(film_s)}, "
                 f"crossfade {xfade:g} s):"]
        for r in tl:
            lines.append(f"  {r['index']+1:2}. {locate_mod.fmt_tc(r['start_s']):>6}–"
                         f"{locate_mod.fmt_tc(r['end_s']):<6}  {locate_mod.src_name(r):13} {r['label'] or ''}")
        emit(payload, args.json, "\n".join(lines))
        return 0

    results, lines = [], []
    for q in args.queries:
        t = locate_mod.parse_timecode(q)
        if t is not None:
            res = locate_mod.locate_time(tl, t, xfade)
            results.append({"query": q, "mode": "time", **res})
            clip = res["clip"]
            if clip is None:
                lines.append(f"  {q}: outside the film (0–{locate_mod.fmt_tc(film_s)})")
            elif res.get("in_transition"):
                lines.append(f"  {locate_mod.fmt_tc(t):>6}  {locate_mod.src_name(clip):13} {clip['label'] or ''} "
                             f"→ {locate_mod.src_name(res['next'])} (transition)")
            else:
                off = t - clip["start_s"]
                lines.append(f"  {locate_mod.fmt_tc(t):>6}  {locate_mod.src_name(clip):13} {clip['label'] or ''} "
                             f"(clip {clip['index']+1}/{len(tl)}, +{locate_mod.fmt_tc(off)} into the clip)")
        else:
            matches = locate_mod.locate_match(tl, q)
            results.append({"query": q, "mode": "match",
                            "matches": [m["index"] for m in matches]})
            if not matches:
                lines.append(f"  {q}: not in the sequence")
            for m in matches:
                prev = tl[m["index"]-1] if m["index"] > 0 else None
                nxt = tl[m["index"]+1] if m["index"]+1 < len(tl) else None
                lines.append(f"  {locate_mod.src_name(m):13} {m['label'] or ''} — clip "
                             f"{m['index']+1}/{len(tl)}, {locate_mod.fmt_tc(m['start_s'])}–{locate_mod.fmt_tc(m['end_s'])}")
                lines.append(f"      prev: {locate_mod.src_name(prev)} {prev['label'] if prev else '—'}"
                             f"   next: {locate_mod.src_name(nxt)} {nxt['label'] if nxt else '—'}")
    emit({"xfade_s": xfade, "film_s": round(film_s, 1), "render": state,
          "results": results}, args.json, "\n".join(lines))
    return 0


def _energy_lines(p: dict) -> list[str]:
    """Track energy curve as text bars — the agent cannot hear, it has to see."""
    vals = [w["lufs"] for w in p["energy"]]
    if not vals:
        return []
    lo, span = min(vals), max(max(vals) - min(vals), 1e-6)
    return [f"  {w['t0']:>6.1f}–{w['t1']:<6.1f} "
            f"{'▮' * (1 + round(19 * (w['lufs'] - lo) / span))} {w['lufs']} LUFS"
            for w in p["energy"]]


def _music_apply(args, tracks: list[Path]) -> int:
    """Muxes tracks onto the latest montage render -> output/cuts/<cut>-final.mp4."""
    missing = [str(t) for t in tracks if not t.exists()]
    if missing:
        for m in missing:
            log(f"missing file: {m}")
        return 1
    mont = manifest.get_cut(cut=args.cut)
    rstate = montage_mod.render_state(mont)
    if rstate["state"] != montage_mod.STATE_FRESH:
        reason = f" ({rstate['reason']})" if rstate.get("reason") else ""
        log(f"cut \"{args.cut}\" render: {rstate['state']}{reason} — first run "
            f"`shot montage{'' if args.cut == 'main' else f' --cut {args.cut}'}`")
        return 1
    if rstate.get("draft"):
        log(f"cut \"{args.cut}\" render is a --draft preview — render the final "
            f"`shot montage{'' if args.cut == 'main' else f' --cut {args.cut}'}` "
            f"before muxing music")
        return 1
    video = Path(mont["render"]["out"])
    out = args.out or paths.cut_final(args.cut)
    log(f"Applying music ({len(tracks)} {'file' if len(tracks) == 1 else 'files'}) "
        f"onto {video} -> {out} (video stream copy) ...")
    result = music_mod.apply_music(video, tracks, out,
                                   fade_in=args.fade_in, fade_out=args.fade_out,
                                   lufs=args.lufs, loop=args.loop)
    if result["gap_s"] > 0.5:
        log(f"WARNING: the music ends {result['gap_s']:g} s before the end of the film "
            f"(silence) — consider --loop or a longer/additional track")
    manifest.record_music_applied({
        "tracks": [str(t) for t in tracks], "out": result["out"],
        "fade_in": args.fade_in, "fade_out": args.fade_out, "lufs": args.lufs,
        "loop": args.loop, "audio_s": result["audio_s"],
        "render_rendered_at": mont["render"]["rendered_at"]}, cut=args.cut)
    looped = f", loop ×{result['parts'] // len(tracks)}" if result["looped"] else ""
    emit({**result, "tracks": [str(t) for t in tracks]}, args.json,
         f"{out} ({result['video_s']:.1f} s video, music {result['audio_s']:.1f} s"
         f"{looped}, loudnorm {args.lufs:g} LUFS)")
    return 0


def cmd_music(args) -> int:
    """Music for the montage: --generate / --probe / TRACK... (mux) / no args: status."""
    if args.generate and args.tracks:
        log("--generate and TRACK files are mutually exclusive (--apply muxes the generated one)")
        return 1
    if args.probe:
        if not args.tracks:
            log("--probe requires audio files")
            return 1
        results, rc = _batch(args.tracks, music_mod.probe_track)
        lines = []
        for r in results:
            if "error" in r:
                lines.append(f"{r['file']}: ERROR {r['error']}")
                continue
            lines.append(f"{r['file']}: {r['duration_s']:g} s, "
                         f"I {r['integrated_lufs']} LUFS, LRA {r['lra_lu']} LU")
            lines += _energy_lines(r)
        emit({"results": results}, args.json, "\n".join(lines))
        return rc

    tracks = args.tracks
    if args.generate:
        duration = args.duration
        if duration is None:
            render = manifest.get_cut(cut=args.cut).get("render") or {}
            duration = render.get("duration_s")
            if not duration:
                log(f"no render for cut \"{args.cut}\" to read the duration from — "
                    f"provide --duration SEC")
                return 1
        out = music_mod.slug_for(args.generate)
        log(f"Generating {duration:g} s of music ({music_mod.MODEL}, ~$0.20) -> {out} ...")
        try:
            music_mod.generate_track(args.generate, duration, out)
        except (RuntimeError, ValueError) as e:
            log(str(e))
            return 1
        real = round(music_mod.audio_duration(out), 2)
        manifest.add_music_track({"file": str(out), "prompt": args.generate,
                                  "provider": music_mod.MODEL, "duration_s": real},
                                 cut=args.cut)
        if not args.apply:
            emit({"file": str(out), "duration_s": real, "prompt": args.generate},
                 args.json, f"{out} ({real:g} s) — apply with: shot music {out}")
            return 0
        tracks = [out]

    if tracks:
        return _music_apply(args, tracks)

    # without arguments: music status
    mont = manifest.get_cut(cut=args.cut)
    music = mont.get("music", {})
    track_rows = [{**t, "exists": Path(t["file"]).exists()}
                  for t in music.get("tracks", [])]
    state = montage_mod.music_state(mont)
    balance = music_mod.account_balance()
    payload = {"tracks": track_rows, "applied": music.get("applied"),
               "state": state, "balance_credits": balance}
    lines = [f"Tracks ({len(track_rows)}):"] if track_rows else ["No tracks — `shot music --generate \"PROMPT\"`"]
    for t in track_rows:
        mark = "✓" if t["exists"] else "✗"
        lines.append(f"  {mark} {t['file']} ({t.get('duration_s', '?')} s) — {t.get('prompt', '')}")
    reason = f" ({state['reason']})" if state.get("reason") else ""
    lines.append(f"Applied: {state['state']}{reason}"
                 + (f" -> {state['out']}" if state.get("out") else ""))
    if balance is not None:
        lines.append(f"Stability balance: {balance} credits "
                     f"(~{balance // music_mod.CREDITS_PER_GENERATION} generations)")
    emit(payload, args.json, "\n".join(lines))
    return 0


def cmd_publish(args) -> int:
    """YT publishing assets: --frame (thumbnail) / --title + --description-file
    (description from template) / no arguments: status."""
    did = {}

    if args.frame or args.at is not None or args.text:
        if not (args.frame and args.at is not None and args.text):
            log("thumbnail render requires --frame, --at and --text together")
            return 1
        if not args.frame.exists():
            log(f"file does not exist: {args.frame}")
            return 1
        out = args.out or publish_mod.PUBLISH_DIR / "thumbnail.jpg"
        try:
            res = publish_mod.render_thumbnail(args.frame, args.at, args.text,
                                               out, subtitle=args.subtitle,
                                               pos=args.pos, text_size=args.text_size)
        except (RuntimeError, ValueError, subprocess.CalledProcessError) as e:
            log(str(e))
            return 1
        if args.out is None:  # custom --out = working variant, no manifest entry
            manifest.set_publish({"thumbnail": {
                "file": res["out"], "source": res["source"], "at_s": res["at_s"],
                "text": res["text"], "subtitle": res["subtitle"], "pos": res["pos"],
                "rendered_at": datetime.datetime.now().isoformat(timespec="seconds")}})
        did["thumbnail"] = res

    if args.title and not args.description_file:
        manifest.set_publish({"title": args.title})
        did["title"] = args.title
    elif args.description_file:
        if not args.title:
            log("--description-file requires --title (title and description are accepted together)")
            return 1
        if not args.description_file.exists():
            log(f"file does not exist: {args.description_file}")
            return 1
        try:
            merged = publish_mod.merge_description(
                args.description_file.read_text(), publish_mod.template_path())
        except RuntimeError as e:
            log(str(e))
            return 1
        desc_out = publish_mod.PUBLISH_DIR / "description.txt"
        desc_out.parent.mkdir(parents=True, exist_ok=True)
        desc_out.write_text(merged)
        manifest.set_publish({"title": args.title,
                              "description_file": str(desc_out)})
        did.update(title=args.title, description_file=str(desc_out),
                   description_chars=len(merged))

    if did:
        lines = []
        if "thumbnail" in did:
            t = did["thumbnail"]
            lines.append(f"{t['out']} ({t['width']}x{t['height']}, "
                         f"{t['bytes'] // 1024} KB, font {t['font']}) — view it with Read")
        if "title" in did:
            lines.append(f"Title: {did['title']}")
        if "description_file" in did:
            lines.append(f"Description: {did['description_file']} ({did['description_chars']} chars)")
        emit(did, args.json, "\n".join(lines))
        return 0

    # without arguments: publishing status
    pub = manifest.load().get("publish", {})
    tpl = publish_mod.template_path()
    th = pub.get("thumbnail")
    desc = pub.get("description_file")
    orphan = (not desc and (publish_mod.PUBLISH_DIR / "description.txt").exists())
    payload = {"title": pub.get("title"), "description_file": desc,
               "description_exists": bool(desc and Path(desc).exists()),
               "description_orphan": orphan,
               "thumbnail": th,
               "thumbnail_exists": bool(th and Path(th["file"]).exists()),
               "template": str(tpl), "template_exists": tpl.exists()}
    lines = [f"Title: {pub.get('title') or '— (shot publish --title ...)'}",
             "Description: " + (f"{desc}{'' if payload['description_exists'] else ' (FILE MISSING)'}"
                         if desc else
                         ("— BUT output/publish/description.txt exists without a manifest "
                          "entry — register it via --title + --description-file"
                          if orphan else "—")),
             "Thumbnail: " + (f"{th['file']} ({Path(th['source']).name} @ {th['at_s']:g}s, "
                            f"\"{th['text']}\")" if th else "—"),
             "Template: " + (f"{tpl} ✓" if tpl.exists()
                            else f"{tpl} MISSING — copy publish-template.example.txt")]
    emit(payload, args.json, "\n".join(lines))
    return 0


def cmd_archive(args) -> int:
    """Moves (does not delete) the whole project state to archive/<date>_<name>/."""
    if "/" in args.name or not args.name.strip():
        log("name cannot be empty or contain '/'")
        return 1
    out_dir = paths.OUTPUT
    if not out_dir.exists() or not any(out_dir.iterdir()):
        log("output/ is empty — nothing to archive")
        return 1
    base = paths.ARCHIVE / f"{datetime.date.today().isoformat()}_{args.name}"
    dest, n = base, 2
    while dest.exists():  # same name on the same day -> suffix
        dest = base.with_name(f"{base.name}-{n}")
        n += 1

    n_selects = len(manifest.load()["selects"])
    dest.mkdir(parents=True)
    shutil.move(str(out_dir), str(dest / "output"))
    out_dir.mkdir()

    # working files (kept in output/ against convention) must not clutter the snapshot:
    # they land in dest/work/ — restore does not bring them back (moves output/ only)
    workfiles = [f for f in sorted((dest / "output").iterdir())
                 if f.name.startswith("_") or f.suffix == ".log"]
    for f in workfiles:
        (dest / "work").mkdir(exist_ok=True)
        shutil.move(str(f), str(dest / "work" / f.name))
        log(f"  working file -> work/: {f.name}")

    # snapshot of the YT description template — without it, regenerating the
    # description after restore on another machine fails (per-machine file, not in git)
    tpl = publish_mod.template_path()
    if tpl.exists():
        shutil.copy2(tpl, dest / "publish-template.txt")

    moved_inputs = []
    input_dir = config.input_dir()
    if args.with_input and input_dir.exists():
        (dest / "input").mkdir()
        for f in sorted(input_dir.iterdir()):
            if f.suffix.lower() in VIDEO_EXT:
                shutil.move(str(f), str(dest / "input" / f.name))
                moved_inputs.append(f.name)

    payload = {"archive": str(dest), "selects_archived": n_selects,
               "inputs_moved": moved_inputs,
               "workfiles_set_aside": [f.name for f in workfiles]}
    emit(payload, args.json,
         f"{dest}  (selects: {n_selects}, inputs moved: {len(moved_inputs)})\n"
         f"Project state clean — drop new files into {input_dir}/.")
    return 0


def cmd_restore(args) -> int:
    """Inverse of shot archive: moves state from the archive back into the project."""
    src = Path(args.archive)
    if not src.exists():
        src = paths.ARCHIVE / args.archive
    if not (src / "output").exists():
        log(f"no archive with output/ found: {src}")
        return 1
    out_dir = paths.OUTPUT
    if out_dir.exists() and any(out_dir.iterdir()):
        log("output/ is not empty — first `shot archive <name>` for the current state")
        return 1
    if out_dir.exists():
        out_dir.rmdir()
    shutil.move(str(src / "output"), str(out_dir))

    moved_inputs = []
    if (src / "input").exists():
        input_dir = config.input_dir()
        input_dir.mkdir(exist_ok=True)
        for f in sorted((src / "input").iterdir()):
            target = input_dir / f.name
            if target.exists():
                log(f"  skipping {f.name} — already exists in {input_dir}/")
                continue
            shutil.move(str(f), str(target))
            moved_inputs.append(f.name)
        if not any((src / "input").iterdir()):
            (src / "input").rmdir()
    if not any(src.iterdir()):
        src.rmdir()

    payload = {"restored_from": str(src), "inputs_moved": moved_inputs,
               "selects": len(manifest.load()["selects"])}
    emit(payload, args.json,
         f"Restored from {src} (selects: {payload['selects']}, "
         f"inputs: {len(moved_inputs)})")
    return 0


def cmd_config(args) -> int:
    """Shows/changes configuration (config.json). Without flags: show only."""
    if args.reset:
        config.save(dict(config.DEFAULTS))
    elif args.input_dir is not None:
        if not args.input_dir.is_dir():
            log(f"directory does not exist: {args.input_dir}")
            return 1
        cfg = config.load()
        cfg["input_dir"] = str(args.input_dir)
        config.save(cfg)
    payload = {"config": config.load(),
               "input_dir": str(config.input_dir()),
               "input_dir_source": config.input_dir_source()}
    emit(payload, args.json,
         f"input_dir: {payload['input_dir']}  [{payload['input_dir_source']}]")
    return 0


def cmd_validate(args) -> int:
    """Checks the persistent JSON artifacts against pipeline/schemas/ (the contract).

    Covered: output/project.json (post-migration view, as every command sees it),
    every output/<stem>/summary.json, config.json. Missing files are skipped —
    a fresh project validates clean.
    """
    results = []

    def one(file: Path, name: str, load) -> None:
        try:
            errs = schema_mod.errors(load(), name)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            errs = [f"invalid JSON: {e}"]
        results.append({"file": str(file), "schema": name,
                        "ok": not errs, "errors": errs})

    if manifest.MANIFEST_PATH.exists():
        one(manifest.MANIFEST_PATH, "project", manifest.load)
    for f in sorted(paths.OUTPUT.glob("*/summary.json")):
        one(f, "summary", lambda f=f: json.loads(f.read_text()))
    if config.CONFIG_PATH.exists():
        one(config.CONFIG_PATH, "config",
            lambda: json.loads(config.CONFIG_PATH.read_text()))

    failed = [r for r in results if not r["ok"]]
    lines = []
    for r in results:
        if r["ok"]:
            lines.append(f"OK   {r['file']}")
        else:
            lines.append(f"FAIL {r['file']}: {r['errors'][0]}")
            lines += [f"     {e}" for e in r["errors"][1:]]
    lines.append(f"{len(results)} files checked, {len(failed)} failed")
    emit({"ok": not failed, "checked": len(results), "results": results},
         args.json, "\n".join(lines))
    return 1 if failed else 0


# ------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="shot", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, help_, fn):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--json", action="store_true", help="JSON result on stdout")
        sp.set_defaults(fn=fn)
        return sp

    add("status", "project dashboard: inputs, selects, variants", cmd_status)

    sp = add("scan", "smoothness analysis (batch); segment cutting only with --cut", cmd_scan)
    sp.add_argument("files", nargs="+", type=Path)
    sp.add_argument("--cut", action="store_true", help="cut detected segments into clips/")
    sp.add_argument("--threshold", default="auto")
    sp.add_argument("--min-clip", type=float, default=2.5)
    sp.add_argument("--margin", type=float, default=0.3)
    sp.add_argument("--force", action="store_true", help="ignore the motion.csv cache")

    sp = add("sheet", "contact sheet: frame grid every N s (batch; scan does this itself)", cmd_sheet)
    sp.add_argument("files", nargs="+", type=Path)
    sp.add_argument("--interval", type=float, default=2.0)
    sp.add_argument("--cols", type=int, default=5)
    sp.add_argument("--force", action="store_true", help="ignore the contact.png cache")

    sp = add("frames", "evaluation frames at the given seconds", cmd_frames)
    sp.add_argument("file", type=Path)
    sp.add_argument("times", nargs="+", type=float, metavar="T")
    sp.add_argument("--width", type=int, default=1280)

    sp = add("jitter", "jitter test: velocity sign flips within a range", cmd_jitter)
    sp.add_argument("file", type=Path)
    sp.add_argument("--from", dest="start", type=float, default=0.0)
    sp.add_argument("--to", dest="end", type=float, default=None)

    sp = add("select", "cut a select and register it in the manifest", cmd_select)
    sp.add_argument("file", type=Path, nargs="?")
    sp.add_argument("start", type=float, nargs="?")
    sp.add_argument("end", type=float, nargs="?")
    sp.add_argument("--label", help="kebab-case label for the file name")
    sp.add_argument("--stars", type=int, choices=range(1, 6))
    sp.add_argument("--note", help="decision note for the manifest")
    sp.add_argument("--out", type=Path)
    sp.add_argument("--plan", type=Path, metavar="PLAN.jsonl",
                    help="batch: one JSON per line {file, start, end, label, stars?, note?, out?}; "
                         "already-cut ranges are skipped, so re-running RESUMES an interrupted batch")
    sp.add_argument("--force", action="store_true",
                    help="with --plan: re-cut even when the select already exists")

    sp = add("pace", "screen pace measurement (batch)", cmd_pace)
    sp.add_argument("files", nargs="*", type=Path, default=[])
    sp.add_argument("--selects", action="store_true", help="all selects from the manifest")
    sp.add_argument("--target", type=float, default=4.0)
    sp.add_argument("--slow-below", type=float, default=2.0)
    sp.add_argument("--fast-above", type=float, default=8.0)
    sp.add_argument("--max-speed", type=float, default=3.0)
    sp.add_argument("--profile", action="store_true",
                    help="pace profile in time windows + dull-stretch detection")
    sp.add_argument("--window", type=float, default=2.0, metavar="SEC",
                    help="profile window length (default: 2.0)")

    sp = add("speed", "speed-up render (no motion analysis)", cmd_speed)
    sp.add_argument("file", type=Path)
    sp.add_argument("factor", type=float)
    sp.add_argument("--out", type=Path)

    sp = add("tag", "content tags and select metadata (batch)", cmd_tag)
    sp.add_argument("files", nargs="+", type=Path)
    sp.add_argument("--scene", help="location/subject (kebab-case, e.g. mountain-ridge)")
    sp.add_argument("--shot", choices=montage_mod.SHOTS,
                    help="shot type (closed list; criteria in docs/decision-rules.md)")
    sp.add_argument("--light", help="light/mood (e.g. golden-hour, fog)")
    sp.add_argument("--role", choices=montage_mod.ROLES,
                    help="clip's narrative role (criteria in docs/decision-rules.md)")
    sp.add_argument("--note", help="replaces the decision note")
    sp.add_argument("--append-note", help="appends to the note with '; '")
    sp.add_argument("--stars", type=int, choices=range(1, 6))
    sp.add_argument("--reject", action="store_true",
                    help="permanently exclude the select from montages (out of the casting pool; lint flags it)")
    sp.add_argument("--unreject", action="store_true", help="undo --reject")

    sp = add("trim", "trim a select: re-cut from the source to a narrower range", cmd_trim)
    sp.add_argument("file", type=Path, help="select file from output/selects/")
    sp.add_argument("start", type=float)
    sp.add_argument("end", type=float)
    sp.add_argument("--note", help="appended to the note (trim reason)")
    sp.add_argument("--drop-variants", action="store_true",
                    help="delete speed variants instead of refreshing them")

    sp = add("sequence", "set/show the montage ordering (files = selects or _x* variants)",
             cmd_sequence)
    sp.add_argument("files", nargs="*", type=Path, default=[])
    sp.add_argument("--cut", default="main", metavar="NAME",
                    help="cut name (default: main = the main montage); a new name "
                         "creates a cut with its own sequence/render/music")
    sp.add_argument("--clear", action="store_true", help="clear the sequence")
    sp.add_argument("--target", type=float, metavar="SEC",
                    help="target montage duration saved in the manifest (0 = remove)")
    sp.add_argument("--note", help="the cut's decision note (casting/ordering/"
                                   "lint rationale; replaces the previous one)")
    sp.add_argument("--append-note", help="appends to the cut's note with '; '")

    sp = add("montage", "splice the sequence into a film (crossfade + re-encode; --xfade 0 = draft without re-encode)",
             cmd_montage)
    sp.add_argument("--cut", default="main", metavar="NAME",
                    help="cut to render (default: main); render -> output/cuts/<name>.mp4")
    sp.add_argument("--out", type=Path,
                    help="output file (default: output/cuts/<cut>.mp4)")
    sp.add_argument("--xfade", type=float, default=1.0, metavar="SEC",
                    help="crossfade transition length in seconds; 0 = hard cuts, "
                         "concat without re-encode (default: 1.0)")
    sp.add_argument("--smooth", action="store_true",
                    help="motion interpolation (minterpolate) for clips with fps other than "
                         "target — removes mixed-frame-rate judder; much slower render")
    sp.add_argument("--draft", action="store_true",
                    help="fast preview encode of the crossfade render (hardware encoder "
                         "when available) — for order iterations; re-render without "
                         "--draft before music/publish")
    sp.add_argument("--force", action="store_true",
                    help="re-render even when the recorded render is fresh and matches "
                         "the requested parameters")
    sp.add_argument("--files", nargs="*", type=Path, default=[], metavar="CLIP",
                    help="render an explicit clip list to --out (external version, does "
                         "not touch the manifest); check the timeline via `shot locate --files`")

    sp = add("smooth", "warm the motion-interpolation cache (before `shot montage --smooth`)",
             cmd_smooth)
    sp.add_argument("files", nargs="*", type=Path, default=[], metavar="CLIP",
                    help="clips to smooth (default: the cut's sequence)")
    sp.add_argument("--cut", default="main", metavar="NAME",
                    help="cut to take the sequence from (default: main)")
    sp.add_argument("--fps", help="target r_frame_rate, e.g. 30000/1001 "
                                  "(default: highest in the set)")

    sp = add("locate", "montage time -> source file (and back); crossfade-aware timeline",
             cmd_locate)
    sp.add_argument("queries", nargs="*", default=[],
                    metavar="TIME|FILE",
                    help="times (M:SS / sec) or file/label names; no args = full timeline")
    sp.add_argument("--cut", default="main", metavar="NAME",
                    help="cut whose timeline to compute (default: main)")
    sp.add_argument("--xfade", type=float, default=None, metavar="SEC",
                    help="override xfade (default: from the last render, otherwise 1.0)")
    sp.add_argument("--files", nargs="*", type=Path, default=[], metavar="FILE",
                    help="timeline from an explicit clip list (external montage, not from the manifest)")

    sp = add("music", "music for the montage: AI generation, track analysis, mux onto the film",
             cmd_music)
    sp.add_argument("tracks", nargs="*", type=Path, default=[],
                    help="audio files to apply (multiple = parts joined with acrossfade)")
    sp.add_argument("--generate", metavar="PROMPT",
                    help=f"generate a track ({music_mod.MODEL}; requires STABILITY_API_KEY; ~$0.20)")
    sp.add_argument("--duration", type=float, metavar="SEC",
                    help=f"generation length (default: duration of the last render; "
                         f"max {music_mod.MAX_DURATION_S})")
    sp.add_argument("--apply", action="store_true",
                    help="apply to the montage right after generation")
    sp.add_argument("--probe", action="store_true",
                    help="track analysis: duration, loudness, energy curve")
    sp.add_argument("--loop", action="store_true",
                    help="loop the music to the film length (acrossfade between repeats)")
    sp.add_argument("--fade-in", type=float, default=music_mod.DEFAULT_FADE_IN_S,
                    metavar="SEC", help=f"default: {music_mod.DEFAULT_FADE_IN_S:g}")
    sp.add_argument("--fade-out", type=float, default=music_mod.DEFAULT_FADE_OUT_S,
                    metavar="SEC", help=f"default: {music_mod.DEFAULT_FADE_OUT_S:g}")
    sp.add_argument("--lufs", type=float, default=music_mod.DEFAULT_LUFS,
                    help=f"loudnorm integrated loudness (default: {music_mod.DEFAULT_LUFS})")
    sp.add_argument("--cut", default="main", metavar="NAME",
                    help="cut whose render to mux / read (default: main)")
    sp.add_argument("--out", type=Path,
                    help="output file (default: output/cuts/<cut>-final.mp4); "
                         "NOTE: a mux with custom --out ALSO records music.applied "
                         "in the manifest (unlike publish --out)")

    sp = add("publish", "YT publishing assets: thumbnail (frame + text), title and description from template",
             cmd_publish)
    sp.add_argument("--frame", type=Path, metavar="SOURCE",
                    help="video the thumbnail frame is taken from (full resolution)")
    sp.add_argument("--at", type=float, metavar="SEC", help="frame time in the source")
    sp.add_argument("--text", help="main thumbnail text (place name; forced UPPERCASE)")
    sp.add_argument("--subtitle", help="smaller caption next to the main text (optional)")
    sp.add_argument("--pos", choices=["bottom", "top"], default="bottom",
                    help="text and gradient position (default: bottom)")
    sp.add_argument("--text-size", type=int, metavar="PX",
                    help="upper bound of the main text font size in px (default auto-fit from 150)")
    sp.add_argument("--out", type=Path,
                    help="thumbnail file (default: output/publish/thumbnail.jpg; "
                         "custom = working variant, no manifest entry)")
    sp.add_argument("--title", help="video title (in English; saved to the manifest)")
    sp.add_argument("--description-file", type=Path, metavar="FILE",
                    help="video-specific part of the description (written by the agent); "
                         "merged with the template -> output/publish/description.txt")

    sp = add("archive", "archive the project state: output/ -> archive/<date>_<name>/", cmd_archive)
    sp.add_argument("name", help="project name (kebab-case, no '/')")
    sp.add_argument("--with-input", action="store_true",
                    help="also move source recordings from input/")

    sp = add("restore", "restore project state from an archive (inverse of archive)", cmd_restore)
    sp.add_argument("archive", help="archive directory (full path or a name inside archive/)")

    sp = add("config", "show/change configuration (input folder)", cmd_config)
    sp.add_argument("--input-dir", type=Path,
                    help="folder with source recordings, e.g. an SD card (default: input/)")
    sp.add_argument("--reset", action="store_true", help="restore default settings")

    add("validate", "check manifest/summary/config files against the schema "
                    "contract (pipeline/schemas/)", cmd_validate)

    return p


def main(argv: list[str] | None = None) -> int:
    config.load_env()
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except subprocess.CalledProcessError as e:
        # ffmpeg/ffprobe already printed their error to stderr (-v error) — here just
        # a clean line instead of a traceback; we grab stderr when it was captured
        if e.stderr:
            err = (e.stderr if isinstance(e.stderr, str)
                   else e.stderr.decode("utf-8", "replace"))
            for line in err.strip().splitlines()[-5:]:
                log(f"  {line}")
        tool = Path(str(e.cmd[0])).name if e.cmd else "subprocess"
        log(f"`shot {args.cmd}` aborted: {tool} exited with an error "
            f"(exit {e.returncode})")
        return 1


if __name__ == "__main__":
    sys.exit(main())
