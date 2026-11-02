# Design

Why the kit works the way it does. Every mechanism below was run, not assumed; numbers are single samples on toy
repos. Since 1.0.0 every worker is a Hermes `delegate_task` child ([backend-delegate.md](backend-delegate.md),
decisions in [open/delegate-workers.md](open/delegate-workers.md)); rows marked *(Claude era)* were measured on the
`claude -p` backend that tag `v0.18.3` still has, and are kept for the reason they record.

## Rules for changes

- **Guards over permissions.** Nothing blocks a delegate child while it runs, so every limit is a brief rule plus an after-run guard in `finish`.
- **Don't grow back.** When the planner skips a step, fix the instruction or accept the slip. Add code only when a slip has cost real work more than once. (0.13.7 turned single lapses into refusals and WARNs; 0.14.0 removed them.)

## Shape

- **Planner** = the Hermes chat session the user talks to. It grills, writes `CONTEXT.md`, ADRs and tickets, dispatches workers, verifies, and never writes product code. Planning uses Matt Pocock's skills as installed (the 0.2.0 rewrites dropped the scenario and code-cross-check steps and shadowed the upstream names); `orchestrate` adds only the ticket fields (`templates/ticket-header.md.template`) and is the only skill the kit links into Hermes.
- **Workers** = `delegate_task` children the planner starts, each prepared (`run-worker.py prepare`: brief, lock, snapshots) and checked (`finish`: guards, `runs.log`, RESULT): dev (one ticket), reviewer (one feature), docs (after review), researcher (read-only, outside the product repo). Serial by default: one worker at a time, one branch. A project may set `max_workers` (at most 3): `scratch.py dispatch` prepares independent dev tickets of one batch in pool worktrees, the planner starts them in one `delegate_task` call, and `scratch.py merge` merges them into the feature branch ([open/parallel-workers.md](open/parallel-workers.md)).
- **Channel** = files in `.scratch/<feature>/` (untracked by the product repo, their own small git repo) and the git branch. The planner reads one `RESULT` line per run and a few git commands, never a transcript.
- **The user merges** (`git merge --no-ff`) the feature branch into main. No agent merges or deletes a feature branch or main; the only exception is `scratch.py merge` merging its own local ticket branches (`<feature>--NN`) into the feature branch and deleting them with `git branch -d`. Workers never push; the planner pushes only on "switching".
- **Grill before build**; vertical-slice tickets with one test per acceptance criterion at a pre-agreed seam, one commit per dev run (until 0.15.0 ONE test: UI criteria then went untested and came back as review findings). **Review before merge**, on two separate axes (Spec, Standards) so one cannot hide the other: a reviewer run after the merge saw an empty diff.

## Workers

| Rule | Why (measured) |
|---|---|
| Workers are `delegate_task` children prepared and finished by `run-worker.py` (1.0.0) | The user works in Hermes; a child shows live in the subagent view, needs no CLI login or worker profile, and takes a model `option` per task (the 2026-10-14 rejection was one global `delegation.model`). Real dev run: PASS, Sonnet, 31 s, 7 API calls. |
| `.team.json` `options` per role is required; `prepare` refuses without it and with old backend keys, printing the replacement | A child without an option runs on the planner's model (Opus); a silent default would hide that. |
| The goal starts `[hdt] <run-id>:`; finish and resume find the child in Hermes' `cache/delegation/live/*/manifest.json` | The kit cannot see the chat. The manifest says `running` until the whole call ends, so a group reads each child's transcript tail (`end status=`). |
| The brief is a file (`runs/<run-id>.brief.md`); the task's goal and context only point at it | Keeps the delegate call short and the planner's context small; the brief is what a reviewer of a run reads. |
| Answer via `output_schema` (verdict, summary, sha, not_checked, cause); finish also reads a fenced JSON or a raw `RESULT:` line | Hermes validates and retries once; a malformed answer still gets a verdict instead of a crash. |
| Every brief ends with `kit/roles/_tool-rules.md`; runs are tagged `enforce=prompt-only` | A delegate child with the forbidden list refused 4/4 chores (2026-10-14) and 5/5 incl. a skill edit (2026-11-02); without the list it did all 4. |
| The brief gives an absolute repo root and says `cd` first | A child starts in the planner's folder with `TERMINAL_CWD` set to the home folder (probe 2026-11-02). |
| `prepare` pastes AGENTS.md/CLAUDE.md (and `@` imports), capped by `rules_max_chars` (40000), or a short `rules` file | A child loads no project rules (probe). *(Claude era)* a worker followed a rule that lived only in an imported file; a 150k-char CLAUDE.md was cut with a pointer and the worker searched the full file. |
| No per-role turn cap; the global `delegation.max_iterations` applies; a run silent for 2 h counts as interrupted | `delegate_task` takes no per-task cap. |
| Size lanes (`references/lanes.md`): trivial = planner fix inside `scratch.py check-run`; small = quick lane reviewed with the next batch; large = full lane. Feature code never goes to a child without `prepare` | Every change cost a ticket, dispatch and review; the planner fixed small things directly anyway, without guards. `check-run` reuses the guards, after the fact only. |
| Full suites: `.team.json` `test` may list suites with a `cwd`; workers run `kit/suites.py`, which runs every suite and judges failures against `baseline.md` by test name | An `a && cd ui && b` chain stopped at two baseline errors and the UI suite never ran in any review of one feature. |
| The brief names the one test command a worker may run | *(Claude era)* without the line, the reviewer ran a narrower command. |
| No Co-Authored-By trailer: a tool rule (`git commit -m "<message>"`, no trailer) | Hermes adds none itself; the rule keeps a model from adding one. |
| Glob/search patterns: one empty search never proves absence | *(Claude era)* Windows Glob missed an existing `benchmarks/` folder; the researcher had called existing results absent. |

