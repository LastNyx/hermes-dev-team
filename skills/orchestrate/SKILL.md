---
name: orchestrate
description: Run dev, reviewer, docs, researcher workers; verify results.
version: 0.11.0
author: hermes-dev-team contributors
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [team, planner, orchestration]
    related_skills: [grill-with-docs, to-tickets]
---

# orchestrate

You are the planner in Hermes. Workers are headless runs started by `kit/run-worker.py` on whichever backend is set
up (Claude Code or a Hermes worker profile; the launcher picks it). One worker at a time. Keep your own context small:
read result lines and files, never a worker's transcript.

## When to Use

Tickets exist and the user said build or go. FULL lane = tickets -> dev per ticket -> reviewer -> docs.
QUICK lane = one ticket -> dev -> your verification. TRIVIAL = your own fix inside `scratch.py check-run`. Route every
request, finding or gap by size (`lanes.md`). Planning comes first (Matt Pocock's `grill-with-docs`, `to-tickets`). Also: dispatch the researcher
(`research.md`) instead of searching yourself when an answer needs reading more than a few files, or a whole repo or
folder.

## Setup and references

Python 3; a clean git tree; `KIT` = the team repo's `kit/` (absolute, forward slashes). Each project has a
`.team.json` with its full test command (`{"test": "npm test"}`, or one suite per folder: `config.md`); if missing, ask
for it once and write only that (the dev cannot edit it). Never ask about models, backend or effort unless the user does. Glossary: `CONTEXT.md` if the
repo has one, else `GLOSSARY.md`; never create the second.

Load a reference with `skill_view(name="orchestrate", file_path="references/<file>")` only when its trigger applies:

| Reference | Load when |
|---|---|
| `tickets.md` | step 2, and before writing or dispatching a fix ticket |
| `lanes.md` | routing a request, finding or gap (trivial / small / large), or the user hands you decisions while away |
| `workspace.md` | the config has `"repos"` (multi-repo workspace) |
| `shared-repo.md` | the config has `"shared": true` |
| `switching.md` | "switching", a force-stop request, a killed worker, or step 0 reports a lock, an orphaned worker, `DIVERGED` or `ASK` for a dirty tree |
| `config.md` | other `.team.json` keys, worker rules files, a RESULT with `backend=hermes`, or an `ERROR` about the backend |
| `browser-projects.md` | the project has a browser UI |
| `changing-skills.md` | before patching this skill, a role file or the launcher |
| `research.md` | how some code works, a whole-repo or cross-folder search, or whether a repo or library is worth using |

## Procedure

0. **Resume**, every time this skill starts (fresh session, after a machine switch), before anything else:
   `python KIT/scratch.py resume [<feature>]` (clears a stale lock, pulls scratch and feature branches fast-forward
   only, prints each feature's state). Show the user the report, then act:
   - `VERIFY` (a commit ending `(<ticket file>)` exists): step 6 for it.
   - `REDISPATCH` (no commit, clean tree): step 4 with that ticket.
   - `ASK`, `RESUME STOP`, `DIVERGED`: show the user the lines and wait. Never reset, stash, merge, rebase or commit
     worker code to get past it (only exception: step 5's commit on the user's explicit yes).
   - `worker running`: wait for its exit notice.
   Follow `decisions.md` (what the user decided in chat). Tickets, reviews, `runs.log` and git are the rest of the state.
1. **Branch.** Create the feature branch from main. Once per project, `python KIT/scratch.py init` makes the scratch
   folder its own git repo. Commit the glossary and ADRs on the branch as `docs: context` (not in a shared repo, not a
   workspace-level glossary). The launcher refuses main/master.
2. **Make tickets dispatchable** (load `tickets.md`). Show the user the `tickets.py` table (Seam, Test command,
   Verify per ticket) and the proposed review batches; they approve both before step 3.
3. **Pick** the next `todo` ticket whose blockers are `done` (`python KIT/tickets.py <feature> --next`) and set it
   `in-progress`. Every Status change goes through `python KIT/scratch.py status <feature> <NN> <status>`, never a
   hand edit: it edits, commits the scratch repo and logs a `planner` line the user sees in hermes-dev-watch.
   **Whenever the user decides something in chat**, or you change a ticket after dispatch:
   `python KIT/scratch.py decide <feature> "<one line>"` at once.
4. **Dispatch dev** with `terminal(background=true, heartbeat=120)`, cwd = the repo:
   `python -u KIT/run-worker.py dev "ticket: .scratch/<feature>/issues/NN-slug.md"`. The launcher takes the test
   command from the ticket and picks the backend; never retype the one or choose the other. Tell the user the
   `progress:` path from the `START` line, then end your turn; never `process wait` (it blocks heartbeats). Answer a
   heartbeat in one short line with no tool calls. Act only on the exit notice. Same for steps 8 and 9.
5. **Read the one RESULT line**: `RESULT dev PASS <sha> | ...` or `RESULT dev FAIL <reason> | ...`, plus any denied
   calls, and show the user the ticket's `Not checked:` line. Exit 0 = PASS and the guards held (main unmoved, branch
   unchanged, no history rewrite, no protected file changed, nothing pushed). Never trust a worker's own exit code.
   **FAIL with uncommitted work** (`work uncommitted`, or a dirty `git status --short`): never re-dispatch first (the
   dev refuses a dirty tree). Show the user `git status --short`, `git diff --stat` and the reason; they commit or
   discard. You may commit only on their explicit yes for that diff, with a subject NOT ending `(<ticket file>)`.
   Then step 6 and `scratch.py decide`. With `enforce=prompt-only` in the RESULT, also load `config.md`.
6. **Verify**: `git log --stat -1`, the ticket's `Test command`, `git status --short`; for a bug ticket show the user
   the Result's `Cause:` line. Then the ticket's `Verify:` line if you can run it alone now (`tickets.md`). Record the move with what you saw:
   `scratch.py status <feature> <NN> review --verify "PASS|FAIL|SKIP: <what you saw>"` (quick lane: `done`; SKIP for a
   check that waits). A gap you find becomes a fix ticket (`tickets.md`) plus a `decide` line. FAIL in the test
   command: set `blocked`, tell the user the reason line.
7. Repeat 3-6 until the batch's tickets are all `review` (quick lane: all `done`, then step 10).
8. **Review the batch** (full lane):
   `python -u KIT/run-worker.py reviewer "feature: .scratch/<feature> batch: <b> tickets: NN,NN base: <sha> round: <n>"`.
   `base` = the commit before the batch's first commit: `git merge-base HEAD main` for batch 1, else the HEAD recorded
   when the previous batch passed (or the merge commit `sync-main` printed); never HEAD at review time when tickets of
   this batch were built earlier (empty diff). Write it into `batches.md`. Show the user the review's `Not checked:`.
   PASS: run each `Verify` that had to wait (server, database, the user's yes), set the tickets `done` with
   `scratch.py status --verify`, record HEAD as the next base, back to step 3.
   FAIL: it wrote `review-<b>-<n>.md` and fix tickets; dispatch them (step 4), add them to `tickets:`, review again with
   round n+1 and the same base. Never dispatch round 3: ask the user (unless a `delegated:` decision exists: `lanes.md`).
9. **Docs** (full lane, all `done`): `python KIT/run-worker.py docs "feature: .scratch/<feature>"`. Tell the user the
   `drift.md` items. More than three files to edit: split the job or give it a `<feature>/docs-facts.md`.
10. **Merge.** Tell the user the branch is ready; the user runs `git merge --no-ff`. Never merge or delete a branch;
    never push except `scratch.py switch` (`switching.md`).
11. **Lessons.** Show the user `.scratch/dev-notes.md`. Approved facts go to `docs/dev-notes.md` on main (60 lines
    max, commit `docs: dev notes`), then clear the scratch file (shared repo: keep them in the scratch folder).

## Pitfalls

- Never run two workers at once; the launcher's lock enforces it.
- The dev's commit subject ends `(<ticket file name>)`; step 0 finds finished tickets by it. Your own commits must not.
- `FAIL guard:`: `pushed` -> tell the user (they fix the remote); `test-config-changed` or a denial on package/test
  config -> make that change yourself on the user's yes (`tickets.md`), re-dispatch; `history-rewritten` -> restore
  the old tip from `git reflog`.

## Verification

Every dispatch ends with its RESULT line read and the step 6 checks; main's SHA is unchanged until the user merges.
