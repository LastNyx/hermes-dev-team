# hermes-dev-team

A small dev team for [Hermes Agent](https://hermes-agent.nousresearch.com/docs): you talk to a planner in Hermes, and it
starts headless workers (dev, reviewer, docs, researcher) one at a time, on
[Claude Code](https://docs.anthropic.com/en/docs/claude-code) or, without Claude, on a Hermes worker profile. On Claude
each worker runs under a deny-by-default allow-list; on both backends the launcher checks the repo after every run, so
a worker that switched branches, pushed or rewrote history FAILs.

**Status:** the kit has run end to end on a toy repo (dev, reviewer and docs all PASS) and blocked 8 of 8 forbidden
actions; see [docs/design.md](docs/design.md). The researcher passed a planted-instruction test and a real run on
a public repo (0.13.0). It has not yet been used on a real project.

## What is here

- [TEAM.md](TEAM.md): the roles on one page.
- `kit/run-worker.py`: the launcher and its after-run guards. `kit/backends.py`: everything backend-specific (claude,
  hermes). `kit/roles/<role>.md` + `<role>.json`: each worker's prompt and Claude allow-list.
- `kit/scratch.py`: resume in a new session or on another machine, force-stop a worker, switch machines.
- `kit/research.py`: the research home (clone a repo to study, create a topic, delete the clone when done).
- `skills/orchestrate`: the planner's one Hermes skill. Backend notes for maintainers: [docs/backend-claude.md](docs/backend-claude.md),
  [docs/backend-hermes.md](docs/backend-hermes.md).
- Planning uses [Matt Pocock's skills](https://github.com/mattpocock/skills) as-is, installed from the Hermes skill hub
  (not vendored, so they keep updating): grill-with-docs, grilling, domain-modeling, to-tickets.
- [LEARN.md](LEARN.md): a prompt to paste into Hermes on another machine so it installs and learns this repo.
- [EXISTING-PROJECTS.md](EXISTING-PROJECTS.md): turning on resume + machine switching for a project that used the kit before 0.11.0, per machine.
- [CHANGELOG.md](CHANGELOG.md): every skill, role, or launcher change; marks what to re-copy after `git pull`.
- [docs/design.md](docs/design.md): how the kit works and why (measured), and what was rejected.
- [docs/benchmarks.md](docs/benchmarks.md): every measured run (time, calls, tokens, cost) per backend and model.
- [docs/testing.md](docs/testing.md) + `tests/`: the repeatable tests (guards, resume, force-stop, switching, shared repos, worker
  behaviour, researcher), what they check, results.

## Requirements

Claude Code 2.1.296 or newer, logged in (`claude auth login`) or `ANTHROPIC_API_KEY` set; Python 3; git; Hermes
Agent for the planner. Without Claude, workers fall back to a Hermes worker profile (see Backends below).
This repo holds no credentials.

## Install

Easiest: clone the repo, then paste the prompt in [LEARN.md](LEARN.md) into Hermes. It checks prerequisites, copies
the skills, saves the kit path to memory, and runs a smoke test. By hand:

1. Clone this repo anywhere. `KIT` below is the absolute path of its `kit/` folder.
2. `python KIT/link-skills.py` (dry run), then `python KIT/link-skills.py --apply`. It links the team skills and
   prints a `MISSING` line with the install command for each of Matt's skills you do not have:
   `hermes skills install skills-sh/mattpocock/skills/<name> --yes` for grill-with-docs, grilling, domain-modeling, to-tickets.
3. In a project repo, on a feature branch:
   `python KIT/run-worker.py dev "ticket: .scratch/<feature>/issues/01-x.md" --test-cmd "<test command>"`
   prints one line, for example `RESULT dev PASS 6397d42 | turns=18 22s denials=0 | .scratch/runs/....json`.

Each project needs a committed `.team.json`, e.g. `{"test": "npm test"}` (optional `"models": {"docs": "haiku"}`,
`"protected": ["<file-name glob>"]`). The dev worker can never edit `package.json`, lockfiles, jest/vitest/tsconfig
files or `.team.json` (they decide what the test command runs); the planner makes those changes.
The launcher pastes the project's `AGENTS.md` and `CLAUDE.md` (whichever exist, in that order; `@` imports inlined,
each file once) into every worker's prompt; `"rules": "<file or list>"` replaces that default. START shows `rules=...`.
The dev worker takes its test command from the ticket; the reviewer uses `test` from `.team.json`.
Extra changelogs: name each one and its rules (file, language, audience) in `AGENTS.md`; the docs worker follows them.
It may edit only README, `CHANGELOG.md` and `docs/`, so list any other file in `"docs_edit": ["<repo-relative path>"]`.

A long rules file is cut at `rules_max_chars` (default 40000); point `rules` at a short file instead.
`python KIT/tickets.py <feature>` checks tickets and prints the approval table; `--next` lists what can run next.

## Project shapes

**Single repo** (the default). `.team.json` at the repo root, scratch in `<repo>/.scratch/` (git-excluded), workers
run from the root.

```json
{"test": "npm test"}
```

**Monorepo** (one git repo, several packages). Same as a single repo: one `.team.json` at the root, workers run from
the root, one feature branch.

```json
{"test": "pnpm -r test", "protected": ["vitest.workspace.*"]}
```

- `test` is the full suite (the reviewer runs it through `kit/suites.py`). Several suites in different folders go in a
  list, `[{"name": "py", "cmd": "..."}, {"name": "ui", "cmd": "npx vitest run", "cwd": "ui"}]`, never a `cd` chain. A ticket's `Test command:` can narrow it to one package, e.g.
  `pnpm --filter api test`; the dev runs only that.
- Protected files match by file name at any depth, so every package's `package.json`, `tsconfig*.json` and test
  config is already protected. Add workspace-level files (e.g. `pnpm-workspace.yaml`) to `protected` if they decide
  what the tests run.
- Limit: only the ROOT `AGENTS.md`/`CLAUDE.md` are pasted. A package's own `packages/api/AGENTS.md` is not, unless you
  list it in `"rules"` or `@`-import it from the root file (both cost prompt space on every run).
- Not yet measured on a real monorepo.

**Workspace** (several git repos side by side in a folder that is not a repo, team files kept out of the repos). One
`.team.json` in the workspace folder with shared keys and a `repos` map for per-repo keys:

```json
{
  "scratch": ".scratch",
  "rules": "worker-rules.md",
  "glossary": "CONTEXT.md",
  "repos": {"api": {"test": "yarn typecheck"}, "web": {"test": "yarn typecheck"}}
}
```

- Scratch (tickets, reviews, runs, `runs.log`) lives in the workspace, outside every repo. The worker gets read/write
  on that folder only; the sibling repo and the workspace root stay denied (measured on Linux and Windows).
- Run each worker with cwd = the ticket's repo. Every ticket has a `Repo:` line; the launcher refuses another cwd.
- One feature branch per repo (same name). A review batch holds tickets of one repo only. `Blocked by` may cross repos.
- `glossary` names one shared glossary that reviewer and docs read; it is never committed into a repo.
- Merge order is yours: the upstream repo first (e.g. `api` before `web`).
- `scratch.py` and `tickets.py` find the workspace config from inside any of its repos.

Every run appends one line to `.scratch/runs.log` (turns, seconds, tokens, list price, denials), which
[hermes-dev-watch](https://github.com/LastNyx/hermes-dev-watch) shows live.
Commits never get a Co-Authored-By trailer.

## Research

Ask the planner how some code works, whether a repo is worth learning, or to search a whole repo ("every place that
does X"): it writes 3-6 questions or a survey scope, you approve them, and a read-only `researcher` worker answers
with `path:line` citations and a coverage list (folders read and skipped) in one findings file (never running the
source's code or git; its numbers are "claimed, not run"). It runs on either backend. The planner gives the recommendation. Research outside a
project lives in a research home: `HDT_RESEARCH_HOME`, else `~/research` (keep it out of OneDrive, AppData, and
paths with spaces); `python KIT/research.py init`, `clone <url>`, `topic <name>`, `done <name>`. See the orchestrate
skill's `references/research.md`.

## Shared repos

If other people commit to the repo with their own workflow, the kit stays your personal workflow: set
`"shared": true` in a `.team.json` in the PARENT folder (`"repos": {"<repo>": {...}}`), keep glossary and notes in the
scratch folder, run `python KIT/scratch.py baseline <feature>` before the first ticket, and bring main in only between
batches with `python KIT/scratch.py sync-main --apply`. See the orchestrate skill's `references/shared-repo.md`.

## Resume and switching machines

There is no handoff file. The scratch folder (tickets, `batches.md`, reviews, `runs.log`, and one `decisions.md` per
feature for what you decided in chat) is its own small git repo, separate from the product repo, and the planner
rebuilds the state from it and git every time it starts (`orchestrate` step 0).

```bash
python KIT/scratch.py init --remote <private-url>   # once per project; you create the private remote
python KIT/scratch.py resume                        # step 0, run by the planner: ff-only pull + state report
python KIT/scratch.py switch                        # you say "switching": push scratch + feature branch(es)
python KIT/scratch.py init --clone <private-url>    # first time on the other machine
python KIT/scratch.py stop                          # force-stop the running worker
python KIT/scratch.py status <feature> NN review --verify "PASS: ..."   # planner: Status + Verify, logged
python KIT/scratch.py decide <feature> "<what you decided>"             # planner: a chat decision, logged
```

A project that used the kit before 0.11.0: follow [EXISTING-PROJECTS.md](EXISTING-PROJECTS.md) on each machine.

- `resume` sorts each `in-progress` ticket: a dev commit for it exists -> verify; no commit and a clean tree ->
  redispatch; a dirty tree -> ask you. It never resets, stashes, merges or rebases; if both machines committed, it
  stops and shows both sides.
- `switch` is the only push an agent makes: never main, never `--force`, never while a worker runs, never with
  uncommitted product changes (you discard them or commit a WIP yourself).
- `runs/` (result JSON, progress logs) is not carried; `runs.log` is.

## Backends

Workers run on Claude Code (`claude -p`) whenever it is logged in or `ANTHROPIC_API_KEY` is set. Otherwise, or when
`.team.json` says so, they run as a headless Hermes session in a dedicated worker profile, on any model Hermes can
reach. The dispatch command is the same; the launcher picks.

| | claude (default) | hermes (fallback) |
|---|---|---|
| Blocked while running | deny-by-default allow-list | nothing: prompt rules only (`enforce=prompt-only`) |
| Caught after the run | main moved, branch changed, history rewritten, protected file changed, git hooks/config changed, pushed; researcher: source changed, extra file written | the same |
| Not caught | - | a write outside the repo; a checkout that switches back |
| Model | `models` (`sonnet`, `haiku`) | `hermes_models` (the provider's full id), else the profile default |
| Effort | `effort` -> `--effort` | `effort` -> `--reasoning` |
| Cost in `runs.log` | list price | tokens only (`list$=None`) |

`python tests/backend_smoke.py` runs one trivial ticket on each backend that is set up (it calls a model): run it on a
new machine and after a kit change, so the fallback is known to work before you need it.

Every key is optional; a project needs only `test`. Full example:

```json
{
  "test": "npm test",
  "backend": "auto",
  "models": {"dev": "sonnet", "reviewer": "sonnet", "docs": "haiku"},
  "hermes_models": {"dev": "claude-sonnet-5-5", "docs": "claude-haiku-5-5"},
  "hermes_provider": "anthropic",
  "hermes_profile": "hdt-worker",
  "effort": "medium"
}
```

`backend` is `auto`, `claude`, `hermes`, or a per-role map like `{"docs": "hermes"}`. `effort` may also be a map.

One-time Hermes setup per machine (only if you use the hermes backend; the launcher prints these when the profile is
missing and never edits Hermes config itself):

```
hermes profile create hdt-worker --no-skills --no-alias
hermes -p hdt-worker model                    # provider + default model for workers
hermes -p hdt-worker chat -q "reply OK" -Q    # first run installs dependencies (slow once)
```

Worker sessions are tagged `--source tool` and stay in that profile, out of your chat list. Each run also makes one
small title-generation call that `runs.log` does not count.

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
