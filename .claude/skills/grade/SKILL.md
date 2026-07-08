---
name: grade
description: >
  Color grading of the film: per-clip color analysis, exposure/white-balance
  corrections so clips match, one creative look per cut (preset or .cube LUT),
  before/after preview — baked in non-destructively at the montage render.
  Use when the user says "grade it", "color correct", "colors don't match",
  "add a look/LUT", "the film looks flat/washed out", or after an accepted
  montage order as an optional step before the final render.
---

# Grade — color correction and the film's look

Everything via `./shot grade`; **criteria (correct vs leave alone, stat
thresholds, look subtlety, log footage): `docs/decision-rules.md`, the
"Grading" section** — read it before making decisions. A grade is a manifest
decision applied at `shot montage` (re-encode happens anyway) — select files
are never touched.

## Step 1: State and analysis

```sh
./shot grade --json 2>/dev/null                     # look, corrections, sources, render freshness
./shot grade --analyze --selects --json 2>/dev/null # color stats (mtime-cached)
```

Read per clip: `mean_luma`, `luma_p5/p95`, `mean_sat`, `cast`+`cast_strength`,
`clip_high_pct`/`clip_low_pct`. Check manifest notes first — grade decisions
may already exist.

## Step 2: Normalize check (log footage)

Sources shot in D-Log/flat profile (facts in `CLAUDE.local.md`; symptom: very
low `mean_sat` + narrow luma spread) need a conversion LUT BEFORE anything else:

```sh
./shot grade --source input/DJI_0301.MP4 --input-lut luts/dji-dlog-to-rec709.cube --profile d-log
./shot grade --analyze --selects --force --json     # re-measure through the LUT
```

## Step 3: Propose corrections

Per the decision-rules thresholds, table: clip | mean_luma | cast | proposal |
reason. **Default is NO correction** — only clips the stats flag, matching
neighbors in the sequence rather than absolute targets.

```sh
./shot grade output/selects/CLIP.mp4 --exposure 0.3 --temperature 6800   # merge; neutral value removes the key
./shot grade output/selects/CLIP.mp4 --clear
```

## Step 4: Look candidates

```sh
./shot grade --list-looks
./shot grade --look golden          # or --look-lut luts/X.cube; --clear-look removes
```

Pick 1–2 candidates that match the film's light tags (golden-hour footage →
`golden`, fog/overcast → `nordic`, hazy midday → `punch`). One look per film.

## Step 5: Preview loop

```sh
./shot grade --preview --json       # -> output/grade-preview/<cut>.png
```

Inspect the PNG via Read (RAW vs GRADED rows — the render bakes the identical
chain), iterate on corrections/look, then show the human the preview path and
the proposal table. In `shot ui` every graded select gets a before/after pair.
**Wait for acceptance before re-rendering** — the render is the costly step.

## Step 6 (after acceptance): Render and verify

```sh
./shot montage        # in the background for >30 s of footage (CLAUDE.md re-encode rule)
```

- The grade change already made the render stale — `shot montage` re-renders
  and records the grade snapshot; `--xfade 0` drafts stay ungraded (expected
  warning, see decision-rules).
- After the render: `./shot status` — render fresh; music (if applied) shows
  stale, a re-mux takes seconds: `./shot music TRACK...`.
- Ask the human to watch the render — stills undersell a grade in motion;
  persist their verdict in the cut note
  (`./shot sequence --append-note "grade: golden accepted"`).
