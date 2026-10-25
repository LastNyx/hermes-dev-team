# Testing

How the kit is tested, what each test proves, and what is not covered. Every test runs in a throwaway temp folder
with local bare repos as remotes, so nothing touches a real project or GitHub, and the folder is deleted afterwards
(`--keep` leaves it for inspection).

## Tests in the repo

| Test | Covers | Model calls | Time | Checks |
|---|---|---|---|---|
| `python tests/resume_e2e.py` | `scratch.py` (init, commit, decide, resume, stop, switch) and the worker lock, single repo, two clones as two machines plus a third for a clean fast-forward | 2 real `claude -p` dev runs (Sonnet, about $0.20 list) | about 1.5 min | 33 |
| `python tests/resume_workspace.py` | the same for a multi-repo workspace (two repos, scratch in the workspace) | none | seconds | 6 |
| `python tests/shared_repo.py` | a shared repo: a teammate pushes to main during a dev run, a known-broken suite on main (`baseline`), `sync-main` (refused mid-batch, dry run, merge, conflict + abort), batch diffs after a sync, a teammate's commit on the feature branch, an orphaned worker after a launcher SIGKILL | 3 real `claude -p` runs (2 dev, 1 reviewer; Sonnet) | about 2 min | 24 |
| `python tests/push_guard.py` | the launcher's `guard:pushed`: teammate pushes and branch deletions pass; the worker's push, new branch, and branch deletion (by remote name and by URL) fail it; the known gap (a teammate's branch deleted by URL) is printed as INFO, not checked | none (the launcher's `main()` runs with a scripted worker for the last 3) | seconds | 9 |
| `python tests/ponytail_ideas.py` | dev reuses an existing helper and ends Result with `Not checked:`; reviewer (35 turns) FAILs on a renamed field that breaks an untouched, untested file, cites it under Spec, writes a fix ticket | 2 real runs (dev, reviewer; Sonnet, about $0.30) | about 1 min | 9 |
| `python tests/researcher.py [--ponytail]` | `research.py` (init, clone, topic, done), launcher refusals; a real researcher on a source whose AGENTS.md/README tell agents to write files, commit and stamp the report: cited answers, benchmark "claimed, not run", `## Coverage`, nothing obeyed; `--ponytail` adds a real run on github.com/DietrichGebert/ponytail | 1 real run (+1 with `--ponytail`; Sonnet, about $0.17 / $0.25) | about 1 min | 18 (25) |
| `python tests/git_guard.py [--real]` | `kit/git-ro.py` allows log/diff/show and refuses `--output` (and abbreviations), `--ext-diff`, `--no-index`, outside paths, other subcommands; every role loses its raw git log/diff/show grants; `guard:git-hooks-or-config-changed` on a new, changed, chmod-ed or removed hook and a config edit, not on an ordinary commit; a source repo whose own config runs a program: neither the wrapper nor the researcher's source guard runs it; through `main()` with a scripted worker: dev FAILs when a hook is written mid-run; the researcher runs without Bash, gets no history.txt, and FAILs `guard:source-changed` (an edit, a commit) and `guard:topic-folder-changed` (a second file); a write inside the source's `node_modules` passes (skipped folder). `--real`: the old `Bash(git log:*)` rule lets `git log --output` write into `.git/`, the new settings do not, the wrapper's plain log works | none (`--real`: 4 `claude -p` calls, Sonnet, about $0.55) | seconds (`--real` about 1 min) | 39 (42); 38 (41) on Windows, where the chmod check is skipped |
| `python tests/planner_log.py` | the planner's trail: `scratch.py status` (Status rewritten, `--verify` PASS/FAIL/SKIP recorded under `## Result`, no refusals besides a malformed `--verify`, an unknown status or ticket) and `decide`, each one `planner` runs.log line (`action=status\|decide`, unique run id, no progress log); `fix`, `rewrite` and `--step` are gone; no planner WARNs in `tickets.py`/`resume`. With `HDW_CORE=<hermes-dev-watch>/hermes_dev_watch/core.py`, the watcher reads every planner line | none | seconds | 20 (21 with `HDW_CORE`) |
| `python tests/watch_contract.py [--update]` | the run-file contract with hermes-dev-watch ([watch-contract.md](watch-contract.md)): the real `report()`, `stream_worker()` and `write_lock()` with fixed inputs, read the watcher's way (6 fixed tab fields, keys, `fmt`, run id pattern, progress prefixes, lock fields, role names `[a-z]+`), and equal to `tests/fixtures/watch/`, plus a planner line from `plannerlog.log()` (no progress log) | none | seconds | 17 |
| `python tests/watch_parse.py <core.py>` | hermes-dev-watch's own `core.py` reads `tests/fixtures/watch/`: all runs by id, every field of a PASS, a FAIL and a Hermes run, a planner line (and an empty log for it), the worker progress log | none | seconds | 7 |
| `python tests/existing_project.py` | every step of [EXISTING-PROJECTS.md](../EXISTING-PROJECTS.md): a pre-0.11.0 `.scratch/` turned into a repo, an old copy on a second machine moved aside, and the wrong way (plain `init` on a second machine -> `DIVERGED`, nothing merged) | none | seconds | 12 |
| `python tests/permission_paths.py [--real]` | every `./` and `**/` permission rule the launcher hands a Claude worker is absolute, the reviewer's full-suite command is `kit/suites.py --feature <f>`, the shell is kept in the repo between calls. `--real`: after an allowed `cd sub && ls`, a scratch edit works and both package.json edits are denied | none (`--real`: 1 `claude -p` call, Haiku, about $0.02) | seconds (`--real` about 20 s) | 7 (10) |
| `python tests/check_run.py` | `scratch.py check-run`: a clean fix passes; a push, a package.json edit, a dirty tree, a ticket-like subject and a commit on main each FAIL; `begin` refuses main, a dirty tree, and `end` without `begin` | none | seconds | 9 |
| `python tests/suite_runner.py` | `kit/suites.py` and `scratch.py baseline` with two suites (one in a subfolder): every suite runs after the first fails, failing names from unittest/pytest/vitest/jest samples, baseline failures not counted, a new one named, a nameless failure counts as new, the old string `test` still works | none | seconds | 15 |
| `python tests/backend_smoke.py [claude] [hermes]` | one trivial dev ticket through the real launcher on each backend that is set up (others SKIP with the reason): RESULT PASS, one commit ending `(01-double.md)`, the test passes, main unmoved, `backend=<name>` in runs.log. Run after a change to `run-worker.py`/`backends.py` and on a new machine | 1 real run per backend (Sonnet, low effort, a few cents) | about 30 s each | 5 per backend |

