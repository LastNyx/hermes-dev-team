# Config keys, worker rules, delegate children

Load when the user brings up models or rules, when `prepare` refuses with `.team.json not set up for delegate
workers`, or when you need to know what a child can and cannot do.

## `.team.json` keys

Write only `test`, `options` plus what the user named; every other key has a working default.

| Key | Meaning |
|---|---|
| `test` | the full suites: a command string, or a list `[{"name": "py", "cmd": "..."}, {"name": "ui", "cmd": "npx vitest run", "cwd": "ui"}]` (never `cd x && ...` chains). Workers run them through `kit/suites.py`, which runs every suite and ignores failures named in `baseline.md`; dev uses the ticket's `Test command:` |
| `options` | required: the delegate model option per role, `{"dev": "standard", "reviewer": "standard", "docs": "fast", "researcher": "standard"}` (the user's `delegation` options; here fast = Haiku, standard = Sonnet, deep = Opus). `prepare --option deep` overrides one run |
| `protected` | more file-name globs the dev may not edit, on top of the launcher's package/lock/test-config list |
| `research_skip` | more folder-name globs the researcher's source guard does not fingerprint, on top of `node_modules`, `.venv`, `venv`, `dist`, `build`, `.next`, `.turbo`, `__pycache__`, `.gradle`, `target`, `.cache` (`.git` is always checked) |
| `rules`, `rules_max_chars` | file or list pasted into every brief instead of AGENTS.md/CLAUDE.md; cap default 40000 |
| `path` | folders the worker puts in front of PATH, e.g. `["~/.local/bin"]` |
| `docs_edit` | repo-relative files the docs worker may also edit (a changelog outside README/CHANGELOG/docs) |
| `max_workers`, `setup` | parallel dev (step 4b): at most 3; `setup` = commands that install dependencies in a pool worktree |
| `scratch`, `glossary`, `repos` | workspace layout (references/workspace.md) |
| `shared` | other people commit to the repo (references/shared-repo.md) |

Old keys `backend`, `models`, `hermes_profile`, `hermes_models`, `hermes_provider`, `effort` make `prepare` refuse:
show the user the replacement JSON it printed, write it on their yes, commit as `chore: .team.json for delegate
workers` (a planner commit, subject never ending `(<ticket file>)`).

## Worker rules

`prepare` pastes the repo's `AGENTS.md` and `CLAUDE.md` (whichever exist, in that order; `@` imports inlined, each
file once; `PREPARED` shows `rules=<files>`, plus `(<n>k>cap)` when cut) into the brief, cut at `rules_max_chars`.
A child loads no project rules itself. If those files are long, ask the user for a short worker rules file (commit
author, no AI trailers, migration checks, ports, toolchain) and set `"rules"`. Put rules there, not in tickets. Extra
changelogs: the project's rules name each one (file, language, audience) and `docs_edit` lists files outside
README/CHANGELOG/docs.

## What a child is

- Starts in your working folder; the brief gives it the absolute repo root and tells it to `cd` there.
- Has file, terminal, browser, web, skill and code-execution tools; no memory, clarify or delegate. Nothing blocks a
  call: every run is `enforce=prompt-only`, held by the brief's tool rules and finish's guards (main, branch,
  history, protected files, git hooks/config, push, skills folder; researcher: source and topic folders). Not caught:
  a write outside the repo, scratch, source and skills folders; a branch switch that switches back. So in step 6 also
  confirm `git status --short` is clean.
- Dies with `/new` or a closed app; no result comes. Step 0 finishes the run as `interrupted`.
- Its transcript (`runs.log` field `transcript=`) is for the user; you read only the RESULT line.
- Details and measurements: the team repo's `docs/backend-delegate.md`.
