# Workers are `claude -p` again (grill, 2026-11-04; shipped in 2.0.0)

Goal: undo 1.0.0's `delegate_task` workers. Measured on the user's real projects (`runs.log`, all projects on this
machine): dev median 102 s on `claude -p` (390 runs) vs 133 s as a delegate child (58 runs); reviewer 45 s (183) vs
78 s (23), with delegate reviewer runs up to 15 min. A delegate child also blocks the planner's chat turn, and the
`skills-changed` guard failed 5 delegate runs, at least 4 of them caused by Hermes' own skill curator, not the worker.

## Decisions (user: "all recommended")

- D21 `claude -p` is the only runner. The delegate `prepare`/`finish` split is deleted; tag `v1.0.0` keeps it.
- D22 Restore selectively from `v0.18.3`: the Claude backend (`kit/backends.py`, Claude class only), `kit/roles/*.json`
  allow-lists, `kit/git-ro.py`, `docs/backend-claude.md`, tests `backend_smoke`, `permission_paths`, `parallel_real`.
  Not restored: `kanban.py` and its systemd unit, the headless hermes backend, the progress log, the hermes-dev-watch
  contract. A plain `git revert` of the 1.0.0 merge was rejected because it would bring those back.
- D23 Workers run detached from the planner's chat: `kit/bg.py start` (a `systemd-run --user --collect` unit, else a
  detached process; Hermes kills a chat's own background jobs), `bg.py wait <id>` returns within about 5 minutes with
  the RESULT lines or `BG RUNNING`. Parallel groups stay `scratch.py dispatch` (up to 3 `claude -p` workers in pool
  worktrees), started through `bg.py` too. Kanban, progress log and hermes-dev-watch stay removed.
- D24 `.team.json` goes back to `models` per role (`sonnet`/`haiku`/`opus`) plus `effort`. `options` and the hermes
  keys are refused with the exact replacement printed (same pattern as 1.0.0's D11). Defaults: dev, reviewer and
  researcher `sonnet`, docs `haiku`.
- D25 No `skills-changed` guard and no "no skill_manage/memory/browser" tool rules: a `claude -p` worker cannot reach
  Hermes skills or memory, and the allow-list blocks the rest.
- D26 Release 2.0.0 (the config format changes again), built in a scratch clone on `feat/claude-workers`; tests before
  and after, one real `claude -p` dev and reviewer run on a toy repo before handover; the user reviews the diff, the
  planner merges `--no-ff` locally, push only on the user's explicit "push".

## Build notes

- D22 named three 1.0.0 pieces to keep: `scratch.py merge`, `resume` finishing an interrupted run, the
  `main-checkout-changed` guard. All three exist only because a delegate child could not be waited on or confined:
  `dispatch` now waits and merges itself, `resume` again clears a dead launcher's lock and reports an orphaned worker
  (0.18.3 behaviour), and a `claude -p` worker in a pool slot has its cwd and allow-list on the slot, so it cannot write
  the main checkout. They were not carried over; the 0.18.3 code was restored as a whole instead.