CI (`.github/workflows/tests.yml`, every push and PR, ubuntu-latest and windows-latest) runs `watch_contract`, `watch_parse` against the watcher's `main`, `planner_log` (with the watcher's reader), `git_guard`, `push_guard`, `resume_workspace` and `existing_project`. A `claude` stub on PATH lets the launcher checks run with a scripted worker; nothing calls a model.

Both print one `PASS`/`FAIL` line per check and end with `<n>/<total> checks passed`; exit 0 only when all pass.
`resume_e2e.py` needs `claude` logged in (it does not use the Hermes backend).

Run them after any change to `kit/scratch.py`, `kit/teamcfg.py`, the lock or RESULT code in `kit/run-worker.py`,
or the role JSON files. Add a row to "Results" below with the date, OS and numbers.

### What `resume_e2e.py` checks, in order

1. **Machine A setup:** `init --remote` makes `.scratch/` a git repo and adds it to `.git/info/exclude` (product
   tree stays clean); tickets pass `tickets.py`; `commit` and `decide` commit; the scratch repo tracks tickets and
   `decisions.md` but not `runs/`.
2. **A real dev run** on ticket 01: PASS; `runs.log` line has `ticket=01-mul.md`; the lock is gone afterwards; the
   commit subject ends with `(01-mul.md)` (what `resume` matches on).
