# Run-file contract (read by hermes-dev-watch)

[hermes-dev-watch](https://github.com/LastNyx/hermes-dev-watch) is a separate desktop app that shows worker runs
live by reading three kinds of file the launcher writes. The kit owns this format: this page is the spec,
`tests/watch_contract.py` checks the kit against it, and `tests/fixtures/watch/` holds samples the kit's real writers
produce, which the watcher's CI parses.

Format version: `teamcfg.RUN_FORMAT` = **1**, written as `fmt=1` (runs.log) and `"fmt": 1` (lock).

## Files

All paths are under the project's scratch folder. `<stamp>` is `YYYYMMDD-HHMMSS`; a run's id is `<stamp>-<role>` and
must match `^\d{8}-\d{6}-[a-z]+$`.

| File | Writer (`kit/run-worker.py`) | Format |
|---|---|---|
| `runs.log` | `report()`, one line per finished run | tab-separated. Positions 1-6 fixed: `stamp`, `role`, `model`, `verdict`, `turns=<n>`, `<secs>s`. Then `key=value` fields, in this order today: `out`, `list$`, `denials`, the result JSON path (no `=`), `feature`, `test`, `in`, `cw`, `cr`, `repo`, `backend`, `enforce` (Hermes only), `ticket`, `fmt`. Absent values are `-` (feature, test, ticket) or `None` (tokens, `list$`) |
| `runs/<id>.progress.log` | `stream_worker()`, while the run happens | one line per event: `  [<secs right-aligned to 4>s] <text>`. `<text>` is `<Tool> <arg>`, `say: <worker text, whitespace folded, 160 chars>`, `DENIED <tool>: <reason>` or `exit!=0 <tool>: <output>` |
| `runs/worker.lock` | `main()` / `write_lock()`, while a worker runs | JSON: `launcher_pid`, `worker_pid` (once started), `role`, `ticket`, `feature`, `repo`, `stamp`, `fmt`; `stop_requested` after `scratch.py stop`. The run is live while either pid is alive. Deleted when the launcher exits |

`runs/` stays out of the scratch git repo, so a run made on another machine has a `runs.log` line but no log or lock.

## Rules for kit changes

- **Additive only, at the end.** A new `runs.log` field is appended after `fmt`; a new progress prefix is a new
  `<word>: ` (an old watcher shows it as a tool name); a new lock key is just another key. These keep `fmt`.
- **Breaking** = reordering or removing positions 1-6, renaming a key the watcher reads (`turns`, `list$`, `denials`,
  `feature`, `ticket`, `repo`, `backend`), changing the stamp or the progress line shape, renaming the lock fields, or a
  role name outside `[a-z]+`. A breaking change bumps `RUN_FORMAT` in the same commit.
- Any change to these files: run `python tests/watch_contract.py`; if it fails on the samples only, regenerate them
  with `--update`, read the sample diff, and mark the CHANGELOG entry **watch:** so the watcher follows.

## The watcher's side

- **The kit's CI** (`.github/workflows/tests.yml`, every push and PR, Linux and Windows) checks out the watcher's
  `main` and runs `python tests/watch_parse.py <watcher>/hermes_dev_watch/core.py`: the watcher's own parser on the
  kit's samples. A kit change the watcher cannot read fails on the kit commit that made it. No token: both repos
  are public and the workflow runs with `contents: read`.
- The watcher's own CI should do the same against the kit's `main` (weekly is enough), so a watcher change that
  stops reading the samples is caught on its side too.
- It treats a `fmt` above the one it knows as "kit format newer than this watcher: update it" instead of guessing.
  A line or lock without `fmt` is format 1 (kit 0.13.2 and older).
