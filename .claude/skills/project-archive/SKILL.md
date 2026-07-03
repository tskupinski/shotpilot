---
name: project-archive
description: >
  Archiving the project state and a clean start for new footage. Use when the
  user says "clean up the project", "start over", "I'm done with this
  footage", "archive it", "new project", or wants to drop in new recordings
  after finishing work.
---

# Project archive — clean start

The project state is entirely `output/` (manifest, selects, reports, motion
cache) and `input/` (source recordings). Archiving **moves, never deletes** —
selects are the end product of the user's work.

**Forbidden: never `rm -rf output/`** nor deleting files from `input/` —
cleaning is what `vm archive` is for.

## Step 1: Show what will be archived

```sh
./vm status
```

Present the user a summary (how many selects, variants, inputs) and agree the
archive name with them (kebab-case describing the footage, e.g.
`mountains-september-2024`).

## Step 2: Confirmation

The operation moves the entire state — **wait for the user's confirmation**
(of the name, and of whether the source recordings should also go into the
archive).

## Step 3: Archiving

```sh
./vm archive mountains-september-2024                # output/ -> archive/<date>_<name>/output/
./vm archive mountains-september-2024 --with-input   # plus recordings from input/ -> .../input/
```

Without `--with-input` the source recordings stay in `input/` — the user
decides what to do with them (they are the user's large files; they can go to
an external drive).

## Step 4: Verify the clean start

```sh
./vm status
```

Should show 0 selects and (after `--with-input`) an empty input. Tell the user
where the archive is, and that they can drop new footage into `input/`.

## Restoring an archive

```sh
./vm restore 2026-06-11_name     # or the full path archive/...
```

The inverse of archiving: moves `output/` (and the archive's `input/` files,
if present) back into the project. Requires an empty `output/` — with active
work in progress, `vm archive` the current state first. Paths in the manifest
are relative, so everything works after restoring.
