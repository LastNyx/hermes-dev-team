# The team

| Role | Runs as | Model | Definition | May change |
|---|---|---|---|---|
| planner | your Hermes chat session | your choice | [grill-with-docs](skills/grill-with-docs/SKILL.md), [to-tickets](skills/to-tickets/SKILL.md), [orchestrate](skills/orchestrate/SKILL.md) | tickets, CONTEXT.md, ADRs, ticket Status |
| dev | `claude -p`, one ticket per run | sonnet | [dev.md](kit/roles/dev.md), [dev.json](kit/roles/dev.json) | repo files except `.git`, `.claude`, CONTEXT.md, ADRs, `docs/dev-notes.md`; `git add`/`commit` |
| reviewer | `claude -p`, one feature per run | sonnet | [reviewer.md](kit/roles/reviewer.md), [reviewer.json](kit/roles/reviewer.json) | `.scratch/` only (review file, fix tickets); git read commands |
| docs | `claude -p`, after review | haiku | [docs.md](kit/roles/docs.md), [docs.json](kit/roles/docs.json) | README, CHANGELOG, non-ADR `docs/`, `.scratch/`; `git add`/`commit` |

Every worker gets the [tool rules](kit/roles/_tool-rules.md) appended, plus the one test command the planner passes
with `--test-cmd`. Nothing outside the allow-list runs: no checkout, push, merge, reset, or writes outside the repo.

Channel between roles: files in `.scratch/<feature>/` (tickets, `review-<n>.md`, `drift.md`) and the git branch.
The planner reads one `RESULT` line per run, never a transcript.
