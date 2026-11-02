# Workers as Hermes `delegate_task` children (1.0.0)

Every worker (dev, reviewer, docs, researcher) is one `delegate_task` child that the planner starts in the Hermes
chat. The kit cannot start, watch or kill a child; it prepares the run before and checks it after. Design and the 20
decisions behind it: [open/delegate-workers.md](open/delegate-workers.md). The last version with the `claude -p` and
headless `hermes -p` backends is tag `v0.18.3`.

## One run

1. `python KIT/run-worker.py prepare <role> "<prompt>"` (cwd = the repo):
   - refuses a `.team.json` without an `options` entry for the role, or with a key of the old backends (`backend`,
     `models`, `hermes_*`, `effort`), and prints the replacement JSON (the planner asks the user before writing it);
   - takes the worker lock, snapshots git (main, branch, HEAD, remotes, hooks/config, protected files) and the
     Hermes skills folder, and for the researcher the source and topic folders;
   - writes the brief, `<scratch>/runs/<run-id>.brief.md`: role prompt + project rules (AGENTS.md/CLAUDE.md, `@`
     imports inlined, capped at `rules_max_chars`) + `kit/roles/_tool-rules.md` + the task (absolute repo root,
     branch, scratch folder, prompt, the one allowed test command, protected files);
   - prints `DELEGATE {goal, context, option, output_schema}`: the task to pass to `delegate_task` unchanged.
2. The planner calls `delegate_task` with that task (background). The goal starts `[hdt] <run-id>:`, which is how the
   kit finds the child afterwards in `<hermes home>/cache/delegation/live/*/manifest.json`.
3. When the child's result arrives: `python KIT/run-worker.py finish <run-id>` with the child's final answer on stdin.
   It runs the guards, appends one `runs.log` line (`backend=delegate enforce=prompt-only`, model and transcript from
   the manifest), removes the lock and prints `RESULT <role> PASS|FAIL ...` (last line).

## What a child is and is not (measured 2026-11-02, probe child on `fast`)

- `option` picks the model per task: `fast` ran on claude-haiku-4-5. Without an option a child runs on the
  planner's model, so prepare always sets one.
- It starts in the planner's working folder with `TERMINAL_CWD` set to the home folder, so the brief gives an
  absolute repo root and tells it to `cd` there first.
- It gets no memory, no user profile and no AGENTS.md/CLAUDE.md: the brief pastes the project rules.
- It has file, terminal, patch, execute_code, browser, web, vision, skill_view/skill_manage and tool_search/call;
  no delegate, clarify or memory. `delegate_task` cannot narrow that list per task, so the researcher has a terminal
  too.
- The child's final answer is checked against `output_schema` by Hermes (one retry). finish also reads a fenced JSON
  or a raw last line `RESULT: PASS|FAIL ...`.
- `/new`, closing the app or a crash kills a child; no exit notice comes. `scratch.py resume` finds the open lock and
  finishes the run as `FAIL interrupted` (guards checked).
- Turn cap: the global `delegation.max_iterations` (250 here); there is no per-role cap any more.
- The manifest says `running` until the whole `delegate_task` call ends; the transcript's last line
  (`final | end status=...`) is per child, so the kit reads that for a group.

## What enforces the limits

Nothing blocks a call while a child runs. Runs are tagged `enforce=prompt-only`.

- **Brief:** `kit/roles/_tool-rules.md`. Measured 2026-10-14 on a delegate child: with the forbidden list it refused
  all 4 chores; without it, it did all 4.
- **After-run guards (finish):** main moved, branch changed, history rewritten, protected test-config file changed,
  git hooks or config changed, a push from this clone, the Hermes skills folder changed (`skills-changed`; dot files
  such as the curator ledger are ignored), and for a pool slot the main checkout changed. For the researcher also any
  file in the source folder changed (`.git` included, build folders skipped; no git run) and anything but the
  findings file written in its topic folder.

Not caught: a write outside the repo, scratch, source and skills folders; a branch switch that switches back; memory
writes (a child has no memory tool). Review before merge is the defence, as for the test command.

## Parallel groups

`scratch.py dispatch <feature> NN NN` prepares one run per pool worktree (`<repo>.wN`, slot locks) and prints one
DELEGATE line per ticket; the planner starts all of them in ONE `delegate_task` call (`tasks` = the lines), finishes
each run as its child returns, then `scratch.py merge <feature>` merges the PASS branches in ticket order. A child's
terminal starts in the planner's folder, not its slot: the brief names the slot as repo root, and the
`main-checkout-changed` guard FAILs every run of the group when the main checkout changed (the kit cannot tell which
child did it).
