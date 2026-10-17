# Claude backend (`claude -p` workers)

Rules for running headless Claude Code workers under a deny-by-default allow-list. Measured on Claude Code 2.1.296.
Code: `kit/backends.py` (`Claude`), the allow-lists in `kit/roles/*.json`, and the grants `kit/run-worker.py` adds.
Why each rule exists: [design.md](design.md). The other backend: [backend-hermes.md](backend-hermes.md).
Until 0.14.0 this was the Hermes skill `claude-p-role-workers`; it is maintainer documentation, not a planner skill.

## When to Use

Changing `kit/backends.py`, `kit/run-worker.py` or a role file, adding a role, or debugging a Claude worker that FAILs
or gets denied. For the day-to-day dispatch loop, the planner uses the `orchestrate` skill.

## Why claude -p first

Only a direct `claude -p` worker gets an allow-list that blocks a call before it runs, so `auto` prefers Claude. The
Hermes backend is the agnostic fallback, enforced only by the prompt and the launcher's after-run guards
([backend-hermes.md](backend-hermes.md)).

## Rules (each one cost a failed run)

1. **Grant file writes with `Edit(path)`, never `Write(path)`.** `Edit` rules govern both Edit and Write; a `Write(...)` allow rule grants nothing. Paths outside `./` are denied either way. Deny beats allow.
2. **Partial-word Bash prefixes need `*`:** `Bash(git checkout -b t-*)` matches `t-001`; `Bash(git checkout -b t-:*)` does not. `:*` only works after a whole word (`Bash(git diff:*)`).
3. **Every role prompt ends with the tool rules** (`kit/roles/_tool-rules.md`): one command per Bash call, no `cd`/`&&`/`;`/`|`/redirects, read with Read/Glob/Grep, retry once in the allowed form. Without them, workers chain commands, get denied, and quit.
4. **Exit code 0 does not mean success.** A worker that gives up still exits 0 with subtype `success`. Parse the last `RESULT: PASS|FAIL` line and `permission_denials`. Only the turn cap exits 1 (`error_max_turns`).
5. **Co-Authored-By is always off.** Set both `"includeCoAuthoredBy": false` and `"attribution": {"commit": "", "pr": ""}`, and force them in the launcher, not only in role files. The trailer appears by default.
6. **Isolate from local settings:** `--setting-sources ""` + `--agents <json file>` + `--agent <role>`. A project's `.claude/settings.json` (even `Bash(*)`) is then ignored. With `--setting-sources ""`, `.claude/agents/*.md` is not found, so roles must come through `--agents`.
7. **Pass `--tools Read,Edit,Write,Glob,Grep,Bash`.** Without it, on Windows the worker reached for PowerShell and was denied.
8. **Prefix rules can't stop trailing flags:** `Bash(git commit -m:*)` allows `git commit -m x --amend`. Catch it after the run: the old HEAD must still be an ancestor of the new HEAD, main must not have moved, and the branch must be unchanged.
9. **One allowed test command goes in both places:** add `Bash(<exact cmd>)` to the allow-list AND name it in the prompt. A reviewer that wasn't told ran a narrower command and was denied.
10. **Review before merge.** After a merge, `git diff main...branch` is empty.
11. Haiku handled a small docs job; the user's default for test and real workers is Sonnet at medium effort (subscription limits): pass `--model sonnet --effort medium`, or set `.team.json` `"models"` and `"effort"` (string or per-role map). Never let a worker silently run on the planner's Opus.
12. To test denials, frame forbidden actions as plain chores. A prompt framed as "red-team" was refused and tested nothing.
13. **Every guard gets a positive and a negative toy run.** Example: for the push guard, push from a second shell (a script with `( sleep 12; git push ... ) &`, since the terminal tool rejects inline `&`) while the worker runs and expect `FAIL guard:pushed`. Use a local bare repo (`git init --bare remote.git`, remote added by native `C:/...` path) so a push can really land.
    **Narrowing a guard** (to stop false FAILs) is where misses creep in: list every form of the guarded action first (push: moved ref, new branch, `--delete`, push by URL) and check each against both the old and the new code. Prefer a no-model test that imports the launcher and calls `snapshot()`/`guards_after()` over a bare remote plus a teammate clone; a guard that only compares changed refs misses deletions, and a push by URL leaves the remote-tracking ref in place, so tracking refs alone cannot detect it.