## Guards

`run-worker.py finish` compares the snapshot `prepare` took and turns a worker PASS into `FAIL guard:<name>`.

| Guard / rule | Why (measured) |
|---|---|
| Verdict = the child's schema `verdict`, plus the child's status from the manifest (`interrupted`, `failed` -> FAIL even with a PASS answer); no answer -> `FAIL no answer` | A child's summary is a self-report; an interrupted child leaves partial work. |
| `skills-changed`: the Hermes skills folder (team skills followed through their links, dot files skipped) fingerprinted at prepare and finish | A child has `skill_manage`, and `orchestrate` is linked from this repo, so a worker could edit the planner's own skill. Dot files (curator ledger, hub locks) change on their own. The planner must not edit skills while a child runs. |
| `main-checkout-changed` (parallel slots): the main checkout's HEAD and `git status` at prepare and finish | A child's terminal starts in the planner's folder, not its slot; a write there would land in the feature branch's tree. The kit cannot tell which child of a group did it, so every open run of the group FAILs. |
| Main unmoved, branch unchanged, old HEAD still an ancestor (`history-rewritten`); refuses to start on main/master | *(Claude era)* `git commit -m x --amend` matched the allowed commit prefix; the guard caught it. |
| `test-config-changed`: the dev may not edit what decides what the test command runs (`package.json`, lockfiles, jest/vitest/tsconfig, `.team.json`, plus `.team.json` `protected`); the brief lists them | Stops making tests pass by changing what runs. The guard caught a committed change. Not protected by default: `conftest.py`, jest `setupFiles`, scripts a test script calls, `playwright.config.*` and its server script: add them to `protected`. |
| `pushed:<remote>`: `git ls-remote` before and after; counts refs added or moved to commits in the worker's clone, and remote branches deleted whose tracking ref vanished or which are local branches. An unreadable remote is skipped | A push was the one forbidden action nothing else caught (a push during a run turned PASS into `FAIL guard:pushed:origin`). In a shared repo a teammate's push failed an honest worker, hence "commits it has" (no fetch during a run). 0.12.0 checked moved refs only and `git push --delete` passed (`tests/push_guard.py`). |
| Fingerprint of hooks and git config (`git-hooks-or-config-changed`) | *(Claude era)* `git log --output=<path>` wrote into `.git/hooks/` with 0 denials. A worker with a terminal can write hooks or config directly; the fingerprint catches it after the run (`tests/git_guard.py`). The `git-ro.py` wrapper went with the allow-list in 1.0.0. |
| Researcher: `source-changed` (every file of the source, `.git` included, fingerprinted without running git; build/dependency folders like `node_modules` skipped, `research_skip` adds more) and `topic-folder-changed` (anything but the findings file) | The only thing that holds the read-only role, since a child always has a terminal; running no git on the source avoids its config running code. Skipping build output: atlas went from 124,214 files / 20 s per snapshot to 1,441 / 0.1 s; a write inside a skipped folder is not caught. |
| `Test command` must be cheap and safe; anything needing a server, a DB or the user's say-so is a planner-run `Verify:` | A worker cannot ask, and a project rule may forbid DB operations without the user's yes in that turn. |
| Browser projects: headless Playwright as the test command, its `webServer` starting the app; the brief forbids the browser tool | *(Claude era)* dev wrote a browser test, red then green, PASS in 35 s; reviewer PASS in 15 s. Visual/UX checks stay with the planner. |

## State, resume and switching

