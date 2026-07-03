# Decision rules — selection, pacing and montage

The single source of truth for review criteria. The skills (`/shot-review`, `/pace-review`,
`/montage`, `/autopilot`) point here — rule changes are made in THIS file,
not in the skills.

## Selection (aesthetic review)

**Star scale:** ★5 exceptional (highlight of the collection) · ★4 strong subject/light ·
★3 solid or adds variety · ★2 weak · ★1 no value. **Cut ≥ ★3.**

- **Twins:** from a family of similar shots only the best one stays; report the
  rejected ones as "weaker twin of <the chosen one>".
- **Reject patterns:** grass/ground at takeoff, flat panoramas under a white sky,
  lens flare, no subject/anchor point, < 5 s (unless exceptional),
  0% smooth frames.
- **What to reward:** a clear subject (architecture, animals, infrastructure), leading
  lines, golden hour / dramatic light, fog/mood, graphic top-downs,
  variety relative to the selects you already have (check the manifest!).
- **Verify with frames before rejecting:** contact sheet thumbnails can misrepresent
  LIGHT (flat vs golden) and lose small MOVING subjects (trains, cars). Confirm
  borderline "flat little town" rejects and clips with leading lines (tracks/road)
  with `vm frames` (1280 px) BEFORE you reject — it's easy to blindly write off a strong frame.