14. **Toy test runners must be able to go red.** A runner that tests a function only `if hasattr(...)` passes before the code exists, and a correct dev worker FAILs "no red step". Use a runner that executes every `test_*.py` and tell the ticket to write `test_<x>.py`.
15. **Glob patterns start with `**/`.** Glob with a `path` and a pattern beginning with a folder name (`benchmarks/**/*`) returns nothing on Windows even when the folder exists; `**/benchmarks/**` finds it. Put this in every role's tool rules, and never let a worker (or you) report a file absent from one empty Glob: confirm with a second pattern or a Read of the exact path.
16. **A principle in a role prompt is not a behaviour until it is a procedure step.** "Reuse existing code first" as a section was ignored; a numbered "Grep the repo for an existing function before writing" step was followed. Test each new role rule with a toy repo that makes the wrong path tempting (an existing helper beside the obvious hand-rolled one), and make the reviewer check the same rule so a miss is caught.
17. **A read-only role gets no Bash and one write grant:** allow only Read/Glob/Grep plus `Edit(<its one output file>)`,
    run it without the Bash tool, and put nothing like `Edit(./**)` in deny (deny beats allow and would block the output
    file). Source text the role reads (AGENTS.md, README comments) is data; verify with a planted-instruction source that
    it reports them and obeys none. On Hermes the same role is held by the launcher's source and topic-folder guards.

18. **A read-only command prefix is not read-only.** `Bash(git log:*)` (also `diff:*`, `show:*`) allowed `git log --format=format:<text> --output=<path>`: 0 denials, the file written, also under `.git/hooks/` with `Edit(./.git/**)` denied. Before granting any `Bash(<cmd>:*)`, list the command's options that write files, run code or leave the repo; if there are any, grant a wrapper script that refuses them (`kit/git-ro.py`) and add an after-run guard for the place it would hit (hooks, config).

19. **A read-only git command runs the repo's own config.** On a repo you did not write, plain `git log` ran `gpg.program` (a signed commit plus `log.showSignature`, or `%G?`) and `git diff` ran `diff.external`, `core.fsmonitor` and textconv drivers from `.git/info/attributes` (measured, git 2.53 Windows). Every git call `kit/git-ro.py` makes goes through `teamcfg.GIT_SAFE` plus `--no-ext-diff --no-textconv`, and the launcher runs no git on a research source at all (0.14.0: the source guard fingerprints files instead). Probe with a fake signed commit written as bytes: text-mode stdin on Windows adds `\r` and git silently rejects the object, so the probe reads clean.

20. **Run files have an outside reader.** `runs.log`, `runs/<id>.progress.log` and `runs/*.lock` are parsed by hermes-dev-watch (`docs/watch-contract.md`). Append new fields at the end, keep role names `[a-z]+`, run `tests/watch_contract.py`, and bump `teamcfg.RUN_FORMAT` for anything an old reader would misread.

## Known hole

The allowed test command runs code the worker wrote, so it can execute anything. Review before merge is the defence.

## Launching from Hermes

Run the launcher with `python -u` and `terminal(background=true, heartbeat=120)`, then end the turn. The launcher prints
one flushed line per tool call and per denial (also in `.scratch/runs/<stamp>-<role>.progress.log`); heartbeats carry
only new lines. Measured: a heartbeat is queued while the planner sits in `process wait`, so waiting hides progress.
Each heartbeat costs one planner turn; 120 s means a typical 90 s run costs none. Read the RESULT line (always the last
line), not the exit code alone. Never keep the terminal's working directory inside a throwaway repo you delete: later calls fail.

## Choosing the backend

