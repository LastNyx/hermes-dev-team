"""Find a project's team config, scratch folder and worker rules. Shared by run-worker.py, tickets.py and
scratch.py.

Lookup, from the repo root (or any folder):
1. <dir>/.team.json: the repo's own config (committed). Scratch defaults to <dir>/.scratch.
2. else <dir>/../.team.json with a "repos" map that names this folder: a WORKSPACE config, for several repos side by
   side whose team files must stay out of the repos. Shared keys apply to every repo; "repos": {"<folder>": {...}}
   overrides them per repo. Scratch defaults to <workspace>/.scratch.
Relative paths in a config resolve from the folder the config file sits in.

Keys: test (a command string, or a list of {name, cmd, cwd} suites run by suites.py), options (delegate model option per
role, required: {"dev": "standard", ...}), protected, docs_edit (repo-relative files the docs worker may also edit),
shared (true: other people work in the repo; see orchestrate), scratch (folder for tickets, reviews, runs), rules (file
or list of files pasted into every worker brief; default: the repo's AGENTS.md and CLAUDE.md, whichever exist, in that
order), rules_max_chars (default 40000), path (folders the worker puts in front of PATH, e.g. ["~/.local/bin"]),
max_workers (parallel dev workers for `scratch.py dispatch`, at most 3, default 1 = serial), setup (list of {cmd, cwd}
that install dependencies in a fresh pool worktree; required with max_workers).

Worker lock: from `run-worker.py prepare` until `finish`, <scratch>/runs/worker.lock (JSON: run_id, role, ticket,
prepared time). A run prepared by `scratch.py dispatch` for pool slot N keeps runs/slot-N.lock instead. A serial run is
refused while any lock exists; a slot run while worker.lock or its own slot lock exists.

Pool worktrees: `scratch.py dispatch` prepares runs in sibling worktrees `<repo>.w1`, `<repo>.w2`, ... and tells the
launcher where the real config is with HDT_CONFIG (the config file), HDT_SLOT_OF (the repo's folder name) and
HDT_MAIN_CHECKOUT (the main checkout), so a slot uses the main checkout's config and scratch folder.
"""
import json, os, pathlib, re, time

RULES_MAX_CHARS = 40000

# Format of runs.log lines (docs/design.md "runs.log"). Positions 1-6 are fixed; keys after them are read by name.
RUN_FORMAT = 1


