---
name: locate
description: >
  Mapping the montage timeline to source shots (and back). Use when the user
  refers to a specific time in the film: "which clip/file is at 2:15", "what
  plays at 5:07", "map this time to a shot", "which clip is after/before X",
  "where in the montage is DJI_0301", or asks for the "montage timeline".
  Read-only — renders and writes nothing.
---

# Locate — montage time ↔ source shot

Everything goes through `./vm locate`; syntax and flags: `./vm locate --help`.
The tool is **read-only** — it only
reads the manifest (the cut's sequence and render record), renders nothing.
By default it works on the main montage (cut `main`); for another cut add
`--cut NAME`.

**Why:** the montage render (`output/cuts/<cut>.mp4`) has a crossfade at every
seam, so time in the film ≠ a simple sum of clip lengths (each transition
overlaps by `xfade` s). `vm locate` computes the timeline correctly (the same
offset formula as the render) and answers which shot is playing at a given
moment — no manual arithmetic.

## Three modes (argument auto-detection)

```sh
./vm locate 2:15 9:07            # TIME -> source file + label + offset within the clip
./vm locate DJI_0301.MP4         # FILE/label -> position in the sequence + neighbors
./vm locate                      # no argument -> full montage timeline
```

- **Time → clip:** argument as `M:SS`, `M:SS.s` or bare seconds. Batch (many
  times at once). When a time falls inside a **crossfade overlap**, the result
  reports the transition `A → B` (both shots are visible at once) — if the
  user means the clip "after the transition", it is the latter.
- **File/label → position:** matches against the source file name, the select
  file or the label (substring). Returns the number in the sequence, the time
  range in the montage, and the predecessor and successor — covers questions
  like "which clip comes after X".
- **No argument:** the whole timeline (number, start–end, file, label).

`--xfade SEC` overrides the crossfade (by default taken from the last render,
otherwise 1.0). `--json` = result on stdout (timeline/clips with
`start_s`/`end_s`/`source`), logs on stderr.

**Other cuts and external montages:** a named cut's timeline comes from the
manifest — `./vm locate --cut short 0:52`. For a one-off render outside the
manifest (`vm montage --files ... --out`) pass its clips in order:
`./vm locate 0:52 --files A.mp4 B.mp4 ...` — clips known from the manifest
resolve source/label immediately, unknown ones resolve via ffprobe + the
file name.

## Important

- The timeline is computed from the **CURRENT sequence** in the manifest
  (= what the next render will produce), not from the current render file.
  If the render is stale relative to the sequence, the command adds a note on
  stderr — the times refer to what will exist after the next `vm montage`.
- With a FRESH render the timeline is computed from the measured clip
  durations in its record (matches the file to the millisecond); without a
  fresh render — from the manifest (range / multiplier), with a sub-second
  difference versus the future render (irrelevant for "which clip").
- Report results to the user by **source file** name (`DJI_XXXX.MP4`) — that
  is their language during review; add the label for orientation.

Works naturally with `/montage` (review by timecode) — when the user points
at a time and wants a change (trim/swap/order), locate the shot with this
tool, and do the edit itself via `/montage` (`vm trim`/`vm sequence`).
