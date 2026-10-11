# hermes-dev-team

A small dev team for [Hermes Agent](https://hermes-agent.nousresearch.com/docs): you talk to a planner in Hermes, and it
starts headless [Claude Code](https://docs.anthropic.com/en/docs/claude-code) workers (dev, reviewer, docs) one at a
time. Each worker runs under a deny-by-default allow-list, so no single session carries all the context and no worker
can switch branches, push, or write outside the repo.

**Status:** the kit has run end to end on a toy repo (dev, reviewer and docs all PASS) and blocked 8 of 8 forbidden
actions; see [docs/design.md](docs/design.md). It has not yet been used on a real project.

## What is here

- [TEAM.md](TEAM.md): the roles on one page.
- `kit/run-worker.py`: the launcher. `kit/roles/<role>.md` + `<role>.json`: each worker's prompt and permissions.
- `skills/`: four Hermes skills for the planner (grill-with-docs, to-tickets, orchestrate, claude-p-role-workers).
- [LEARN.md](LEARN.md): a prompt to paste into Hermes on another machine so it installs and learns this repo.
- [CHANGELOG.md](CHANGELOG.md): every skill, role, or launcher change; marks what to re-copy after `git pull`.
- [docs/design.md](docs/design.md): decisions, measurements, and what was rejected.

## Requirements

Claude Code 2.1.296 or newer, logged in (`claude auth login`); Python 3; git; Hermes Agent for the planner.
This repo holds no credentials.

## Install

Easiest: clone the repo, then paste the prompt in [LEARN.md](LEARN.md) into Hermes. It checks prerequisites, copies
the skills, saves the kit path to memory, and runs a smoke test. By hand:

1. Clone this repo anywhere. `KIT` below is the absolute path of its `kit/` folder.
2. `python KIT/link-skills.py` (dry run), then `python KIT/link-skills.py --apply`.
3. In a project repo, on a feature branch:
   `python KIT/run-worker.py dev "ticket: .scratch/<feature>/issues/01-x.md" --test-cmd "<test command>"`
   prints one line, for example `RESULT dev PASS 6397d42 | turns=18 22s denials=0 | .scratch/runs/....json`.

Each project needs a committed `.team.json`, e.g. `{"test": "npm test"}` (optional `"models": {"docs": "haiku"}`).
The dev worker takes its test command from the ticket; the reviewer uses `test` from `.team.json`.

Every run appends to `.scratch/runs.log`; `python KIT/costs.py --by-role` totals it per feature.
Commits never get a Co-Authored-By trailer.

## When a skill grows

`kit/link-skills.py` links each folder in `skills/` into Hermes' skills directory (a junction on Windows, a symlink
elsewhere). When Hermes improves a team skill while you work, the edit lands in this repo's working tree, not a
hidden copy. Nothing is published until you commit:

1. **Work.** Let Hermes grow the skill. It is only an uncommitted change.
2. **Review.** `python kit/review-skills.py` (diff, machine-path warnings, draft CHANGELOG lines). Keep what helps every project; drop anything tied to one project (paths, names).
   `git restore skills/<name>` discards an edit.
3. **Commit.** Add a CHANGELOG line, then commit and push.
4. **Other machine.** `git pull`. Linked skills update at once.

A skill Hermes creates from scratch lands in its own skills directory, not here. If it belongs to the team, move it
into `skills/` and re-run `link-skills.py --apply`. To remove a linked skill, delete the link (`rmdir` on Windows),
never the repo folder: Hermes' own delete refuses on a link by design.

## Attribution

The grill, ticket, test-first and two-axis review patterns are learned from
[mattpocock/skills](https://github.com/mattpocock/skills) (MIT, Copyright (c) 2026 Matt Pocock). The skills here are
rewritten and shortened, not copied.

## Licence

MIT, see [LICENSE](LICENSE).