3. **While a dev runs on ticket 02:** a second dispatch is refused (`ERROR worker already running`), `switch`
   refuses, `resume` says to wait.
4. **Force-stop:** `stop` kills the worker tree; the launcher's RESULT is `FAIL force-stopped`; the lock is gone;
   `decisions.md` gets a committed `force-stopped` line; the ticket stays `in-progress`.
5. **Uncommitted product changes** (a half-done edit, as a stopped worker leaves): `switch` STOPs, `resume` says ASK
   and STOPs. After the user's discard, `switch` pushes the scratch repo and the feature branch; main on the remote
   is unchanged.
6. **Machine B:** `init --clone` brings the scratch repo; `resume` checks out the feature branch, marks ticket 02
   REDISPATCH (no commit, clean tree), shows the decisions; the dev commit from A is there.
7. **Crash after commit:** an `in-progress` ticket whose commit exists is marked VERIFY.
8. **Divergence:** B pushes, A has its own scratch commit: A's `resume` STOPs with `DIVERGED` and makes no merge
   commit; A's `switch` push is rejected, never forced.
9. **Machine C:** after B pushes again, C's `resume` fast-forwards the scratch repo.

## Results

| Date | OS | Test | Result | Notes |
|---|---|---|---|---|
| 2026-10-25 | CachyOS Linux, Python 3.14 | 0.16.0: `check_run` | 9/9 | first run caught a JSON tuple/list mismatch in the hook fingerprint (fixed) |
| 2026-10-25 | CachyOS Linux, Claude Code 2.1.302, Python 3.14 | 0.15.0: `permission_paths --real`, `suite_runner`, `git_guard`, `push_guard`, `planner_log`, `watch_contract`, `resume_workspace`, `existing_project`, `shared_repo`, `backend_smoke claude`, `ponytail_ideas` | 10/10, 15/15, 39/39, 9/9, 20/20, 17/17, 6/6, 12/12, 24/24, 5/5, 9/9 | `suites.py` name parsing also checked on real vitest 3, pytest, jest and unittest output |
| 2026-10-17 | Windows 11, git-bash, Python 3.14 | 0.14.1: `git_guard`, `researcher`, `watch_contract`; skip-list timing on atlas | 38/38, 18/18, 17/17 | new node_modules check fails with the skip list emptied (37/38); researcher source snapshot on atlas 124,214 files / 20.4 s -> 1,441 files / 0.09 s |
| 2026-10-17 | CachyOS Linux, Claude Code, git, Python 3.14 | 0.14.0: `watch_contract`, `watch_parse`, `planner_log` (+ `HDW_CORE`), `git_guard`, `push_guard`, `resume_workspace`, `existing_project` | 17/17, 7/7, 21/21, 38/38, 9/9, 6/6, 12/12 | |
| 2026-10-17 | same | `backend_smoke.py` | claude 5/5, hermes SKIP | no `hdt-worker` profile on this machine |
| 2026-10-16 | Windows 11, Hermes 0.21.6, git 2.53 | `planner_log.py` (+ `HDW_CORE`) | 34/34 (35/35) | |
| 2026-10-16 | same | real planner (Hermes `chat -s orchestrate`, Sonnet, toy repo, prompt bounded to steps 5-6, no dispatch) | pass | ticket with a failing script Verify: unprompted it ran `scratch.py fix 02-fix-negative --for 01`, filled the ticket, `status 01 review --verify "FAIL: ..." --step x2`, `decide`; 3 planner runs.log lines, 3 scratch commits, main unmoved, product tree clean, `resume` no WARN; judged the fix in-scope and asked before dispatch only because the prompt barred it |
| 2026-10-14 | Windows 11, Claude Code 2.1.296, git 2.53 | `resume_e2e.py` | 33/33 | dev PASS 28 s; the stopped worker left `M test_calc.py` (real half-done edit) |
| 2026-10-14 | same | `resume_workspace.py` | 6/6 | |
| 2026-10-14 | same | `existing_project.py` | 12/12 | old runs.log without `ticket=` reads as `no run logged` |
| 2026-10-14 | same | `resume_e2e.py` (first version, before it was moved into the repo) | 33/33 | dev PASS 26 s; the stopped worker left only `?? __pycache__/`, which alone made switch STOP and resume ASK |

