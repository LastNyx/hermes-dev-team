# The team

| Role | Runs as (a Hermes `delegate_task` child, prepared and checked by `kit/run-worker.py`) | Option | Definition | May change |
|---|---|---|---|---|
| planner | your Hermes chat session; fixes trivial things itself inside `scratch.py check-run` (`references/lanes.md`) | your choice | Matt Pocock's grill-with-docs + to-tickets (installed separately), [orchestrate](skills/orchestrate/SKILL.md) | tickets, glossary (CONTEXT.md or GLOSSARY.md), ADRs, ticket Status, package/test config |
| dev | one ticket per run | standard | [dev.md](kit/roles/dev.md) | repo files except `.git`, glossary, ADRs, `docs/dev-notes.md`, package/lock/test-config files, `.team.json`; `git add`/`commit` |
| reviewer | one feature or batch per run | standard | [reviewer.md](kit/roles/reviewer.md) | `.scratch/` only (review file, fix tickets); git read commands |
| researcher | one question list or survey per run | standard | [researcher.md](kit/roles/researcher.md) | its one findings file; reads the source folder; never runs the source's code or git; web only with `--web` |
| docs | after review | fast | [docs.md](kit/roles/docs.md) | README, CHANGELOG, non-ADR `docs/`, `.scratch/`; `git add`/`commit` |

The option per role comes from the project's `.team.json` `options` (fast/standard/deep are the user's `delegation`
options). Every brief holds the role prompt, the project's `AGENTS.md`/`CLAUDE.md` (imports inlined; for the
researcher, those of its cwd, never the cloned source's) and [the tool rules](kit/roles/_tool-rules.md). Dev and
reviewer also get the one allowed test command (dev: the ticket's; reviewer: `kit/suites.py`, every suite judged
against the baseline); docs and researcher get none.

Nothing blocks a child while it runs (`enforce=prompt-only`): the "May change" column is a rule in the brief, and
`run-worker.py finish` checks it afterwards (main moved, branch changed, history rewritten, protected file changed,
git hooks/config changed, pushed, a Hermes skill changed; researcher: source or topic folder changed). Details:
[docs/backend-delegate.md](docs/backend-delegate.md).

Channel between roles: files in `.scratch/<feature>/` (tickets, `review-<n>.md`, `drift.md`, `research/<topic>/findings.md`)
and the git branch. Research outside a project: `<research home>/.scratch/<topic>/findings.md`.
The planner reads one `RESULT` line per run, never a transcript.