def _read(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise SystemExit(f"RESULT ERROR {p} is not valid JSON: {e}")


def _list(v):
    return [v] if isinstance(v, str) else list(v or [])


def load(folder=None):
    d = pathlib.Path(folder or os.getcwd()).resolve()
    own, ws = d / ".team.json", d.parent / ".team.json"
    cfg, base, source, repos = {}, d, None, {}
    forced, name = os.environ.get("HDT_CONFIG"), os.environ.get("HDT_SLOT_OF") or d.name
    if forced:                                        # a pool slot: the main checkout's config (see the module doc)
        source = pathlib.Path(forced).resolve()
        w = _read(source)
        base, repos = source.parent, w.get("repos") or {}
        cfg = {**{k: v for k, v in w.items() if k != "repos"}, **(repos.get(name) or {})}
    elif own.is_file():
        cfg, source = _read(own), own
        repos = cfg.get("repos") or {}                # a workspace root: scratch.py runs from here
        if d.name in repos:                           # run from inside a workspace root that is also a repo: unusual
            cfg = {**{k: v for k, v in cfg.items() if k != "repos"}, **cfg["repos"][d.name]}
    elif ws.is_file():
        w = _read(ws)
        if d.name in (w.get("repos") or {}):
            cfg = {**{k: v for k, v in w.items() if k != "repos"}, **(w["repos"][d.name] or {})}
            base, source, repos = ws.parent, ws, w["repos"]
    scratch = (base / cfg.get("scratch", ".scratch")).expanduser().resolve()
    rules = [(base / r).expanduser().resolve() for r in _list(cfg.get("rules"))] or \
        [d / n for n in ("AGENTS.md", "CLAUDE.md") if (d / n).is_file()]
    path = [str(pathlib.Path(p).expanduser()) for p in _list(cfg.get("path"))]
    return {"cfg": cfg, "source": source, "base": base, "scratch": scratch, "rules": rules, "path": path,
            "repos": {name: (base / name).resolve() for name in repos},
            "rules_max_chars": int(cfg.get("rules_max_chars", RULES_MAX_CHARS))}


def hermes_home():
    if os.environ.get("HERMES_HOME"):
        return pathlib.Path(os.environ["HERMES_HOME"]).expanduser()
    la = os.environ.get("LOCALAPPDATA")
    if la and (pathlib.Path(la) / "hermes").is_dir():
        return pathlib.Path(la) / "hermes"
    return pathlib.Path.home() / ".hermes"


# ------------------------------------------------------------------ delegate children (1.0.0)
#
# A worker is a Hermes `delegate_task` child the planner starts in chat. The kit cannot start, watch or kill it; it finds
# it afterwards through Hermes' live transcripts: <hermes home>/cache/delegation/live/<delegation id>/manifest.json lists
# each task's goal, status (running, completed, failed, interrupted, ...), model and transcript path. prepare puts
# `GOAL_TAG <run id>` at the start of the goal, so the run id finds its child. Hermes prunes those folders after 7 days.

GOAL_TAG = "[hdt]"
STALE_SECS = 2 * 3600       # D12/D17: a run with no sign of life for this long is interrupted
DISPATCH_SECS = 600         # a prepared run with no child yet is "prepared" for this long, then interrupted


def _ts(s):
    try:
        return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        return None


def child_of(run_id, transcript=None):
    """The delegate child of a run: {status, model, log, started_ts, ended_ts} from the newest manifest whose task goal
    names the run id; None when there is none (not dispatched yet, pruned, or another machine)."""
    live = hermes_home() / "cache" / "delegation" / "live"
    best = None
    for mf in live.glob("*/manifest.json") if live.is_dir() else []:
        try:
            m = json.loads(mf.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        for t in m.get("tasks") or []:
            if f"{GOAL_TAG} {run_id}" in str(t.get("goal") or "") or (transcript and t.get("log") == transcript):
                c = {"status": t.get("status"), "model": t.get("model") or m.get("model"), "log": t.get("log"),
                     "started_ts": _ts(m.get("started")), "ended_ts": None}
                log = pathlib.Path(c["log"]) if c["log"] else None
                if log and log.is_file():
                    c["log_mtime"] = log.stat().st_mtime
                    tail = log.read_text(encoding="utf-8", errors="replace")[-4000:]
                    d = re.findall(r"status=(\w+) duration=([\d.]+)s", tail)
                    if d and c["started_ts"]:
                        c["ended_ts"] = c["started_ts"] + float(d[-1][1])
                    # measured: the manifest says `running` until the whole delegate call ends (a parallel group's
                    # first child finishes earlier); the transcript's last line `final | end status=<s>` is per child
                    end = re.findall(r"\| end status=(\w+)", tail)
                    if end and c["status"] == "running":
                        c["status"] = end[-1]
                if best is None or (c["started_ts"] or 0) > (best["started_ts"] or 0):
                    best = c
    return best


def lock_path(scratch, slot=None):
    return pathlib.Path(scratch) / "runs" / (f"slot-{int(slot)}.lock" if slot else "worker.lock")


def write_lock(scratch, slot, data):
    p = lock_path(scratch, slot)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


def remove_lock(scratch, slot, run_id):
    p = lock_path(scratch, slot)
    try:
        if json.loads(p.read_text(encoding="utf-8")).get("run_id") == run_id:
            p.unlink()
    except (OSError, ValueError):
        pass


def lock_state(lock, now=None):
    """`running` (its child runs and wrote to its transcript in the last 2 h), `prepared` (no child yet, prepared in the
    last 10 min), `done` (its child ended; finish has not run) or `interrupted` (anything else: /new, the app closed,
    a child that never started)."""
    now = now or time.time()
    age = now - float(lock.get("prepared") or 0)
    c = child_of(lock.get("run_id") or "?")
    if c is None:
        return "prepared" if age < DISPATCH_SECS else "interrupted"
    if c["status"] == "running":
        seen = c.get("log_mtime") or c["started_ts"] or 0
        return "running" if now - seen < STALE_SECS else "interrupted"
    return "done"


def read_locks(scratch):
    """[(lock dict, state)] for worker.lock and every slot lock. The dict's `_path` names its file."""
    runs = pathlib.Path(scratch) / "runs"
    out = []
    for f in sorted(runs.glob("*.lock")) if runs.is_dir() else []:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {"raw": f.read_text(encoding="utf-8", errors="replace")}
        d["_path"] = str(f)
        out.append((d, lock_state(d)))
    return out


def busy(scratch):
    """The first (lock, state) whose run is running or about to (prepared), else None."""
    return next(((l, s) for l, s in read_locks(scratch) if s in ("running", "prepared")), None)


def blocking_lock(scratch, slot=None):
    """(lock, state) that refuses a new prepare, else None. A serial run is refused by any lock; a slot run by
    worker.lock or its own slot lock. Unfinished runs (done, interrupted) refuse too: resume finishes them first."""
    mine = {lock_path(scratch).name, lock_path(scratch, slot).name} if slot else None
    for l, s in read_locks(scratch):
        if mine is None or pathlib.Path(l["_path"]).name in mine:
            return l, s
    return None

