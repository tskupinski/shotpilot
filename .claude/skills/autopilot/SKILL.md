---
name: autopilot
description: >
  Fully autonomous run of the entire selection process: scan, aesthetic review,
  cutting selects, pacing and speed variants — no questions to the user, with a
  decision report at the end. Use when the user says "autopilot", "do everything
  yourself", "full auto", "process the new footage without asking", "run the whole
  process". Montage is OUT OF SCOPE — that's what the montage skill is for.
---

# Autopilot — the full process without user input

The flow is like `shot-review` + `pace-review`, but **you make the decisions
yourself** — strictly per `docs/decision-rules.md` (read it BEFORE starting; it's the
only source of selection and pacing criteria). Instead of acceptance stops you write
an auditable report. The user gave consent up front by invoking this skill.

## Scope and hard limits

- In scope: `shot scan` (also makes contact sheets) → aesthetic review → `shot select`
  + `shot tag` → `shot pace` → `shot speed` → report.
- OUT of scope (requires a human): `shot archive`/`shot restore`, deleting anything,
  montage (the `montage` skill — interactive), changes to the pipeline code.
- **Escalate instead of guessing** (stop and ask) when: scan/render errors,
  `tracking_failed_pct > 20` on many files, footage of a completely different kind
  than before (e.g. night, interiors, people up close — reference point: the
  footage facts in CLAUDE.local.md and the notes of existing selects
  from `./shot status --json`), or < 3 files worth a select.

## Flow

Autopilot does NOT define its own selection and pacing procedure — it executes the
existing skills in auto-decision mode (workflow changes are made in THOSE skills,
autopilot inherits them automatically):

1. **Rules**: read `docs/decision-rules.md`.
2. **Selection**: read `.claude/skills/shot-review/SKILL.md` and execute its
   steps. Auto mode: skip the "wait for acceptance" stop (the verdict) —
   verdicts become decisions and go into the report; right after cutting also assign
   full content tags (`shot tag --scene --shot --light`, vocabulary: "Content tags"
   in the rules) — you've just looked at the contact sheets, the montage will
   consume the tags later.
3. **Pacing**: read `.claude/skills/pace-review/SKILL.md` and execute its
   steps. Auto mode: the corrections table without the acceptance stop — corrections
   per the rules ("Pacing") become decisions and go into the report.
4. **Verification**: ffprobe on the clips and variants.
5. **Report**: `output/autopilot-report.md` + a summary in the conversation.

## Report (`output/autopilot-report.md`)

Sections: (1) numeric summary, (2) table of selects: file | ★ | range | pace |
variant | tags | note, (3) rejects with a one-sentence reason (grouped by family),
(4) pacing decisions with corrections relative to the mechanical recommendations,
(5) warnings/escalations, (6) what's next (montage — outside autopilot's scope).
All decisions must be auditable after the fact.
