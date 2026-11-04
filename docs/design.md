# Design

Why the kit (2.0.0) works the way it does. Every mechanism below was run, not assumed; numbers are single samples on
toy repos (Claude Code 2.1.296 unless noted). Details for `claude -p`: [backend-claude.md](backend-claude.md).

## Rules for changes

- **One runner.** Workers are `claude -p`. Everything specific to it sits in `kit/backends.py`; the planner, tickets and guards do not depend on it.
- **Don't grow back.** When the planner skips a step, fix the instruction or accept the slip. Add code only when a slip has cost real work more than once. (0.13.7 turned single lapses into refusals and WARNs; 0.14.0 removed them.)

## Shape

- **Planner** = the Hermes chat session the user talks to. It grills, writes `CONTEXT.md`, ADRs and tickets, dispatches workers, verifies, and never writes product code. Planning uses Matt Pocock's skills as installed (the 0.2.0 rewrites dropped the scenario and code-cross-check steps and shadowed the upstream names); `orchestrate` adds only the ticket fields (`templates/ticket-header.md.template`) and is the only skill the kit links into Hermes.
- **Workers** = headless runs started by `kit/run-worker.py`: dev (one ticket), reviewer (one feature), docs (after review), researcher (read-only, outside the product repo). Serial by default: one worker at a time, one branch. Since 0.17.0 a project may set `max_workers` > 1: `scratch.py dispatch` then runs independent dev tickets of one batch in pool worktrees, merged into the feature branch by the kit ([open/parallel-workers.md](open/parallel-workers.md)).
- **Channel** = files in `.scratch/<feature>/` (untracked by the product repo, their own small git repo) and the git branch. The planner reads one `RESULT` line per run and a few git commands, never a transcript.
- **The user merges** (`git merge --no-ff`) the feature branch into main. No agent merges or deletes a feature branch or main; the only exception is `scratch.py dispatch` merging its own local ticket branches (`<feature>--NN`) into the feature branch and deleting them with `git branch -d` (0.17.0). Workers never push; the planner pushes only on "switching".
- **Grill before build**; vertical-slice tickets with one test per acceptance criterion at a pre-agreed seam, one commit per dev run (until 0.15.0 ONE test: UI criteria then went untested and came back as review findings). **Review before merge**, on two separate axes (Spec, Standards) so one cannot hide the other: a reviewer run after the merge saw an empty diff.

## Runner and adapter

| Rule | Why (measured) |
|---|---|
| `kit/backends.py` has one class (`Claude`) with `ready`, `setup_error`, `command`, `progress`, `result`; `run-worker.py` calls only those | Everything specific to `claude -p` stays in one file. Only `claude -p` gets an allow-list that blocks a call before it runs. |
| `tests/backend_smoke.py`: one trivial ticket through the real launcher, on demand (calls a model) | A change to the launcher or adapter is checked end to end on a new machine. |
| Claude: deny by default (`defaultMode: dontAsk`) + per-role allow-list | A string-matching hook was bypassed with `python -c subprocess`; the allow-list blocked every bypass, compound commands included. |
| Claude: `--setting-sources ""` and `--agents <file>` | A repo's `.claude/settings.json` cannot widen a worker (a hostile `Bash(*)` was ignored). Role prompts come from the kit. |
| Claude: every path rule absolute (`anchor_rules`), shell kept in the repo between calls | `./` and `**/` follow the worker's `cd`: 27 of 36 dev runs on one feature could not write their Result after a `cd ui && vitest` test command; a root package.json became editable. |
| Size lanes (`references/lanes.md`): trivial = planner fix inside `scratch.py check-run`; small = quick lane reviewed with the next batch; large = full lane. Feature code never goes to a delegated subagent | Every change cost a ticket, dispatch and review; the planner fixed small things directly anyway, without guards. `check-run` reuses the launcher's guards, after the fact only. A subagent has no allow-list, runs on the planner model and leaves no runs.log line. |
| Step 6 verification goes to a read-only planner subagent (Hermes `delegate_task`, Claude Code Task/Agent) that returns one `PASS:`/`FAIL:`/`ASK:` line; the planner keeps that line, checks the tree is clean and HEAD unmoved, and reads the diff only to explain an ASK or FAIL (D61, 2.1.0) | About 30% of a long Hermes planner chat's context was the planner's own verification reads (`git show`, `git diff`, source, `tickets.py` tables, test output). Not the rejected `delegate_task` workers: the subagent writes no code. Hermes cannot narrow a child's tools, so the brief forbids edits; a forbidden list in the prompt held in a `delegate_task` probe (`benchmarks.md`). Works the same with Claude Code as planner. |
| Planner starts a `terminal(background=true, notify=true)` watcher (`bg.py wait <id> --timeout 21600`) after every `bg.py start` (2.1.1) | The systemd unit notifies nobody and `bg.py wait` only blocks the planner's own call, so a planner that ended its turn never learned the worker finished. The watcher is disposable: killing it leaves the worker running. |
| Full suites: `.team.json` `test` may list suites with a `cwd`; workers run `kit/suites.py`, which runs every suite and judges failures against `baseline.md` by test name | An `a && cd ui && b` chain stopped at two baseline errors and the UI suite never ran in any review of one feature. |
| Claude: grant writes with `Edit(path)`, never `Write(path)`; deny beats allow | `Edit` rules govern both tools; a `Write(...)` allow rule granted nothing. |
| Every role prompt ends with the tool rules (`_tool-rules.md`) | Without "one command per Bash call, no cd/&&", workers chained commands, were denied and quit; with them, 0 denials. |
| `--test-cmd` adds one exact command to the allow-list and names it in the prompt | Without the prompt line, the reviewer ran a narrower command and was denied. |
| No Co-Authored-By trailer, ever: forced `includeCoAuthoredBy: false` and empty `attribution` | The trailer appears by default and is gone with either key. |
| Models: dev, reviewer and researcher on Sonnet, docs on Haiku (`models` per role, `effort`) | Haiku did the docs job correctly in 7 s for about $0.006 list price. |
| The launcher pastes the project's `CLAUDE.md` (and its `@` imports) into the prompt, capped by `rules_max_chars` (40000), or a short `rules` file | With `--setting-sources ""` a worker does not see `CLAUDE.md` (codeword test: NONE vs found with `project`); an `@../other-repo/CLAUDE.md` import points where the worker may not read. With the paste, a worker followed a rule that lived only in the imported file. A 150k-char CLAUDE.md was pasted into every run; cut text ends with a pointer, and the worker Grepped the full file for a line past the cut. |
| Glob patterns start with `**/`; one empty Glob never proves absence | Windows: Glob with a `path` and `benchmarks/**/*` returned nothing for an existing folder; `**/benchmarks/**` found it. The researcher had called existing results absent. |

