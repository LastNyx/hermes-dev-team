# The team

| Role | Runs as | Model | Definition | May change |
|---|---|---|---|---|
| planner | your Hermes chat session | your choice | Matt Pocock's grill-with-docs + to-tickets (installed separately), [orchestrate](skills/orchestrate/SKILL.md) | tickets, glossary (CONTEXT.md or GLOSSARY.md), ADRs, ticket Status, package/test config |
| dev | `claude -p`, one ticket per run | sonnet | [dev.md](kit/roles/dev.md), [dev.json](kit/roles/dev.json) | repo files except `.git`, `.claude`, glossary, ADRs, `docs/dev-notes.md`, package/lock/test-config files, `.team.json`; `git add`/`commit` |
| reviewer | `claude -p`, one feature per run | sonnet | [reviewer.md](kit/roles/reviewer.md), [reviewer.json](kit/roles/reviewer.json) | `.scratch/` only (review file, fix tickets); git read commands |
| researcher | `claude -p`, one question list per run (Claude backend only) | sonnet | [researcher.md](kit/roles/researcher.md), [researcher.json](kit/roles/researcher.json) | its one findings file; reads the source folder and `history.txt` (the launcher writes it); no Bash; web only with `--web` |
| docs | `claude -p`, after review | haiku | [docs.md](kit/roles/docs.md), [docs.json](kit/roles/docs.json) | README, CHANGELOG, non-ADR `docs/`, `.scratch/`; `git add`/`commit` |

Every worker gets the project's `AGENTS.md`/`CLAUDE.md` (imports inlined; for the researcher, those of its cwd, never
the cloned source's) and the [tool rules](kit/roles/_tool-rules.md) appended. Dev and reviewer also get the one
allowed test command; docs and researcher get none. Nothing outside the allow-list runs: no checkout, push, merge, reset, or writes outside the repo. `git log/diff/show`
run only through [git-ro.py](kit/git-ro.py), which refuses options that write a file (0.13.1).

Channel between roles: files in `.scratch/<feature>/` (tickets, `review-<n>.md`, `drift.md`, `research/<topic>/findings.md`)
and the git branch. Research outside a project: `<research home>/.scratch/<topic>/findings.md`.
The planner reads one `RESULT` line per run, never a transcript.
