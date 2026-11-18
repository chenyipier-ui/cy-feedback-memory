# Feedback memory protocol (shared by all agents, maintained as a relay)

> This folder is the user's feedback memory (cy-feedback-memory {VERSION}). Every agent — Claude, Codex, Gemini, Cursor... — is part of the relay: all may read and write, all follow this page.
> The tool lives inside the library: `.fm/tool/scripts/fm.py` ("fm"). Agents don't each need the skill installed.

## Store reusable feedback and conclusions

Keep: corrections, rejections, decisions, praise (say what was good), "from now on", standards, judgments about a subject, and reusable conclusions the user confirmed or that were verified in real work. Candidate: plausible but unconfirmed — note evidence and when it applies. Skip: one-off commands, progress, unsupported guesses. Facts like paths go only in Reference.

When: self-check once at the end of every turn; write only on a hit, otherwise don't touch the library. Misses are recovered from transcripts at maintenance.

| Scope | When | How |
|---|---|---|
| once | only this time | just do it, don't write |
| always | user said "from now on / always / remember" | `fm add --scope always` with their words |
| verified | confirmed, or verified by checkable evidence | `fm add --scope always` with conditions and source; never claim the user approved what was only verified |
| candidate | looks reusable, not yet confirmed | `fm add --scope candidate` with evidence |

## Layout (read as a tree)

```
{CORE}  {ROUTES}           layer 0 (loaded every session)
{GENERAL}/ {TASKS}/ {SUBJECTS}/<branch>/
  {BRANCH}  layer 1: Standards / Candidates / Reference / External
  {ENTRIES}/  layer 2     {DOCS}/  whole documents     {LOG}  append-only
{DROP}/  files in any format awaiting maintenance     {INBOX}  unsure items
{ARCHIVE}/  superseded     {MAINTLOG}  maintenance log     .fm/  tool, locks, journal, roster (don't edit)
```

Read: matching branch only, via `fm read NAME` (Standards + Candidates only) → details on demand. Big branch → split by topic with `fm move` (verbatim); never trim or rewrite. Never read logs, archive, inbox or drop zone in normal work.

## Write through the tool

```bash
fm add --branch "code-review" --text "Blocking issues first, nits last" --words "user's exact words" --scope always --agent YOUR_NAME [--key review.order]
```

The tool locks (no clobbering between agents), de-duplicates (a repeat bumps [×N]), archives the old version when a [key] changes, and writes the branch log and the relay journal. New kind of work → `--create --layer ... --triggers ... --summary ...`; unsure → `fm remember "words" --agent YOUR_NAME`. You may edit entries/docs directly, then run `fm lint`. Don't hand-edit branch structure, front-matter, `.fm/`, or route blocks. During maintenance, `add` falls back to the inbox; when the library is offline, it saves locally and merges later.

## Maintenance (one agent on duty per run, weekly by default)

`fm maint start` → process material (convert drop files, file inbox and transcript feedback, review other agents' direct writes, merge duplicates) → `fm maint finish`. Default timing: 1 hour before the user's weekly quota resets. The user can also say "tidy the memory" any time. A second agent starting the same day is told it's already done or locked. Full steps: `.fm/tool/references/curate-prompt.en.md`.

Say which state you mean: **saved**, **applied**, **verified**.
