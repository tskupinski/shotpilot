---
name: montage
description: >
  Montage of a film from the selects: filling in content tags, trim review
  (cutting dull stretches), ordering per the variety and storytelling rules,
  splice with crossfade transitions (music after the render is accepted — the
  /music skill). By default montages ALL selects; optional
  casting (cutting the cast down to a target duration) — only when the user chooses it.
  Use when the user says "assemble it", "splice the film", "montage", "arrange the order",
  "trim the selects", or after a finished pace review as the natural next step.
---

# Montage — from selects to a film

Everything via `./vm`; **all criteria and numeric thresholds: `docs/decision-rules.md`**
(sections "Content tags", "Casting", "Montage — ordering", "Transitions", "Trim review",
"Music") — read it before making decisions, don't reproduce the rules from memory.
The sequence in the manifest (`cuts.main.sequence`) is the state; the render is `vm montage`.

## Step 0: State, rules, mode

```sh
./vm status --json 2>/dev/null    # selects, tags, pace, target, sequence/render state
```

Read `docs/decision-rules.md`. If the selects lack measured pace
or variant decisions — `/pace-review` first.

**Ask the user for the mode** (the "Casting" section of the rules):

- **Full cast (default)** — all selects go in; variety comes from the
  ordering, not from cutting. No target — skip step 2. **Always skip selects
  with `reject=true`** (`vm status` marks them `✗`; the `rejected_clip` lint flags them
  if they end up in the sequence) — those are permanent quality rejects.
- **Casting toward a target** — only when the user chooses cutting: agree the target
  duration (a proposal from the footage: the "Casting" section) and record it:

```sh
./vm sequence --target 120        # the manifest holds it; 0 clears it
```

## Step 1: Tagging the gaps

Selects with `tags: null` (`[no tags]` in the status): look at the source's
`contact.png` (Read) — you'll locate the select's range by the timestamps on the frames;
refine ambiguous ones via `vm frames`. Then:

```sh
./vm tag output/selects/CLIP.mp4 [CLIP2...] --scene X --shot Y --light Z [--role breather]
```

