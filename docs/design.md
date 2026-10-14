# Design

Why the team works the way it does. Every mechanism below was run, not assumed; numbers are single samples on a toy
repo with Claude Code 2.1.296.

## Shape

- **Planner** = the Hermes chat session the user talks to. It grills, writes `CONTEXT.md`, ADRs and tickets,
  dispatches workers, verifies, and never writes product code.
- **Workers** = headless `claude -p` runs started by `kit/run-worker.py`: dev (one ticket), reviewer (one feature),
  docs (after review). Serial: one worker at a time, one branch, no worktrees.
- **Channel** = files in `.scratch/<feature>/` (untracked by the product repo; their own small git repo, decision 25)
  and the git branch. The planner reads one `RESULT` line
  per run and a few git commands, never a transcript.
- **The user merges** (`git merge --no-ff`). No agent merges or deletes branches. Workers never push; the planner
  pushes only when the user says "switching" (decision 26).

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | Workers are `claude -p`, not Hermes profiles | Headless Hermes auto-approves every command, so nothing enforces a worker's limits. A Hermes provider that wraps Claude Code turns Claude Code's own tools off, so its allow-lists cannot guard a Hermes worker. Direct `claude -p` can be enforced and was ~5x faster (8-22 s vs 92 s for the same trivial ticket). |
| 2 | Deny by default (`defaultMode: dontAsk`) + per-role allow-list | A string-matching hook was bypassed with `python -c subprocess`; the allow-list blocked every bypass, including compound commands. |
| 3 | `--setting-sources ""` and `--agents <file>` | Ignores user and project settings, so a repo's `.claude/settings.json` cannot widen a worker (measured: a hostile `Bash(*)` was ignored). Role prompts come from the kit, not the project. |
| 4 | The launcher parses the last `RESULT:` line | `claude -p` exits 0 even when a worker gives up. Only the turn cap exits 1. |
| 5 | Launcher guards: main unmoved, branch unchanged, old HEAD still an ancestor | Catches what a prefix rule cannot (`git commit -m x --amend` matched the allowed commit prefix; the guard caught it). Refuses to start on main/master. |
| 6 | Grant writes with `Edit(path)` rules, never `Write(path)` | `Edit` rules govern both tools; a `Write(...)` allow rule granted nothing. Deny rules beat allow rules. |
| 7 | Every role prompt ends with the same tool rules | Without "one command per Bash call, no cd/&&", workers chained commands, were denied, and quit. With them: 0 denials. |
| 8 | `--test-cmd` adds one exact command to the allow-list and names it in the prompt | Without the prompt line, the reviewer ran a narrower command and was denied. |
| 9 | No Co-Authored-By trailer, ever | The launcher forces `includeCoAuthoredBy: false` and empty `attribution` (measured: the trailer appears by default and is gone with either key). |
| 10 | Models: dev and reviewer on Sonnet, docs on Haiku | Haiku did the docs job correctly in 7 s for about $0.006 list price. |
| 11 | Grill before build; vertical-slice tickets with ONE test at a pre-agreed seam | From mattpocock/skills; keeps each dev run to one commit. |
| 12 | Review before merge, on two separate axes (Spec, Standards) | A reviewer run after the merge saw an empty diff. Axes stay separate so one cannot hide the other. |
| 13 | Dev notes: workers append to `.scratch/dev-notes.md`; the planner promotes approved facts to `docs/dev-notes.md` with the user's say-so | Later runs reuse what earlier runs learned without polluting the repo with unreviewed text. |
| 14 | Every run logs one line to `.scratch/runs.log` | Turns, seconds, tokens, list price, denials: enough to see cost and drift without transcripts. Tokens are logged as four fields (uncached input, cache write, cache read, output) because `input_tokens` alone is misleading: measured on a Haiku dev run, `input_tokens` = 24 while cache write was 33.5k and cache read 366.8k. |
| 15 | The launcher inlines the project's `CLAUDE.md` (and its `@` imports) into the worker prompt | Measured: with `--setting-sources ""` a worker does not see `CLAUDE.md` at all (codeword test: NONE vs found with `project`). An `@../other-repo/CLAUDE.md` import points outside the repo, where the worker may not read. With inlining, a worker followed a naming rule that lived only in the imported file. |
| 16 | The dev may not edit files that decide what the test command runs (`package.json`, lockfiles, jest/vitest/tsconfig, `.team.json`, plus `.team.json` `protected`): deny rule `Edit(**/<name>)` plus a post-run guard | Stops a dev from making tests pass by changing what runs (the `test` script, jest `testMatch`, skipped folders). It does NOT close the code-execution hole: the dev still writes the test files, and they run anything. Also unprotected by default: `conftest.py`, jest `setupFiles`, scripts a test script calls (`node scripts/test.js`); add those to `.team.json` `protected`. Measured: the deny blocked root and nested files for Edit and Write; the guard (`FAIL guard:test-config-changed`) caught a committed change. |
| 17 | Planning uses Matt Pocock's skills as installed, not rewrites | The 0.2.0 rewrites dropped the scenario and code-cross-check steps and shadowed the upstream names. The team only needs extra ticket fields, which `orchestrate` adds from `templates/ticket-header.md.template`. |
| 18 | Browser-based projects: browser checks run as the test command (headless Playwright), not as a worker tool | Workers load no MCP servers (`--setting-sources ""`) and run only one test command, so the Playwright config's `webServer` must start the app. Measured: dev wrote a browser test, saw it red then green, PASS in 35 s; reviewer ran it and PASSed in 15 s; 0 denials, no launcher change. Assertions only: visual/UX checks stay with the planner. `playwright.config.*` and the server script must be added to `.team.json` `protected` (the dev's deny on them was configured but not exercised). |
| 19 | Multi-repo workspace: one workspace `.team.json` with a `repos` map, scratch outside the repos, `Repo:` per ticket | Some teams keep agent files out of shared repos. The worker gets the scratch folder by absolute-path rules (`Read(//abs/**)`, `Edit(//abs/**)`), measured to work without `--add-dir` while the sibling repo stayed denied. One worker, one repo, one branch per run is unchanged; batches stay inside one repo so a review diff has one base. |
| 20 | Worker rules are capped (`rules_max_chars`, default 40000) and may come from a short `rules` file | A 150k-char CLAUDE.md was pasted into every run. Cut text ends with a pointer to the full file; measured: the worker Grepped it for a line past the cut. |
| 22 | Workers are backend-agnostic: claude by default, a headless Hermes worker profile as the fallback (`backend` auto/claude/hermes, per role) | Not every machine has Claude. Measured on Sonnet 5.5 (anthropic provider): 28 s vs 27 s for `claude -p` on the same ticket, about a quarter of the cached prompt tokens, sessions kept out of the user's chat list. The first Hermes spike (92 s) was slow because of the claude-subscription plugin, not Hermes. |
| 23 | Hermes workers are labelled `enforce=prompt-only` and judged by the same after-run guards | `-t file,terminal` blocks nothing inside the shell and headless approvals were off. With the forbidden list in the prompt the worker refused all 4 forbidden chores; without it, it checked out main and back, wrote outside the repo, edited package.json and pushed. |
| 24 | New guard for both backends: `git ls-remote` per remote before and after, `FAIL guard:pushed:<remote>` on any change | A push was the one forbidden action no earlier guard caught. Measured: a push during a Claude run turned a worker PASS into `FAIL guard:pushed:origin`. An unreadable remote (offline, no auth) is skipped, not failed. |
| 25 | No handoff file: a new planner session (or another machine) rebuilds state from tickets, `batches.md`, reviews, `runs.log`, git and a `decisions.md` per feature; the scratch folder is its own git repo | A summary goes stale (the old handoff note grew to 104 lines, partly marked obsolete). The files are the state already; only chat-made decisions were missing, so they get one line each in `decisions.md`, written when made. The scratch repo lets the state travel without putting agent files on the product branch. `scratch.py resume` reads an `in-progress` ticket three ways: a commit ending `(<ticket file>)` = verify, none + clean tree = redispatch, dirty tree = ask (never reset or stash). Measured with two clones as two machines: 33/33 checks, real dev runs. |
| 26 | The planner pushes only on "switching": the scratch repo and the feature branch(es), never main, never `--force`, never while a worker runs, never with uncommitted product changes. Workers never push (the `pushed` guard stays) | The user moves between machines mid-feature. Routine per-ticket pushes were rejected (not wanted). Divergence is never merged: `resume` pulls fast-forward only and stops with both sides shown; a rejected push is reported, not forced. Measured: diverged scratch -> `RESUME STOP`, no merge commit; the push rejected, not forced. |
| 27 | A worker lock (`<scratch>/runs/worker.lock`) holds the launcher and worker pids; `scratch.py stop` kills the worker tree and logs `force-stopped` | "One worker at a time" was a prompt rule only; a second planner session could break it. The lock refuses a second dispatch and a switch mid-run. Measured: second dispatch refused, switch refused, stop turned the run into `FAIL force-stopped` in runs.log, ticket stayed `in-progress`, and the next resume on the other machine said REDISPATCH. |
| 21 | `Test command` must be cheap and safe; anything needing a server, DB, or the user's say-so is a planner-run `Verify:` | A worker cannot ask, and a project rule may forbid DB operations without the user's yes in that turn. |

