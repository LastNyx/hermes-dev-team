# Config keys, worker rules

Load when the user brings up models, effort or rules, when the launcher refuses with `.team.json has old keys`, or on
an `ERROR` about `claude` (not on PATH, not logged in).

## `.team.json` keys

Write only `test` plus what the user named; every other key has a working default.

| Key | Meaning |
|---|---|
| `test` | the full suites: a command string, or a list `[{"name": "py", "cmd": "..."}, {"name": "ui", "cmd": "npx vitest run", "cwd": "ui"}]` (never `cd x && ...` chains). Workers run them through `kit/suites.py`, which runs every suite and ignores failures named in `baseline.md`; dev uses the ticket's `Test command:` |
| `models` | per role, e.g. `{"dev": "sonnet", "reviewer": "sonnet", "docs": "haiku", "researcher": "sonnet"}` (these are the defaults); `--model opus` overrides one run |
| `effort` | `"medium"`, or per role |
| `protected` | more file-name globs the dev may not edit, on top of the launcher's package/lock/test-config list |
| `research_skip` | more folder-name globs the researcher's source guard does not fingerprint, on top of `node_modules`, `.venv`, `venv`, `dist`, `build`, `.next`, `.turbo`, `__pycache__`, `.gradle`, `target`, `.cache` (`.git` is always checked) |
| `rules`, `rules_max_chars` | file or list pasted into every worker prompt instead of AGENTS.md/CLAUDE.md; cap default 40000 |
| `path` | folders put in front of the worker's PATH, e.g. `["~/.local/bin"]` |
| `docs_edit` | repo-relative files the docs worker may also edit (a changelog outside README/CHANGELOG/docs) |
| `max_workers`, `setup` | parallel dev (step 4b): at most 3; `setup` = commands that install dependencies in a pool worktree |
| `scratch`, `glossary`, `repos` | workspace layout (references/workspace.md) |
| `shared` | other people commit to the repo (references/shared-repo.md) |

## Worker rules

The launcher pastes the repo's `AGENTS.md` and `CLAUDE.md` (whichever exist, in that order; `@` imports inlined, each
file once; `START` shows `rules=<files>`, plus `(<n>k>cap)` when cut) into every prompt, cut at `rules_max_chars`.
Workers do not load them themselves: their rules apply, their permissions do not. If those
files are long, ask the user for a short worker rules file (commit author, no AI trailers, migration checks, ports,
toolchain) and set `"rules"`. Put rules there, not in tickets. Extra changelogs: the project's rules name each one
(file, language, audience) and `docs_edit` lists files outside README/CHANGELOG/docs.

## Old keys (kit 1.0.0 and earlier)

`options` (1.0.0's delegate model option), `backend`, `hermes_profile`, `hermes_models` and `hermes_provider` make the
launcher refuse the run (`RESULT <role> ERROR .team.json has old keys`) and print a replacement with `models` mapped
from `options` (fast = haiku, standard = sonnet, deep = opus). Show the user the replacement, write it on their yes,
commit as `chore: .team.json for claude workers` (a planner commit, subject never ending `(<ticket file>)`). In a
workspace or a shared repo the file is outside the product repo or not committed: just write it.

## claude

Workers need `claude` on PATH and a login (`claude auth status`) or `ANTHROPIC_API_KEY`. On `ERROR claude CLI not on
PATH`, tell the user; never log in or handle credentials yourself. Allow-list rules, each measured: the team repo's
`docs/backend-claude.md`.
