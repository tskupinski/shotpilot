"""Project manifest (output/project.json) — persistent state of select decisions.

Source of truth for: a select's source range, rating, notes (e.g. "stays slow —
mood"), measured pace, speed variants, content tags (scene/shot/light/role)
and montage cuts (top-level "cuts": name -> {sequence, target_s, render, music};
"main" = the main montage, the rest = alternative versions — same machinery);
also YT publish assets (top-level "publish": title, description, thumbnail).
Read by `shot status`, updated by `shot select` / `shot tag` / `shot pace` / `shot speed`
/ `shot trim` / `shot sequence` / `shot montage` / `shot music` / `shot publish`.

Shape contract: pipeline/schemas/project.schema.json (strict — unknown fields
fail), validated on every save(); explicit check of files on disk: `shot validate`.
Schema bump: 1) _migrate_vN() + call in load(), 2) bump SCHEMA_VERSION,
3) update the const and shapes in the schema file.
"""

import datetime
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from . import paths, schema

MANIFEST_PATH = paths.MANIFEST


@contextmanager
def _lock():
    """Exclusive read-modify-write — parallel shot select/speed don't overwrite each other."""
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_PATH.with_suffix(".lock"), "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


# Schema version written on every save; older manifests are migrated by load()
# on the fly (persisted at the next save). v2: "montage" -> cuts["main"];
# v3: main's render in output/cuts/main.mp4 instead of historical loose paths;
# v4: Polish tags.role values -> English (oddech/przejsciowka);
# v5: grading (select.grade, cuts.<name>.grade, top-level "sources",
# render.grade snapshot) — all new fields optional, so no migration needed.
SCHEMA_VERSION = 5

# v4: translation of the closed role vocabulary (montage.ROLES) to English
_V4_ROLES = {"oddech": "breather", "przejsciowka": "transition"}

# v3: old main render paths -> uniform cuts/ (records + files on disk)
_V3_RENAMES = {"output/montage.mp4": str(paths.cut_render("main")),
               "output/final.mp4": str(paths.cut_final("main"))}


def _migrate_v3(data: dict) -> None:
    """Rewrites main's render/mux paths and moves existing files
    (also the .concat.txt next to the render). Idempotent — acts only on old
    paths; persisted by the next save()."""
    main = data.get("cuts", {}).get("main", {})
    for rec, key in ((main.get("render"), "out"),
                     (main.get("music", {}).get("applied"), "out")):
        if rec and rec.get(key) in _V3_RENAMES:
            rec[key] = _V3_RENAMES[rec[key]]
    for old, new in _V3_RENAMES.items():
        old_p, new_p = Path(old), Path(new)
        for src, dst in ((old_p, new_p),
                         (old_p.with_suffix(".concat.txt"),
                          new_p.with_suffix(".concat.txt"))):
            if src.exists() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                src.replace(dst)


def _migrate_v4(data: dict) -> None:
    """Translates old Polish tags.role values to the English ROLES vocabulary."""
    for s in data.get("selects", []):
        tags = s.get("tags") or {}
        if tags.get("role") in _V4_ROLES:
            tags["role"] = _V4_ROLES[tags["role"]]


def load() -> dict:
    if not MANIFEST_PATH.exists():
        return {"selects": []}
    data = json.loads(MANIFEST_PATH.read_text())
    if "montage" in data and "cuts" not in data:  # v1 -> v2 migration
        data["cuts"] = {"main": data.pop("montage")}
    if data.get("schema_version", 0) < 3:
        _migrate_v3(data)
    if data.get("schema_version", 0) < 4:
        _migrate_v4(data)
    # the in-memory view is always current-version shape (migrations above),
    # so stamp it here too — `shot validate` checks THIS view, and an old
    # on-disk version must not fail the const before the next save persists it
    data["schema_version"] = SCHEMA_VERSION
    return data


def save(data: dict) -> None:
    data["schema_version"] = SCHEMA_VERSION
    schema.check(data, "project", str(MANIFEST_PATH))
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=MANIFEST_PATH.parent, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, MANIFEST_PATH)


def find(file: str | Path) -> dict | None:
    file = str(file)
    return next((s for s in load()["selects"] if s["file"] == file), None)


def upsert_select(entry: dict) -> None:
    """Adds/updates a select entry (key: entry['file']); merges fields."""
    with _lock():
        data = load()
        for s in data["selects"]:
            if s["file"] == entry["file"]:
                s.update(entry)
                break
        else:
            defaults = {"stars": None, "notes": "", "pace_pct_s": None,
                        "speed_variants": {}, "tags": None}
            data["selects"].append({**defaults, **entry})
        save(data)


def update_fields(file: str | Path, **fields) -> bool:
    """Updates fields of an existing entry; False when the file is not in the manifest."""
    with _lock():
        data = load()
        for s in data["selects"]:
            if s["file"] == str(file):
                for k, v in fields.items():
                    if k == "speed_variants":
                        s.setdefault("speed_variants", {}).update(v)
                    else:
                        s[k] = v
                save(data)
                return True
        return False


def set_speed_variants(file: str | Path, variants: dict) -> bool:
    """Replaces (does not merge like update_fields) the entry's speed variants."""
    with _lock():
        data = load()
        for s in data["selects"]:
            if s["file"] == str(file):
                s["speed_variants"] = variants
                save(data)
                return True
        return False


