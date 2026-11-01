# Kanban dispatcher: a step-by-step guide

For someone who knows the kit's normal flow (planner, tickets, `run-worker.py`) and wants to run a whole feature's tickets
without starting each one by hand. The reference is the module docstring of `kit/kanban.py` and the grill log
`docs/open/kanban-dispatcher.md`; this file is the walk-through.

**Status:** built and tested on Linux with a fake `claude` (five suites) and one real run (3 tickets, a throwaway repo,
Sonnet workers, `tests/kanban_real.py`). It has **not** been used on a real project yet. Everything below marked
*(untested)* has no run behind it.

## 1. What it is, in three sentences

> **Not Hermes' built-in kanban.** `hermes kanban` and its dashboard are a separate Hermes feature: its own SQLite board,
> `hermes` workers. This dispatcher belongs to the dev-kit: it reads the ticket files and runs the kit's `run-worker.py`
> workers, with the kit's guards. The two share nothing, and one does not show the other's tickets.

`kanban.py run <feature>` is a loop. Every few seconds it looks at the feature's ticket files, starts every ticket that is
ready (up to `max_workers`, at most 3) in its own worktree, and when a worker finishes it merges the result into the
feature branch and moves the ticket on. The ticket files stay the only source of truth: no database, and the planner can
still read and edit them.

It does not run reviewers, write `done`, or touch more than one feature at a time. Those stay the planner's job.

## 2. How it differs from `scratch.py dispatch`

| | `scratch.py dispatch <feature> 01 02` | `kanban.py run <feature>` |
|---|---|---|
| Who picks the tickets | you name them | it picks every ready one |
| When it ends | after your group is merged | when nothing is left to start or run |
| Refills a free slot | no | yes |
| Retry, usage-limit pause, timeout, orphan check | usage-limit stop only | all four |
| Merge | after all workers end, in ticket order | as each worker passes, one at a time |
| Worker, guards, protected files | `run-worker.py` | the same `run-worker.py`, so the same guards |

They share the worker locks, so only one of them runs at a time. `dispatch` refuses while a dispatcher is alive, and
`scratch.py resume` leaves everything alone while it runs.

## 3. One-time setup per project

1. `.team.json` needs `"max_workers": N` (1 to 3) and `"setup": [...]` (the commands that install dependencies in a fresh
   worktree, the same as for `dispatch`). Optional: `"run_seconds": 1800` (a run longer than this is stopped).
2. The tickets of the feature need these header lines: `Status`, `Branch`, `Blocked by`, `Seam`, `Test command`
   (and `Repo` in a multi-repo workspace). The planner already writes them.
