# Workers as Hermes `delegate_task` subagents (grill, 2026-11-02; shipped in 1.0.0)

Goal: replace `claude -p` (and the kit's other process-based runners) with Hermes `delegate_task` children started
by the planner in chat. Earlier rejection (2026-10-14, `docs/design.md` Rejected): one global `delegation.model`
(now solved: per-task `option` fast/standard/deep), a three-step planner flow (accepted below), child sessions in the
user's profile (still true).

## Decisions

- D1 Kanban dispatcher (`kit/kanban.py`, step 4c, systemd unit, kanban tests, `docs/kanban-guide.md`) is removed
  completely. What a worker is doing is seen through the subagent view instead.
- D2 Guards stay, through a split launcher: `run-worker.py prepare` (lock, git snapshot, prompt + option) -> planner
  calls `delegate_task` -> `run-worker.py finish` (same after-run guards, `runs.log` line, `RESULT` line).
- D3 Model per role comes from `.team.json` `"options"` (`fast`/`standard`/`deep`), e.g. dev/reviewer/researcher
  `standard`, docs `fast`. No kit fallback: a project without the new settings is refused by `prepare`, and the
  planner asks the user to adjust `.team.json` first.
- D4 The child reports through `output_schema` (verdict, sha, not_checked, cause, ...); `finish` reads the fields.
- D5 No live progress file: `runs/<id>.progress.log` and its writer go. (Open: whether "throw away" means the
  hermes-dev-watch app and its run-file contract too; see round 2.)
- D6 Claude code is deleted, not disabled: `Claude` backend, `roles/*.json` allow-lists, `git-ro.py`,
  `tests/permission_paths.py`, Claude parts of tests, `docs/backend-claude.md`. Git history keeps it.
- D7 No other changes in this branch. hermes-desk (the user's project, has a kanban view) is a later, separate grill.

## Facts (probe child, option `fast`, 2026-11-02)

- `option: fast` ran on claude-haiku-4-5 (per-task model works). 16 s, 2 API calls.
- cwd = the planner's cwd (the repo); `TERMINAL_CWD=/home/ngeng` is set in its env.
- No MEMORY/USER PROFILE block, no AGENTS.md/CLAUDE.md rules. It does get the full skills list.
- Tools: file, terminal, patch, execute_code, browser, web, vision, TTS, skill_view/skill_manage, tool_search/call.
  No delegate/clarify/memory.

## Decisions, round 2 (user: "go with recommendation")

- D8 hermes-dev-watch is retired; `docs/watch-contract.md` stops being a contract with another app (and
  `tests/watch_contract.py`, `tests/watch_parse.py`, `tests/fixtures/watch/` go). `runs.log` stays as the kit's own
  record (resume, costs).
- D9 Parallel groups (step 4b) move to delegate: `scratch.py dispatch` prepares one pool worktree per ticket, the
  planner sends one `delegate_task` call with up to 3 tasks, `finish` merges each PASS branch in ticket order.
- D10 The headless `hermes -p hdt-worker chat` backend is removed too. Delegate is the only runner: `backends.py` goes,
  with the `backend`, `models`, `hermes_profile`, `hermes_models`, `hermes_provider` keys.
- D11 `prepare` refuses when `.team.json` has no `"options"` entry for the role, or still has an old key (`backend`,
  `models`, `hermes_*`, `max_workers` without `setup`), and prints the exact replacement JSON.
- D12 A run cut off by `/new` or app close: resume reports `INTERRUPTED <ticket>` and runs `finish` on it (guards check
  what was left; dirty tree -> the usual commit-or-discard question). A lock older than the child's iteration cap is
  cleared automatically.
- D13 Researcher keeps a terminal (delegate cannot narrow tools); limits = prompt rules + the source-folder
  fingerprint guard. Accepted.
- D14 Release 1.0.0; tag `v0.18.3` (kit version) on the last version with the `claude -p` backend. CHANGELOG migration block: new `.team.json`
  keys, `git pull` + `link-skills.py --apply` on the Windows PC.
- D15 `prepare` pastes AGENTS.md/CLAUDE.md (with `@` imports) into the child's `context`, plus role prompt and repo
  root; `rules` and `rules_max_chars` keep working.
- D16 Workers are told never to use `skill_manage`, `memory` or the browser; `finish` fingerprints `~/.hermes/skills`
  (taken at prepare) and FAILs `guard:skills-changed` on any change.

## Decisions, round 3 (user: "agree to all")

- D17 No per-role turn caps; the global `delegation.max_iterations` (250) applies. Turn limits leave the role files and
  `.team.json`. D12's stale lock = older than 2 hours.
- D18 `runs.log` keeps its positions: `turns=<api_calls>`, duration, `None` for tokens and price. `costs.py` is removed
  (unused).
- D19 Tests: scripts cover `prepare`/`finish` with a faked child answer on real git repos (guards, refusals, merge);
  a hand checklist in `docs/testing.md` covers real chat runs (dev PASS, 4 chores, skills guard, interrupted run),
  run once before calling it done, results in `docs/benchmarks.md`.
- D20 Built directly by the planner on `feat/delegate-workers`, not by kit workers. Order: tag `v0.18.3-claude`;
  removals; `prepare`/`finish`; parallel groups; orchestrate skill + docs; tests + chat checklist. User reviews the
  diff before any commit.
