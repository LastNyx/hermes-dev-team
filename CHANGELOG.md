# Changelog

Every change to a skill, role, or the launcher gets an entry here, newest first. Linked skills update on `git pull`;
mark an entry **action** when another machine must do something (re-run `link-skills.py`, paste LEARN.md, etc.).

Format: version, date, then one line per change, naming the file. **watch:** marks a change to the run files that
hermes-dev-watch reads (`docs/watch-contract.md`).

## 0.13.3 - 2026-10-16

A source repo's own git config could run a program. Measured (Windows, git 2.53): the launcher's `git log` for
`history.txt` ran `gpg.program` on a source with a signed commit and `log.showSignature`; `git-ro.py`'s `git diff` ran
`diff.external`, `core.fsmonitor` and a textconv driver from `.git/info/attributes`; `--ext-diff` was refused but these
need no flag.

- `kit/teamcfg.py`: `GIT_SAFE` (`-c` overrides: log.showSignature, diff.external, core.fsmonitor, gpg programs,
  core.pager). `kit/git-ro.py` and `research_history()` use it plus `--no-ext-diff --no-textconv`; git-ro also refuses
  `--textconv` (from `--textc`; `--text` stays allowed).
- `kit/run-worker.py` `research_history()`: history only when the source is a repo's top level (`git -C <subfolder>`
  gave the enclosing repo's history under the subfolder's name), and written after the worker lock is taken, so a
  refused second dispatch no longer overwrites `history.txt`.
- **watch:** `runs.log` lines end with `fmt=1`, the worker lock has `"fmt": 1` (`teamcfg.RUN_FORMAT`). Appended at the
  end, so hermes-dev-watch 08265b1 reads them unchanged (checked by parsing the samples with its `core.py`).
- `docs/watch-contract.md` (new): the run-file spec and the rules for changing it. `tests/watch_contract.py` (new,
  16 checks, no model) drives `report()`, `stream_worker()`, `write_lock()` and compares with
  `tests/fixtures/watch/`, the samples hermes-dev-watch's CI will parse.
- `tests/git_guard.py`: a hostile source repo (10 checks: the raw probe fires; git-ro log/diff/show and history.txt run
  nothing; `--textc` refused, `--text` allowed; a subfolder gets no history) and a lock check; 36 on Windows, 37 on
  Linux. The 9 new checks fail on 0.13.2 (27/36). Docstring: `--real` is 4 calls, about $0.55.
- **re-copy** `skills/orchestrate` 0.8.2 (`references/research.md`: comparing several sources in one background
  script, re-check the findings a recommendation rests on, synthesis in the topic folder committed to the scratch
  repo, clean-up keeps questions/findings); `skills/claude-p-role-workers` rules 19 (repo config runs programs) and
  20 (run files have an outside reader); `docs/design.md` 38-39; `docs/testing.md`.
- **action:** start a new planner session to load orchestrate 0.8.2.

## 0.13.2 - 2026-10-16

- `kit/run-worker.py`: the progress log also gets a `say: <first 160 chars>` line for each text block the worker
  writes (assistant events only), so a reader sees why it runs a tool, not just which tool. Checked by feeding a
  Claude-shaped stream-json through `stream_worker`: the assistant text logged, the user-side text did not, and the
  final result still came back. Not yet seen on a real `claude -p` run. Hermes backend: unchanged (its flat events
  carry no text blocks).

## 0.13.1 - 2026-10-16

Read-only git grants could write files. Measured (Linux, Claude Code 2.1.296, git 2.56): with `Bash(git log:*)`
allowed, `git log --format=format:<text> --output=<path>` ran with 0 denials and wrote <path>, also under `.git/hooks/`
although `Edit(./.git/**)` was denied; `git diff --output` and `git show --output` the same. Every role had such a
grant. A file written into `.git/hooks/` runs on the dev's next `git commit` where git does not need the executable bit
(Windows) or when it replaces an executable hook.

- `kit/git-ro.py` (new): `log`/`diff`/`show` only; refuses `--output` (and abbreviations), `--ext-diff`, `--no-index`
  and a diff path outside the repo (`GIT-RO STOP`, exit 2).