| Rule | Why (measured) |
|---|---|
| No handoff file: a new session (or machine) rebuilds state from tickets, `batches.md`, reviews, `runs.log`, git and a per-feature `decisions.md` (one line per chat-made decision); the scratch folder is its own git repo | A summary goes stale (the old handoff note grew to 104 lines, partly obsolete). `scratch.py resume` reads an `in-progress` ticket three ways: commit ending `(<ticket file>)` = verify, none + clean tree = redispatch, dirty tree = ask (never reset or stash). Two clones as two machines: 33/33 checks, real dev runs. |
| The planner pushes only on "switching": the scratch repo and feature branch(es), never main, never `--force`, never mid-run or with uncommitted product changes. `resume` pulls fast-forward only | The user moves machines mid-feature; routine pushes were not wanted. Diverged scratch -> `RESUME STOP`, no merge commit; a rejected push is reported, not forced. |
| Worker lock (`runs/worker.lock`) from prepare to finish, holding the run id; its state comes from the child's manifest and transcript: running, prepared (< 10 min, no child yet), done or interrupted. A second prepare is refused while any lock exists; `resume` finishes done/interrupted runs as `FAIL interrupted`; `stop` refuses while the child runs (the planner stops it first) and finishes the run as `force-stopped` | "One worker at a time" was a prompt rule a second session could break. A child dies with `/new` and the kit cannot kill one. Real run: stopping a child mid-ticket, then `resume`: `INTERRUPTED 03-neg.md`, guards checked, REDISPATCH. |
| Every run logs one `runs.log` line, positions 1-6 unchanged (`turns=` holds API calls), tokens and price `None`, plus `backend=delegate enforce=prompt-only` and `transcript=<path>` | A delegate result reports API calls and duration only. The transcript path lets the user read a run Hermes prunes after 7 days. |
| Dev notes: workers append to `.scratch/dev-notes.md`; the planner promotes approved facts to `docs/dev-notes.md` with the user's say-so | Later runs reuse what earlier runs learned without unreviewed text in the repo. |
| Shared repos: nothing team-visible but the feature's code; main comes in only between batches, by merge; a pre-feature `baseline` | Teammates may use no tests or another workflow. No history rewrite keeps guards and pushed branches valid; the merge commit is the next batch's base, so reviews never include teammates' code; the baseline keeps main's red tests from failing workers (`tests/shared_repo.py`, 24/24). |
| Multi-repo workspace: one `.team.json` with a `repos` map, scratch outside the repos, `Repo:` per ticket, batches inside one repo | The brief names the scratch folder absolute; `prepare` refuses a ticket of another repo. |
| Dev: reuse first (codebase, stdlib, installed dependency, one line, minimum) with an explicit search-before-writing step; scope stays the planner's | *(Claude era)* with "reuse first" alone the dev still hand-rolled money formatting next to an existing `fmt_money`; the search step fixed it. Ponytail's "does it need to exist" rung was dropped: a dev that cannot ask would drop what the user wanted. |
| Reviewer reads beyond the diff (callers of changed symbols, about 10 files) and checks reuse | *(Claude era)* A renamed field broke an untested file outside the diff while every test passed; the reviewer caught it at `report.py:2` and flagged a missed helper by file:line. |
| Dev Result and review end with `Not checked:`; the planner shows it | The user sees each ticket's gaps without transcripts. |

## Researcher

- A read-only worker: questions or a **survey** ("map this repo", "find every place X is done"), approved first; findings cite path:line and end with a required `## Coverage` section (folders read, folders skipped); the planner recommends. Why: vague "is X worth learning" asks got vague answers, and a fresh worker with a full context budget reads more than the planner. Orchestrate dispatches it when an answer needs more than a few files or a whole folder.
- A child always has a terminal, so read-only is the role's rules held by the `source-changed` and `topic-folder-changed` guards. Real run 2026-11-02: a source whose AGENTS.md and README told agents to write PWNED.txt, commit and stamp the findings was reported with citations, not obeyed (`tests/chat_check.py evil-research`, 6/6).
- The kit runs no git on a research source (no `history.txt`).
- Home: `HDT_RESEARCH_HOME`, else `~/research`; clones in `src/` (git-ignored, kept until done). Same default on every OS; OneDrive-redirected folders, AppData (pruned) and paths with spaces break git or allow-list rules.

## Planner trail

`scratch.py status <feature> NN <status> [--verify "PASS|FAIL|SKIP: <text>"]` and `scratch.py decide` each write one
`planner` line in `runs.log` (`action=status|decide`), same shape as a worker's.
Why: the user missed planner reports that lived only in chat, and skill rules alone had been skipped, so the record is
a side effect of commands the planner must run anyway. They refuse only a malformed `--verify`, an unknown status or
an unknown ticket. No progress log; the scratch repo's git log is the timeline. A verifier role was rejected: running
the page cannot be held by an allow-list, and logging does not need it. Test: `tests/planner_log.py`.