## Guards

After every run, `run-worker.py` compares a snapshot and turns a worker PASS into `FAIL guard:<name>`.

| Guard / rule | Why (measured) |
|---|---|
| Verdict = the last `RESULT:` line, plus any non-zero exit | `claude -p` exits 0 when a worker gives up; only the turn cap exits 1. |
| Main unmoved, branch unchanged, old HEAD still an ancestor (`history-rewritten`); refuses to start on main/master | `git commit -m x --amend` matched the allowed commit prefix; the guard caught it. |
| `test-config-changed`: the dev may not edit what decides what the test command runs (`package.json`, lockfiles, jest/vitest/tsconfig, `.team.json`, plus `.team.json` `protected`); Claude also gets deny `Edit(**/<name>)` | Stops making tests pass by changing what runs. The deny blocked root and nested files for Edit and Write; the guard caught a committed change. Not protected by default: `conftest.py`, jest `setupFiles`, scripts a test script calls, `playwright.config.*` and its server script: add them to `protected`. |
| `pushed:<remote>`: `git ls-remote` before and after; counts refs added or moved to commits in the worker's clone, and remote branches deleted whose tracking ref vanished or which are local branches. An unreadable remote is skipped | A push was the one forbidden action nothing else caught (a push during a run turned PASS into `FAIL guard:pushed:origin`). In a shared repo a teammate's push failed an honest worker, hence "commits it has" (no fetch during a run). 0.12.0 checked moved refs only and `git push --delete` passed (`tests/push_guard.py`). |
| `git log/diff/show` only through `kit/git-ro.py` (refuses writing flags, runs with `teamcfg.GIT_SAFE` + `--no-ext-diff --no-textconv`); fingerprint of hooks and git config | `Bash(git log:*)` allowed `git log --output=<path>` with 0 denials and wrote into `.git/hooks/` despite `Edit(./.git/**)`. A repo's own config ran a program on plain `git log` (gpg.program via a signed commit) and on `git diff` (diff.external, core.fsmonitor, textconv); with the overrides none ran (git 2.53 Windows, `tests/git_guard.py`). |
| Researcher: `source-changed` (every file of the source, `.git` included, fingerprinted without running git; build/dependency folders like `node_modules` skipped, `research_skip` adds more) and `topic-folder-changed` (anything but the findings file) | The allow-list already blocks writes; the guard also catches a write through a test command. Running no git on the source avoids its config running code. Skipping build output: atlas went from 124,214 files / 20 s per snapshot to 1,441 / 0.1 s; a write inside a skipped folder is not caught. |
| `Test command` must be cheap and safe; anything needing a server, a DB or the user's say-so is a planner-run `Verify:` | A worker cannot ask, and a project rule may forbid DB operations without the user's yes in that turn. |
| Browser projects: headless Playwright as the test command, its `webServer` starting the app | Workers load no MCP servers. Dev wrote a browser test, red then green, PASS in 35 s; reviewer PASS in 15 s; 0 denials, no launcher change. Visual/UX checks stay with the planner. |