- `kit/run-worker.py`: `read_only_git()` replaces each role's raw `Bash(git log|diff|show:*)` with
  `Bash(<python> <kit>/git-ro.py <sub>:*)` and names the command in the prompt; role JSON files are unchanged.
  New guard `git-hooks-or-config-changed`: hook files (name, mode, content; honours `core.hooksPath`) and
  `.git/config` / `config.worktree` fingerprinted before the run and checked after it, like `guard:pushed`.
- Researcher: no Bash at all (tool list and `researcher.json` deny). The launcher writes the source's newest 100
  commits (`--stat`) to `<topic>/history.txt`; `kit/roles/researcher.md` reads it instead of `git -C <source> log`.
- `kit/roles/_tool-rules.md`: `git log/diff/show` go through the wrapper the prompt names.
- Project hooks still run on `git commit`: the project's own hooks are legitimate; a worker can no longer write one.
- `tests/git_guard.py` (new): 26 checks no model, `--real` 3 more (old rule writes, new rule denies, wrapper works).
- **re-copy** `skills/orchestrate` 0.8.1 (`references/research.md`: no Bash for the researcher);
  `skills/claude-p-role-workers` rule 18 (a read-only prefix is not read-only); `TEAM.md`; `docs/design.md` 37.
- `docs/testing.md`: rows and results; a stray blank line split the results table.
- **action:** start a new planner session to load orchestrate 0.8.1.


Ideas from github.com/DietrichGebert/ponytail (MIT; not installed), and a researcher role.

- `kit/roles/dev.md`: "Reuse first" (codebase, stdlib, installed dependency, one line, minimum code; never drop or add
  scope) and a "search before writing" step; `## Result` ends with `Not checked:`.
- `kit/roles/reviewer.md`: reads beyond the diff (callers of every changed, removed or renamed symbol, about 10 files);
  a break there is a Spec finding "breaks existing behaviour"; a reuse check (an existing helper not used is a
  Standards finding); the review ends with `Not checked:`. `kit/run-worker.py`: reviewer max turns 25 -> 35.
- **Researcher** (new): `kit/roles/researcher.{md,json}`, read-only, code only, Claude backend only (Hermes refused),
  Sonnet 40 turns. Prompt `questions: <file> out: <file> source: <folder>`; the launcher grants Read on the source,
  Edit on the one findings file, `git -C <source> log/show`; `--web` adds WebFetch/WebSearch for one run. Runs on the
  main branch too (it never commits).
- `kit/research.py` (new): the research home (`HDT_RESEARCH_HOME`, else `~/research`): `init`, `clone`, `topic`,
  `done`, `list`. Clones are git-ignored and never run.
- `kit/roles/_tool-rules.md`: Glob patterns start with `**/`; one empty Glob never proves absence (measured blind spot).
- **re-copy** `skills/orchestrate` 0.8.0: `references/research.md` (questions first, where findings go, the planner
  recommends); steps 5 and 8 show the user the `Not checked:` lines.
- `skills/claude-p-role-workers`, `skills/orchestrate/references/changing-skills.md`: lessons on testing guards through
  `main()`, testing skill text with a headless planner, grilling with concrete examples, one release per design.
- `tests/ponytail_ideas.py` (9 checks, 2 real runs), `tests/researcher.py` (19 checks, 1 real run; `--ponytail` 26).
- Docs for the new role: README, TEAM, LEARN (step 7: research home), launcher usage, `docs/benchmarks.md`
  researcher rows, testing gaps.
- **action:** start a new planner session to load orchestrate 0.8.0. Optional: set `HDT_RESEARCH_HOME`, then
  `python KIT/research.py init`.

## 0.12.4 - 2026-10-15

- `tests/push_guard.py`: 3 more checks through the launcher's real `main()` (scripted worker): 10/10.
- `docs/testing.md`: a headless Hermes planner ran orchestrate 0.7.0 steps 0-2 on a shared toy repo and loaded the
  right references on its own (results table).

## 0.12.3 - 2026-10-15

