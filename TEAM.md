# The team

| Role | Runs as (headless worker on the claude or hermes backend, `kit/backends.py`) | Model | Definition | May change |
|---|---|---|---|---|
| planner | your Hermes chat session; fixes trivial things itself inside `scratch.py check-run` (`references/lanes.md`) | your choice | Matt Pocock's grill-with-docs + to-tickets (installed separately), [orchestrate](skills/orchestrate/SKILL.md) | tickets, glossary (CONTEXT.md or GLOSSARY.md), ADRs, ticket Status, package/test config |
| dev | one ticket per run | sonnet | [dev.md](kit/roles/dev.md), [dev.json](kit/roles/dev.json) | repo files except `.git`, `.claude`, glossary, ADRs, `docs/dev-notes.md`, package/lock/test-config files, `.team.json`; `git add`/`commit` |
| reviewer | one feature per run | sonnet | [reviewer.md](kit/roles/reviewer.md), [reviewer.json](kit/roles/reviewer.json) | `.scratch/` only (review file, fix tickets); git read commands |
| researcher | one question list or survey per run, either backend | sonnet | [researcher.md](kit/roles/researcher.md), [researcher.json](kit/roles/researcher.json) | its one findings file; reads the source folder and `history.txt` (the launcher writes it); no Bash; web only with `--web` |
| docs | after review | haiku | [docs.md](kit/roles/docs.md), [docs.json](kit/roles/docs.json) | README, CHANGELOG, non-ADR `docs/`, `.scratch/`; `git add`/`commit` |

Every worker gets the project's `AGENTS.md`/`CLAUDE.md` (imports inlined; for the researcher, those of its cwd, never
the cloned source's) and the tool rules for its backend ([claude](kit/roles/_tool-rules.md), [hermes](kit/roles/_tool-rules-hermes.md)) appended. Dev and reviewer also get the one
allowed test command (dev: the ticket's; reviewer: `kit/suites.py`, every suite judged against the baseline); docs and researcher get none.
Path rules are written absolute, so a `cd` in the test command cannot move them (0.15.0). Nothing outside the allow-list runs: no checkout, push, merge, reset, or writes outside the repo. `git log/diff/show`
run only through [git-ro.py](kit/git-ro.py), which refuses options that write a file (0.13.1).

Channel between roles: files in `.scratch/<feature>/` (tickets, `review-<n>.md`, `drift.md`, `research/<topic>/findings.md`)
and the git branch. Research outside a project: `<research home>/.scratch/<topic>/findings.md`.
The planner reads one `RESULT` line per run, never a transcript.