## State, resume and switching

| Rule | Why (measured) |
|---|---|
| No handoff file: a new session (or machine) rebuilds state from tickets, `batches.md`, reviews, `runs.log`, git and a per-feature `decisions.md` (one line per chat-made decision); the scratch folder is its own git repo | A summary goes stale (the old handoff note grew to 104 lines, partly obsolete). `scratch.py resume` reads an `in-progress` ticket three ways: commit ending `(<ticket file>)` = verify, none + clean tree = redispatch, dirty tree = ask (never reset or stash). Two clones as two machines: 33/33 checks, real dev runs. |
| The planner pushes only on "switching": the scratch repo and feature branch(es), never main, never `--force`, never mid-run or with uncommitted product changes. `resume` pulls fast-forward only | The user moves machines mid-feature; routine pushes were not wanted. Diverged scratch -> `RESUME STOP`, no merge commit; a rejected push is reported, not forced. |
| Worker lock (`runs/worker.lock`) holds launcher and worker pids, alive while either runs; `scratch.py stop` kills the worker tree and logs `force-stopped` | "One worker at a time" was a prompt rule a second session could break. Second dispatch and a mid-run switch refused; stop gave `FAIL force-stopped`, ticket stayed `in-progress`, the other machine's resume said REDISPATCH. A SIGKILLed launcher left its worker running and a launcher-only lock read as stale (Linux). |
| Every run logs one `runs.log` line: turns, seconds, four token fields (uncached in, cache write, cache read, out), list price, denials | Enough to see cost and drift without transcripts. `input_tokens` alone misleads: a Haiku dev run had 24 while cache write was 33.5k and cache read 366.8k. |
| Dev notes: workers append to `.scratch/dev-notes.md`; the planner promotes approved facts to `docs/dev-notes.md` with the user's say-so | Later runs reuse what earlier runs learned without unreviewed text in the repo. |
| Shared repos: nothing team-visible but the feature's code; main comes in only between batches, by merge; a pre-feature `baseline` | Teammates may use no tests or another workflow. No history rewrite keeps guards and pushed branches valid; the merge commit is the next batch's base, so reviews never include teammates' code; the baseline keeps main's red tests from failing workers (`tests/shared_repo.py`, 24/24). |
| Multi-repo workspace: one `.team.json` with a `repos` map, scratch outside the repos, `Repo:` per ticket, batches inside one repo | The scratch folder is granted by absolute-path rules (`Read(//abs/**)`), which worked without `--add-dir` while the sibling repo stayed denied. |
| Dev: reuse first (codebase, stdlib, installed dependency, one line, minimum) with an explicit search-before-writing step; scope stays the planner's | With "reuse first" alone the dev still hand-rolled money formatting next to an existing `fmt_money`; the search step fixed it. Ponytail's "does it need to exist" rung was dropped: a dev that cannot ask would drop what the user wanted. |
| Reviewer reads beyond the diff (callers of changed symbols, about 10 files) and checks reuse; 35 turns | A renamed field broke an untested file outside the diff while every test passed; the reviewer caught it at `report.py:2` and flagged a missed helper by file:line. |
| Dev Result and review end with `Not checked:`; the planner shows it | The user sees each ticket's gaps without transcripts. |

## Researcher

- A read-only worker: questions or a **survey** ("map this repo", "find every place X is done"), approved first; findings cite path:line and end with a required `## Coverage` section (folders read, folders skipped); the planner recommends. Why: vague "is X worth learning" asks got vague answers, and a fresh worker with a full context budget reads more than the planner. Orchestrate dispatches it when an answer needs more than a few files or a whole folder.
- Only Read/Glob/Grep plus `Edit(<findings file>)`, no Bash, backed by the `source-changed` and `topic-folder-changed` guards. A source whose AGENTS.md told agents to write files and commit was reported, not obeyed (`tests/researcher.py`).
- The launcher runs no git on a research source (no `history.txt`); `GIT_SAFE` remains only for `git-ro.py`.
- Home: `HDT_RESEARCH_HOME`, else `~/research`; clones in `src/` (git-ignored, kept until done). Same default on every OS; OneDrive-redirected folders, AppData (pruned) and paths with spaces break git or allow-list rules.

## Planner trail

`scratch.py status <feature> NN <status> [--verify "PASS|FAIL|SKIP: <text>"]` and `scratch.py decide` each write one
`planner` line in `runs.log` (`action=status|decide`), same shape as a worker's.
Why: the user missed planner reports that lived only in chat, and skill rules alone had been skipped, so the record is
a side effect of commands the planner must run anyway. They refuse only a malformed `--verify`, an unknown status or
an unknown ticket. No progress log; the scratch repo's git log is the timeline. A verifier role was rejected: running
the page cannot be held by an allow-list, and logging does not need it. Test: `tests/planner_log.py`.

