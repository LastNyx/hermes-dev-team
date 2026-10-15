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
| `python tests/push_guard.py` | the launcher's `guard:pushed`: teammate pushes and branch deletions pass; the worker's push, new branch, and branch deletion (by remote name and by URL) fail it | none | seconds | 7 |
| `python tests/existing_project.py` | every step of [EXISTING-PROJECTS.md](../EXISTING-PROJECTS.md): a pre-0.11.0 `.scratch/` turned into a repo, an old copy on a second machine moved aside, and the wrong way (plain `init` on a second machine -> `DIVERGED`, nothing merged) | none | seconds | 12 |

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

macOS: not run.

## Not covered

- The Hermes backend under `stop` (only the claude backend was force-stopped).
- `stop` while the launcher itself is killed first: covered on Linux by `shared_repo.py` (fake worker pid) and an ad
  hoc real run; not on Windows.
- `sync-main` in a multi-repo workspace (tested with one repo in a workspace config only), and a real PR flow.
- A real GitHub private remote (tests use local bare repos; auth prompts are disabled with `GIT_TERMINAL_PROMPT=0`,
  so a remote that needs a login fails the fetch and `resume` reports `fetch failed`, not synced).
- The planner following orchestrate step 0 in a real Hermes session (the scripts were tested, not the skill text).

## Earlier tests

Older end-to-end runs (roles, allow-lists, guards, backends) were scripted in throwaway folders and are recorded as
numbers in [design.md](design.md) ("Measured") and [benchmarks.md](benchmarks.md). The method for a new guard: one
positive and one negative toy run, with a local bare remote so a push can really land
(`skills/claude-p-role-workers/SKILL.md`).