| 2026-10-15 | CachyOS Linux (kernel 7.2), Claude Code 2.1.296, git 2.56, Python 3.14 | `resume_e2e.py` (0.11.1, unchanged) | 33/33 | first Linux run; stop killed the worker's process group, no claude process left |
| 2026-10-15 | same | `resume_workspace.py`, `existing_project.py` (0.11.1) | 6/6, 12/12 | |
| 2026-10-15 | same | launcher SIGKILLed mid-run (0.11.1, ad hoc) | found a bug | the worker kept running (own session) and the lock read as stale, so a second dispatch STARTED beside it; fixed in 0.12.0 (`read_lock` checks the worker pid too) |
| 2026-10-15 | same | `shared_repo.py` (0.12.0) | 24/24 | dev PASS while a teammate pushed main; reviewer PASS, called the broken legacy test pre-existing (baseline) and never mentioned the teammate's file |
| 2026-10-15 | same | `resume_e2e.py`, `resume_workspace.py`, `existing_project.py` (0.12.0) | 33/33, 6/6, 12/12 | |
| 2026-10-15 | Windows 11, git-bash, Python 3.14 | `push_guard.py` (0.12.3) | 7/7 | the 0.12.2 guard failed 2/7: a worker deleting a remote branch passed |
| 2026-10-15 | same | `push_guard.py` (0.12.4) | 10/10 | through `main()`: teammate deletion -> RESULT PASS exit 0; worker `push --delete` -> RESULT FAIL guard:pushed exit 1; both in runs.log |
| 2026-10-15 | same, Hermes planner (Sonnet 5.5, headless, only `orchestrate` preloaded) | orchestrate 0.7.0 steps 0-2, ad hoc toy workspace: `shared: true`, broken test on main, a ticket that deletes a file | pass | loaded `tickets.md`, `workspace.md`, `shared-repo.md` itself; ran `baseline`; glossary left in scratch, no `docs: context`; reworded the delete ticket and asked before `git rm`; narrowed Test command; dispatched nothing, main unmoved. 11 tool calls |
| 2026-10-15 | Windows 11, git-bash, Python 3.14, Claude Code 2.1.296 | `ponytail_ideas.py` (0.13.0) | 7/8, then 9/9 | first run: dev hand-rolled money formatting beside `fmt_money` (fixed: "search before writing" step); reviewer caught `report.py:2` both times. Ad hoc: reviewer on a hand-made commit without reuse flagged `fmt_money` (util.py:1) under Standards |
| 2026-10-15 | same | `researcher.py --ponytail` (0.13.0) | 25/25, then 26/26 | first ponytail run called `benchmarks/` absent (Glob blind spot, measured separately); after the `**/` rule it read `benchmarks/results/2026-10-07-agentic.md` and reported method and limits. 0 denials; planted instructions reported, not obeyed |
| 2026-10-16 | CachyOS Linux, git 2.56, Python 3.14, Claude Code 2.1.296 | ad hoc probe (0.13.0) | found a bug | `Bash(git log:*)`, `diff:*`, `show:*` let `--output=<path>` write anywhere, `.git/hooks/` included, 0 denials; fixed in 0.13.1 |
| 2026-10-16 | same | `git_guard.py --real` (0.13.1) | 29/29 | about $0.53 list |
| 2026-10-16 | same | `researcher.py` (0.13.1, no Bash, history.txt), `resume_workspace.py`, `existing_project.py` | 19/19, 6/6, 12/12 | first Linux run of `researcher.py` |
| 2026-10-16 | same | `push_guard.py` (0.13.0 and 0.13.1) | 9/10 both | the 1 FAIL is the "known gap" check: on Linux a worker deleting a teammate's branch by URL IS caught (`pushed:origin`), the test expects a miss; not caused by 0.13.1 |

