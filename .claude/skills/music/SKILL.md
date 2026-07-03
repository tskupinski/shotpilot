---
name: music
description: >
  Music for the assembled film: AI track generation (Stable Audio), analysis
  (probe — duration, loudness, energy curve) and mux onto the montage render
  (-> output/cuts/<cut>-final.mp4). Use when the user says "add music", "lay in
  music", "generate a track", "swap the music", "re-mux after a re-render",
  or after an accepted /montage render as the natural next step.
  Also works without the full montage workflow (e.g. just a re-mux).
---

# Music — music for the montage

Everything goes through `./vm music`; **costs, gates, building the prompt from
tags, loop vs parts, mux defaults: `docs/decision-rules.md`, the "Music
(`vm music`)" section** — read it before generating, do not reproduce the rules
from memory.

## Step 0: State

```sh
./vm status --json 2>/dev/null   # montage render fresh? music stale?
./vm music                       # music state + credit balance
```

Precondition for generation: the user **has watched the render and accepted the
order/trims** — generation costs real credits, and changing the montage after
generation (especially the film's length) invalidates the fit. A mux alone only
requires a fresh render (`render: fresh`).

## Step 1: Generation (COSTS MONEY — after prompt approval)

Build the prompt from the sequence tags and the effective pace (mapping: the
"Music (`vm music`)" section). **Always show the prompt to the user for
approval before sending**; only then:

```sh
./vm music --generate "PROMPT" --apply    # takes the length from the render; --apply muxes immediately
```

Film longer than the single-generation limit: loop or parts with prompt
variation — the choice and seam planning per the rules ("Music (`vm music`)").

## Step 2: Evaluation before/after the mux

The agent cannot hear — it has to see:

```sh
./vm music --probe output/music/TRACK.mp3   # duration, loudness, energy curve
./vm music output/music/TRACK.mp3           # mux only -> output/cuts/main-final.mp4 (seconds)
./vm music --probe output/cuts/main-final.mp4         # after the mux: loudness ≈ target, energy vs acts
```

Match the track's climax to the dramaturgy of the sequence (highest energy where
the most dynamic clips are). After the mux, suggest the user listen to the
`-final.mp4` file — judging the sound belongs to the user.

## Iteration and freshness

- Iterating on the mux (different track, fades, `--loop`) is cheap — the video
  is not re-encoded; re-muxing existing tracks is free.
- **Every mux is recorded in the manifest as `music.applied` — ALSO with a
  custom `--out`** (unlike `vm publish --out`, which is a working variant).
  When comparing tracks A/B as separate files, the last mux becomes the official
  music state — finish the comparison by muxing the chosen track onto the
  default out.
- Re-rendering the montage invalidates the `-final.mp4` file (status: music
  `stale`) — a re-mux takes seconds, generated tracks stay in `output/music/`.
- A re-render of the SAME length does not require new music; changing the
  film's length = new generations (real cost) — warn the user.
- Music for a named cut (skill `/version`): every command with `--cut NAME`
  (generation reads the render length of THAT cut; mux ->
  `output/cuts/<name>-final.mp4`).
