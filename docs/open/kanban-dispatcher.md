# Open: kanban dispatcher (grill log)

Started 2026-11-01. Goal: a standing dispatcher that keeps up to N Claude Code dev workers busy from the ticket files, plus a
board to watch it. Hermes' own Kanban (`hermes kanban`, `plugins/kanban`) was read and rejected as the runner: it spawns
`hermes -p <profile> chat -q`, so no Claude Code, and it has no equivalent of this kit's protected files, push and git-state guards.

## Facts read from source (kit 0.17.0)

- F1. `scratch.py dispatch` (`cmd_dispatch`) takes a fixed list of >= 2 tickets, refuses when ANY lock is alive
  (`teamcfg.read_lock`), starts one worker per pool slot, waits for all, then merges PASS branches in ticket order.
  A standing dispatcher cannot call it to top up a free slot, so it needs its own loop over the same parts.
- F2. Reusable parts in `kit/parallel.py`: `prepare_slot`, `run_worker`, `merge_ticket`, `seam_paths`, `seam_conflicts`,
  `ticket_branch` (`<feature>--NN`). Slot locks are `runs/slot-N.lock`; `teamcfg.read_locks` lists them.
- F3. Ticket states are `todo, in-progress, review, blocked, done` (`tickets.py` `STATUSES`); `Blocked by`, `Seam`, `Branch`,
  `Repo` are header lines; a blocker counts only when `done`.
- F4. A run's verdict is the last `RESULT` line; `FAIL usage-limit` already stops a group's queue.
- F5. The kit allows one feature branch per dispatch group and a clean tree; merges into the feature branch must be one at a time.
- F6. `max_workers` and `setup` live in `.team.json`; default is serial.

## Decisions (user answered "all recommended", 2026-11-01)

- D1. The ticket files in `.scratch/<feature>/issues/` stay the only source of truth (tracked in git, planner flow unchanged).
  No database. The board is a view computed from them.
- D2. The dispatcher lives in the kit: `kit/kanban.py` (new) with subcommands `board`, `run`, `stop`, `status`, and a systemd
  user unit template (`kit/systemd/hdt-kanban@.service`) so it survives Hermes killing background jobs. No desk needed.
- D3. Loop (one project, one feature at a time in v1): every 10 s read the tickets; a ticket is READY when Status is `todo`,
  every `Blocked by` is `done`, its Seam paths do not overlap any running ticket's Seam, and a slot is free (cap = `max_workers`,
  hard max 3). Start it with the kit's own slot code (F2), mark `in-progress`, and on PASS merge its branch into the feature
  branch (serialised by one merge lock) and mark it `review`. Reviewers stay a planner step (unchanged, batch review).
- D4. Failure handling: a crash or `FAIL no RESULT` retries ONCE, then marks the ticket `blocked` with the reason in `## Result`.
  `FAIL usage-limit` pauses the whole queue until the reset time and does NOT count as a retry. A run past its time limit
  (`.team.json` `"run_seconds"`, default 1800) is stopped with the kit's own stop and treated as a crash. A run whose
  launcher/worker pids are gone with the lock stale is an orphan: marked `blocked: orphaned`, never silently re-run.
- D5. Guards are untouched: the dispatcher only calls `run-worker.py`, so protected files, no push and git-state checks apply.
  It never edits code and never commits except the scratch commit the kit already makes.
- D6. One writer: the dispatcher is the only process that moves a ticket `todo -> in-progress -> review|blocked` while it runs.
  The planner pauses it (`kanban.py stop`) before editing tickets by hand; `status` shows whether it runs. A lock
  `runs/dispatcher.lock` (pid) refuses a second dispatcher.
- D7. The board data comes from one command, `kanban.py board --json` (columns by status, per ticket: number, title, repo,
  branch, blockers, seam, last RESULT line, run progress tail, slot, started time, retries). The desk and any terminal use
  that one output; no second parser.
- D8. Desk side (hermes-desk, separate repo and later): a read-only Kanban view in the Workspace layout, lazy panel, in its own
  folders (`ui/src/kanban/`, `hermes_desk/kanban/`), calling `kanban.py board --json` through a sidecar method for the
  project's folder; columns Todo / In progress / Review / Blocked / Done, live worker progress from the progress logs,
  poll only while the view is open. Buttons (pause/resume dispatcher, stop a run) come after v1.
- D9. Built in the scratch clone `hdt-work` on `feat/kanban-dispatcher`, tests in `tests/` in the kit's style (temp repo,
  fake `claude`, one PASS/FAIL line per check), plus one real run with two cheap Sonnet tickets. Handover as a patch if this
  machine cannot push (`git-patch-handover`); LastNyx is active here, but ask before pushing.

## Not in v1

Several features at once, auto reviewers and fix tickets, writes from the board, remote machines, a database.

## Tickets (kit repo, `.scratch` not used: this repo has none)

| # | Ticket | Blocked by |
|---|---|---|
| K1 | `kanban.py board --json` + `status` (read-only: tickets, locks, runs.log, progress tail) + tests | - |
| K2 | `kanban.py run` loop: ready test (blockers, Seam overlap, cap), start, mark in-progress, dispatcher lock | K1 |
| K3 | Finish handling: merge under the merge lock, mark review/blocked, retry once, usage-limit pause, timeout, orphan | K2 |
| K4 | `kanban.py stop` (graceful: stop starting, wait for runs; `--now` stops workers) + systemd unit + README/CHANGELOG/design entry | K3 |
| K5 | Real two-ticket run in a throwaway repo, `tests/kanban_real.py` | K4 |
| D1 | hermes-desk: sidecar method + read-only Kanban view (separate repo, after K1 ships) | K1 |

Batches: 1 = K1; 2 = K2; 3 = K3; 4 = K4, K5; later = D1.
