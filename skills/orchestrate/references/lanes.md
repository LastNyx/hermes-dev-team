# Size lanes and delegated decisions

Load before you route a change (a new request, a reviewer finding, a gap your Verify found) and when the user hands
you decisions while away ("keep going", "decide yourself", "continue till finish").

## Pick the lane by size, not by habit

| Lane | When (all must hold) | What you do |
|---|---|---|
| **Trivial** | one or two files; the cause is already known and you can name it; no new behaviour (a wrong label, a visible off-by-one, a reviewer's style or naming nit, a baseline note) | Fix it yourself inside `check-run` (below). No ticket. |
| **Small** | new behaviour, but one seam and a few acceptance criteria | QUICK lane: a short ticket, one dev run, your step 6. It joins the next batch's `batches.md` line, so it gets reviewed with that batch, not in a round of its own. |
| **Large / risky** | several seams; data or schema changes; auth, money, deletion, migrations; anything you would want a second pair of eyes on | FULL lane, as in the procedure. |

When unsure between two lanes, take the larger one. Never write feature code yourself in a delegated subagent: it
has no allow-list, runs on the planner's model and leaves no `runs.log` line; use the launcher.

## Trivial lane, step by step

1. `python KIT/scratch.py check-run begin` (from the repo): refuses main, a dirty tree and a running worker, then
   snapshots main, branch, HEAD, remote refs, git hooks/config and protected files.
2. Make the fix. Run the full suites: `python KIT/suites.py --feature <feature>` (last line `TEST PASS`).
3. Commit with a subject that does NOT end `(<ticket file>)` (step 0 would take it for a dev's ticket commit).
4. `python KIT/scratch.py check-run end`: the launcher's own guards plus a clean tree. `CHECK-RUN FAIL` -> tell the
   user the reason before anything else.
5. `python KIT/scratch.py decide <feature> "planner fixed directly: <what, why trivial, commit>"`. If it settled a
   ticket or a review finding: `scratch.py status <feature> <ticket file> done --verify "PASS: planner fixed directly
   (<why>)"`.

`check-run` catches a push, a protected-file edit or a moved main after the fact; it cannot stop them. That is why
the trivial lane is for your own small edits only.

A batch review later flags these commits as belonging to no ticket: answer with `scratch.py decide` naming the
commits, not with a fix ticket.

## Delegated decisions (the user is away)

When the user hands you review decisions, record it first:
`python KIT/scratch.py decide <feature> "delegated: review rounds and small findings"`.
With that line in `decisions.md` (and only then):

- A batch may get review round 3 and later instead of stopping to ask; each round still gets its RESULT line read.
- Settle findings by lane: a failure that already fails on main is a `decide` line naming the test (never
  `baseline --force` mid-feature: it would hide this feature's own failures); style and naming nits and a Result missing a statement you can check yourself
  are trivial-lane fixes; anything else is a fix ticket.
- Keep going through dispatch, verify, review, fix and docs. Stop only for what needs the user's eyes or a real
  account: merge, push, a remote sign-in, a decision outside the tickets.
- In the final report name every batch closed without a further review round, and every check that only a real run
  can do (`Not checked:` lines, `Cause: UNCONFIRMED`).

"One worker at a time" is never delegated: the lock is what keeps resume and switching safe. The one exception is
`scratch.py dispatch` (SKILL.md step 4b), which runs its own group of slots under its own locks.