- **Cut ranges:** inside smooth segments from `segments.json`; aim for 16–28 s
  (headroom for the montage); cut off boring starts and broken endings; boundaries ±2 s
  (a review from stills doesn't see motion pace).

### Notes (`--note`)

They should carry a DECISION, not a description — "moody, stays slow",
"best shot of the collection", "weaker twin of X rejected".

## Pacing

**Calibration:** < 2 %/s slow (contemplative) · 3–8 %/s good pace for a dynamic
montage · > 8 %/s fast. Measurement: `vm pace` (median of trans+dolly+rot, % of frame
width/s). The script's recommendation is mechanical — correct it for context:

- **Moody shots (fog, calm panoramas as a "breather")** → x1 even at "slow";
  record the decision in a manifest note.
- Pace 3–8 %/s → x1 (don't fix what's good).
- Multipliers at most **x2.5** (x3 only for abstracts/top-downs with no subject);
  round to 0.25; fog in motion takes x2 nicely (a "flowing timelapse").
- **Moving elements in the frame (people, cars, trains)** → a lower multiplier than
  the recommendation: speeding up turns them into an unnatural timelapse much
  faster than a landscape. When in doubt, render two variants
  (e.g. x1.5 and x2) and pick during the montage.
- **Pace contrast between shots is a value** — don't even everything out.
- The other lever: instead of a strong speed-up, a shorter cut is sometimes better.
- Pacing will ultimately be subordinate to the music — multipliers are a starting point.

## Content tags (`vm tag`)

A structural description of the shot in the manifest — the foundation of montage
variety. Assign from contact sheets during selection (or fill in before the montage),
once; kebab-case, one value per dimension (the shot's dominant trait):

- **`scene`** — location/subject, open vocabulary; examples: `mountain-ridge`,
  `lake`, `dam`, `village`, `monastery`, `river-bend`, `meadow`.
- **`shot`** — shot type, **closed list** (validated by the CLI; extensions:
  here AND in `SHOTS` in `pipeline/montage.py`):
  `top-down` · `panorama` · `reveal` · `flyover` · `orbit` · `dolly-in` ·
  `rise` · `chase`.
- **`light`** — light/mood: `golden-hour` · `midday` · `overcast` · `fog` ·
  `moody` · `backlight`.
- **`role`** — narrative role in the film, **closed list** (validated by the CLI):
  `hook` (opening: ★4–5, clear subject, natural pace) · `breather` (slow,
  moody interlude after a run of dynamic clips) · `transition` (flight into fog/white —
  a seam between acts) · `final` (wide/waning closing shot). Assign only
  to clips meant to play the role: `breather`/`transition` are traits of the clip (can
  be assigned already at selection), `hook`/`final` are positional decisions — they are
  assigned by `/montage` while ordering (and in casting mode — by the casting).
  The tag replaces the old free-text notes ("breather", "montage transition")
  — it's what lint uses to check structure.

## Casting — selection for the montage (OPTIONAL, on request)

**Default: full cast — the montage uses ALL selects.** Variety is achieved
with ORDERING (acts, separators, pace contrast), not by cutting clips;
`shot_overuse`/`scene_overuse` warnings then guide arrangement
(spacing with separators), not rejections. No target is set.
The `hook`/`final` roles are assigned while ordering.

**At the start of `/montage` ask the user**: montage all selects
(default), or propose cutting the cast for a shorter film? Casting runs
ONLY when the user chooses cutting. Then stars stop being a solo
rating — casting judges the contribution to a FILM of a given length, and good clips
can drop out too. Casting mode procedure:

- **Target:** agree it with the user.
  Record it: `vm sequence --target SEC` (the manifest holds it, `vm status` shows it).
- **Number of slots** ≈ target / median EFFECTIVE select duration (range ÷ variant
  multiplier — the `vm sequence` preview lists these durations). With standard
  16–28 s selects that's ~15–20 s of screen time per clip, so a 5–6 min film
  ≈ 18–22 clips; a ~7 s/clip figure applies only after an aggressive trim review —
  do NOT divide the target by 7 s when the selects still run long. Sure slots:
  ★5 and the sole representatives of their scene. The rest compete: twins by
  tags (the same `scene`+`shot` pair) — only the best one gets in.
- **Role casting:** exactly 1 `hook` and 1 `final`; a `breather` every 2–3 dynamic
  clips; a `transition` on act seams. No candidate for a role = a signal to
  review the rejects, not to skip the role.
- **Auditability:** every reject with a one-sentence justification (table:
  clip | ★ | verdict | reason). Rejects STAY in the manifest and `output/selects/` —
  casting deletes nothing, it just doesn't put them into the sequence.
  **Persist the justifications in the manifest** (not only in the chat — the next
  session must be able to reconstruct the casting logic): a synthetic summary of the
  casting and act structure → `vm sequence --note "..."` (the cut's note, per `--cut`);
  the reject reason of a specific clip → `vm tag CLIP --append-note "..."`.
- Mechanical support: the `vm sequence` preview shows the pool outside the sequence
  (`unused`), the total vs the target and drop candidates (`drop_candidates`:
  lowest ★, twins) — that's a hint, the aesthetic decision belongs to the agent.

**Permanent reject vs per-version casting (`reject`):** casting (above) is per-montage —
a clip doesn't enter THIS sequence but returns to the pool for another version. When a clip
is **objectively flawed** (odd camera work, drift, a repeat with no value) and belongs in
NO version — mark it with `vm tag --reject`. Then: it disappears from the casting pool
(`unused`), shows up separately ("Rejected"), and the `rejected_clip` lint warns if
it ends up in a sequence anyway. `--unreject` reverses it. Reject is a permanent quality
decision (like ★2/★1 at selection), not a tool for shortening toward a target — that's what casting is for.

## Montage — ordering

The agent arranges the sequence (`vm sequence`); lint mechanically flags violations
of variety and structure — a warning may be accepted, but only with a
conscious justification presented to the user. **Record accepted
warnings and the ordering/act rationale in the manifest**
(`vm sequence --append-note "lint scene_overuse OK: deliberate narrative pair
0236+0237"`) — it's the only durable trace of the "why" between sessions.

- **Opening hook:** ★4–5, clear subject, natural pace (not a slow clip
  as the opener) — the first 5 s decide whether the viewer stays.
- **Closing:** a wide, calm shot or waning light (golden hour) —
  it should give a sense of an ending, not cut off abruptly.
- **Variety:** no two adjacent clips with the same `shot`; the same `scene`
  next to itself only as a deliberate narrative pair with a directional relation —
  wide→detail or detail→wide (e.g. a sea of fog → backlit fog up close).
- **Pace contrast:** after 2–3 dynamic clips a "breather" at x1 — candidates are
  clips with `role=breather` in the manifest; don't splice only fast ones. Pace counts
  as EFFECTIVE (measured × variant multiplier) — x2 of a slow clip makes a dynamic one.
- **Narrative arcs — the act technique:** first group the selects into 3–5 acts
  by location/light, order the acts by light chronology (morning fog → day →
  golden hour) and geographic coherence, and only then order clips inside the
  acts — don't try to arrange 20+ clips at once as a single list.
- **Transition clips:** clips with `role=transition` (e.g. the camera flying into fog/white) —
  reserve them for seams between acts — they give a natural cut.
- **Plan the most numerous shot type first:** when one `shot` dominates
  (usually top-down), spread those clips across the whole film with separators of
  other types, and arrange the rest around them — otherwise the adjacency ban
  becomes unsolvable near the end of ordering.
- **Clip duration in the montage:** aim for ~4–12 s; longer → trim review candidate.
- **Variant minimum:** the variant chosen for the splice must give ≥ ~4 s
  (range / multiplier); if it comes out shorter — go down to a lower multiplier or x1.

The `vm sequence` lint enforces the above mechanically (the numeric thresholds live
in the implementation in `pipeline/lint.py`, the criteria are defined by this file):
missing tags; `rejected_clip` (a select with `reject=true` in the sequence);
adjacency of the same `shot`/`scene`; `weak_hook` (first clip
without `role=hook` and below ★4); `weak_closer` (last one without `role=final`,
not a panorama, not golden-hour); `shot_overuse` (one type > 40% of the sequence);
`scene_overuse` (the same scene ≥ 3×); `light_run` (≥ 4 consecutive with the same
light); `tempo_monotony` (≥ 4 consecutive without effective pace contrast);
`missing_breath` (≥ 3 dynamic in a row with no breather after them);
`duration_off_target` (the total deviates from the target by more than 10% of the
target, minimum 10 s).

## Transitions (`vm montage --xfade`)

Crossfade between all adjacent clips; a single length for the whole film.

- **Default 1.0 s** — fits slow, contemplative footage (pace
  1.3–1.9 %/s); with a more dynamic montage (lots of clips at 3–8 %/s, short
  cuts) consider 0.5–0.75 s so the transitions don't eat the pace.
- **Every clip must be longer than 2× the transition** (middle clips have an overlap
  on both sides) — the command enforces this; on a conflict, first a shorter `--xfade`,
  only then a decision about the clip.
- **A transition eats ~`xfade` s from each seam** — the total film time is the sum
  of the clips minus (n−1)×xfade; account for this when computing durations in trim review.
- **`--xfade 0` for drafts only** (order iteration — concat without re-encode,
  seconds); the final render always with transitions.
- **Montage transition clips** (clips flying into fog/white) — still reserve them for
  seams between acts — crossfade doesn't replace a natural seam, it only softens the cuts.

### Mixed frame rates and `--smooth`

Drones often record in mixed modes (e.g. 23.976 + 29.97 fps — check your
footage facts in CLAUDE.local.md). The montage normalizes to the highest frame
rate in the set; without `--smooth` the lower-fps clips have slight judder
(frame duplication). Render **without** `--smooth` by default — drafts, order
iterations and working versions should be fast. Reserve `--smooth` (motion interpolation,
render many times slower) for the **final render** of the chosen version, when the judder
should really disappear; the interpolation cache can be warmed earlier in the background (`vm smooth`).
Normalizing upward is deliberate: it is smooth on 60 Hz screens (a 24 fps target
would catch 3:2 pulldown) and it preserves the real frames of the highest-fps
clips, interpolating only the rest.

**Cache mechanics** (`output/smooth-cache/`; this is the ONLY full description — CLAUDE.md
and the skills point here): smoothing goes ONE clip at a time (a single
minterpolate process ≈ 6 GB RAM — parallelism risks OOM); the cache is per source
clip (by filename), so changing the sequence ORDER doesn't invalidate it — warming
can run in the background while iterating on the order, and the final `vm montage --smooth`
only fills in the gaps; invalidation on mtime and a mismatched fps; reused across
versions/cuts; deletable — it will be rebuilt on the next `--smooth`.

## Music (`vm music`)

Music is a separate, cheap step AFTER the montage render (a mux onto the cut's render,
video stream copy → `output/cuts/<cut>-final.mp4`) — iterating on the mux doesn't require
a re-render. Generation: Stable Audio via the Stability API (key
`STABILITY_API_KEY` from `.env` in the repo root; max 190 s per generation).

**Gate: generate ONLY after the user accepts the montage** —
the render watched, the ordering and trims closed. Changing the montage after generation
(especially the film length) invalidates the fit of the parts = new generations
= real costs. A plain re-mux of existing tracks is free (seconds),
so a re-render at the SAME length doesn't require new music.
**Always show the prompt to the user for acceptance before sending.**

- **Cost: 20 credits per generation, REGARDLESS of length** (10 s costs
  the same as 190 s) — don't generate short "trials", go straight for target
  lengths. `vm music` (no arguments) shows the balance; endpoint:
  `GET https://api.stability.ai/v1/user/balance`.
- **402 right after buying credits = propagation** (the balance is already visible,
  generation still refuses) — wait a moment and retry, it's not an implementation bug.
- **190 s is a hard server limit** (API validation: "duration: number must be
  less than or equal to 190" — verified 2026-06); longer in a single shot is
  impossible, that's what parts/loop are for. Validation errors (HTTP 400) do NOT cost
  credits — parameter doubts can be probed for free.

- **Build the prompt from the manifest** (sequence tags + effective pace), in English:
  - always: `instrumental, no vocals` + the duration; a mood consistent with the film;
  - mountain/lake scenes + `golden-hour`/`fog` → `cinematic ambient,
    warm strings/pads, organic, spacious`;
  - slow pace (1.3–1.9 %/s, contemplative) → `slow build, calm, airy`;
    a dynamic montage (many clips ≥ 4 %/s) → `driving percussion, steady
    pulse` — the music's tempo should support the screen pace, not fight it;
  - structure: a film with a hook and a final → `gentle intro, gradual build,
    soft resolution ending`.
- **Length = the RENDER duration** (`vm music --generate` takes it from the manifest) —
  not the sum of the sequence's clips: crossfades eat (n−1)×xfade, so the film is
  ~30 s shorter than the sum at 30 clips. Plan the parts so that the sum −
  4 s/seam ≈ the render duration (+0–5 s of headroom) — excess cuts off the composed
  ending of the last part. Film > 190 s — two ways:
  - **loop** (`vm music TRACK --loop`) — for ambient music without a clear
    structure, a uniform mood through the whole film;
  - **parts** (`vm music PART1 PART2 ...`) — generate 2+ tracks with prompt variation
    (`part 1: build` / `part 2: resolution`), spliced with acrossfade; better for
    films > ~3 min and music with a build. Align the seams between parts with the film's
    acts, but count them in SCREEN time: an act boundary on screen ≈ the sum of
    clips up to the seam − (the number of video seams so far)×xfade.
    `vm music --generate` suffixes filenames itself (`base`, `base-1`, …) and doesn't
    overwrite — do NOT change names between part generations; for meaningful names,
    differentiate the first ~5 words of each part's prompt.
- **Mux defaults:** fade-in 1 s, fade-out 3 s, loudnorm −14 LUFS (YouTube);
  crossfade between parts/repeats 4 s. Change only with a reason
  (e.g. a hook from the first frame → `--fade-in 0`).
- **The agent can't hear** — `vm music --probe TRACK` shows the duration, loudness
  and an energy curve in 5 s windows; match the track's climax to the sequence's
  dramaturgy (e.g. the highest energy where the most dynamic clips are),
  and leave the final judgment of the sound to the user after listening.
- **After the mux:** `vm music --probe output/cuts/main-final.mp4` — the integrated
  loudness should come out ≈ the loudnorm target (−14 LUFS), and the energy curve
  shows whether the music's dramaturgy hit the acts.
- **Freshness:** a re-render of the montage invalidates the `-final.mp4` file (status:
  music `stale`) — a repeat mux takes seconds, the generated tracks stay.

## Publishing (`vm publish`)

Assets for YouTube: thumbnail, title and description. **Everything published
in ENGLISH** (thumbnail text, title, description) — communication with the user
stays in the user's own language. The agent writes the title and
description; the module renders the thumbnail (Pillow)
and merges the description with the channel template (`publish-template.txt` in the repo
root, gitignored; example: `publish-template.example.txt`). A cheap and non-destructive
step, but it's the film's face — **render the final thumbnail and save the title/description
after the user accepts the proposals**.

- **Hero frame (thumbnail):** search among the sources of ★4–5 selects; prefer
  `golden-hour`/`fog` (mood sells the click); a composition with a clean spot
  for the text (the bottom or top of the frame without important detail — that's where
  the gradient and the caption go). Take the frame FROM THE SOURCE at full resolution
  (`--frame SOURCE --at SEC`, not from a select/render); find the time via `contact.png`
  and refine with `vm frames`.
- **Legibility at small preview size decides** — the agent LOOKS at the rendered
  thumbnail via Read and judges: does the text blend into the background, did the frame's
  subject survive the crop/grading, does the whole read at thumbnail size. Render
  candidates with `--out output/publish/cand-N.jpg` (no manifest entry),
  the final one on the default out.
- **Thumbnail text:** the place name, 1–3 words (the module enforces UPPERCASE);
  no clickbait (arrows, circles, emoji, "YOU WON'T BELIEVE"). A subtitle
  (`--subtitle`) only when it adds something (e.g. `CINEMATIC 4K`); default position
  bottom, `--pos top` when the bottom of the frame matters more compositionally.
  **CONFIRM the place name with the user** — it can't be derived
  from the project: the manifest tags are generic (`lake`, `dam`), and the footage
  facts in CLAUDE.local.md may be out of date. This also applies to the name in the title.
- **Thumbnail style (codified "trends"):** a heavy condensed font (Impact,
  fallback Arial Black), white with a black stroke, bottom 1/3 of the frame, a legibility
  gradient under the text (alpha up to 140 over 45% of the height), subtle grading
  (contrast 1.08, saturation 1.15), JPEG 1280×720 < 2 MB. The values = constants
  in `pipeline/publish.py`; style changes ONLY there + here.
- **Text size:** auto-fit from 150 px down (until the caption with its stroke fits
  within 86% of the width). `--text-size PX` lowers the upper size — when a 1-word
  caption at the max is too dominant, go subtler (e.g. `--text-size 90`).
- **Title:** drone/travel patterns — `Place — Cinematic 4K Drone Film`,
  `Place from Above | 4K Drone`; ≤ ~70 characters (YT truncates longer ones), the place
  name first (SEO), no all-caps for the whole title.
- **Description — the specific part** (the agent writes it from the manifest: scene/light
  tags, notes, the season from the facts about the footage): 2–4 sentences — what, where,
  when, shot with what; it goes FIRST (the first ~150 characters show in search),
  the template boilerplate after it. If the music comes from `vm music` — AI attribution
  in the description (`Music: AI-generated, Stable Audio`) per YT's requirement
  to disclose synthetic content (the template has a line for this — don't duplicate it,
  just verify it's there).
- **Freshness:** the thumbnail is made from the source, the description from the project's
  content — a re-render of the montage invalidates nothing here (deliberately no staleness mechanics).

## Trim review (`vm trim`)

A review of the selects for dull stretches — a separate step before ordering.

- **Too long:** > 12 s without internal development (nothing new enters the frame,
  uniform motion). Exception: moody "breathers" may run 15–20 s.
- **Detecting dull stretches:** `vm pace --profile` — pace in 2 s windows from the
  source's motion.csv (seconds, no 4K decoding); `DULL` = ≥ 2 consecutive windows below 50%
  of the clip's median (with the default 2 s window that's a dull stretch of ≥ 4 s), with
  times on the clip's and the SOURCE's axis (ready for `vm trim`). **This is the only
  definition of DULL** — the skills point here.
  The profile doesn't see composition — before you cut, confirm with frames (`vm frames`)
  that nothing visual is happening in the "dead" fragment (e.g. a subject entering
  the frame with a static camera).
- **How to cut:** down to the strongest phase of motion/composition; a clean entry and
  exit (no truncated maneuver); minimum ~4 s.
- **Always `vm trim`** — a re-cut from the source (no generation loss); speed
  variants refresh automatically, the pace is measured anew.

## Technical verification

The `warnings[]` vocabulary from `vm scan` and reactions:

- **"No segments found"** → look at `review.png`; try a higher
  `--threshold`; in autopilot, escalate.
- **"Tracking failed for >20% of frames"** → the result is unreliable (motion blur / night /
  little texture); in autopilot this is an escalation threshold.
- **"Kept >95% of the footage"** → with gimbal footage this is usually a false alarm —
  `vm jitter` decides (verdict `jitter` vs `smooth-maneuver`).
- **"Kept <30% of the footage"** → the threshold is probably too strict; consider a higher
  `--threshold`, look at `review.png`.

Additionally:

- Typical gimbal scores: p50 ≈ 0.03–0.05; upward deviations → look at `review.png`.
- After renders always ffprobe: clip duration ≈ the range, a variant's ≈ original/multiplier
  (`vm montage` verifies the splice duration ≈ the sum of the clips on its own).
- **A non-uniform clip in `vm montage`** (codec/dimensions/pix_fmt deviate from the
  rest; fps is checked ONLY with `--xfade 0` — the crossfade path normalizes
  fps on the fly, mixed frame rates don't block it) → re-render the faulty clip
  via `vm select`/`vm speed`, don't work around the check.
