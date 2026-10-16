# Open: logging the planner's own actions

Status: agreed direction, not designed. Grill before building, probably together with
[remote-runs.md](remote-runs.md) (both add to `runs.log` and the run-file contract). Saved 2026-10-16.

## The problem

Workers leave a trail for free: a progress log, a `runs.log` line, a result JSON, and a row in hermes-dev-watch. The
planner leaves almost none. In hermes-dev-watch `filters-search` the user missed reports because they existed only in
chat:

| Planner action | Where it was recorded |
|---|---|
| Browser Verify of 9 tickets | one line in each ticket's `## Result` |
| Status changes | scratch commit messages (made with `sed`, invisible to the kit) |
| Fix tickets 12 and 13, written and dispatched without asking | the ticket files only; no `decide` line |
| A wrong commit (skill edits swept in) and its revert | the kit CHANGELOG and chat |
| Time and cost of the planner's checks | nowhere |

The skill already said "`scratch.py decide` at once" and "read the diff before committing skills"; both were skipped.
A rule that relies on the planner remembering is not a behaviour (claude-p-role-workers rule 16).

## Settled

- No new role for this. A verifier worker that runs the page cannot be enforced by an allow-list (it runs project code
  in a browser), and a role costs a release. Logging is solved in the kit; running UI checks stays with the planner,
  or moves to browser tests (Playwright) that dev and reviewer run.
- Option 1: planner actions go into `runs.log` with role `planner`, plus a short progress log, so hermes-dev-watch
  shows them on the same timeline as workers with no change on its side (a `planner` role chip appears by itself).

## Direction (three layers, strongest first)

1. **Log as a side effect of commands the planner must run.**
   - `scratch.py status <feature> NN <status> [--verify "<PASS|FAIL>: <what was checked>"]` replaces hand edits:
     sets `Status:`, appends the Verify note to the ticket, commits the scratch repo, writes the planner `runs.log`
     line (e.g. `PASS verify 05`, `FAIL verify 10: blank verdict`).
   - `scratch.py fix <feature> NN "<what was seen>"` creates a planner-written fix ticket with a `Found by the
     planner` line, logs it, and adds the `decide` line.
2. **Refuse or warn when a step is skipped.** Moving a ticket with a `Verify:` line to `review`/`done` without a
   recorded result fails; `resume` and `tickets.py` print `WARN` for a reviewed ticket with no Verify result and a fix
   ticket with no `Found by` line.
3. **Skill text last:** orchestrate step 6 and `references/tickets.md` name these commands.

## Not covered by design

What the planner never tells a command: the steps inside a browser check, debugging dead ends, a crashed session, a
mistake it does not notice. The record shows conclusions at each step, not everything tried.

## Questions for the grill

1. `runs.log` fields for a planner line: model (`planner`?), turns and secs (wall time of the check?), cost (unknown:
   `None`)? Does a planner line need `fmt` 2, or is it additive (role name only) and `fmt` stays 1?
2. A progress log per planner action, or one per feature session? Who writes the `[ Ns]` lines?
3. Is the planner action live in the lock (dev-watch "running"), or only logged when done?
4. Which actions log: Verify, status, fix ticket, ticket rewrite, decide, dispatch, skill/kit commits?
5. Does `status --verify` FAIL block anything, or only record?
6. The "fix ticket I wrote myself" lesson (orchestrate 0.8.3): Verify before or after review, user's yes for new
   scope, what counts as a gap, how the reviewer keeps fresh eyes. These were asked once and left unanswered.
7. Test plan: kit unit tests for the commands, `tests/watch_contract.py` samples with a planner line, and one real
   planner run on a toy repo that checks the log appears without a reminder.
