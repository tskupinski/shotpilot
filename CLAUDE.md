# Shotpilot

Agent-first pipeline from drone footage to an edited film: smoothness analysis
(OpenCV), aesthetic and pacing review, cutting selects, content tags, sequencing,
splicing (ffmpeg), music (AI generation + mux) and YouTube publishing assets
(`shot publish`: thumbnail + title + description). Current stage: the full cycle
selection → pacing → montage with crossfade transitions → music → publishing.

## First command of every session

```sh
./shot status          # or --json
```

Shows the whole project state: inputs (analyzed?), selects with ★ ratings,
decision notes, speed variants and the montage target. **The manifest
`output/project.json` is the source of truth for decisions** (source ranges,
stars, "stays slow — mood") — do not reconstruct them from memory or chat history.

## CLI: `./shot <command>` (wrapper around `.venv/bin/python -m pipeline`)

Conventions (all commands): `--json` = result on stdout, logs always on stderr
(batch commands wrap results in `{"results": [...]}`, single-file ones return
a bare object); native batch (many files as arguments — **do not write shell
loops**); exit != 0 on error. Below is only an index — **take flag semantics
from `./shot <command> --help`**, not from memory; decision criteria from
`docs/decision-rules.md`; workflows from the skills.

```sh
./shot status           # project dashboard: inputs, selects, sequence, render, music, publishing
./shot scan FILE...     # smoothness analysis + contact.png in one pass (mtime cache)
./shot sheet FILE...    # contact sheet alone (scan already makes one — don't run after scan)
./shot frames FILE T... # 1280px evaluation frames -> output/<stem>/frames/
./shot jitter FILE      # settles jitter vs smooth maneuver within a range
./shot select FILE A B  # cut a select from the source + manifest entry (--label/--stars/--note);
                      # --plan PLAN.jsonl = batch of many selects, resumable (skips already-cut)
./shot pace ...         # screen pace (%/s) + recommendation; --profile = dull stretches (DULL) for shot trim
./shot speed CLIP N     # sped-up variant _xN (refuses on _x* files)
./shot tag CLIP...      # content tags + metadata (instead of editing the manifest by hand); --reject/--unreject
./shot trim CLIP A B    # trim a select: re-cut from the SOURCE, variants and pace refreshed automatically
./shot sequence ...     # montage order (the files ACTUALLY spliced) + lint; --target SEC;
                      # --note/--append-note = persistent cut decision note; no args: preview
./shot montage          # splice the sequence (crossfade = re-encode; --xfade 0 = stream-copy draft;
                      # --draft = fast preview encode with transitions; a fresh matching
                      # render is skipped (--force re-renders);
                      # --smooth = mixed-frame-rate interpolation; --files + --out = one-off
                      # ad-hoc render without touching the manifest)
./shot smooth [CLIP...] # warm the interpolation cache in the background BEFORE the final `shot montage --smooth`
./shot locate ...       # read-only: montage timeline <-> shots (TIME/FILE/full timeline; --files = external)
./shot music ...        # --generate = track from Stable Audio (COSTS MONEY — gates in decision-rules "Music");
                      # TRACK... = mux onto the render -> output/cuts/<cut>-final.mp4; --probe = analysis; no args: state
./shot publish ...      # YT thumbnail (--frame, inspect the result via Read) / title + description
                      # (--title --description-file); no args: publishing state
./shot archive NAME     # output/ -> archive/<date>_<name>/ + clean start; shot restore = the reverse
./shot config           # input folder (--input-dir, e.g. an SD card); no flags shows the state
./shot validate         # check manifest/summary/config files against the schema contract (pipeline/schemas/)
```

Cuts: `sequence`/`montage`/`smooth`/`locate`/`music` accept `--cut NAME`
(default `main` = the main montage). A named cut has its own sequence, target,
render record and music in the manifest; render -> `output/cuts/<name>.mp4`
(with music: `<name>-final.mp4`). Version workflow: skill `/version`.

Selection workflow: skill `/shot-review`. Pacing: skill `/pace-review` (the
`shot pace` recommendation is mechanical — adjusting for the shot's mood is the
agent's job). Montage: skill `/montage` (tagging → trim review → ordering →
splice; by default ALL selects — variety comes from the ordering; optional
casting against a target, which the agent asks about at the start — weaker clips
then drop out with justification; arranging and picking are the agent's aesthetic
decisions, lint flags variety and narrative structure).
Music: skill `/music` (Stable Audio generation with cost gates, probe, mux onto
the render → `output/cuts/<cut>-final.mp4`) — the natural step after an accepted
render, also works standalone (re-mux, swapping the track).
YouTube publishing (thumbnail + title + description, in English): skill `/publish` —
the agent renders thumbnail candidates with `--out` and inspects them via Read;
style, title and description rules in decision-rules.md ("Publishing").
Fully automatic, no questions (selection + pacing, no montage): skill `/autopilot` —
decisions per the codified rules, auditable report in `output/autopilot-report.md`.

## Artifacts

- `output/project.json` — manifest: decision state (read by `status`/`locate`; written by
  `select`/`tag`/`pace`/`speed`/`trim`/`sequence`/`montage`/`music`/`publish`); per select
  also `tags` (scene/shot/light/role), `range_history` and `reject` (true = out of all montages);
  top-level `cuts` = name -> {sequence, `target_s`, last render record,
  `music` (tracks and last mux)} — `main` is the main montage;
  top-level `publish` = title + description + thumbnail (deliberately without freshness
  mechanics — the thumbnail comes from the source, not the render); `schema_version`
  is written by manifest.py; the shape contract is `pipeline/schemas/project.schema.json`
  (strict, validated on every save; closed tag vocabularies live THERE — montage.py
  reads SHOTS/ROLES from it); on-demand check of all persistent JSON: `shot validate`