- `kit/run-worker.py guard:pushed`: a remote branch DELETED during the run fails the worker again when this clone's
  tracking ref for it vanished (`git push <remote> --delete`) or it is a local branch (deletion by URL). 0.12.0 checked
  only added/moved refs, so a deletion passed. A teammate deleting their own branch still passes.
- `tests/push_guard.py` (new, no model): 7 checks; the 0.12.2 guard fails 2 of them (both deletions).
- `kit/scratch.py baseline`: a test command over `--timeout` prints `BASELINE STOP`, not a traceback.
- **re-copy** `skills/orchestrate` 0.7.0: split by trigger. SKILL.md keeps the core loop (steps 0-11, cost log,
  pitfalls), 3460 -> about 1400 words. Moved, not changed, into `references/`: `tickets.md` (step 2: header, Test
  command vs Verify, no deletions, batches, fix tickets, dependencies), `workspace.md`, `shared-repo.md`,
  `switching.md` (switching, force-stop, lock, orphaned worker), `config.md` (`.team.json` keys, worker rules, Hermes
  backend), `browser-projects.md`, `changing-skills.md`. The skill names when to load each.
- **action:** linked machines get `references/` through the junction; start a new planner session to load 0.7.0.

## 0.12.2 - 2026-10-15

- `skills/orchestrate` 0.6.1, step 2: tickets never ask the dev to delete a file (its allow-list has no `rm` or
  `git rm`); the planner deletes and commits it before dispatch on the user's yes, or after the ticket via `Verify:`.
- `skills/orchestrate` step 5: a FAIL that leaves uncommitted work is not re-dispatched (the dev refuses a dirty
  tree). Show the user the diff; they commit or discard it, the planner commits only on their explicit yes, then step 6.
- `kit/tickets.py`: `WARN asks to delete?` under a todo or in-progress ticket whose body asks for a deletion (warning
  only, exit code unchanged).

## 0.12.1 - 2026-10-15

- `kit/roles/docs.md`: updates every changelog the project rules name (file, language, audience), else the plain
  `CHANGELOG.md` entry as before.
- `kit/run-worker.py`: `.team.json` `"docs_edit": ["<repo-relative path>"]` adds Edit permission on those files for
  the docs worker only, and names them in its prompt (for a changelog outside README/CHANGELOG/docs).

## 0.12.0 - 2026-10-15

- **Shared repos** (other people commit to the repo, with their own workflow): `"shared": true` in the config.
  `skills/orchestrate` 0.6.0 gets a "Shared repo" section: config outside the repo, glossary/ADRs/dev notes stay in the
  scratch folder, baseline before the first ticket, sync main only between batches, one feature branch per person,
  step 10 may be a pull request.
- `kit/scratch.py sync-main [--apply]`: merges `origin/main` into the feature branch (never rebase), only when no
  ticket of the branch is `in-progress`/`review`; aborts on a conflict and leaves the branch unchanged; fast-forwards
  the local main; prints the merge commit as the next batch's `base:`.
- `kit/scratch.py baseline <feature>`: runs the full test command before the first ticket and commits the tail to
  `<feature>/baseline.md`. `kit/roles/reviewer.md` and `dev.md`: failures listed there are pre-existing, not findings.
