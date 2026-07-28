---
name: version
description: >
  Alternative versions/cuts of the same footage alongside the main montage:
  short (e.g. 5 min), long/ambient, "best-of", a variant for a different
  target. Use when the user says "short version", "long version", "second
  version", "different montage variant", "an X-minute version", "alternative
  cut", "make a separate file with...". Each cut lives in the manifest
  (--cut NAME) with its own sequence, lint, render and music — the main
  montage (cut "main") stays untouched.
---

# Version — alternative montage cuts

A cut is a named sequence in the manifest (`cuts.<name>`), handled by the same
machinery as the main montage (= cut `main`): `shot sequence/montage/locate/music
--cut NAME`. The render lands in `output/cuts/<name>.mp4` (with music:
`<name>-final.mp4`). Clip selection rules = casting: `docs/decision-rules.md`
(the "Casting — selection for the montage (OPTIONAL, on request)" section).

## Workflow

1. **Name and goal**: agree the cut's name with the user (kebab-case, e.g.
   `short`, `best-of`) and its goal (length, mood). **Skip selects with
   `reject=true`.**
2. **Proposal and approval**: show the cut's table (no | clip (variant) |
   duration | tags | role) + the predicted film length and the selection
   rationale. **Wait for the user's approval before cutting clips and
   rendering** — as in the `montage` skill.
3. **Sequence + lint:**
   ```sh
   ./shot sequence --cut short --target 300        # the cut's target (optional)
   ./shot sequence --cut short CLIP1 CLIP2 ...     # order (the files actually spliced)
   ./shot sequence --cut short                     # preview + lint
   ```
   Lint runs per cut — comment on warnings as in the `montage` skill. Persist the
   version's goal, the selection rationale and accepted warnings in the cut's
   note: `shot sequence --cut short --note "..."` (visible in
   `shot status`/`shot sequence`).
4. **Version-specific clips** (a different range than the select in the
   manifest): **do NOT `shot trim` a select from the manifest** — that changes
   the clip used in main and other cuts. Instead cut a separate select from
   the source (`shot select SOURCE A B --label <name>-...`) and use it in the
   cut's sequence.
5. **Render:**
   ```sh
   ./shot montage --cut short [--xfade 0 | --smooth]   # -> output/cuts/short.mp4
   ```
   Drafts with `--xfade 0`, the final with transitions; `--smooth` for mixed
   frame rates (pre-warming: `shot smooth --cut short` in the background; cache
   mechanics: rules, the "Mixed frame rates and `--smooth`" section).
6. **Timeline and music:**
   ```sh
   ./shot locate --cut short 2:15        # time <-> shot for the cut
   ./shot music --cut short TRACK.mp3    # mux -> output/cuts/short-final.mp4
   ```
   A cut's music has its own freshness and record in the manifest —
   workflow: the `music` skill (with `--cut` on every command).

## Notes

- `shot status` shows all cuts with separate render and music state.
- **Large files:** every cut is a full 4K render — `output/` balloons; once
  cuts are closed out, suggest cleaning up unneeded renders or
  the `project-archive` skill.
- Renders of > 30 s of footage go in the background; `--smooth` = many times
  slower.
- Ad-hoc render without a manifest entry (one-off export): still
  `shot montage --files CLIP... --out FILE` + `shot locate --files` — but for
  versions you will come back to, use cuts.
