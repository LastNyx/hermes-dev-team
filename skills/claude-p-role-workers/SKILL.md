---
name: claude-p-role-workers
description: Use when running claude -p role workers with allow-lists.
version: 0.1.0
author: hermes-dev-team contributors
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [team, claude-code, permissions]
    related_skills: [orchestrate]
---

# claude -p role workers

Rules for running headless Claude Code workers under a deny-by-default allow-list. Measured on Claude Code 2.1.296.
Reference implementation: this repo's `kit/run-worker.py` and `kit/roles/`. Evidence: `docs/design.md`.

## When to Use

Changing `kit/run-worker.py` or a role file, adding a role, or debugging a worker that FAILs or gets denied.
For the day-to-day dispatch loop, use `orchestrate` instead.

## Why claude -p and not Hermes profiles

Headless Hermes auto-approves commands, so nothing enforces a worker's limits. A Hermes provider that wraps Claude Code
turns Claude Code's own tools off, so its allow-lists cannot guard a Hermes worker. Only a direct `claude -p` worker
gets the allow-list. It was also about 5x faster on the same trivial ticket.

## Rules (each one cost a failed run)

1. **Grant file writes with `Edit(path)`, never `Write(path)`.** `Edit` rules govern both Edit and Write; a `Write(...)` allow rule grants nothing. Paths outside `./` are denied either way. Deny beats allow.
2. **Partial-word Bash prefixes need `*`:** `Bash(git checkout -b t-*)` matches `t-001`; `Bash(git checkout -b t-:*)` does not. `:*` only works after a whole word (`Bash(git diff:*)`).
3. **Every role prompt ends with the tool rules** (`kit/roles/_tool-rules.md`): one command per Bash call, no `cd`/`&&`/`;`/`|`/redirects, read with Read/Glob/Grep, retry once in the allowed form. Without them, workers chain commands, get denied, and quit.
4. **Exit code 0 does not mean success.** A worker that gives up still exits 0 with subtype `success`. Parse the last `RESULT: PASS|FAIL` line and `permission_denials`. Only the turn cap exits 1 (`error_max_turns`).
5. **Co-Authored-By is always off.** Set both `"includeCoAuthoredBy": false` and `"attribution": {"commit": "", "pr": ""}`, and force them in the launcher, not only in role files. The trailer appears by default.
6. **Isolate from local settings:** `--setting-sources ""` + `--agents <json file>` + `--agent <role>`. A project's `.claude/settings.json` (even `Bash(*)`) is then ignored. With `--setting-sources ""`, `.claude/agents/*.md` is not found, so roles must come through `--agents`.
7. **Pass `--tools Read,Edit,Write,Glob,Grep,Bash`.** Without it, on Windows the worker reached for PowerShell and was denied.
8. **Prefix rules can't stop trailing flags:** `Bash(git commit -m:*)` allows `git commit -m x --amend`. Catch it after the run: the old HEAD must still be an ancestor of the new HEAD, main must not have moved, and the branch must be unchanged.
9. **One allowed test command goes in both places:** add `Bash(<exact cmd>)` to the allow-list AND name it in the prompt. A reviewer that wasn't told ran a narrower command and was denied.
10. **Review before merge.** After a merge, `git diff main...branch` is empty.
11. Haiku is enough for the docs role.
12. To test denials, frame forbidden actions as plain chores. A prompt framed as "red-team" was refused and tested nothing.

## Known hole

The allowed test command runs code the worker wrote, so it can execute anything. Review before merge is the defence.

## Launching from Hermes

Run the launcher with `python -u` and `terminal(background=true, heartbeat=120)`, then end the turn. The launcher prints
one flushed line per tool call and per denial (also in `.scratch/runs/<stamp>-<role>.progress.log`); heartbeats carry
only new lines. Measured: a heartbeat is queued while the planner sits in `process wait`, so waiting hides progress.
Each heartbeat costs one planner turn; 120 s means a typical 90 s run costs none. Read the RESULT line (always the last
line), not the exit code alone. Never keep the terminal's working directory inside a throwaway repo you delete: later calls fail.

## Process lesson

Measure a mechanism end to end before designing around it.
