# Design: logging the planner's own actions

Status: grilled and settled 2026-10-16; not built. Build waits for the user's go. Designed alone, before
[remote-runs.md](remote-runs.md), which reuses the planner line's shape later.

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
A rule that relies on the planner remembering is not a behaviour (claude-p-role-workers rule 16), so logging is a side
effect of commands the planner must run, and skipping them is refused or warned about.

## Decisions

1. **No new role.** A verifier worker that runs the page cannot be enforced by an allow-list (it runs project code in a
   browser), and a role costs a release. UI checks stay with the planner, or move to browser tests (Playwright) that
   dev and reviewer run.
2. **What is logged:** Verify results, status changes, planner-written fix tickets, ticket rewrites, `decide` lines.
   Not dispatches (the worker's own line covers them) and not kit/skill commits (git history covers those).
3. **Where:** `runs.log`, role `planner`, same contract (`docs/watch-contract.md`). Logged when done; no lock and
   nothing "live" (a check is minutes of chat, not a process with a pid).
4. **Line shape** (additive, `fmt` stays 1): the 6 fixed fields are `stamp`, `planner`, model `planner`, verdict,
   `turns=-`, `<secs>s` (time since the previous planner line of this feature, `0s` for the first); then
   `list$=None`, `feature=`, `ticket=` (or `-`), and a new `action=` key (`verify`, `status`, `fix`, `rewrite`,
   `decide`), then the usual trailing keys and `fmt=1`. `costs.py` shows a planner row with $0.
5. **Verdicts:** checks use `PASS verify 05`, `FAIL verify 10: <what failed>`, `SKIP verify 05: <reason>`. Every
   other action starts with `NOTE` (`NOTE status 05 review`, `NOTE fix 13: <what was seen>`, `NOTE rewrite 11: <why>`,
   `NOTE decide: <text>`), so it falls in dev-watch's "no result" group and never counts as a pass.
6. **Unique run ids:** if `<stamp>-planner` already exists in `runs.log` or `runs/`, the command uses the next second.
7. **Progress log** `runs/<stamp>-planner.progress.log` (stays on the machine, like workers'): the planner passes its
   check as `--step "..."` lines, written as `[   0s] say: <step>`; the command adds a line for what it changed, e.g.
   `[   0s] status 05 in-progress -> review`. dev-watch's `say` filter then shows the checks.
8. **Commands** (`kit/scratch.py`; each one edits, logs, and commits the scratch repo):
   - `status <feature> NN <status> [--verify "PASS|FAIL|SKIP: <text>"] [--step "..."]...`: sets `Status:`, appends
     `Planner Verify: <verdict> <text>` and the steps to the ticket's `## Result`, writes the planner line.
   - `fix <feature> NN-fix-<slug> --for NN "<what was seen>"`: creates the fix ticket from the template with
     `Found by the planner: <what was seen>` and `Blocked by: NN`, adds it to the batch's `batches.md` line, writes a
     `decide` line and the planner line.
   - `rewrite <feature> NN "<why>"`, run after the planner edited the ticket: adds `Rewritten by the planner: <why>`,
     writes a `decide` line and the planner line.
   - `decide` (exists): also writes a planner line.
9. **Refusals** (the command exits `STOP`, nothing changed):
   - a ticket with a `Verify:` line other than `None` cannot go to `review` or `done` without `--verify`;
   - `--verify FAIL` to `review` only when a fix ticket made by `fix --for NN` names that ticket;
   - to `done` while any fix ticket naming it is not `done`.

   `resume` and `tickets.py` print `WARN` (never refuse) for: a ticket in `review`/`done` with a `Verify:` line and no
   `Planner Verify:` record; a fix ticket with neither `Found by the planner:` nor a reviewer origin; a ticket whose
   body (above `## Result`) changed in a scratch commit after the commit that set it `in-progress`, with no
   `Rewritten by the planner:` line (read from the scratch repo's git history, so it works on every machine).
10. **When Verify runs:** before review for checks the planner can run alone (browser with a stand-in bridge,
    scripts); after review, as today, for anything needing a server, a database or the user's yes.
11. **What counts as a gap the planner fixes before review:** a failed acceptance criterion or Verify, a crash, or a
    broken existing behaviour. A design opinion goes to the reviewer.
12. **User's yes:** a planner-written fix ticket needs it only when it adds scope beyond the original ticket's
    criteria (like 12, a crash on junk input); a failed criterion or Verify (like 13) is dispatched at once. Either way
    the `decide` line shows in dev-watch.
13. **Fresh eyes:** the launcher scans the batch's tickets for `Found by the planner:` and adds one line to the
    reviewer's prompt naming them: check their design first.
14. **Skill text** (orchestrate patch): step 6 and `references/tickets.md` name the commands; the 0.8.3 "gap in own
    Verify" lesson is replaced by decisions 10-12, keeping its "observed once, judge after the next feature" note.
15. **hermes-dev-watch** (small release after the kit): the header's "today" counts only non-`planner` runs and shows
    `· N planner` separately (today NOTE lines would count as failures: fail = runs - pass).

## Not covered by design

What the planner never tells a command: the steps inside a check beyond what it passes as `--step`, debugging dead
ends, a crashed session, a mistake it does not notice. The record shows conclusions at each step, not everything
tried.

## Build and test plan

- Kit: the commands and refusals in `kit/scratch.py`, the WARNs in `scratch.py resume` and `kit/tickets.py`, the
  reviewer prompt line in `kit/run-worker.py`, `costs.py` unchanged unless the planner row needs it.
- Tests: unit tests for `status`/`fix`/`rewrite`/`decide`, every refusal and WARN; `tests/watch_contract.py` samples
  gain a planner line (`--update`; hermes-dev-watch's CI parses them); one real planner run on a toy repo
  (`hermes chat` with the orchestrate skill, as in `references/changing-skills.md`) checking that status changes,
  Verify records and planner lines appear without a reminder.
- One kit release (Z only), orchestrate patch, CHANGELOG marked **watch:** and **re-copy**; then the dev-watch release.
- Judge after the next real feature: did every planner Verify, fix and rewrite show up in `runs.log` without the user
  asking, and did the reviewer find design flaws in planner-written fixes? Record review rounds and cost per batch.
