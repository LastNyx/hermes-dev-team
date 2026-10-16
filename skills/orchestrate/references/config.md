# Config keys, worker rules, Hermes backend

Load when the user brings up models, backend, effort or rules, when a RESULT line says `backend=hermes`, or when the
launcher prints `ERROR no Hermes profile`.

## `.team.json` keys

Write only `test` plus what the user named; every other key has a working default.

| Key | Meaning |
|---|---|
| `test` | the full test command (reviewer runs it; dev uses the ticket's `Test command:`) |
| `models` | per role, Claude runs only, e.g. `{"dev": "sonnet"}` |
| `effort` | `"medium"`, or per role |
| `protected` | more file-name globs the dev may not edit, on top of the launcher's package/lock/test-config list |
| `backend` | `auto` (default), `claude`, `hermes`, or a per-role map |
| `hermes_models`, `hermes_provider`, `hermes_profile` | Hermes runs only; full provider model id per role; profile default `hdt-worker` |
| `rules`, `rules_max_chars` | file or list pasted into every worker prompt instead of AGENTS.md/CLAUDE.md; cap default 40000 |
| `path` | folders put in front of the worker's PATH, e.g. `["~/.local/bin"]` |
| `docs_edit` | repo-relative files the docs worker may also edit (a changelog outside README/CHANGELOG/docs) |
| `scratch`, `glossary`, `repos` | workspace layout (references/workspace.md) |
| `shared` | other people commit to the repo (references/shared-repo.md) |

## Worker rules

The launcher pastes the repo's `AGENTS.md` and `CLAUDE.md` (whichever exist, in that order; `@` imports inlined, each
file once; `START` shows `rules=<files>`, plus `(<n>k>cap)` when cut) into every prompt, cut at `rules_max_chars`.
Workers do not load them themselves (`--setting-sources ""`): their rules apply, their permissions do not. If those
files are long, ask the user for a short worker rules file (commit author, no AI trailers, migration checks, ports,
toolchain) and set `"rules"`. Put rules there, not in tickets. Extra changelogs: the project's rules name each one
(file, language, audience) and `docs_edit` lists files outside README/CHANGELOG/docs.

## Hermes backend

The launcher picks the backend: Claude when `claude` is logged in or `ANTHROPIC_API_KEY` is set, else the Hermes
worker profile; `backend` overrides. Never choose or switch the backend yourself. On `RESULT … ERROR no Hermes profile
<name>`, show the user the setup commands it printed and stop; never create or edit a Hermes profile or config.

Hermes workers run as `hermes -p <profile> chat … --source tool`, so their sessions stay in the worker profile. They
are **prompt-only** (`enforce=prompt-only` in RESULT): nothing blocks them while they run, only the launcher's
after-run checks, so a write outside the repo or a checkout that switches back is not caught. Treat such a PASS like
any PASS, but in step 6 also confirm `git status --short` is clean, and tell the user once per feature that these runs
were prompt-only. Their `$` in `costs.py` is unknown (tokens are real). The first run on a new profile is slow because
Hermes installs its dependencies once.
