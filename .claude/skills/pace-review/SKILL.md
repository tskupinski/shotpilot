---
name: pace-review
description: >
  Reviewing the pace of drone shots and speeding up clips that are too slow.
  Use when the user asks "isn't it too slow", "increase the pace", "speed up
  the film/clips", or after cutting selects as the natural next step before the montage.
---

# Pace review — shot pacing

Measurement and rendering via `./shot`; thresholds/target are flags, not hardcode.
**Rules for correcting multipliers (moody shots, limits, pace contrast):
`docs/decision-rules.md`, the "Pacing" section** — read it before making decisions.
The `shot pace` recommendation is mechanical and doesn't know the shot's context.

## Step 1: Measurement

```sh
./shot pace --selects --json 2>/dev/null      # all selects from the manifest
./shot pace CLIP.mp4 [--target 4.0] [--slow-below 2] [--fast-above 8] [--max-speed 3]
```

Read per clip: `pace.total_pct_s` (with a trans/dolly/rot breakdown), `classification`,
`recommended_speed`. For selects the measurement reuses the source's motion via the
manifest (fast). The result is saved to the manifest.

## Step 2: Correcting the recommendation

First check the `notes` in the manifest (`./shot status --json`) — decisions may already
have been made. Then correct the mechanical multipliers per the rules from
`docs/decision-rules.md`.

## Step 3: Table and acceptance

Show: clip | measured pace | mechanical recommendation | your proposal
+ justification of the differences. **Wait for acceptance before rendering.**

## Step 4 (after acceptance): Render

```sh
./shot speed output/selects/CLIP.mp4 2        # -> CLIP_x2.mp4, variant recorded in the manifest
```

- The render is done without motion analysis, audio is dropped; don't overwrite/delete originals.
- `shot speed` refuses on `_x*` files — always render from the original.
- Renders over > 30 s of footage in total — in the background (re-encode cost: CLAUDE.md).
- After the render: ffprobe (duration ≈ original/multiplier) and `./shot status` for the user.
- Persist "don't speed up" decisions in a manifest note:

  ```sh
  ./shot tag output/selects/CLIP.mp4 --append-note "moody, stays slow"
  ```

Once pacing is closed, the natural next step is the montage — propose `/montage`.