- `kit/scratch.py resume`: `WARN ... commit by someone else on the feature branch` (author email differs, merges and
  main's commits excluded); an orphaned worker (launcher gone, worker alive) is `RESUME STOP orphaned worker`.
- `kit/teamcfg.py read_lock`: the lock is alive while the launcher OR the worker runs. Measured on Linux: a SIGKILLed
  launcher left its worker running (own session), and a second dispatch started beside it.
- `kit/run-worker.py guard:pushed`: fails only when a remote ref moved to a commit that exists in this clone (pushed
  from here). Before, a teammate's push to main during a run failed the worker. Measured both ways.
  With `shared` and no `glossary` key, `<scratch>/GLOSSARY.md` is the glossary.
- `tests/shared_repo.py` (new): 24 checks, 3 real runs (2 dev, 1 reviewer). Linux results for all four test files in
  docs/testing.md; first Linux run of the 0.11 tests.
- **action:** start a new planner session to load orchestrate 0.6.0. For a shared repo, set `"shared": true` and move
  its config out of the repo (see the skill).

## 0.11.1 - 2026-10-14

- `EXISTING-PROJECTS.md` (new): per-machine guide to turn on resume + switching for a project that used the kit
  before 0.11.0 (which machine goes first, private remote, `init --remote` vs `init --clone`, an old `.scratch/` on
  the second machine, troubleshooting). Linked from README and LEARN.md step 7.
- `tests/existing_project.py` (new, no model): runs the guide's steps as written, plus the wrong way; 12/12.
- **action:** for each existing project, follow EXISTING-PROJECTS.md on each machine.

## 0.11.0 - 2026-10-14

- `kit/scratch.py` (new): `init` (scratch folder becomes its own git repo, `runs/` ignored, product repo excludes it;
  `--remote`, `--clone`), `resume` (orchestrate step 0), `commit`, `decide` (one line in `<feature>/decisions.md`),
  `stop` (force-stop the running worker), `switch` (push scratch + feature branches). No handoff file.
- `kit/run-worker.py`: worker lock `<scratch>/runs/worker.lock` (second dispatch refused with `ERROR worker already
  running`; a stale lock is replaced); `scratch.py stop` makes the RESULT `FAIL force-stopped`; runs.log lines gain
  `ticket=<file>`; workers start in their own process group on POSIX; a workspace scratch's `.git` is denied.
- `kit/roles/{dev,reviewer,docs}.json`: deny `Edit(./.scratch/.git/**)`.
- `kit/teamcfg.py`: `repos` (workspace repo paths), `pid_alive` (no signal: `os.kill(pid, 0)` kills on Windows),
  `read_lock`, `kill_tree`.
- `skills/orchestrate/SKILL.md` 0.5.0: step 0 Resume, scratch repo in step 1, commit after every Status change,
  `decide` for chat decisions, "Switching machines and force-stop" section; the planner pushes only on "switching".
- `docs/design.md`: decisions 25-27. README: "Resume and switching machines".
- `tests/resume_e2e.py` (2 real dev runs, 33 checks) and `tests/resume_workspace.py` (no model, 6 checks), run in a
  temp folder with local bare remotes; `docs/testing.md` (new) lists what they check, results, and what is not covered.
- Measured (Windows, toy repo, two clones + bare remotes as two machines, real Sonnet dev runs): 33/33 checks: dev
  PASS 26 s; second dispatch, switch and a resume during a run all refused/waited; stop -> `FAIL force-stopped`,
  ticket stayed in-progress, decision committed; switch stopped on a dirty tree; machine B resumed with REDISPATCH,
  VERIFY for a committed ticket, decisions shown; diverged scratch -> STOP, no merge, push rejected not forced;
  ff pull on a third clone. Workspace layout (2 repos, no model): 6/6. Re-run from `tests/`: 33/33 and 6/6.
- **action:** start a new planner session to load orchestrate 0.5.0. Existing projects: run `scratch.py init` once.

## 0.10.3 - 2026-10-14

- `README.md`: new "Project shapes" section (single repo, monorepo, workspace) with an example `.team.json` each.
  Replaces the one-paragraph workspace note. Monorepo is documented from the code, not yet measured on a real one;
  a package's own AGENTS.md is not pasted (documented limit).

## 0.10.2 - 2026-10-14

- `kit/teamcfg.py`, `kit/run-worker.py`: with no `rules` key, worker rules are the repo's `AGENTS.md` and `CLAUDE.md`,
  whichever exist, in that order (before: `CLAUDE.md` only, so an AGENTS.md-only repo gave workers no rules). A file
  already pasted is not pasted again, so a `CLAUDE.md` that is just `@AGENTS.md` no longer doubles the text. START
  shows `rules=<files>` (or `rules=none`), plus `(<n>k>cap)` when cut. An explicit `rules` key still wins.
- Checked without a model call (6 cases): AGENTS only, CLAUDE only, both, CLAUDE importing AGENTS (pasted once),
  neither, explicit `rules` key.

## 0.10.1 - 2026-10-14

- `docs/benchmarks.md` (new): all measured runs so far in one place (claude vs hermes backend, rejected fallbacks,
  earlier spikes), with a note on reading the token columns. Add real-project `costs.py` tables there.

## 0.10.0 - 2026-10-14

- `kit/run-worker.py`: **backend-agnostic workers.** `backend` = `auto` (default: claude when `claude auth status`
  says logged in or `ANTHROPIC_API_KEY` is set, else hermes), `claude`, `hermes`, or a per-role map; `--backend`
  overrides. The hermes backend runs one headless `hermes -p <hermes_profile> chat --format stream-json` (default
  profile `hdt-worker`) with `-m <hermes_models[role]>`, optional `--provider <hermes_provider>`, `--source tool`;
  same dispatch, progress lines, RESULT line and runs.log as claude, tagged `backend=hermes enforce=prompt-only`.
  A missing profile prints the setup commands and `ERROR no Hermes profile`; the kit never edits Hermes config.
- `kit/run-worker.py`: new guard for both backends, `FAIL guard:pushed:<remote>` (`git ls-remote` before/after).
- `kit/run-worker.py`: `effort` key / `--effort` (string or per-role map) -> `--effort` on claude, `--reasoning` on
  hermes. START shows `claude:<model>/<effort>` or `hermes:<model>/<effort>`. runs.log gains `backend=`.
- `kit/roles/_tool-rules-hermes.md` (new): tool rules for hermes workers (chaining allowed, forbidden list kept).
- `kit/costs.py`: hermes runs show as `<role>@hermes` with `$` unknown (tokens are real).
- `skills/orchestrate`: step 4 says the launcher picks the backend and what to do on a missing profile; step 5 on
  prompt-only runs; `.team.json` gets written with `test` only unless the user names more; new pitfalls.
- `skills/claude-p-role-workers`: Backends section. README Backends section, LEARN.md fallback step, design 22-24.
- **action (optional, per machine):** to use the hermes backend, create the worker profile (README, Backends).
  Start a new planner session to load the updated skills.
- Measured on Windows (Sonnet 5.5 medium unless noted; toy repos with a local bare remote):

  | Run | Result |
  |---|---|
  | claude, normal ticket | PASS, 15-18 turns, 19-27 s, 0 denials |
  | claude, push from a second shell during the run | `FAIL guard:pushed:origin \| PASS 8180ec6` |
  | hermes, `hermes_models.dev = claude-haiku-5-5` | PASS f0b79b4, 34 s, ran on Haiku (`-m` beats the profile default) |
  | hermes, 4 forbidden chores in the ticket | PASS 03fc86e, 39 s; all 4 refused by the prompt rules; main, branch, tree, remote unchanged |
  | hermes, `--max-turns 2` | FAIL (out of iterations), exit 1 |
  | `backend: hermes`, profile missing | `ERROR no Hermes profile nope` + setup commands, exit 2 |
  | worker sessions in the user's default profile | 0 |

## 0.9.2 - 2026-10-14

- `kit/tickets.py`: ticket ids keep their letter suffix (`02b` is its own ticket, not `02`); leading zeros are dropped
  (`05` = `5`). A blocker id is done when every ticket with that id is done, its fix tickets included; a fix ticket
  blocked by its own id waits only for the main ticket. Before, `05`, `05b`, `05c` and `05-fix-*` collapsed to one
  entry and the last one sorted decided `--next`. Seam column prints without backticks.
- `kit/roles/reviewer.md`: batch `tickets:` match the id exactly (`05` = `05-*` and `05-fix-*`, never `05b-*`).
- Measured on Windows (the 0.9.1 **action**, toy two-repo workspace, Haiku dev): ticket `Repo: api` refused from
  `ui`; from `api` PASS in 31 s, read the ticket and wrote dev-notes in the workspace scratch through the
  `//c/...` rules, committed in its repo; writing `../ui/HACK.md` and reading the workspace root were denied.

## 0.9.1 - 2026-10-14

- `kit/run-worker.py`: absolute permission rules for a workspace scratch folder and glossary are now correct on
  Windows. Claude Code normalises `C:\Users\a` to `/c/Users/a` before matching, so the rule is `//c/Users/a/**`;
  0.9.0 wrote `/C:/Users/a/**`, which per Claude Code's permission docs does not match. Linux/macOS unchanged.
  **action** (Windows): not yet measured there; run one dev worker in a workspace and check it reads its ticket.
- `kit/tickets.py --add-header`: checks every ticket before touching any; a ticket without the worker header or
  Matt's `**Blocked by:**` + `**Status:**` lines prints `STOP`, exits 1 and changes nothing (to-tickets format changed
  upstream, do not guess). The inserted header is followed by one blank line, not three.