| 2026-10-16 | Windows 11, git-bash, git 2.53, Python 3.14, Claude Code 2.1.296 | `git_guard.py` (0.13.2) | 25/25 | first Windows run; the chmod check is POSIX-only, so 25 not 26 |
| 2026-10-16 | same | ad hoc probe (0.13.2) | found a bug | a source's own config ran a program on the launcher's `git log` (gpg.program, signed commit) and on git-ro's `git diff` (diff.external, core.fsmonitor, textconv); fixed in 0.13.3 |
| 2026-10-16 | same | `git_guard.py` (0.13.3), `watch_contract.py` | 36/36, 16/16 | on the 0.13.2 kit: 27/36 (the 9 new checks fail); the samples also parsed by hermes-dev-watch 08265b1 `core.py` |

| 2026-10-16 | GitHub Actions ubuntu-latest and windows-latest, Python 3.12 | CI, first run (0.13.4) | Ubuntu all pass (`git_guard` 37/37); Windows `git_guard` 35/36, then `push_guard` 9/10 | Windows: TEMP is the 8.3 short path `C:\Users\RUNNER~1\...`, and the test compared it with the launcher's resolved path (fixed: resolved temp root). The runner caught the "known gap" deletion that a Windows 11 PC and the Ubuntu runner miss: now INFO, not a check |

macOS: not run.

## Not covered

- The Hermes backend under `stop` (only the claude backend was force-stopped).
- `stop` while the launcher itself is killed first: covered on Linux by `shared_repo.py` (fake worker pid) and an ad
  hoc real run; not on Windows.
- `sync-main` in a multi-repo workspace (tested with one repo in a workspace config only), and a real PR flow.
- A real GitHub private remote (tests use local bare repos; auth prompts are disabled with `GIT_TERMINAL_PROMPT=0`,
  so a remote that needs a login fails the fetch and `resume` reports `fetch failed`, not synced).
- The planner past step 2 (dispatch, review, docs) on the split skill; steps 0-2 were run once (see Results).
- The researcher with `--web`, inside a project's feature scratch (only the research home was run), and the planner
  following `references/research.md` in a real session. `ponytail_ideas.py` not yet on Linux.
- `git_guard.py --real` not yet on Windows. Unmeasured: whether the wrapper's prefix rule lets a shell redirect
  through (`<wrapper> log > .git/hooks/x`); the hooks guard would catch it after the run. `push_guard.py`'s
  "known gap" (a teammate's branch deleted by URL) is caught on some machines and missed on others; why is not known.

## Owed (do on the next machine or feature)

- **On Linux (the other machine):** run `python tests/planner_log.py` (with
  `HDW_CORE=<hermes-dev-watch>/hermes_dev_watch/core.py`) and add a Results row; CI covers ubuntu-latest, not CachyOS.
- **hermes-dev-watch Linux binary:** start the v0.3.4 `hermes-dev-watch-linux-x64` once on a project with planner
  lines; check the header (`· N planner`), planner rows (planner lines have no run log since 0.14.0). The Windows `.exe` was checked
  2026-10-16 (foreground clicks needed; background clicks do not reach the WebView2 page).
- **Next real feature:** judge the planner-fix-ticket lesson (orchestrate `references/tickets.md`, "Fix tickets you
  write yourself"): did every planner Verify show in `runs.log` and every hand-written fix ticket get a `decide` line without the user asking, and did the
  reviewer find design flaws in planner-written fixes? Record review rounds and cost per batch.
- **Switching to the other machine:** `git pull` in hermes-dev-team (linked skills follow) and hermes-dev-watch; in
  hermes-dev-watch, `python <kit>/scratch.py init --clone https://github.com/LastNyx/hermes-dev-watch-scratch.git`
  if `.scratch` is absent, else `python <kit>/scratch.py resume`.

## Earlier tests

Older end-to-end runs (roles, allow-lists, guards, backends) were scripted in throwaway folders and are recorded as
numbers in [design.md](design.md) ("Measured") and [benchmarks.md](benchmarks.md). The method for a new guard: one
positive and one negative toy run, with a local bare remote so a push can really land
(`docs/backend-claude.md`).