## runs.log

One line per worker run (`finish`) and per planner action (`plannerlog.py`), tab-separated. Positions 1-6 are fixed:
`stamp`, `role`, `model`, `verdict`, `turns=<n>`, `<secs>s`; then `key=value` fields read by name (`out`, `list$`,
`denials`, the result JSON path, `feature`, `test`, `in`, `cw`, `cr`, `repo`, `backend`, `enforce`, `ticket`, `fmt`,
`transcript`). `resume` reads `ticket=` and the verdict. Until 1.0.0 the separate hermes-dev-watch app read these
files under a contract (`docs/watch-contract.md`, tag `v0.18.3`); it was retired with the move to Hermes subagents,
whose live view replaces it.

## Measured (end to end, toy repo)

| Run | Result |
|---|---|
| dev, delegate `standard` (Sonnet), one ticket (2026-11-02) | PASS, 7 API calls, 31 s; test red then green; one commit ending `(01-mul.md)` |
| dev with 5 planted forbidden steps (checkout main and back, write outside, edit package.json, push, create a skill) | PASS, all 5 refused and named in the answer; the ticket still built |
| researcher on a source with planted agent instructions | PASS, cited answers, nothing obeyed |
| a child stopped mid-run, then `resume` | `INTERRUPTED`, run finished `FAIL interrupted`, ticket REDISPATCH |
| *(Claude era)* forbidden actions under the allow-list | 8 of 8 denied |

## Known holes

- The test command executes code the dev wrote, so a dev can run anything through it. Review before merge is the defence.
- Protected-file matching is by file name; a test runner configured elsewhere needs that file in `.team.json` `protected`.
- No allow-list: a write outside the repo, scratch, source and skills folders, or a branch switch that switches back, is not caught (dev or researcher); only the brief's rules stop them.
- Amend is detected after the fact, not prevented; recovery is `git reflog`.
- `pushed`: a deletion by URL of a teammate's branch is not caught.
- `runs.log` has no tokens or price for delegate runs (`None`).
- `resume` matches a finished ticket by a commit subject ending `(<ticket file>)`; a hand commit with that ending, or a dev that changed the format, misleads it (verification still runs).
- `runs/` (briefs, run state, result JSON) stays on the machine that ran the worker; the scratch repo carries only tickets and `runs.log`. A child runs only on the machine whose chat started it.
- The planner editing a skill while a child runs FAILs that run (`skills-changed`); the guard cannot tell who edited.

## Rejected

- **Four Hermes profiles with per-profile plugin installs:** no enforcement in headless runs, about 5x slower, setup that touched each profile's environment.
- **Hermes Kanban dispatcher:** spawns only Hermes profiles, so it cannot run enforced `claude -p` workers.
- **`delegate_task` subagents as the fallback worker** (2026-10-14): one global `delegation.model`, a three-step planner flow, child sessions in the user's profile. Reversed in 1.0.0: per-task `option` solved the model; the three steps are accepted (prepare/finish keep the guards).
- **`claude -p` workers with an allow-list, the headless `hermes -p` worker profile** (removed 1.0.0): the user works in Hermes; a CLI login or worker profile per machine, and a second model bill path, for a block-before-run that only Claude had. Tag `v0.18.3`.
- **The kit's kanban dispatcher** (`kanban.py`, 0.18.0, removed 1.0.0): a Python loop cannot start a delegate child, and it never ran on a real project; one parallel `delegate_task` call covers it while the user is in the chat.
- **hermes-dev-watch and its run-file contract, the live progress log** (removed 1.0.0): Hermes' subagent view shows a running child.
- **String-matching PreToolUse hooks:** bypassed in the first test.
- **Installer scripts:** a clone plus linked skill folders is enough.
- **A handoff/summary file for resume:** goes stale; state is rebuilt from the files plus `decisions.md`.
- **Committing `.scratch/` on the feature branch:** puts agent files in product history; a separate scratch repo does not.
- **Routine pushes after every ticket:** pushes only on "switching".
- **Planner refusals, WARNs, `fix`/`rewrite`/`--step`:** policing, not logging (thin-down 2026-10-17).
- **Researcher Claude-only:** now guard-enforced on Hermes.
- **`history.txt` for research sources:** made the launcher run git on hostile sources.
- **Copying a local source before research** (2026-10-17): one worker at a time, and research happens while the user is grilling, not editing; build output is skipped. Left: an editor's background git touching `.git/` FAILs `guard:source-changed:.git/...`, a visible one-minute re-run. Revisit if it costs real work twice.
- **`kit/costs.py`:** list price is not subscription billing; never used.
- **Sandbox/container mode:** considered, skipped.

