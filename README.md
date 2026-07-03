# Shotpilot

Agent-first pipeline from drone footage to an edited film. It analyzes camera-motion
smoothness (OpenCV), helps judge the footage's appeal and pace, cuts the chosen
shots into uniform clips, splices them into a montage with crossfade transitions
(ffmpeg), adds AI-generated music (Stable Audio) and prepares YouTube publishing
assets (thumbnail + title + description).

**Agent-first** means the primary operator is an AI coding agent (built for
[Claude Code](https://claude.com/claude-code)): the human drops in footage, says
what they want and approves decisions; the agent runs the CLI, looks at the
generated review images, applies the codified decision rules and asks when
a call is genuinely the human's to make. Everything also works by hand — the CLI
is a normal command-line tool with `--json` output.

## Example

A film made end-to-end with this pipeline — selection, pacing, montage with
crossfades and the YouTube publishing assets were all driven by the agent
through the CLI:

[![Pruszków, WKD Komorów — 4K DJI Mini 3 Drone Video](https://img.youtube.com/vi/jn8GxruFrVQ/maxresdefault.jpg)](https://www.youtube.com/watch?v=jn8GxruFrVQ)

*[Pruszków, WKD Komorów — 4K DJI Mini 3 Drone Video](https://www.youtube.com/watch?v=jn8GxruFrVQ)*

## Heads up before you start

- **Early days.** This is the beginning of the project, and it has been largely
  vibe-coded — built iteratively with an AI agent around one person's real
  workflow, not engineered as a product. Expect rough edges, missing features
  and breaking changes.
- **It can be very resource-hungry.** Selection, speed variants and montage
  renders re-encode 4K video with x264 (roughly 4× real time per clip) and
  `--smooth` motion interpolation needs ~6 GB RAM per clip on top of that.
  A full run over an SD card's worth of footage keeps all CPU cores busy for
  a long while — be careful on a machine you're using for other work.
- **Back up your footage.** The pipeline is designed to never modify or delete
  originals (selects are re-encoded copies, cleanup is archiving), but this is
  young software driven by an AI agent: **always keep your own backup of the
  source footage. Use at your own risk — no responsibility is taken for any
  data loss** (see [LICENSE](LICENSE)).

## Requirements

- **Python ≥ 3.10** and a venv (the `./vm` wrapper expects `.venv/`)
- **ffmpeg / ffprobe** on `PATH`
- macOS or Linux (the manifest locking uses `fcntl`; no Windows support)
- Optional: a [Stability AI](https://platform.stability.ai/) API key — only for
  `vm music --generate` (paid; everything else is free and offline)

## Quickstart

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # once
```

1. Drop your footage into `input/` — or point at another folder (e.g. an SD
   card): `./vm config --input-dir /Volumes/SDCARD` (persistent, `--reset`
   restores the default), or one-off: `VM_INPUT_DIR=/Volumes/SDCARD ./vm status`.
2. Open Claude Code in the repo and say e.g. "review the new footage"
   (skill `/shot-review`), "check the pacing" (`/pace-review`), "edit the film"
   (`/montage`), or "autopilot" (`/autopilot` — selection + pacing with no
   questions asked, with a decision report in `output/autopilot-report.md`).
3. Results: `output/selects/` (clips for the montage), `output/cuts/` (rendered
   films), `output/<name>/report.html` (smoothness reports).

`./vm status` shows the whole project state at any point; the manifest
`output/project.json` is the persistent source of truth for every decision.

## CLI: `./vm`

One interface for humans and agents. All commands take multiple files (native
batch) and support `--json` (result on stdout, logs on stderr, exit != 0 on
error). Full flag semantics: `./vm <command> --help`.

| Command | Purpose |
|---|---|
| `./vm status` | project dashboard: inputs, selects with ratings/notes, sequence, render, music, publishing |
| `./vm scan FILE...` | smoothness analysis + contact sheet in one pass (mtime cache) |
| `./vm sheet FILE...` | contact sheet alone (a scan already makes one) |
| `./vm frames FILE T...` | 1280px evaluation frames at given seconds |
| `./vm jitter FILE --from A --to B` | settles jitter vs a smooth maneuver within a range |
| `./vm select FILE A B --label X` | cut a select from the source + manifest entry (`--stars`, `--note`); `--plan FILE.jsonl` cuts a whole batch and resumes after interruptions |
| `./vm pace --selects` | screen pace (% of frame width/s) + speed-up recommendation; `--profile` finds dull stretches for `vm trim` |
| `./vm speed CLIP N` | sped-up variant `_xN` |
| `./vm tag CLIP...` | content tags (scene/shot/light/role) + metadata; `--reject`/`--unreject` |
| `./vm trim CLIP A B` | re-cut a select from the SOURCE; variants and pace refresh automatically |
| `./vm sequence [FILE...]` | montage order + lint (variety, narrative structure); `--target SEC`; no args: preview |
| `./vm montage` | splice the sequence (crossfade = re-encode; `--xfade 0` = stream-copy draft; `--smooth` = motion interpolation for mixed frame rates) |
| `./vm smooth [CLIP...]` | warm the interpolation cache before a final `--smooth` render |
| `./vm locate [T \| FILE]` | read-only mapping montage timeline ↔ source shots |
| `./vm music` | `--generate` (Stable Audio, paid), `--probe` (loudness/energy analysis), `TRACK...` = mux onto the render |
| `./vm publish` | YT thumbnail (`--frame`), title + description (`--title --description-file`) |
| `./vm archive NAME` | move the whole `output/` state to `archive/<date>_<name>/` + clean start; `vm restore` reverses it |
| `./vm config` | input folder configuration |

Alternative versions of the same footage: `sequence`/`montage`/`smooth`/`locate`/
`music` accept `--cut NAME` — each cut keeps its own sequence, target, render and
music in the manifest (skill `/version`).

## How the analysis works

1. **`pipeline/motion.py`** — global camera transform per frame (feature
   tracking → optical flow → affine): dx, dy, rotation, zoom; analysis at
   640 px over an ffmpeg pipe, cached in `motion.csv`.
2. **`pipeline/segments.py`** — smoothness scores the **acceleration** of
   motion (rolling RMS): a fast uniform flyover is smooth, a jerk is an
   acceleration spike. Hysteresis + merging + minimum length.
3. **`pipeline/pace.py`** — screen pace (median of translation+dolly+rotation,
   % of frame width per second); speed-ups via `setpts` (no audio).
4. **`pipeline/report.py` / `contact.py`** — `review.png` (plot + KEPT/CUT
   thumbnails) and `contact.png` — at-a-glance review artifacts for the agent
   or the human.
5. **`pipeline/montage.py` / `lint.py`** — crossfade splicing on a shared
   timeline formula, sequence linting for variety and narrative structure.

Decision criteria (star ratings, pacing calibration, tag vocabulary, ordering
rules, thumbnail/title patterns) are codified in **`docs/decision-rules.md`** —
the agent workflows in `.claude/skills/` all point there.

## Safety properties

- Source footage is never modified; selects are re-encoded copies.
- Renders and paid operations (music generation) happen only after approval;
  music generation has explicit cost gates in the decision rules.
- Cleanup is archiving (`vm archive` moves, nothing deletes); `vm restore`
  brings a project back.

## Configuration files

- `.env` (gitignored) — `STABILITY_API_KEY` for music generation.
- `config.json` (gitignored) — machine config, written by `vm config`.
- `publish-template.txt` (gitignored) — your channel's YouTube description
  boilerplate; copy `publish-template.example.txt` to start.
- `CLAUDE.local.md` (gitignored) — facts about *your* footage and preferences
  for the agent, loaded alongside `CLAUDE.md`.

## Test

```sh
./tests/smoke.sh   # full cycle on synthetic video (~1 min), exit 0 = OK
```

## Roadmap / open topics

- Output structure for multiple projects (currently one project = one `output/`).
- Extending shot review with exposure/color analysis.

## License

[MIT](LICENSE)
