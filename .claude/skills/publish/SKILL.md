---
name: publish
description: >
  YouTube publishing assets: thumbnail (frame from the source + place name),
  title and description in English (specific part + channel boilerplate from
  a template). Use when the user says "thumbnail", "cover image", "title and
  description", "YT description", "publish", "I'm uploading to YouTube" —
  the natural step after the montage, when the film (with or without music)
  is ready.
---

# Publish — thumbnail, title and description for YouTube

Everything goes through `./shot publish`; **criteria for the frame, style, title
and description: `docs/decision-rules.md` (the "Publishing (`shot publish`)"
section)** — read before making decisions.
Title, description and thumbnail text in ENGLISH (they are published assets),
regardless of the language of the conversation.

## Step 0: State and template

```sh
./shot status --json 2>/dev/null    # selects with stars/tags, publishing state
./shot publish                      # asset state + whether the description template exists
```

No `publish-template.txt` → ask the user to copy
`publish-template.example.txt` and fill it in with channel data (once per
machine; the file is gitignored). Do not assemble the description without
the template.

## Step 1: Hero frame (thumbnail candidates)

**First confirm the place name with the user** (thumbnail text and title) —
do not derive it from the manifest tags or from the footage facts in
CLAUDE.local.md (rules: "Publishing (`shot publish`)").

Per the rules: sources of ★4–5 selects, preference for `golden-hour`/`fog`,
clean area for the text. View the sources' `contact.png` images, cut 2–3
candidate frames to be sure about composition:

```sh
./shot frames input/SOURCE.MP4 12.4 33.0
```

Render candidate thumbnails (custom `--out` = no manifest entry):

```sh
./shot publish --frame input/SOURCE.MP4 --at 12.4 --text "DOLOMITES" \
             [--subtitle "CINEMATIC 4K"] [--pos top] --out output/publish/cand-1.jpg
```

**View each candidate image** and judge legibility per the rules (text vs
background, subject after the crop, whether it reads at small preview size).
Show the user the candidates with a recommendation.

## Step 2: Title + description (proposal)

From the manifest (scene/light tags, notes, music) and the facts about the
footage propose, in English: a title (patterns and character limit in the
rules) and the specific part of the description (2–4 sentences; what, where,
when, shot with what; AI music attribution — check it does not duplicate the
template). **Show everything together with the thumbnail choice — one
approval round.**

## Step 3: After approval — final assets

```sh
./shot publish --frame input/SOURCE.MP4 --at 12.4 --text "DOLOMITES"   # default out = manifest entry
```

Write the specific part of the description to a temporary file
(e.g. `/tmp/publish-specific.txt`), then:

```sh
./shot publish --title "Dolomites — Cinematic 4K Drone Film" --description-file /tmp/publish-specific.txt
```

The module merges: specific part FIRST, boilerplate after it →
`output/publish/description.txt`. Both modes can be combined into a single
invocation.

## Step 4: Summary

Give the user the three things for YT Studio: `output/publish/thumbnail.jpg`,
the title (from the manifest) and `output/publish/description.txt`. Clean up
the `cand-*.jpg` candidates. Done working on the footage → suggest the
`project-archive` skill (the thumbnail and description go into the archive
together with `output/`).
