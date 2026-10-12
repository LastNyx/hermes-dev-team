---
name: orchestrate
description: Run dev, reviewer, docs workers on tickets; verify results.
version: 0.3.0
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
QUICK lane = one ticket -> dev -> your verification.

Planning comes before this skill and uses Matt Pocock's skills, installed separately (see the team repo's README):
`grill-with-docs` (grilling + domain-modeling) to settle the design, `to-tickets` to cut tracer-bullet tickets.
This skill does not replace them; it only adds the fields a worker needs (step 2).

## Prerequisites

`claude` CLI logged in; Python 3; a git repo with a clean tree; `KIT` = absolute path to the team repo's `kit/`
(forward slashes on Windows).

Each project has a committed `.team.json` with its full test command, e.g. `{"test": "npm test"}`; optional
`"models": {"dev": "sonnet"}` and `"protected": ["<file-name glob>", ...]` (more files the dev may not edit, on top of
the launcher's package/lock/test-config list). If it is missing, ask the user for the test command once and write
the file (the dev worker cannot edit it).

The glossary is `CONTEXT.md` if the repo has one, else `GLOSSARY.md` (Matt's domain-modeling default). Use whichever
exists; never create the second one.

## Procedure

1. **Branch.** Create the feature branch from main. Add `.scratch/` to `.git/info/exclude`. Commit the glossary and ADRs on the branch as `docs: context`. The launcher refuses to run on main or master.
2. **Make tickets dispatchable.** `to-tickets` writes `.scratch/<feature>/issues/NN-slug.md` with `**Blocked by:**` and `**Status:**` lines. First check the format: if a ticket lacks either the `**Blocked by:**` or the `**Status:**` line, stop and tell the user that Matt's `to-tickets` format has changed upstream and step 2 needs updating; do not guess. Before the first dispatch, replace those two lines in each ticket with the header in this skill's `templates/ticket-header.md.template` (`skill_view` with file_path): `Lane`, `Branch`, `Status: todo`, `Round: 0`, `Blocked by`, `Terms`, `Seam`, `Test command`. Keep the body and its acceptance criteria. A worker has no web and cannot ask: put every fact it needs in the ticket. Show the user the Seam and Test command per ticket in one list; they approve before step 3. The launcher reads only the plain `Test command:` line.
3. **Pick** the next `todo` ticket whose `Blocked by` tickets are all `done`. Set it `in-progress` yourself; workers never change a ticket's Status.
4. **Dispatch dev** with `terminal(background=true, heartbeat=120)`, cwd = the repo:
   `python -u KIT/run-worker.py dev "ticket: .scratch/<feature>/issues/NN-slug.md"`
   The launcher takes the test command from the ticket's `Test command:` line (or `.team.json`), so do not retype it.
   Tell the user the `progress:` path from the `START` line, then end your turn; never `process wait` (it blocks the
   heartbeats). A heartbeat shows the worker's new tool-call lines: answer it in one short line with no tool calls
   (each one is a planner turn, so keep it cheap). Act only on the exit notice. Use the same dispatch for steps 8 and 9.
5. **Read the one result line**: `RESULT dev PASS <sha> | ...` or `RESULT dev FAIL <reason> | ...`, with any denied calls listed under it. Exit 0 means PASS and the guards held (main unmoved, branch unchanged, no history rewrite, no protected file changed). Never trust a bare `claude` exit code: it is 0 even when a worker gives up.
6. **Verify** with 3 commands: `git log --stat -1`, the ticket's `Test command`, `git status --short`. On PASS set the ticket `review` (quick lane: `done`). On FAIL set it `blocked` and tell the user the reason line.
7. Repeat 3-6 until every ticket is `review`.
8. **Review** (full lane): `python KIT/run-worker.py reviewer "feature: .scratch/<feature> round: <n>"` (full test command from `.team.json`). PASS: set the tickets `done`. FAIL: it wrote `review-<n>.md` and fix tickets (`NN-fix-*.md`, Status `todo`); dispatch those as in step 4, then review with round n+1. Round 3 is never dispatched: ask the user.
9. **Docs** (full lane, all `done`): `python KIT/run-worker.py docs "feature: .scratch/<feature>"`. Read `drift.md` and tell the user the items.
10. **Merge.** Tell the user the branch is ready. The user runs `git merge --no-ff`. Never merge, delete a branch, or push.
11. **Lessons.** Show the user each line of `.scratch/dev-notes.md`. With their say-so, add the approved project facts to `docs/dev-notes.md` on main (60 lines or fewer), commit as `docs: dev notes`, and clear the scratch file. A lesson that helps every project goes into the team repo's `kit/roles/*.md` only after the user has read it.

## Cost log

Every run appends one line to `.scratch/runs.log` (role, model, verdict, turns, seconds, tokens, list price, denials,
feature). At the end of a feature run `python KIT/costs.py --feature <feature> --by-role` and show the user the table;
read that table, never the raw log. Prices are list price; a subscription seat is billed differently.

## Changing skills

The team skills (orchestrate, claude-p-role-workers) are linked into the team repo, so any `skill_manage` edit to them
is an uncommitted change there. Before patching one, show the user the proposed change in one or two lines and wait
for a yes. Keep it general: no project paths or names. At the end of a session that changed one, tell the user to run
`python KIT/review-skills.py`, then commit. Matt Pocock's skills are not part of the repo: never patch them, so they
can still be updated from upstream.

## Pitfalls

- Never run two workers at once; the serial chain keeps one branch and one tree.
- A ticket that needs a new dependency or a test-config change FAILs with `guard:test-config-changed` or a denial: the dev may not edit `package.json`, lockfiles, `jest`/`vitest`/`tsconfig` files or `.team.json`, because the allowed test command would run whatever they say. Make that change yourself, with the user's yes, commit it, then re-dispatch.
- The project's `CLAUDE.md` (with `@` imports inlined) is pasted into every worker's prompt by the launcher; workers do not load it themselves (`--setting-sources ""`). Its rules apply; its permissions do not.
- Workers may run the test command, which executes code the dev wrote. That hole remains; review before merge.
- `git commit -m x --amend` matches the allowed commit prefix. The launcher's history guard reports it as `FAIL guard:history-rewritten`; restore the old tip from `git reflog`.
- Commits never carry a Co-Authored-By trailer: the launcher forces `includeCoAuthoredBy: false` and empty `attribution`.
- A repo's own `.claude/settings.json` is ignored (`--setting-sources ""`), so a project cannot widen a worker's permissions.

## Verification

Every dispatch ends with its RESULT line read and the step 6 checks; main's SHA is unchanged until the user merges.
