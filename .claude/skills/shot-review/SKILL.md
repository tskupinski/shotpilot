---
name: shot-review
description: >
  Full review of a drone recording (or all new recordings): smoothness scan,
  contact sheet, aesthetic review and keep/reject recommendations with concrete
  time ranges. Use when the user asks for a review/selection of the footage,
  says "review the recordings", "what should stay", "go through the new video",
  or after new files are dropped into input/.
---

# Shot review — reviewing drone footage

Everything via `./shot` (native batch — no shell loops; `--json` on stdout).
**Rating criteria, reject patterns and range rules: `docs/decision-rules.md`** —
read it before reviewing; don't reproduce the rules from memory.

## Step 0: Project state

```sh
./shot status --json
```

Review files with `analyzed: false` — the `inputs[].analyzed` field in the status
output (or the ones the user points to). The manifest in `output/project.json` is the
source of truth about decisions made so far (it has no `analyzed` field — that's
disk state). The `input_dir` field in the status is the current inputs folder
(configured via `shot config --input-dir`, e.g. an SD card) — replace the `input/...`
paths in the examples below with the paths from `inputs[].file`.

## Step 1: Technical scan (smoothness + contact sheets)

```sh
./shot scan input/A.MP4 input/B.MP4 --json 2>/dev/null
```

The scan generates `contact.png` per file right away (a single decoding pass).
Read `stats` and `warnings`; verify suspicious cases with `shot jitter` /
`review.png` per the "Technical verification" section of the rules.

## Step 2: Aesthetic review

- View the `output/<stem>/contact.png` image — a grid of frames every 2 s captioned
  `t=...s`; triage per the criteria from the rules. Timestamps from the grid are
  candidates for range boundaries and for `shot frames` arguments.
- Ambiguous moments: `./shot frames FILE T1 T2 ...` and look at the 1280px frames.
- **Large batches (>20 files): review in groups of 10–15** and write verdicts
  down as you go (a working table, or cut accepted selects immediately) — the
  manifest is your memory between groups; don't try to hold every contact sheet
  in context at once.

## Step 3: Verdict

Table: file | rating (★) | verdict with a range (keep X–Y s / reject) + justifications
in prose. Add the caveat that you're judging from stills (boundary tolerance: "Cut ranges"
in the rules). **Stop and wait for the user's acceptance.**

## Step 4 (after acceptance): Cutting selects

```sh
./shot select input/A.MP4 10 36 --label kebab-description --stars 4 --note "decision"
```

- `--note` carries a DECISION, not a description (per the "Notes" section of the rules).
  In verdicts also point out candidates for narrative roles (e.g. "hook candidate").
- For clips with an obvious narrative trait, assign the role right after cutting:
  `./shot tag CLIP --role breather|transition` (vocabulary: the "Content tags" section
  of the rules; `hook`/`final` are assigned only by the `montage` skill while ordering).
- Renders over > 30 s of footage in total — in the background (re-encode cost: AGENTS.md).
- After cutting: `./shot status` — show the user the state of the selects.

## Step 5 (optional): Pacing

Propose a pace review — the procedure is in the `pace-review` skill (`shot pace --selects`).