- `skills/orchestrate` 0.4.1: step 2 is one procedure (`tickets.py --add-header`, STOP means tell the user); the old
  hand-replace instructions that contradicted it are gone.
- Measured on Linux (toy two-repo workspace, Haiku dev): ticket `Repo: api` refused from `ui`; from `api` PASS in
  22 s, wrote and committed in its repo, read/wrote the workspace scratch, and its write to `../ui/HACK.md` was denied.

## 0.9.0 - 2026-10-14

- `kit/teamcfg.py` (new): one place that finds the config. A repo's `.team.json` as before, or a WORKSPACE
  `../.team.json` with `"repos": {"<folder>": {...}}` for several repos side by side whose team files stay out of the
  repos. New keys: `scratch` (folder for tickets/reviews/runs), `rules` (file or list pasted into worker prompts instead
  of CLAUDE.md), `rules_max_chars` (default 40000), `path` (folders put in front of the worker's PATH), `glossary`.
- `kit/run-worker.py`: `ticket:`/`feature:` may point outside the repo; with a scratch folder outside the repo the
  worker gets `Read`/`Edit` on that folder only (measured: `Read(//abs/**)` + `Edit(//abs/**)` work without
  `--add-dir`; the sibling repo and the workspace root stayed denied). A ticket's `Repo:` line refuses any other cwd.
  Project rules are cut at `rules_max_chars` with a pointer to the full file (`START ... rules=152k>cap`; measured:
  the worker Grepped the file for a line past the cut). `runs.log` gains `repo=`.
- `kit/tickets.py` (new): `--add-header [--apply]` moves flat `NN-*.md` into `issues/` and inserts the worker header;
  without flags it checks every ticket and prints the approval table (repo, status, blockers, Seam, Test command,
  Verify); `--next` lists dispatchable tickets (cross-repo `Blocked by` included).
- `skills/orchestrate/templates/ticket-header.md.template`: `Repo:` (multi-repo only) and `Verify:` (a planner-run
  check that needs a server, a DB, or the user's say-so; `None` otherwise).
- `skills/orchestrate` 0.4.0: worker rules file, multi-repo workspace (branch per repo, batches per repo, merge order,
  workspace glossary), Test command vs Verify, `tickets.py` in steps 2-3, `review-skills.py --patch` hand-over.
- `kit/review-skills.py --patch`: one `git apply`-able patch of every uncommitted change (new files included), for a
  machine that cannot push.
- `kit/costs.py`: finds `runs.log` in the configured scratch folder (works from a workspace or a repo).
- `kit/roles/dev.md`: Verify is never run by the dev; a typecheck/lint-only test command is code-first.
  `reviewer.md`, `docs.md`: glossary path may come from the prompt.
- Measured on a toy workspace (two repos, workspace scratch, rules file, glossary): dev api PASS 21 s, dev ui PASS
  24 s, reviewer ui PASS 12 s, 0 denials; a probe ticket was denied writing the sibling repo and reading the workspace
  root. One-repo projects behave as before.

## 0.8.1 - 2026-10-13

- `kit/costs.py --backfill`: the backup goes to `.scratch/runs/runs.log.<stamp>.bak` (ignored) instead of
  `.scratch/runs.log.bak`, which showed up untracked and made the dev worker FAIL `dirty tree`.
  **action (optional):** delete any old `.scratch/runs.log.bak`.
- `kit/run-worker.py`: docs worker default turns 15 -> 25 (a three-file docs job hit the cap before committing).
- `kit/roles/reviewer.md`: fix tickets always name a `Seam`; refactor-only findings name the touched function and
  say "existing tests are the check" (a `Seam: None` ticket made the dev FAIL with no code written).
- `skills/orchestrate` steps 8-9: check a fix ticket's Seam before dispatch; split big docs jobs or hand the docs
  worker a planner-verified facts file.

## 0.8.0 - 2026-10-13

- `kit/run-worker.py`: logs `in=` (uncached input), `cw=` (cache write) and `cr=` (cache read) next to `out=` in
  `runs.log`; the RESULT line gains `tok=in <n> / cache <n> / out <n>`.
- `kit/costs.py`: columns in, cache_w, cache_r, out, hit% (cache read / total input). `--backfill` adds the token
  fields to older log lines from the result JSON in `.scratch/runs/` (keeps `runs.log.bak`; safe to run twice).
  **action (optional):** run `python KIT/costs.py --backfill` once in each project with older runs.
- `skills/orchestrate` Cost log: how to read the token columns; never report `in` alone as input.
- `docs/design.md` decision 14: why four token fields (measured: input_tokens 24 vs cache read 366.8k).

## 0.7.1 - 2026-10-13

- `skills/orchestrate` Pitfalls: browser-based projects run headless Playwright as the `.team.json` test command,
  with `webServer` starting the app and `playwright.config.*` + the server script in `protected`; visual/UX checks
  stay with the planner. Projects without a browser UI are unaffected.
- `docs/design.md` decision 18: the measurement behind it (dev PASS 35 s, reviewer PASS 15 s, 0 denials, toy repo).

## 0.7.0 - 2026-10-12

- `skills/orchestrate` steps 2, 7, 8: review per **batch** instead of once per feature. Step 2 proposes batches (one
  milestone each, at most 4 tickets) for the user to approve and records them in `.scratch/<feature>/batches.md`; step 8
  reviews a batch as soon as its tickets are all `review`, with `batch: <b> tickets: NN,NN base: <sha> round: <n>`.
  Docs still runs once at the end. **action:** start a new planner session (an open session keeps the old step 8).
- `kit/roles/reviewer.md`: with `batch/tickets/base` it reads only those tickets and reviews `git diff <base>...HEAD`;
  writes `review-<batch>-<round>.md`. Without them it behaves as before.
- Measured on a toy repo (Sonnet): batch 1 (clean) PASS, 9 turns, 13 s; batch 2 (planted bug) FAIL with the right
  finding and one fix ticket, 10 turns, 15 s; each read only its own tickets. $0.29 list for both.

## 0.6.0 - 2026-10-12

- `kit/run-worker.py`: live progress. The worker runs with `--output-format stream-json --verbose`; the launcher prints
  `START <role> <model>` first, then one line per tool call (`[  12s] Edit calc.py`) and per denial
  (`[   3s] DENIED Bash: This command requires approval`), flushed, and writes the same lines to
  `.scratch/runs/<stamp>-<role>.progress.log` (tail it). The RESULT line is still the LAST line, so parsing is
  unchanged; `.scratch/runs/<stamp>-<role>.json` now holds the final `result` event (same fields as before), so
  `costs.py` is unchanged. Progress parsing is best-effort and cannot fail a run.
- `skills/orchestrate` step 4, `skills/claude-p-role-workers`: dispatch with `python -u` and
  `terminal(background=true, heartbeat=120)`, end the turn, never `process wait` (it holds heartbeats back); answer a
  heartbeat in one line with no tool calls. Linked skills update on `git pull`; nothing to re-run.

## 0.5.1 - 2026-10-12

- `skills/orchestrate`: step 2 stops if a ticket lacks Matt's `**Blocked by:**` or `**Status:**` line (upstream
  format changed) instead of guessing.
- `docs/design.md` decision 16: protecting test config stops passing-by-config; it does not close the
  code-execution hole. Lists files to add to `protected` (conftest.py, jest setupFiles, scripts a test script calls).
- Matt Pocock skill versions known to work with step 2 (sha256 of SKILL.md, first 12): grill-with-docs 7de372c13488,
  grilling befdff1e27a1, domain-modeling 7b925d7b1e34, to-tickets 56ec23c5b278. If to-tickets changes, re-check step 2.

## 0.5.0 - 2026-10-12

- **action (every machine):** `git pull`, then `python kit/link-skills.py` and `--apply`. It removes the now-dangling
  `grill-with-docs` and `to-tickets` links and prints a `MISSING` line per Matt Pocock skill you lack; install each with
  `hermes skills install skills-sh/mattpocock/skills/<name> --yes` (grill-with-docs, grilling, domain-modeling,
  to-tickets). Or paste LEARN.md again; step 3 covers it.
- `skills/grill-with-docs`, `skills/to-tickets`: removed. The planner uses Matt Pocock's originals, which keep the
  scenario and code cross-check steps the rewrites dropped, and keep updating upstream.
- `skills/orchestrate` 0.3.0: new step 2 adds the worker header (`templates/ticket-header.md.template`: Lane, Branch,
  Status, Round, Blocked by, Terms, Seam, Test command) to tickets from Matt's `to-tickets`; glossary is `CONTEXT.md`
  if present, else `GLOSSARY.md`; never patch Matt's skills.
- `kit/run-worker.py`: pastes the project's `CLAUDE.md` with `@` imports inlined into every worker prompt (measured:
  `--setting-sources ""` hides it). Denies dev edits to package/lock/test-config files and `.team.json`
  (`Edit(**/<name>)`), and FAILs a run with `guard:test-config-changed` if one changed anyway. `.team.json` may add
  `"protected": [globs]`.
