---
name: orchestrate
description: Run dev, reviewer, docs workers on tickets; verify results.
version: 0.8.0
author: hermes-dev-team contributors
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [team, planner, orchestration]
    related_skills: [grill-with-docs, to-tickets, claude-p-role-workers]
---

# orchestrate

You are the planner in Hermes. Workers are headless Claude Code runs (`claude -p`) started by `kit/run-worker.py`,
each under a deny-by-default allow-list. One worker at a time. Keep your own context small: read result lines and
files, never a worker's transcript.

## When to Use

Tickets exist and the user said build or go. FULL lane = tickets -> dev per ticket -> reviewer -> docs.
QUICK lane = one ticket -> dev -> your verification. Planning comes first, with Matt Pocock's `grill-with-docs` and
`to-tickets` (installed separately); this skill only adds the fields a worker needs.

## Setup and references

`claude` CLI logged in; Python 3; a git repo with a clean tree; `KIT` = absolute path to the team repo's `kit/`
(forward slashes on Windows). Each project has a `.team.json` with its full test command, e.g. `{"test": "npm test"}`.
If it is missing, ask the user for the test command once and write the file with only `test` plus what they named
(the dev cannot edit it). Never ask about models, backend or effort unless the user brings them up. The glossary is
`CONTEXT.md` if the repo has one, else `GLOSSARY.md`; never create the second one.

Load a reference with `skill_view(name="orchestrate", file_path="references/<file>")` only when its trigger applies:

| Reference | Load when |
|---|---|
| `tickets.md` | step 2, and before dispatching a fix ticket |
| `workspace.md` | the config has `"repos"` (multi-repo workspace) |
| `shared-repo.md` | the config has `"shared": true` |
| `switching.md` | "switching", a force-stop request, or step 0 reports a lock, an orphaned worker or `DIVERGED` |
| `config.md` | other `.team.json` keys, worker rules files, or a RESULT with `backend=hermes` / `ERROR no Hermes profile` |
| `browser-projects.md` | the project has a browser UI |
| `changing-skills.md` | before patching this skill or `claude-p-role-workers` |
| `research.md` | the user asks how some code works, or whether a repo or library is worth learning or using |

## Procedure

0. **Resume**, every time this skill starts (fresh session, after a machine switch), before anything else:
   `python KIT/scratch.py resume [<feature>]`. It removes a stale lock, commits leftover scratch changes, pulls scratch
   and feature branches fast-forward only, and prints per feature: Status counts, each `in-progress` ticket with an
   action, `decisions.md` and `batches.md`. Show the user that report, then act:
   - `VERIFY` (a commit ending `(<ticket file>)` exists): step 6 for it.
   - `REDISPATCH` (no commit, clean tree): step 4 with that ticket.
   - `ASK`, `RESUME STOP`, `DIVERGED`: show the user the lines and wait. Never reset, stash, merge, rebase or commit
     worker code to get past it (only exception: step 5's commit on the user's explicit yes).
   - `worker running`: wait for its exit notice.
   Follow `decisions.md`: it holds what the user decided in chat. There is no handoff file; the state is the tickets,
   `batches.md`, reviews, `runs.log`, `decisions.md` and git.
1. **Branch.** Create the feature branch from main. Once per project, `python KIT/scratch.py init` makes the scratch
   folder its own git repo (and excludes an in-repo `.scratch/`). Commit the glossary and ADRs on the branch as
   `docs: context` (not in a shared repo, not a workspace-level glossary). The launcher refuses main/master.
2. **Make tickets dispatchable** (load `tickets.md`). Show the user the `tickets.py` table (Seam, Test command,
   Verify per ticket) and the proposed review batches; they approve both before step 3.
3. **Pick** the next `todo` ticket whose blockers are `done` (`python KIT/tickets.py <feature> --next`). Set it
   `in-progress` yourself; workers never change Status. **After every Status change** (steps 3, 6, 8):
   `python KIT/scratch.py commit "<NN> <status>"`. **Whenever the user decides something in chat** the files cannot
   show: `python KIT/scratch.py decide <feature> "<one line>"` at once.
4. **Dispatch dev** with `terminal(background=true, heartbeat=120)`, cwd = the repo:
   `python -u KIT/run-worker.py dev "ticket: .scratch/<feature>/issues/NN-slug.md"`. The launcher takes the test
   command from the ticket and picks the backend; never retype the one or choose the other. Tell the user the
   `progress:` path from the `START` line, then end your turn; never `process wait` (it blocks heartbeats). Answer a
   heartbeat in one short line with no tool calls. Act only on the exit notice. Same dispatch for steps 8 and 9.