def set_select_grade(file: str | Path, grade: dict | None) -> bool:
    """Replaces the select's color corrections; None/{} removes the field.
    False when the file is not in the manifest. Values pre-validated by the
    CLI (grade.validate_correction); the schema enforces the same ranges."""
    with _lock():
        data = load()
        for s in data["selects"]:
            if s["file"] == str(file):
                if grade:
                    s["grade"] = grade
                else:
                    s.pop("grade", None)
                save(data)
                return True
        return False


def set_source_grade(source: str, fields: dict | None) -> None:
    """The source's grading facts (top-level "sources": profile, input_lut) —
    the normalize layer for log footage; None removes the entry."""
    with _lock():
        data = load()
        sources = data.setdefault("sources", {})
        if fields:
            sources[source] = {**fields, "updated": _now()}
        else:
            sources.pop(source, None)
        if not sources:
            data.pop("sources", None)
        save(data)


def remove(file: str | Path) -> bool:
    """Removes a select entry and its occurrences from the sequence of EVERY cut."""
    with _lock():
        data = load()
        before = len(data["selects"])
        data["selects"] = [s for s in data["selects"] if s["file"] != str(file)]
        pruned_any = False
        for cut in data.get("cuts", {}).values():
            seq = cut.get("sequence", [])
            pruned = [e for e in seq if e["select"] != str(file)]
            if len(pruned) != len(seq):
                cut["sequence"] = pruned
                cut["updated"] = _now()
                pruned_any = True
        if len(data["selects"]) != before or pruned_any:
            save(data)
            return True
        return False


# --------------------------------------------------------------- montage cuts

def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def get_cut(data: dict | None = None, cut: str = "main") -> dict:
    """Montage cut by name: {sequence, target_s?, render?, music?}."""
    return (data or load()).get("cuts", {}).get(cut, {"sequence": []})


def cut_names(data: dict | None = None) -> list[str]:
    """Cut names, "main" always first (if it exists)."""
    names = list((data or load()).get("cuts", {}))
    if "main" in names:
        names.remove("main")
        names.insert(0, "main")
    return names


def _cut(data: dict, cut: str) -> dict:
    return data.setdefault("cuts", {}).setdefault(cut, {"sequence": []})


def set_sequence(entries: list[dict], cut: str = "main") -> None:
    """Overwrites the cut's whole sequence (entries: {"select", "use"})."""
    with _lock():
        data = load()
        m = _cut(data, cut)
        m["sequence"] = entries
        m["updated"] = _now()
        save(data)


def set_target(seconds: float | None, cut: str = "main") -> None:
    """Sets/clears the cut's target duration (target_s); 0/None = clears."""
    with _lock():
        data = load()
        m = _cut(data, cut)
        if seconds:
            m["target_s"] = seconds
        else:
            m.pop("target_s", None)
        m["updated"] = _now()
        save(data)


def set_cut_notes(note: str, cut: str = "main", append: bool = False) -> None:
    """The cut's decision note — a durable "why" (casting rationale,
    ordering/act choices, accepted lint warnings), so it doesn't get lost
    between sessions. append concatenates with '; '."""
    with _lock():
        data = load()
        m = _cut(data, cut)
        if append and m.get("notes"):
            m["notes"] = m["notes"] + "; " + note
        else:
            m["notes"] = note
        m["updated"] = _now()
        save(data)


def set_cut_grade(grade: dict | None, cut: str = "main") -> None:
    """The cut's creative look ({"look": preset} or {"lut": path});
    None removes it. Freshness: montage.render_state compares the render's
    grade snapshot with the current manifest — no timestamps to maintain here."""
    with _lock():
        data = load()
        m = _cut(data, cut)
        if grade:
            m["grade"] = {**grade, "updated": _now()}
        else:
            m.pop("grade", None)
        m["updated"] = _now()
        save(data)


def record_render(render: dict, cut: str = "main") -> None:
    with _lock():
        data = load()
        _cut(data, cut)["render"] = {**render, "rendered_at": _now()}
        save(data)


# ------------------------------------------------------------------- music

def add_music_track(track: dict, cut: str = "main") -> None:
    """Registers a generated track (cuts[cut].music.tracks)."""
    with _lock():
        data = load()
        music = _cut(data, cut).setdefault("music", {})
        music.setdefault("tracks", []).append({**track, "generated_at": _now()})
        save(data)


def record_music_applied(applied: dict, cut: str = "main") -> None:
    """Records the last music mux (cuts[cut].music.applied) — tied to the render
    via applied['render_rendered_at']."""
    with _lock():
        data = load()
        music = _cut(data, cut).setdefault("music", {})
        music["applied"] = {**applied, "applied_at": _now()}
        save(data)


# --------------------------------------------------------------- publishing

def set_publish(fields: dict) -> None:
    """Merges fields of the YT publish section (top-level "publish"). No freshness
    mechanics — the thumbnail is made from the source, not the render, so a
    montage re-render invalidates nothing here."""
    with _lock():
        data = load()
        pub = data.setdefault("publish", {})
        pub.update(fields)
        pub["updated"] = _now()
        save(data)