## runs.log

Every run appends one line. Positions 1-6 are fixed: stamp, role, model, verdict, turns, secs. Later keys are read by
name, so new ones are appended at the end. `teamcfg.RUN_FORMAT` is written as `fmt=` and bumped when an old reader
would misread a line. The kit's own readers are `resume` and `planner_log`; there is no outside reader any more
(the hermes-dev-watch contract was removed in 2.0.0). Test: `tests/planner_log.py`.

## Detached runs (`kit/bg.py`)

| Rule | Why |
|---|---|
| `bg.py start` runs `run-worker.py` or `scratch.py dispatch` as a `systemd-run --user --collect` unit when user systemd exists, else as a detached process; output goes to `runs/bg-<id>.out`; `wait` polls, `status` lists | Hermes kills a chat's background jobs (a `/new`, a closed app, a timed-out tool call), so a worker started with `terminal(background=true)` could die with the planner's turn. A unit outlives the chat. |
| The unit's shell writes the exit code to `runs/bg-<id>.rc` | A `--collect` unit is gone when it ends, so systemd can no longer tell the exit code. |

## Measured (end to end, toy repo)

| Run | Result |
|---|---|
| dev, Sonnet, one ticket | PASS, 18 turns, 22 s, 0 denials; test seen red then green; one commit, no trailer |
| reviewer, Sonnet | PASS, 11 turns, 17 s, wrote `review-1.md` with both axes |
| docs, Haiku | PASS, 15 turns, 27 s; CHANGELOG entry and drift list |
| forbidden actions (edit CONTEXT.md, edit an ADR, write `.claude/`, checkout main, push, amend, reset --hard, write outside the repo) | 8 of 8 denied; HEAD and branch unchanged |
| `git commit -m x --amend` | allowed by the prefix rule, caught by the launcher guard: `FAIL guard:history-rewritten` |

## Known holes

- The test command executes code the dev wrote, so a dev can run anything through it. Review before merge is the defence.
- Protected-file matching is by file name; a test runner configured elsewhere needs that file in `.team.json` `protected`.
- Amend is detected after the fact, not prevented; recovery is `git reflog`.
- `pushed`: a deletion by URL of a teammate's branch is not caught.
- `runs.log` prices are list price; a subscription seat is billed differently.
- `resume` matches a finished ticket by a commit subject ending `(<ticket file>)`; a hand commit with that ending, or a dev that changed the format, misleads it (verification still runs).
- `runs/` (result JSON, background job output) stays on the machine that ran the worker; the scratch repo carries only tickets and `runs.log`.

## Rejected

- **Four Hermes profiles with per-profile plugin installs:** no enforcement in headless runs, about 5x slower, setup that touched each profile's environment.
- **Kanban dispatcher** (0.18.0): removed in 1.0.0, not restored in 2.0.0.
- **Headless Hermes and `delegate_task` workers:** tried in 0.10 to 1.0.0 and dropped in 2.0.0. Measured on real projects (runs.log medians): `claude -p` dev 102 s (390 runs) vs delegate dev 133 s (58 runs); reviewer 45 s (183) vs 78 s (23), with a tail up to 15 min. Delegate children also blocked the planner's chat turn, and the skills-changed guard misfired on 5 runs because of Hermes' own skill curator. Tag `v1.0.0` keeps the delegate version. This rejects subagents as WORKERS that write feature code; step 6's read-only verification subagent (2.1.0) is a different thing.
- **String-matching PreToolUse hooks:** bypassed in the first test.
- **Installer scripts:** a clone plus linked skill folders is enough.
- **A handoff/summary file for resume:** goes stale; state is rebuilt from the files plus `decisions.md`.
- **Committing `.scratch/` on the feature branch:** puts agent files in product history; a separate scratch repo does not.
- **Routine pushes after every ticket:** pushes only on "switching".
- **Planner refusals, WARNs, `fix`/`rewrite`/`--step`:** policing, not logging (thin-down 2026-10-17).
- **`history.txt` for research sources:** made the launcher run git on hostile sources.
- **Copying a local source before research** (2026-10-17): one worker at a time, and research happens while the user is grilling, not editing; build output is skipped. Left: an editor's background git touching `.git/` FAILs `guard:source-changed:.git/...`, a visible one-minute re-run. Revisit if it costs real work twice.
- **`kit/costs.py`:** list price is not subscription billing.
- **`claude-p-role-workers` as a skill:** vendor-specific; now `docs/backend-claude.md`.
- **Sandbox/container mode:** considered, skipped.
