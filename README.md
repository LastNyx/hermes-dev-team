# hermes-dev-team

A small dev team for [Hermes Agent](https://hermes-agent.nousresearch.com/docs): you talk to a planner in Hermes, and it
starts workers (dev, reviewer, docs, researcher) one at a time as Hermes `delegate_task` subagents. The kit writes each
worker's brief before the run and checks the repo after it, so a worker that switched branches, pushed, rewrote
history, edited a protected file or changed a Hermes skill FAILs.

**Status (1.0.0):** workers moved from `claude -p` to Hermes subagents. Real chat runs on toy repos: a dev ticket PASS
(Sonnet, 31 s), a dev that refused 5 planted forbidden steps and still built the ticket, a researcher that reported a
source's planted instructions without following them, and an interrupted run finished by resume
([docs/testing.md](docs/testing.md)). The last version with the `claude -p` backend is tag `v0.18.3`.

## What is here

- [TEAM.md](TEAM.md): the roles on one page.
- `kit/run-worker.py`: `prepare` (config check, lock, snapshots, the worker's brief and the `delegate_task` task) and
  `finish` (after-run guards, `runs.log`, the RESULT line). `kit/roles/<role>.md`: each worker's prompt;
  `_tool-rules.md`: the rules every brief ends with.
- `kit/scratch.py`: resume in a new session or on another machine, finish a stopped or interrupted run, switch
  machines, parallel dev groups.
- `kit/research.py`: the research home (clone a repo to study, create a topic, delete the clone when done).
- `skills/orchestrate`: the planner's one Hermes skill. Worker notes for maintainers:
  [docs/backend-delegate.md](docs/backend-delegate.md).
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

Hermes Agent with the `delegate_task` tool and model options in its `delegation` config (e.g. fast, standard, deep);
Python 3; git. This repo holds no credentials.

## Install

Easiest: clone the repo, then paste the prompt in [LEARN.md](LEARN.md) into Hermes. It checks prerequisites, copies
the skills, saves the kit path to memory, and runs a smoke test. By hand:

1. Clone this repo anywhere. `KIT` below is the absolute path of its `kit/` folder.
2. `python KIT/link-skills.py` (dry run), then `python KIT/link-skills.py --apply`. It links the team skills and
   prints a `MISSING` line with the install command for each of Matt's skills you do not have:
   `hermes skills install skills-sh/mattpocock/skills/<name> --yes` for grill-with-docs, grilling, domain-modeling, to-tickets.
3. In a project repo, on a feature branch, the planner runs three steps per worker:
   `python KIT/run-worker.py prepare dev "ticket: .scratch/<feature>/issues/01-x.md"` prints a `DELEGATE {...}` line;
   the planner passes it to `delegate_task`; when the subagent returns, its answer goes into
   `python KIT/run-worker.py finish <run-id>`, which prints one line, e.g.
   `RESULT dev PASS 43071b7 | option=standard model=claude-sonnet-5-5 enforce=prompt-only api_calls=7 31s ...`.

Each project needs a committed `.team.json` with its tests and a model option per role, e.g.
`{"test": "npm test", "options": {"dev": "standard", "reviewer": "standard", "docs": "fast", "researcher": "standard"}}`
(optional `"protected": ["<file-name glob>"]`). `prepare` refuses a project without `options` (or with the old
`backend`/`models`/`hermes_*`/`effort` keys) and prints the replacement. The dev worker must never edit
`package.json`, lockfiles, jest/vitest/tsconfig files or `.team.json` (they decide what the test command runs); the
planner makes those changes, and `finish` FAILs a run that touched them.
`prepare` pastes the project's `AGENTS.md` and `CLAUDE.md` (whichever exist, in that order; `@` imports inlined,
each file once) into every brief; `"rules": "<file or list>"` replaces that default. PREPARED shows `rules=...`.
The dev worker takes its test command from the ticket; the reviewer uses `test` from `.team.json`.
Extra changelogs: name each one and its rules (file, language, audience) in `AGENTS.md`; the docs worker follows them.
It may edit only README, `CHANGELOG.md` and `docs/`, so list any other file in `"docs_edit": ["<repo-relative path>"]`.

A long rules file is cut at `rules_max_chars` (default 40000); point `rules` at a short file instead.
`python KIT/tickets.py <feature>` checks tickets and prints the approval table; `--next` lists what can run next.

## Project shapes

**Single repo** (the default). `.team.json` at the repo root, scratch in `<repo>/.scratch/` (git-excluded), workers
run from the root.

```json
{"test": "npm test", "options": {"dev": "standard", "reviewer": "standard", "docs": "fast", "researcher": "standard"}}
```

**Monorepo** (one git repo, several packages). Same as a single repo: one `.team.json` at the root, workers run from
the root, one feature branch.

```json
{"test": "pnpm -r test", "options": {"dev": "standard", "reviewer": "standard", "docs": "fast"}, "protected": ["vitest.workspace.*"]}
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
  "options": {"dev": "standard", "reviewer": "standard", "docs": "fast", "researcher": "standard"},
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

Every run appends one line to `.scratch/runs.log` (model, API calls, seconds, verdict, the subagent's transcript
path). Watch a worker live in Hermes' subagent view.
Commits never get a Co-Authored-By trailer.

## Research

Ask the planner how some code works, whether a repo is worth learning, or to search a whole repo ("every place that
does X"): it writes 3-6 questions or a survey scope, you approve them, and a read-only `researcher` worker answers
with `path:line` citations and a coverage list (folders read and skipped) in one findings file (never running the
source's code or git; its numbers are "claimed, not run"). The planner gives the recommendation. Research outside a
project lives in a research home: `HDT_RESEARCH_HOME`, else `~/research` (keep it out of OneDrive, AppData, and
paths with spaces); `python KIT/research.py init`, `clone <url>`, `topic <name>`, `done <name>`. See the orchestrate
skill's `references/research.md`.

## Shared repos

If other people commit to the repo with their own workflow, the kit stays your personal workflow: set
`"shared": true` in a `.team.json` in the PARENT folder (`"repos": {"<repo>": {...}}`), keep glossary and notes in the
scratch folder, run `python KIT/scratch.py baseline <feature>` before the first ticket, and bring main in only between
batches with `python KIT/scratch.py sync-main --apply`. See the orchestrate skill's `references/shared-repo.md`.

## Talking to the planner

Plain words in the planner chat; nothing else to remember.

| You say | The planner |
|---|---|
| "resume <repo>" (or open a new session on the project) | runs step 0, `scratch.py resume`, and shows the state of every feature |
| "build" / "go" (tickets approved) | starts the loop: dispatch, verify, review, docs, one ticket at a time |
| "go with recommendation" / "agree" | takes the answer it recommended and logs it with `scratch.py decide` |
| "keep going" / "decide yourself" / "continue till finish" | logs a `delegated:` decision, then runs the whole queue without asking: more review rounds, small fixes done itself; stops only for merge, push or a real-account step, and reports once at the end |
| "stop the worker" | stops the subagent, then `scratch.py stop` finishes the run as force-stopped |
| "switching" | `scratch.py switch`: pushes the scratch repo and feature branch for your other machine |
| "merge" | nothing: it tells you the branch is ready; you run `git merge --no-ff` |

Small changes are routed by size on their own (`references/lanes.md`): a trivial fix the planner does itself inside
`scratch.py check-run`, a small one is a quick-lane ticket, anything larger the full lane. You don't need to ask.

**One worker at a time, unless the project turns on parallel dev.** With `"max_workers": 2` (at most 3, and a `setup`
list) in `.team.json`, the planner runs independent dev tickets of one batch together: `scratch.py dispatch <feature>
01 02` prepares each in its own pool worktree (`<repo>.w1`, `.w2`), the planner starts the subagents in one
`delegate_task` call, and `scratch.py merge <feature>` merges them back into the feature branch in ticket order. Tickets whose Seams name the same file are refused and run one after another. Reviews, docs and the researcher
stay serial. Without `max_workers` nothing changes: the lock refuses a second worker and "keep going" runs a queue.
Design: [docs/open/parallel-workers.md](docs/open/parallel-workers.md).

## Resume and switching machines

There is no handoff file. The scratch folder (tickets, `batches.md`, reviews, `runs.log`, and one `decisions.md` per
feature for what you decided in chat) is its own small git repo, separate from the product repo, and the planner
rebuilds the state from it and git every time it starts (`orchestrate` step 0).

```bash
python KIT/scratch.py init --remote <private-url>   # once per project; you create the private remote
python KIT/scratch.py resume                        # step 0, run by the planner: ff-only pull + state report
python KIT/scratch.py switch                        # you say "switching": push scratch + feature branch(es)
python KIT/scratch.py init --clone <private-url>    # first time on the other machine
python KIT/scratch.py stop                          # after stopping the subagent: finish its run as force-stopped
python KIT/scratch.py status <feature> NN review --verify "PASS: ..."   # planner: Status + Verify, logged
python KIT/scratch.py decide <feature> "<what you decided>"             # planner: a chat decision, logged
python KIT/scratch.py baseline <feature>                                # before the first ticket: what already fails, by test name
python KIT/suites.py --feature <feature>                                # every suite; only failures not in the baseline count
python KIT/scratch.py check-run begin|end                               # planner: guards around its own trivial fix
```

A project that used the kit before 0.11.0: follow [EXISTING-PROJECTS.md](EXISTING-PROJECTS.md) on each machine.

- `resume` sorts each `in-progress` ticket: a dev commit for it exists -> verify; no commit and a clean tree ->
  redispatch; a dirty tree -> ask you. It never resets, stashes, merges or rebases; if both machines committed, it
  stops and shows both sides.
- A subagent dies with `/new` or a closed app. The next `resume` reports `INTERRUPTED <ticket>` and finishes that run
  (guards checked) before sorting the ticket as above.
- `switch` is the only push an agent makes: never main, never `--force`, never while a worker runs, never with
  uncommitted product changes (you discard them or commit a WIP yourself).
- `runs/` (briefs, run state, result JSON) is not carried; `runs.log` is.

## Workers

Each worker is a Hermes `delegate_task` subagent on the model option `.team.json` names for its role. Nothing blocks a
subagent while it runs (`enforce=prompt-only`): its brief says what it may do, and `finish` checks afterwards.

| | |
|---|---|
| Caught after the run | main moved, branch changed, history rewritten, protected file changed, git hooks/config changed, pushed, a Hermes skill changed; researcher: source changed, extra file written; parallel slot: the main checkout changed |
| Not caught | a write outside the repo, scratch, source and skills folders; a checkout that switches back |
| Model | `options` per role (your `delegation` options, e.g. fast/standard/deep); `prepare --option` per run |
| Cost in `runs.log` | API calls and seconds; no tokens or price |
| Lifetime | the chat: `/new` or closing the app kills a running subagent; `resume` finishes its run |

Details and measurements: [docs/backend-delegate.md](docs/backend-delegate.md). The `claude -p` and headless
`hermes -p` backends, the kanban dispatcher and the hermes-dev-watch run-file contract were removed in 1.0.0; tag
`v0.18.3` has them.

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
