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
unit tests, a headless browser test that starts its own server). Any other check goes on the `Verify:` line (else
`None`) and you run it yourself:

- **Before the review** (step 6), when you can run it alone: a browser with a stand-in bridge, a script, a sample file.
- **After the review passes** (step 8), when it needs a running server, a database, or the user's say-so (ask first if
  a project rule requires it).

Record every Verify with `scratch.py status ... --verify "PASS|FAIL|SKIP: <what you saw>"`: it writes `Planner
Verify:` under the ticket's `## Result` and a `planner` line in `runs.log`. Nothing forces it: a ticket with a
`Verify:` line moved without one is a gap the user sees in hermes-dev-watch (SKIP for a check that waits).

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

**Fix tickets you write yourself.** A gap your own Verify finds becomes a fix ticket before the review, not a note
for the reviewer:

- A gap is a failed acceptance criterion or Verify, a crash, or a broken existing behaviour. A design opinion ("this
  would be cleaner") goes to the reviewer instead.
- Copy the original ticket to `issues/<NN>-fix-<slug>.md`: same header, `Status: todo`, `Round:` + 1,
  `Blocked by: <NN>`, a `Found by the planner: <what you saw>` line, then new What to build and criteria. Add it to
  the batch's `batches.md` line, run `tickets.py`, `scratch.py decide <feature> "planner fix <file>: <what you saw>"`,
  and record the original with `--verify "FAIL: <gap>"`.
- Dispatch it at once when it fixes a failed criterion or Verify. When it adds scope beyond the original ticket's
  criteria (a crash on input the criteria never named), show it to the user and wait for a yes.
- Tell the reviewer about it in the prompt (`... round: <n> planner fixes: <files>`), so it checks their design first.

## Dependencies and test config

A ticket that needs a new dependency or a test-config change FAILs with `guard:test-config-changed` or a denial: the
dev may not edit `package.json`, lockfiles, `jest`/`vitest`/`tsconfig` files, `.team.json` or `protected` files,
because the allowed test command would run whatever they say. Make that change yourself, with the user's yes, commit
it, then dispatch.
