---
name: orchestrate
description: Run dev, reviewer, docs workers on tickets; verify results.
version: 0.2.0
author: hermes-dev-team contributors
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [team, planner, orchestration]
---

# orchestrate

You are the planner in Hermes. Workers are headless Claude Code runs (`claude -p`) started by `kit/run-worker.py`,
each under a deny-by-default allow-list. One worker at a time. Keep your own context small: read result lines and
files, never a worker's transcript.

## When to Use

Tickets exist and the user said build or go. FULL lane = tickets -> dev per ticket -> reviewer -> docs.
QUICK lane = one ticket -> dev -> your verification.

## Prerequisites

`claude` CLI logged in; Python 3; a git repo with a clean tree; `KIT` = absolute path to the team repo's `kit/`
(forward slashes on Windows).

Each project has a committed `.team.json` with its full test command, e.g. `{"test": "npm test"}`; optional
`"models": {"dev": "sonnet"}`. If it is missing, ask the user for the test command once and write the file
(the dev worker cannot edit it).

## Procedure

1. **Branch.** Create the feature branch from main. Add `.scratch/` to `.git/info/exclude`. Commit `CONTEXT.md` and ADRs on the branch as `docs: context`. The launcher refuses to run on main or master.
2. **Pick** the next `todo` ticket whose `Blocked by` tickets are all `done`. Set it `in-progress` yourself; workers never change a ticket's Status.
3. **Dispatch dev** with `terminal(background=true, notify=true)`, cwd = the repo:
   `python KIT/run-worker.py dev "ticket: .scratch/<feature>/issues/NN-slug.md"`
   The launcher takes the test command from the ticket's `Test command:` line (or `.team.json`), so do not retype it.
   End your turn; you resume when it exits.
4. **Read the one result line**: `RESULT dev PASS <sha> | ...` or `RESULT dev FAIL <reason> | ...`, with any denied calls listed under it. Exit 0 means PASS and the guards held (main unmoved, branch unchanged, no history rewrite). Never trust a bare `claude` exit code: it is 0 even when a worker gives up.
5. **Verify** with 3 commands: `git log --stat -1`, the ticket's `Test command`, `git status --short`. On PASS set the ticket `review` (quick lane: `done`). On FAIL set it `blocked` and tell the user the reason line.
6. Repeat 2-5 until every ticket is `review`.
7. **Review** (full lane): `python KIT/run-worker.py reviewer "feature: .scratch/<feature> round: <n>"` (full test command from `.team.json`). PASS: set the tickets `done`. FAIL: it wrote `review-<n>.md` and fix tickets (`NN-fix-*.md`, Status `todo`); dispatch those as in step 3, then review with round n+1. Round 3 is never dispatched: ask the user.
8. **Docs** (full lane, all `done`): `python KIT/run-worker.py docs "feature: .scratch/<feature>"`. Read `drift.md` and tell the user the items.
9. **Merge.** Tell the user the branch is ready. The user runs `git merge --no-ff`. Never merge, delete a branch, or push.
10. **Lessons.** Show the user each line of `.scratch/dev-notes.md`. With their say-so, add the approved project facts to `docs/dev-notes.md` on main (60 lines or fewer), commit as `docs: dev notes`, and clear the scratch file. A lesson that helps every project goes into the team repo's `kit/roles/*.md` only after the user has read it.

## Cost log

Every run appends one line to `.scratch/runs.log` (role, model, verdict, turns, seconds, tokens, list price, denials,
feature). At the end of a feature run `python KIT/costs.py --feature <feature> --by-role` and show the user the table;
read that table, never the raw log. Prices are list price; a subscription seat is billed differently.

## Changing skills

The team skills are linked into the team repo, so any `skill_manage` edit to them is an uncommitted change there.
Before patching a team skill (grill-with-docs, to-tickets, orchestrate, claude-p-role-workers), show the user the
proposed change in one or two lines and wait for a yes. Keep it general: no project paths or names. At the end of a
session that changed one, tell the user to run `python KIT/review-skills.py`, then commit.

## Pitfalls

- Never run two workers at once; the serial chain keeps one branch and one tree.
- Workers may run the test command, which executes code the dev wrote. That hole remains; review before merge.
- `git commit -m x --amend` matches the allowed commit prefix. The launcher's history guard reports it as `FAIL guard:history-rewritten`; restore the old tip from `git reflog`.
- Commits never carry a Co-Authored-By trailer: the launcher forces `includeCoAuthoredBy: false` and empty `attribution`.
- A repo's own `.claude/settings.json` is ignored (`--setting-sources ""`), so a project cannot widen a worker's permissions.

## Verification

Every dispatch ends with its RESULT line read and the step 5 checks; main's SHA is unchanged until the user merges.