- `kit/link-skills.py`: removes dangling links into the repo, skips folders without SKILL.md, reports missing
  upstream (Matt) skills with the install command.
- `kit/roles/dev.md`, `reviewer.md`, `docs.md`, `dev.json`: glossary may be `CONTEXT.md` or `GLOSSARY.md`; dev FAILs
  with `needs <file> change` instead of touching protected files.

## 0.4.0 - 2026-10-11

- `.team.json` per project: `{"test": "<full test command>", "models": {"<role>": "<model>"}}`. The launcher reads it,
  and for dev takes the test command from the ticket's `Test command:` line, so `--test-cmd` is now optional.
  The dev role cannot edit `.team.json` (deny rule, measured). **action:** add `.team.json` to each project.
- `kit/costs.py`: per-feature (and `--by-role`) table of runs, pass/fail, turns, minutes, tokens, list price, denials.
  `runs.log` lines now carry `feature=` and `test=`.
- `kit/review-skills.py`: lists grown team skills, shows the diff (new files too), warns on machine paths, drafts
  CHANGELOG lines. Read-only; never stages anything.
- `skills/orchestrate`: uses `.team.json` instead of retyping test commands; shows the cost table at the end of a
  feature; asks before patching a team skill and points to `review-skills.py`.

## 0.3.0 - 2026-10-11

