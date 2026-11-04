---
name: orchestrate
description: Run dev, reviewer, docs, researcher workers; verify results.
version: 2.1.1
author: hermes-dev-team contributors
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [team, planner, orchestration]
    related_skills: [grill-with-docs, to-tickets]
---

# orchestrate

You are the planner in Hermes. Workers are headless `claude -p` runs started by `kit/run-worker.py` under a role
allow-list, detached from your chat by `kit/bg.py` (a `systemd-run --user` unit), so your turn is never blocked by one.
One worker at a time, except a parallel dev group (step 4b). Keep your own context small: read result lines and
files, never a worker's transcript.

## When to Use

Tickets exist and the user said build or go. FULL lane = tickets -> dev per ticket -> reviewer -> docs.
QUICK lane = one ticket -> dev -> your verification. TRIVIAL = your own fix inside `scratch.py check-run`. Route every
request, finding or gap by size (`lanes.md`). Planning comes first (Matt Pocock's `grill-with-docs`, `to-tickets`). Also: dispatch the researcher
(`research.md`) instead of searching yourself when an answer needs reading more than a few files, or a whole repo or
folder.

## Setup and references

Python 3; a clean git tree; `KIT` = the team repo's `kit/` (absolute, forward slashes). Each project has a
`.team.json` with its full test command (`{"test": "npm test"}`, or one suite per folder: `config.md`); if missing, ask
for it once and write only that (the dev cannot edit it). Never ask about models or effort unless the user does
(`models` per role: dev/reviewer/researcher `sonnet`, docs `haiku` by default). If the launcher says `.team.json has
old keys` (1.0.0's `options`), show the user the replacement JSON it printed and write it only on their yes. Glossary: `CONTEXT.md` if the
repo has one, else `GLOSSARY.md`; never create the second.

Load a reference with `skill_view(name="orchestrate", file_path="references/<file>")` only when its trigger applies:

| Reference | Load when |
|---|---|
| `tickets.md` | step 2, and before writing or dispatching a fix ticket |
| `lanes.md` | routing a request, finding or gap (trivial / small / large), or the user hands you decisions while away |
| `workspace.md` | the config has `"repos"` (multi-repo workspace) |
| `shared-repo.md` | the config has `"shared": true` |
| `switching.md` | "switching", a force-stop request, a killed worker, or step 0 reports a lock, an orphaned worker, `DIVERGED` or `ASK` for a dirty tree |
| `config.md` | other `.team.json` keys, worker rules files, a `.team.json has old keys` refusal, or an `ERROR` about claude |
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
   - `worker running`: its step-4 watcher wakes you when it ends; else `bg.py status`.
   Follow `decisions.md` (what the user decided in chat). Tickets, reviews, `runs.log` and git are the rest of the state.
1. **Branch.** Create the feature branch from main. Once per project, `python KIT/scratch.py init` makes the scratch
   folder its own git repo. Commit the glossary and ADRs on the branch as `docs: context` (not in a shared repo, not a
   workspace-level glossary). The launcher refuses main/master.
2. **Make tickets dispatchable** (load `tickets.md`). Show the user the `tickets.py` table (Seam, Test command,
   Verify per ticket) and the proposed review batches; they approve both before step 3.
3. **Pick** the next `todo` ticket whose blockers are `done` (`python KIT/tickets.py <feature> --next`) and set it
   `in-progress`. Every Status change goes through `python KIT/scratch.py status <feature> <NN> <status>`, never a
   hand edit: it edits, commits the scratch repo and logs a `planner` line in runs.log the user can read.
   **Whenever the user decides something in chat**, or you change a ticket after dispatch:
   `python KIT/scratch.py decide <feature> "<one line>"` at once.
4. **Dispatch dev**, cwd = the repo:
   `python KIT/bg.py start run-worker.py dev "ticket: .scratch/<feature>/issues/NN-slug.md"`. The launcher takes the
   test command from the ticket and the model from `.team.json`; never retype either. It prints `BG OK <id> ...`.
   Then `python KIT/bg.py wait <id>` (returns within about 5 minutes): `BG RUNNING` -> do other planner work
   (read the next ticket, verify an earlier one) or call `wait` again; `BG DONE <id> exit=<n>` with the RESULT line
   above it -> step 5. **Wake-up:** the unit sends no notice to the chat, so right after `start` also run
   `terminal(background=true, notify=true)` with `python KIT/bg.py wait <id> --timeout 21600` (same cwd). It exits on
   `BG DONE`, and that exit starts a new turn; then read the RESULT line with a plain `bg.py wait <id>`. Without it a
   finished worker is only noticed when the user writes. The watcher is not the worker: if Hermes kills it, the worker
   keeps running, so start a new watcher. Never `terminal(background=true)` for the worker itself, never raw
   `systemd-run` (no job file, no RESULT line), never pipe the launcher into `head`/`tail`. Same for steps 8 and 9.
4b. **Parallel dev** (only when `.team.json` has `max_workers` > 1 and a `setup` list): when the batch has two or
   more `todo` tickets with no blockers between them and Seams that name different files, write them as one group in
   `batches.md` (`Parallel: 01 02 | 03`) and dispatch the group instead of step 3-4 for each:
   `python KIT/bg.py start scratch.py dispatch <feature> 01 02`, then `bg.py wait <id>` as in step 4 (cwd = the
   repo). It refuses overlapping Seams (run those one after another), sets the tickets `in-progress`, runs one dev per
   pool worktree (at most 3), then merges each PASS branch into the feature branch in ticket order. Read one RESULT
   line per ticket and the merge line under it, then step 6 for each merged ticket. `FAIL conflict`: redispatch that
   ticket alone (step 4) from the new tip. Last line `DISPATCH STOP usage limit hit`: pause the whole queue and tell
   the user; never redispatch until the limit resets. Never dispatch a reviewer, docs or researcher in a group.
5. **Read the one RESULT line**: `RESULT dev PASS <sha> | ...` or `RESULT dev FAIL <reason> | ...`, plus any denied
   calls, and show the user the ticket's `Not checked:` line. Exit 0 = PASS and the guards held (main unmoved, branch
   unchanged, no history rewrite, no protected file changed, nothing pushed). Never trust a worker's own exit code.
   **FAIL with uncommitted work** (`work uncommitted`, or a dirty `git status --short`): never re-dispatch first (the
   dev refuses a dirty tree). Show the user `git status --short`, `git diff --stat` and the reason; they commit or
   discard. You may commit only on their explicit yes for that diff, with a subject NOT ending `(<ticket file>)`.
   Then step 6 and `scratch.py decide`.
6. **Verify through a read-only subagent**; keep only its verdict line, not the diff, files or test output. Hermes:
   `delegate_task` (it cannot narrow a child's tools, so the brief forbids edits); Claude Code: the Task/Agent tool,
   an Explore or general-purpose agent told it must not edit files. This is not a worker: it writes nothing, so it
   does not contradict "feature code only through the launcher" (`lanes.md`). Brief: the absolute repo path, the
   ticket path, the commit sha, the ticket's `Test command` and `Verify:` line, and "Read-only: never edit, commit,
   stash, checkout or delete. `cd <repo>`; run `git log --stat -1 <sha>`, the test command, `git status --short`;
   read `git show <sha>` against the ticket's criteria and the Result's `Not checked:`; run the Verify line only if
   it needs no running server, database or user's yes. Answer with ONE line: `PASS: <what was checked>` |
   `FAIL: <reason>` | `ASK: <question for the user>`." Then check yourself that `git status --short` is clean and
   HEAD has not moved (else the subagent wrote: tell the user). Read the diff yourself only to explain an ASK or FAIL
   to the user; ASK: ask, record their answer. For a bug ticket show the user the Result's `Cause:` line. A Verify
   that needs a running server, a database, a look at a screen or the user's say-so stays with you (`tickets.md`).
   Record the verdict: `scratch.py status <feature> <NN> review --verify "PASS|FAIL|SKIP: <verdict text>"` (quick
   lane: `done`; SKIP for a check that waits). A gap becomes a fix ticket (`tickets.md`) plus a `decide` line. FAIL
   in the test command: set `blocked`, tell the user the reason line.
7. Repeat 3-6 until the batch's tickets are all `review` (quick lane: all `done`, then step 10).
8. **Review the batch** (full lane):
   `python KIT/bg.py start run-worker.py reviewer "feature: .scratch/<feature> batch: <b> tickets: NN,NN base: <sha> round: <n>"`, then `bg.py wait`.
   `base` = the commit before the batch's first commit: `git merge-base HEAD main` for batch 1, else the HEAD recorded
   when the previous batch passed (or the merge commit `sync-main` printed); never HEAD at review time when tickets of
   this batch were built earlier (empty diff). Write it into `batches.md`. Show the user the review's `Not checked:`.
   PASS: run each `Verify` that had to wait (server, database, the user's yes), set the tickets `done` with
   `scratch.py status --verify`, record HEAD as the next base, back to step 3.
   FAIL: before dispatching, check each finding against the code and its ticket; one the code disproves, or that is
   outside the ticket, gets a `decide` line and no fix ticket. It wrote `review-<b>-<n>.md` and fix tickets; dispatch them (step 4), add them to `tickets:`, review again with
   round n+1 and the same base. Never dispatch round 3: ask the user (unless a `delegated:` decision exists: `lanes.md`).
9. **Docs** (full lane, all `done`): `python KIT/bg.py start run-worker.py docs "feature: .scratch/<feature>"`, then `bg.py wait`. Tell the user the
   `drift.md` items. More than three files to edit: split the job or give it a `<feature>/docs-facts.md`.
10. **Merge.** Tell the user the branch is ready; the user runs `git merge --no-ff`. Never merge or delete a branch;
    never push except `scratch.py switch` (`switching.md`).
11. **Lessons.** Show the user `.scratch/dev-notes.md`. Approved facts go to `docs/dev-notes.md` on main (60 lines
    max, commit `docs: dev notes`), then clear the scratch file (shared repo: keep them in the scratch folder).

## Pitfalls

- Between tool calls, write at most one short line; tickets, `decisions.md`, RESULT lines and `scratch.py` logs are the
  record. This never shortens what the user must read: the resume report, `Not checked:`, `Cause:`, `drift.md` items,
  the ticket table and the final report.
- Never run two workers at once; the launcher's lock enforces it.
- The dev's commit subject ends `(<ticket file name>)`; step 0 finds finished tickets by it. Your own commits must not.
- `FAIL guard:`: `pushed` -> tell the user (they fix the remote); `test-config-changed` or a denial on package/test
  config -> make that change yourself on the user's yes (`tickets.md`), re-dispatch; `history-rewritten` -> restore
  the old tip from `git reflog`.

## Verification

Every dispatch ends with its RESULT line read and the step 6 checks; main's SHA is unchanged until the user merges.
