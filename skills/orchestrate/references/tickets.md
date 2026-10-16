# Making tickets dispatchable (step 2)

Load at step 2, and before writing or dispatching a fix ticket.

- `python KIT/tickets.py <feature> --add-header` (dry run, then `--apply`) moves flat `NN-*.md` files into `issues/`
  and inserts the header after the title, keeping the body and Matt's `Blocked by`. Fill the placeholders (the header
  is this skill's `templates/ticket-header.md.template`). If it prints `STOP`, a ticket lacks Matt's `**Blocked by:**`
  and `**Status:**` lines: tell the user that `to-tickets` changed upstream and `tickets.py` needs updating; do not edit
  tickets by hand to get past it.
- `python KIT/tickets.py <feature>` checks every ticket and prints the approval table (repo, status, blockers, Seam,
  Test command, Verify); exit 0 means dispatchable. The launcher reads only the plain `Test command:` line.
- A worker has no web and cannot ask: put every fact it needs in the ticket.

## Test command vs Verify

`Test command` is what the dev may run: cheap and safe, no running services, no database writes (typecheck, lint,
unit tests, a headless browser test that starts its own server). A check that needs a running server, a database, or
the user's say-so goes on the `Verify:` line instead (else `None`): you run it yourself after the batch's review
passes, ask first if a project rule requires it, and write the outcome under the ticket's `## Result`.

## No deletions in tickets

The dev's allow-list has no `rm` or `git rm`, so a ticket that tells it to delete a file ends in `FAIL could not
delete ...; work uncommitted` with a dirty tree. Before dispatch, either delete the file yourself and commit it
(`git rm <file>`, `git commit`, both after the user's yes), or reword the ticket so the dev leaves the file in place
(unused, unimported) and add "planner deletes <file> after this ticket" to its `Verify:` line. `tickets.py` prints
`WARN ... asks to delete` for a body that says so.

## Review batches (full lane)

Each batch is one milestone: tickets that deliver one usable capability, at most 4, in dependency order (multi-repo:
one repo per batch). Record them in `<feature>/batches.md`, one line per batch: `<b>: NN,NN,... base: <sha or blank>`.

## Fix tickets

The reviewer writes `NN-fix-*.md` (Status `todo`). Check each one's `Seam` before dispatch: a dev with `Seam: None`
FAILs in seconds without writing code. For a refactor-only fix, name the function or file touched and add "refactor
only, existing tests are the check".

- **A gap you see in your own Verify becomes a fix ticket before the review, not a note for the reviewer.** Observed
  once: a planner noticed a blank field during its browser check of a fix ticket and left it to the reviewer, which
  cost a full extra review round. Write the fix ticket yourself, dispatch it, check again, then review. Recorded after
  one feature; keep or drop it after the next.

## Dependencies and test config

A ticket that needs a new dependency or a test-config change FAILs with `guard:test-config-changed` or a denial: the
dev may not edit `package.json`, lockfiles, `jest`/`vitest`/`tsconfig` files, `.team.json` or `protected` files,
because the allowed test command would run whatever they say. Make that change yourself, with the user's yes, commit
it, then dispatch.