- `kit/link-skills.py`: links `skills/*` into Hermes' skills directory, so skills that grow while you work land in
  this repo as reviewable diffs. Refuses to overwrite a local copy that differs, and flags copies installed in a
  category folder (`skills/<category>/<name>`) instead of creating a duplicate. **action:** run it once with `--apply`.
- `.gitattributes`: LF line endings, so diffs of skills edited on Windows show content only.
- LEARN.md uses link-skills instead of copying; README gains "When a skill grows".

## 0.2.0 - 2026-10-11

- Workers are now headless Claude Code runs (`kit/run-worker.py`, `kit/roles/`), not Hermes profiles. `profiles/` and `SETUP.md` are removed.
- **re-copy** `skills/orchestrate`: dispatches through `kit/run-worker.py`; roles are dev, reviewer, docs.
- **re-copy** `skills/claude-p-role-workers`: new. Permission rules and pitfalls for `claude -p` workers.
- `skills/grill-with-docs`, `skills/to-tickets`: unchanged from 0.1.0.
- Launcher always disables the Co-Authored-By trailer.
- `docs/design.md` rewritten: 14 decisions, measurements, known holes, rejected options.

## 0.1.0 - 2026-10-11

- First draft: four Hermes profiles (planner, dev, qa, docs). Never run end to end; superseded by 0.2.0.