5. **Read the one RESULT line**: `RESULT dev PASS <sha> | ...` or `RESULT dev FAIL <reason> | ...`, plus any denied
   calls, and show the user the ticket's `Not checked:` line. Exit 0 = PASS and the guards held (main unmoved, branch unchanged, no history rewrite, no protected file
   changed, nothing pushed). Never trust a bare `claude` exit code: it is 0 even when a worker gives up.
   **FAIL with uncommitted work** (`work uncommitted`, or a dirty `git status --short`): never re-dispatch first (the
   dev refuses a dirty tree). Show the user `git status --short`, `git diff --stat` and the reason; they commit or
   discard. You may commit only on their explicit yes for that diff, with a subject NOT ending `(<ticket file>)`,
   finishing what the worker could not (e.g. a denied `git rm`). Then step 6 (on pass: `review`, not `blocked`) and
   `scratch.py decide`.
6. **Verify**: `git log --stat -1`, the ticket's `Test command`, `git status --short`. PASS: set `review` (quick lane:
   `done`). FAIL: set `blocked`, tell the user the reason line.
7. Repeat 3-6 until the batch's tickets are all `review` (quick lane: all `done`, then step 10).
8. **Review the batch** (full lane):
   `python -u KIT/run-worker.py reviewer "feature: .scratch/<feature> batch: <b> tickets: NN,NN base: <sha> round: <n>"`.
   `base` = the commit before the batch: `git merge-base HEAD main` for batch 1, else the HEAD recorded when the
   previous batch passed (or the merge commit `sync-main` printed); write it into `batches.md`.
   Show the user the review's `Not checked:` line.
   PASS: run each ticket's `Verify` (if any), set the tickets `done`, record HEAD as the next base, back to step 3.
   FAIL: it wrote `review-<b>-<n>.md` and fix tickets; dispatch them (step 4), add them to `tickets:`, review again with
   round n+1 and the same base. Never dispatch round 3: ask the user.
9. **Docs** (full lane, all `done`): `python KIT/run-worker.py docs "feature: .scratch/<feature>"`. Tell the user the
   `drift.md` items. The docs worker has 25 turns: with more than three files to edit, split the job or give it a
   `<feature>/docs-facts.md` with planner-verified facts (it has no web or device).
10. **Merge.** Tell the user the branch is ready; the user runs `git merge --no-ff`. Never merge or delete a branch;
    never push except `scratch.py switch` (`switching.md`).
11. **Lessons.** Show the user each line of `.scratch/dev-notes.md`. With their say-so, add the approved facts to
    `docs/dev-notes.md` on main (60 lines or fewer), commit as `docs: dev notes`, clear the scratch file (shared repo:
    keep them in the scratch folder, never commit them).

## Cost log

Every run appends one line to `.scratch/runs.log`. At the end of a feature run
`python KIT/costs.py --feature <feature> --by-role` and show the user the table; never read the raw log. Prices are
list price (a subscription seat bills differently). Prompt size = `in` + `cache_w` + `cache_r`; never report `in`
alone as "input". If the table notes runs without token columns, run it once with `--backfill`.

## Pitfalls

- Never run two workers at once; the launcher's lock enforces it.
- An untracked `__pycache__/` (or any build output) is a dirty tree: step 0 says ASK, switching stops. Ask the user to
  add it to `.gitignore`.
- The dev's commit subject ends `(<ticket file name>)`; step 0 finds finished tickets by it. Your own commits must not.
- `FAIL guard:pushed:<remote>`: a remote ref was pushed or deleted from this clone during the run. Nothing an agent
  pushes is kept; tell the user, who decides what to fix on the remote.
- `FAIL guard:test-config-changed` or a denial on `package.json`, a lockfile or test config: the ticket needs a change
  only you may make, with the user's yes (`tickets.md`, "Dependencies and test config"), then re-dispatch.
- `FAIL guard:history-rewritten` (e.g. `git commit --amend` matches the allowed commit prefix): restore the old tip
  from `git reflog`.
- Workers may run the test command, which executes code the dev wrote. That hole remains; review before merge.
- A repo's own `.claude/settings.json` is ignored, so a project cannot widen a worker's permissions. Commits never get
  a Co-Authored-By trailer.

## Verification

Every dispatch ends with its RESULT line read and the step 6 checks; main's SHA is unchanged until the user merges.