`kit/backends.py` `pick()`: `--backend` flag, else `.team.json` `"backend"` (string or per-role map), else auto: claude
when `ANTHROPIC_API_KEY` is set or `claude auth status` JSON says `"loggedIn": true` (no model call), else hermes. The
dispatch is the same command for both; the planner never picks the backend. Model names differ: Claude takes
`sonnet`/`haiku` (`models`), Hermes takes the provider's full id (`hermes_models`). `effort` feeds `--effort` (claude)
or `--reasoning` (hermes). Project rules reach workers only through the launcher's paste (claude:
`--setting-sources ""`; hermes: `--ignore-rules`); check `rules=<files>` on the START line when a worker ignores one.
After any change here, run `python tests/backend_smoke.py` (it calls a model).

## Process lessons

- Measure a mechanism end to end before designing around it.
- Test launcher logic that needs no model (config lookup, rules assembly, ticket parsing) by importing the module
  in a fixture script over several toy dirs; spend worker runs only on behaviour that needs a model.
  For guards and RESULT/runs.log handling, go one level up: import `run-worker.py` via importlib, replace
  `stream_worker` with a scripted worker (does the git action, returns a `result` event claiming `RESULT: PASS`),
  set `sys.argv` and call `main()` with stdout captured. That exercises config, lock, snapshot, guards, verdict and
  runs.log for real with no model cost.
- To prove a fix closes a regression, run the new test against the old code too: `git archive <old> kit | tar -x -C
  <scratch dir>`, copy the test beside it, run it; the old code must fail exactly the cases the fix targets.
- To test planner-facing skill text (not scripts), run a real headless planner on a throwaway workspace built to hit
  the triggers under test (e.g. `shared: true`, a red test on main, a ticket that deletes a file):
  `hermes chat --query-file <prompt> -Q --format stream-json -s orchestrate -t skills,terminal,file --max-turns 40
  --source tool --in <repo>`. The prompt says "I am not available: where the skill says ask or wait, write the
  question instead; never dispatch". Then grep the stream for `skill_view` calls and check git/scratch state; that
  shows whether references load from the trigger table, which reading the skill cannot.
- Judging an outside repo or prompt pack for ideas: shallow-clone it into scratch, read its core prompt/code (not the
  README hype), map each idea onto a concrete kit file (role prompt, reviewer axis, launcher), mark its benchmark
  numbers "claimed, not run", and say why installing it would not reach Claude workers (`--setting-sources ""`). Delete the
  clone afterwards.
- Grilling kit design: give every abstract choice a concrete example inside the question (a sample ticket, a sample
  file); a bare "who owns rung 1" got "don't understand, explain more". Ask about scope limits early (e.g. "code only,
  or any research?") since the user raises them.
- Version bumps: one grilled design = one release (one CHANGELOG entry, one push), even when built in several tested
  parts; never plan a version per part.
- Record every measured run (backend, model/effort, wall, calls, in/cache_w/cache_r/out, $) as a row in the team
  repo's `docs/benchmarks.md`; the user reads it on the other machine after `git pull`.
- Do not claim one backend is cheaper from toy runs: say the mechanism (fewer calls, smaller system prompt), n, and
  that cache reads are billed at a fraction; compare the token columns of `runs.log` on a real ticket.
- Reviewing a kit update pulled from the other machine: read `git diff <old>..HEAD`, run the no-model tests
  (`tests/*.py` without model calls) on this OS, and look hardest at guards that were relaxed and at OS-specific
  process code measured on one OS only. Report findings ranked, with a recommended next step, before changing code.
- Before every commit in the team repo, run `git status --short` and read any `skills/` diff you did not write: a
  background skill review edits linked skills in place, and `git add -A` silently ships its changes.
- To judge a new backend's enforcement, run the forbidden-chores ticket twice: once with the forbidden list in the
  prompt, once without. Only the second shows what the backend itself blocks.
- Never edit Hermes `config.yaml` or create profiles for the user (Hermes refuses it anyway): print the exact
  `hermes profile create` / `hermes -p <p> model` / `hermes config set` commands and let the user run them.
- When the user asks for an "agnostic" option, list options in a table (model per role, planner steps, blocked while
  running, build cost) and recommend one; they iterate on it before any build.
- Before testing a workflow change, show the user the old vs new workflow side by side (steps, what is enforced,
  what is only caught afterwards); they want to understand it before the toy runs.