- `config.json` (root, optional) — machine config (e.g. `input_dir` = an SD card);
  excluded from archiving; write via `shot config`, not by hand
- `.env` (root, gitignored) — machine secrets (`STABILITY_API_KEY`); loaded at CLI
  startup, real env wins over the file; excluded from archiving and from git
- `publish-template.txt` (root, gitignored) — your channel's YT description
  boilerplate (template to copy: `publish-template.example.txt` in git); excluded
  from archiving; env `SHOT_PUBLISH_TEMPLATE` points to another file (mainly for tests)
- `output/<stem>/` — per video: `summary.json` (+`warnings[]`), `review.png` and
  `contact.png` (**inspect via Read**), `report.html` (for humans),
  `segments.json`, `motion.csv` (motion-analysis cache), `frames/`
- `output/selects/` — selects for the montage: `<SOURCE>_<label>.mp4` + `_x<multiplier>` variants
- `output/cuts/` — renders of ALL cuts (main and named, skill `/version`):
  `<name>.mp4` + `<name>-final.mp4` (with music) + `<name>.concat.txt` (input
  list, debug artifact only with `--xfade 0`); sequences/records in the manifest
  (`cuts`). Historical `output/montage.mp4`/`final.mp4` is migrated by manifest.py (v3)
- `output/smooth-cache/` — motion-interpolation cache for `--smooth` (full mechanics
  and usage rules: decision-rules.md "Mixed frame rates and `--smooth`")
- `output/music/` — generated tracks (mp3; metadata in the manifest:
  `cuts.<name>.music.tracks`); film with music → `output/cuts/<name>-final.mp4`
  (the cut's `music.applied` ties it to the render — re-render = music stale)
- `output/publish/` — YT assets: `thumbnail.jpg` (1280×720 thumbnail, **inspect
  via Read**), `description.txt` (ready-to-paste description), working `cand-*.jpg`

## Environment and conventions

- Python: always `.venv/bin/python` (or simply `./shot`); ffmpeg/ffprobe on PATH.
- Git is set up (`.gitignore` protects `input/`, `output/`, `.env`). Test:
  `./tests/smoke.sh` (full cycle on synthetic footage, ~1 min; fully isolated
  in a temp dir — never touches the project's input/output/manifest) — run
  after every larger change in `pipeline/`, especially before committing.
- If ffmpeg were ever invoked by hand in a shell loop: `-nostdin` (it eats the
  loop's stdin). But that's what shot's native batch is for.
- Keep working files (your own scripts, logs, check frames) OUTSIDE `output/` —
  e.g. in `/tmp`: `output/` goes into the archive, and `rm` may be blocked.
- Re-encode (h264 crf 18, uniform format for splicing) is a deliberate design
  decision — do not switch to stream copy when CUTTING from the source. Renders
  of >30 s of footage — in the background; this includes the default `shot montage`
  (crossfade = re-encode of everything). Exception: `shot montage --xfade 0` splices
  already-uniform clips with stream copy (that's what the uniformity was for) —
  the only render without re-encode, always foreground.

## Working agreements

- The project is **agent-first**: convenient for the agent, the human provides
  input and the go-ahead.
- The agent's aesthetic judgment is welcome (composition, light, subject, mood)
  with concrete ranges and the honest caveat that stills don't show motion pace.
- Destructive decisions/renders — after approval; never overwrite originals.
- Done with the footage → suggest `/project-archive` (`shot archive`).
  **Never `rm -rf output/`** — cleanup always via archiving (moves, not deletes).
- Open topic for later: output structure for multiple projects (e.g. timestamped
  directories) — for now one project = one `output/` directory.

## Decision rules

**`docs/decision-rules.md` is the only source of criteria for selection (stars,
reject/reward patterns, ranges), pacing (%/s calibration, multiplier corrections),
montage (tag vocabulary, ordering rules, trim review) and publishing (hero frame,
thumbnail style, title/description patterns)** — the skills point there; rule
changes ONLY in that file.

## Machine- and footage-specific facts

Keep facts about YOUR footage and machine (camera modes, typical pace of your
material, mixed frame rates, preferred language of communication) in
`CLAUDE.local.md` — it is gitignored and loaded automatically alongside this
file. Generic performance facts that hold for any setup:

- Decoding for analysis uses an ffmpeg pipe (640 px); a scan also generates
  contact.png in the same pass — don't run `shot sheet` after `shot scan`.
- Caches by mtime: `motion.csv` and `contact.png`; `shot pace` on selects reuses
  the source's motion via the manifest (seconds instead of decoding 4K).
- 4K re-encode ≈ 4× real time; crf 18 gives a bitrate above typical drone
  sources (accepted; if size becomes a problem — crf 20).
- Mixed-frame-rate footage (e.g. 23.976 + 29.97 fps) is normalized up to the
  highest rate at montage; without `--smooth` the lower-fps clips get slight
  judder (frame duplication), `--smooth` removes it with motion interpolation
  at the cost of render time. Details: decision-rules.md "Mixed frame rates
  and `--smooth`".
