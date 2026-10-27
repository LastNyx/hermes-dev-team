# Open: parallel workers (grill log)

Started 2026-10-27. Goal: run more than one worker at a time so a ticket queue finishes faster.

## Why it was turned down before

- `docs/design.md` Shape: "Serial: one worker at a time, one branch, no worktrees." The worker lock (`runs/worker.lock`) made it a hard rule after a second planner session broke the prompt-only rule.
- 2026-10-16 chat: the planner argued (1) the user, not the worker, is the bottleneck (reviewing diffs); (2) parallel dev in one repo needs worktrees, a merge step and conflict handling, and every guard assumes one working tree; (3) subscription usage limits are hit N times faster; (4) proposed instead: one lock per repo, read-only roles skip the lock, `max_workers` cap, and worktrees only if tickets are shown to wait on the worker.
- 2026-10-25 README "Talking to the planner": "One worker at a time, always"; a queue ("keep going") runs tickets one after another.

## Facts (measured from runs.log, 2026-10-27)

| repo | worker runs | days | worker time | median run | idle between runs in one sitting | median gap |
|---|---|---|---|---|---|---|
| hermes-desk | 198 (121 dev, 68 reviewer) | 5 | 4.8 h | 78 s | 5.9 h | 31 s |
| BKM-monorepo | 85 (54 dev, 26 reviewer) | 3 | 2.1 h | 66 s | 1.4 h | 22 s |

The median gap between runs is 22-31 s: in a queue the next worker starts almost at once, so during a queue the user is NOT the bottleneck; the worker is. This answers objection (1) for queue mode. Both active projects are single repos, so "one lock per repo" alone speeds up nothing for them.

## Decisions

- **D1** Parallel = dev tickets of one batch that do not depend on each other, each in its own worktree and branch, merged back into the feature branch. Not dev/reviewer overlap, not only cross-repo.
- **D2** `.team.json` `max_workers`, default 1 (every project stays serial unless turned on); hermes-desk and BKM set 2.
- **D3** Planner proposes the groups in `batches.md` (e.g. `Parallel: 01 02 | 03`) from each ticket's Seam; the kit refuses a group whose Seam files overlap.
- **D4** The kit merges ticket branches into the feature branch one at a time; a conflict marks the ticket `FAIL conflict` and it is redispatched from the updated feature branch. New kind of merge (ticket -> feature branch, inside the kit); "the user merges" still holds for feature -> main.
- **D5** A fixed pool of N worktrees (`<repo>.w1`, `<repo>.w2`), reused across tickets; each run resets its slot to the feature branch tip. Install cost is paid once per slot.
- **D6** Each slot gets its own dependency install (`npm ci`, `uv sync`, `yarn`) when the pool is created and again when a lockfile changes; no symlinked `node_modules`/`.venv`.
- **D7** New command `scratch.py dispatch <feature> 01 02`: checks Seams (D3), starts the workers in pool slots, waits, merges in ticket order (D4), prints one RESULT line per ticket.
- **D8** One worker failing does not stop the group: the rest finish, the failed ticket's merge is skipped. Hitting the usage limit is its own verdict `FAIL usage-limit`, so the planner pauses the queue instead of redispatching.
- **D9** Reviews unchanged: one reviewer run per batch on the feature branch after the group is merged.

- **D10** Ticket branches `<feature>--NN`, local only, never pushed; the kit deletes one with `git branch -d` after a clean merge into the feature branch; a failed ticket's branch stays. "No agent deletes branches" still holds for feature branches and main.
- **D11** Workers only write files (own ticket `## Result`, append to `runs.log`); `dispatch` makes one scratch commit after the group ends.
- **D12** `scratch.py resume` applies today's three rules (verify / redispatch / ask) per slot; a finished but unmerged ticket branch is merged; a dirty slot is reported, never reset.
- **D13** Rollout: build on a branch in hermes-dev-team, test on the toy repos (two real parallel dev runs, a forced conflict, a mid-group crash plus resume), then hermes-desk with `max_workers: 2` on the next real batch, BKM only after that. Release 0.17.0 (minor: new command and setting).

## Facts checked in code (round 2)

- hermes-dev-watch reads every `runs/*.lock` (README line 90), so one lock per worker needs no contract change.
- `anchor_rules` writes allow-list paths absolute, so a worker in a slot gets rules for its own folder.
- Guards compare against a per-run snapshot; `main-moved` and `pushed` read shared refs, fine since workers never move them.
- A slot has no `.scratch`; the kit must be pointed at the main checkout's scratch.

## Built (0.17.0, branch feat/parallel-workers)

- `kit/parallel.py`, `scratch.py dispatch`, slot locks, `FAIL usage-limit`, resume per slot.
- `tests/parallel_dispatch.py` 24/24 (fake claude: overlap/blocker/max/setup refusals, concurrency, merge order, branch
  cleanup, conflict, usage limit, crash + stop + resume). Existing no-model suites unchanged and green.
- `tests/parallel_real.py` 5/5 on 2026-10-27: two real Sonnet dev runs (16 s, 15 s) finished in 17 s wall, both merged.
- Not done yet (D13): a real hermes-desk batch with `max_workers: 2`, then BKM.
