# Design: thin down the kit (0.14.0)

Status: built 2026-10-17 as kit 0.14.0, merged to main the same day (Windows check: suite, both backend smoke tests
5/5, `researcher.py` 18/18). Follow-up 0.14.1: the researcher's source guard skips build folders (see CHANGELOG).

## The problem

Kit 0.13.7 is about 3,400 lines (kit, roles, docs), 40 design decisions and a 35 KB CHANGELOG, written in six days.
Recent weight came from turning single lapses into mechanisms (planner-log refusals, WARNs) rather than from features
in use. Usage across four projects' `runs.log` (2026-10-17): dev 226, reviewer 106, docs 11, researcher 2, planner
lines 0, `backend=hermes` 0. The orchestrate skill is re-read every planner session, so its length is a cost too.

## Rule for this and later changes

- **Agnostic by default.** The skill and kit must work without Claude. Everything Claude-specific sits behind one
  backend adapter; the planner, tickets, guards and the watch contract never depend on which backend ran.
- **Don't grow back.** When the planner skips a step, fix the instruction or accept the slip. Add code only when a
  mistake has cost real work more than once.

## Decisions

1. **Backends.** Keep the Hermes backend (insurance when Claude is unavailable; 0 runs is not a reason to cut) and the
   Claude allow-lists. Move Claude-specific code in `run-worker.py` (`--agents`, `--setting-sources`, allow-list
   JSON, stream-json parsing, attribution keys) behind an adapter with four parts: build the command, apply limits,
   parse progress, return the final result. Post-run guards stay as they are: they are the only enforcement on every
   backend. Add an on-demand smoke test per backend (one trivial ticket), so the unused path is known to work before
   it is needed. A sandbox (container) mode was considered and skipped.
2. **Planner log: status and decide lines only.** `scratch.py status` (with `--verify "PASS|FAIL|SKIP: <text>"`) and
   `decide` each write one `planner` line in `runs.log`. Cut: the refusals (no `review`/`done` without `--verify`,
   no FAIL without a fix ticket, no `done` with an open fix ticket), the WARNs in `resume` and `tickets.py`
   (including rewrite detection), the `fix` and `rewrite` commands, `--step`, and planner progress logs. Logging was
   the need; policing the planner was not. The scratch repo's git log remains a free timeline.
3. **Researcher.** Keep it: a fresh worker with a full context budget reads more thoroughly than the planner.
   - Survey mode beside the question list (e.g. "map this repo", "find every place X is done"); findings end with a
     coverage list: folders read, folders skipped.
   - Generic orchestrate trigger, no project names: dispatch the researcher instead of searching yourself when the
     answer needs reading more than a few files, or a whole repo or folder.
   - Drop `history.txt`: the launcher no longer runs git on the source, which removes most of the `GIT_SAFE` use on
     research sources.
   - Allowed on the Hermes backend, with a post-run guard: source HEAD unchanged, `git status` clean, only the
     findings file written. Known gap (as for dev on Hermes): a write outside both folders is not caught.
4. **Costs.** Delete `kit/costs.py` (and `--backfill`) and orchestrate's "Cost log" section. `runs.log` is written by
   `run-worker.py` and `plannerlog.py` and does not change. Totals, if ever wanted, go in hermes-dev-watch.
5. **Skill `claude-p-role-workers` becomes docs.** Move it to `docs/backend-claude.md` (reference for maintaining the
   Claude adapter); remove the skill so orchestrate is the only skill linked into Hermes. Remove rules 17 (researcher
   refuses Hermes) and 21 (planner refusals/WARNs); update 18 and 19 for decision 3. Add a short
   `docs/backend-hermes.md` (prompt-only limits, which guards apply). `references/changing-skills.md` points to both.
6. **`docs/design.md` describes the kit as it is now**, grouped by topic (shape, backends and adapter, guards, state
   and switching, researcher, watch contract), with the measured reason beside each rule. Each reversed decision gets
   one line under "Rejected". `#N` references in skills and docs become section names; old CHANGELOG entries stay.
7. **Orchestrate SKILL.md to about 90 lines** (from 136). Remove the Cost log section; step 3 loses `rewrite` (a ticket
   edited after dispatch gets a `decide` line); step 6 becomes "run the checks, then `scratch.py status ... --verify`;
   a gap becomes a fix ticket from the template plus a `decide` line"; Claude-only pitfalls move to
   `docs/backend-claude.md`; `research.md` covers survey mode, the trigger and the Hermes backend; one line on the
   backend smoke test. Steps, lanes, resume, switching, "never merge or push" and "the user decides on a dirty tree"
   stay.
8. **Tests and rollout.**
   - `planner_log.py`: status and decide lines only. `git_guard.py`: keep dev/reviewer read-only git, drop
     research-source cases. `researcher.py`: survey mode, coverage list, Hermes run with the source guard.
     `watch_contract.py` + fixtures: regenerate; planner lines keep their shape with `action=status|decide`, `fmt`
     stays 1. New `backend_smoke.py` (on demand, calls a model). Guard, push, shared-repo and resume tests unchanged.
   - hermes-dev-watch needs no change; verify by running its parser (`tests/watch_parse.py`) on the new fixtures.
   - Build on branch `thin-down` as kit 0.14.0, CHANGELOG entry marked **watch:**. Run the suite, show
     `git diff --stat` and before/after line counts. Nothing is committed without the user's yes; the user merges and
     removes the `claude-p-role-workers` symlink from `~/.hermes/skills`. On the Windows PC: pull, run the smoke test
     once.

## Supersedes

Parts of [planner-log.md](planner-log.md): decisions 8 (`fix`, `rewrite`, `--step`), 9 (refusals and WARNs) and the
planner progress log. Its line shape (decision 4) and verdict wording (decision 5) stay for `status` and `decide`.