3. The repo must be clean and on the feature branch (the tickets' `Branch`). When `.scratch` is tracked in the project repo, the planner commits it between batches as usual; the dispatcher ignores changes under it.

## 4. The planner flow, step by step

1. **Grill and write the tickets** as usual. Give independent tickets Seams that do not overlap: the dispatcher refuses to
   run two tickets with overlapping Seam files at the same time, so overlap just means they run one after another.
2. **Check the plan without starting anything:**
   `python <kit>/kanban.py run <feature> --dry-run` prints `would start NN in slot K` for each ready ticket and writes
   nothing.
3. **Get the start line:** `python <kit>/kanban.py service <feature>` prints a `systemd-run --user ...` command. It does
   not install anything. Use that line, not a plain background job: Hermes kills plain background jobs, a transient
   systemd unit survives.
4. **Start it** by running the printed line.
5. **Watch:** `kanban.py board` (columns Todo, In progress, Review, Blocked, Done, with each ticket's last result and the
   last progress lines of a running worker) and `kanban.py status` (is the dispatcher alive, which workers, any pause).
   `--json` on both gives the same data as one JSON document.
6. **It ends by itself** with `KANBAN OK done: merged N, review N, blocked N`.
7. **Review as before.** Finished tickets sit in `review`, already merged into the feature branch. Run the batch review,
   mark the tickets `done` with `scratch.py status`, as you do today. Tickets in `blocked` need you (see section 6).

## 5. What happens to a ticket

```
todo --(ready, slot free)--> in-progress --(PASS + commit, merged)--> review --(planner)--> done
                                   |
                                   +--(failure)--> todo (first time, retried) --> blocked (second time)
```

A ticket is **ready** when its Status is `todo`, every `Blocked by` ticket is `done` **or `review`** (review means it is
already merged into the feature branch), its Seam files do not overlap a running ticket, it is on the same Branch as the
running group, and a slot is free.

| Situation | What the dispatcher does |
|---|---|
| PASS and a commit on the ticket branch | merges it (one at a time, in ticket order), marks `review` |
| PASS but no commit | marks `blocked`, "PASS but no commit" |
| Merge conflict | counts as a failure, keeps the branch |
| FAIL, crash, no result | first time: back to `todo` (retried); second time: `blocked`, with `Dispatcher: blocked after 2 failed runs: <last RESULT line>` added under `## Result` |
| `FAIL usage-limit` | no retry counted, ticket back to `todo`, the whole queue pauses until the reset time (parsed from `resets at 5pm` or `in 20 minutes`, otherwise 30 minutes) |
| Run longer than `run_seconds` | that slot is stopped, then treated as a crash |
| Worker gone, no result, on two checks in a row | marked `blocked: orphaned`, never silently re-run |

## 6. Controls

| Command | Effect |
|---|---|
| `kanban.py pause [--minutes N]` | nothing new starts for N minutes (default 60); running workers go on |
| `kanban.py resume` | removes the pause |
| `kanban.py stop` | graceful: starts nothing new, lets running workers finish, releases the lock |
| `kanban.py stop --now` | also stops the running workers; their tickets go back to `todo`; one `NOTE kanban force-stopped NN` in `runs.log` |
| `kanban.py run <feature> --drain` | no new starts; keeps merging until nothing runs, then exits |

`stop` only signals a process whose command line contains `kanban.py`. A stale lock for some other process is refused, not
killed.

A graceful stop can leave workers running ("draining"). They are not lost: the next `run` adopts them (their slot lock and
ticket are in-progress) and merges them when they finish.

**Edit tickets by hand only while it is stopped or paused.** While it runs, the dispatcher is the only writer that moves a
ticket between `todo`, `in-progress`, `review` and `blocked`.

## 7. `scratch.py resume` while the dispatcher runs

`resume` prints `RESUME OK dispatcher running (pid N, feature F)` and changes nothing: no locks removed, no branches
merged, no checkout. A dead dispatcher's lock is left for the next `run` to replace; `resume` only mentions it.

## 8. Limits in this version

- One feature at a time, one repo's Branch at a time.
- No automatic reviewers or fix tickets, and no writes from the board.
- The systemd template `kit/systemd/hdt-kanban@.service` is **unverified** (`systemd-analyze verify` fails). Use the
  `service` line.
- Not run for real: usage-limit and crash paths with a real `claude`, several repos in one workspace, Windows
  *(untested)*.
- The first real project run should be a small personal one, not a repo with other committers.

## 9. When something is off

| You see | Do |
|---|---|
| `KANBAN STOP a dispatcher is already running (pid N)` | `kanban.py status`; if you want it gone, `kanban.py stop` |
| `KANBAN STOP .team.json has no max_workers >= 1` / `no setup` | add them to `.team.json` |
| `KANBAN STOP <repo> has uncommitted changes` / `is not on <branch>` | commit or stash, check out the feature branch |
| `KANBAN STOP a worker is running (...) it is not one of this feature's` | a `scratch.py dispatch` or single `run-worker.py` is running; wait or `scratch.py stop` |
| A ticket is `blocked` | read its `## Result` (the `Dispatcher:` line has the last RESULT line) and the run's progress log under `runs/`; fix the cause, then set it back to `todo` with `scratch.py status` and the dispatcher picks it up. It has no fresh retry: the failure count comes from the ticket's earlier failed runs in `runs.log` (read from `kanban.py` `failure()`, not run), so one more failure blocks it again |
| Nothing starts, `status` says paused | `kanban.py resume`, or wait for the usage-limit reset time |
| `stop` says `draining (N workers still running)` | normal; they finish on their own, the next `run` merges them |
| `status` says running but nothing moves | `kanban.py board` shows the progress tail of each running ticket; `stop --now` if it is truly stuck |

## 10. Which kit version has it

Kit 0.18.0 (`kit/kanban.py`) and orchestrate skill 0.13.0 (step 4c). Check with `grep ^version skills/orchestrate/SKILL.md`
and `ls kit/kanban.py`. On an older kit: `git pull` in the hermes-dev-team repo, run `python kit/link-skills.py`, start a new
Hermes session. No kit folder at all: install it first (`LEARN.md`).

## 11. Quick start

```
cd <project repo root>
python <kit>/kanban.py run <feature> --dry-run     # what would start
python <kit>/kanban.py service <feature>           # prints the systemd-run line; run that line
python <kit>/kanban.py board                       # watch
python <kit>/kanban.py stop                        # when you want it to stop
```
