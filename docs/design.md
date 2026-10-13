# Design

Why the team works the way it does. Every mechanism below was run, not assumed; numbers are single samples on a toy
repo with Claude Code 2.1.296.

## Shape

- **Planner** = the Hermes chat session the user talks to. It grills, writes `CONTEXT.md`, ADRs and tickets,
  dispatches workers, verifies, and never writes product code.
- **Workers** = headless `claude -p` runs started by `kit/run-worker.py`: dev (one ticket), reviewer (one feature),
  docs (after review). Serial: one worker at a time, one branch, no worktrees.
- **Channel** = files in `.scratch/<feature>/` (untracked) and the git branch. The planner reads one `RESULT` line
  per run and a few git commands, never a transcript.
- **The user merges** (`git merge --no-ff`). No agent merges, pushes, or deletes branches.

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
- Amend is detected after the fact, not prevented; recovery is `git reflog`.
- Prices in the log are list price; a subscription seat is billed differently.

## Rejected

- **Four Hermes profiles with per-profile plugin installs:** no enforcement in headless runs, about 5x slower, and setup
  that touched each profile's environment.
- **Hermes Kanban dispatcher:** spawns only Hermes profiles, so it cannot run enforced `claude -p` workers.
- **String-matching PreToolUse hooks:** bypassed in the first test.
- **Installer scripts:** a clone plus three copied skill folders is enough.
