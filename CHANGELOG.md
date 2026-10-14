# Changelog

Every change to a skill, role, or the launcher gets an entry here, newest first. Linked skills update on `git pull`;
mark an entry **action** when another machine must do something (re-run `link-skills.py`, paste LEARN.md, etc.).

Format: version, date, then one line per change, naming the file.

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