## Measured (end to end, toy repo)

| Run | Result |
|---|---|
| dev, Sonnet, one ticket | PASS, 18 turns, 22 s, 0 denials; test seen red then green; one commit, no trailer |
| reviewer, Sonnet | PASS, 11 turns, 17 s, wrote `review-1.md` with both axes |
| docs, Haiku | PASS, 15 turns, 27 s; CHANGELOG entry and drift list |
| forbidden actions (edit CONTEXT.md, edit an ADR, write `.claude/`, checkout main, push, amend, reset --hard, write outside the repo) | 8 of 8 denied; HEAD and branch unchanged |
| `git commit -m x --amend` | allowed by the prefix rule, caught by the launcher guard: `FAIL guard:history-rewritten` |

## Known holes

- Protected-file matching is by file name. A test runner configured somewhere else (e.g. a custom script path) needs that file in `.team.json` `protected`.

- The test command executes code the dev wrote, so a dev can run anything through it. Review before merge is the defence.
- Hermes backend: no allow-list. A write outside the repo, or a branch switch that switches back, is not caught;
  only the prompt rules stop them. Review before merge is the defence, as for the test command.
- Amend is detected after the fact, not prevented; recovery is `git reflog`.
- Prices in the log are list price; a subscription seat is billed differently.
- `resume` matches a finished ticket by the dev's commit subject ending `(<ticket file>)`. A hand commit with that
  ending, or a dev that changed the format, misleads it (VERIFY when the dev never finished; step 6 still checks).
- The scratch repo holds tickets and `runs.log` only; `runs/` (result JSON, progress logs) stays on the machine
  that ran the worker, so `costs.py --backfill` cannot fill old lines from the other machine.

## Rejected

- **Four Hermes profiles with per-profile plugin installs:** no enforcement in headless runs, about 5x slower, and setup
  that touched each profile's environment.
- **Hermes Kanban dispatcher:** spawns only Hermes profiles, so it cannot run enforced `claude -p` workers.
- **Hermes `delegate_task` subagents as the fallback worker:** one global `delegation.model` for every role, a
  three-step prepare/delegate/finish flow for the planner, and child sessions in the user's own profile. Replaced by
  a headless run in a worker profile with `-m` per role.
- **String-matching PreToolUse hooks:** bypassed in the first test.
- **Installer scripts:** a clone plus three copied skill folders is enough.
- **A handoff/summary file for resume:** goes stale; state is rebuilt from the files plus `decisions.md`.
- **Committing `.scratch/` on the feature branch:** puts agent files in the product history or needs stripping before
  merge; a separate scratch repo does not.
- **Routine pushes after every ticket:** pushes only on "switching".