Batch for clips with the same scene. Value vocabulary: the "Content tags" section
(`shot` and `role` are validated by the CLI). `--role` right away where the clip's trait
is obvious (`breather`, `transition`); you'll assign `hook`/`final` while
ordering. Tags are assigned once; merge is per dimension (`--light` alone
doesn't erase `scene`).

## Step 2 (OPTIONAL — casting mode only): who's in the film

Do this only when the user chose cutting the cast in step 0; in full-cast
mode go straight to trim review (step 3).

The procedure, number of slots, sure picks, the twin contest and role casting: the
"Casting" section of the rules. Show the casting table: clip | ★ | tags | verdict (in /
out) | reason — every reject with a one-sentence justification. Rejects stay
in the manifest, they just don't enter the sequence. Assign roles via
`vm tag --role`. The `vm sequence` preview shows the pool outside the sequence and
the drop candidates (`drop_candidates`) — a mechanical hint,
the aesthetic decision is yours.

**Casting (if any), trims (step 3) and the ordering (step 4) are best presented
in ONE turn for a single acceptance** — cut and arrange only after it.

## Step 3: Trim review

Candidates and duration thresholds are defined by the "Trim review" section of the rules.
Run the pace profile on the candidates — it points to where the clip "dies":

```sh
./vm pace output/selects/CLIP.mp4 --profile    # time windows + DULL lines
```

The `DULL` lines (definition: "Trim review") carry times on the SOURCE's axis — you paste
them straight into `vm trim`. The profile doesn't see composition — before cutting,
confirm with frames (`vm frames` on the source) that nothing visual is happening
in the dead fragment.

Compute a clip's duration in the montage from the variant that will actually go into
the splice (range / multiplier); the variant minimum — see "Montage — ordering". Show
a table: clip | current range | proposed | reason. **Wait for acceptance
before cutting** (best as a single one, together with the casting and the ordering —
compute the post-trim durations up front).

```sh
./vm trim output/selects/CLIP.mp4 START END --note "trim reason"
```

A re-cut from the SOURCE (no generation loss); speed variants refresh
automatically (`--drop-variants` deletes them instead), pace is measured
anew. Renders > 30 s of footage in total — in the background.

## Step 4: Ordering

Arrange the sequence (the full cast or the cast from casting) per the "Montage —
ordering" section (the act technique, hook → arcs → pace contrast → closing,
the most numerous `shot` first). In full-cast mode assign the positional
roles now (`vm tag --role hook|final`). Show a proposal table:
no. | clip (variant) | duration | tags | role in the narrative. **Wait for acceptance**
(if it didn't come together with the casting and trims), then:

```sh
./vm sequence output/selects/A_x2.mp4 output/selects/B.mp4 ...   # spliced files, in order
./vm sequence            # preview + lint (no arguments)
```

The arguments are the files that ACTUALLY go into the splice — the `_x*` variant where
the pacing decision says so, otherwise the x1 select. Lint checks variety,
narrative structure, pace monotony and the total vs the target — the full list of types
is in the rules. **Justify every accepted warning to the user**;
before the render verify the story structure (hook at 1, final at the end,
breathers spread out).

**After acceptance persist the decisions in the manifest** (rules: "Casting" and
"Montage — ordering"): the casting/acts summary and the justifications of accepted
lint warnings → `vm sequence --note "..."` (or `--append-note`); per-clip
reject reasons → `vm tag CLIP --append-note "..."`. Without this the "why"
exists only in the chat and is lost between sessions.

## Step 5: Render and verification

```sh
./vm montage              # -> output/cuts/main.mp4 (crossfade, re-encode)
./vm montage --xfade 0    # draft: hard cuts, concat without re-encode (seconds)
./vm montage --smooth     # final render: motion interpolation (mixed frame rates)
```

- Transition calibration (the default, when to go shorter) and the `--smooth` rules for
  mixed frame rates: the "Transitions" and "Mixed frame rates and `--smooth`" sections
  of the rules. Crossfade = a re-encode of the whole thing — with > 30 s of footage run it
  in the background and verify after it finishes. The splice has no audio — music is step 6.
- **Warming smooth in the background:** when a final `--smooth` render is coming,
  start `vm smooth` in the background already while iterating on the ordering (cache
  mechanics: the rules, section "Mixed frame rates and `--smooth`").
- `--xfade 0` (stream copy, seconds, foreground) is for drafts while
  iterating on the ordering — the final render always with transitions.
- The command itself checks clip uniformity and the output duration; on "non-uniform
  clip" → re-render the faulty clip, don't work around it. On "clip too short
  for transitions" → a shorter `--xfade` or a decision about the clip (trim/variant).
  A render rejected by the duration check lands as `*.rejected.mp4` (for
  inspection, not for use).
- After the render `./vm status` (render: fresh) and offer to watch the file.

## Step 6 (OPTIONAL, AFTER THE MONTAGE IS ACCEPTED): Music

Propose music only once the user **has watched the render and accepted the
ordering/trims**. The whole workflow (generation with cost gates, probe,
mux, iteration): the `/music` skill; rules: the "Music" section in decision-rules.md.

## Iteration

Changing the order = another `vm sequence` + `vm montage --xfade 0` on drafts —
cheap, encourage experiments; the full render with transitions only after the
ordering is accepted. After `vm trim` the status will show `render: stale` —
another `vm montage` fixes it (and after it another music mux, if there was one).
When the user refers to a time in the film ("what's at 2:15") — map
timecode↔shot via `/locate`. Separate cuts (short/long/a variant alongside the
main montage) — the `/version` skill (`--cut NAME` on sequence/montage/
locate/music; the main montage = the `main` cut). Film done (render accepted,
music optional) → YouTube assets: `/publish`. Done working on the footage →
`/project-archive`.
