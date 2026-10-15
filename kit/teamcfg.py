"""Find a project's team config, scratch folder and worker rules. Shared by run-worker.py, costs.py, tickets.py and
scratch.py.

Lookup, from the repo root (or any folder):
1. <dir>/.team.json: the repo's own config (committed). Scratch defaults to <dir>/.scratch.
2. else <dir>/../.team.json with a "repos" map that names this folder: a WORKSPACE config, for several repos side by
   side whose team files must stay out of the repos. Shared keys apply to every repo; "repos": {"<folder>": {...}}
   overrides them per repo. Scratch defaults to <workspace>/.scratch.
Relative paths in a config resolve from the folder the config file sits in.

Keys: test, models, protected (as before), shared (true: other people work in the repo; see orchestrate),
scratch (folder for tickets, reviews, runs), rules (file or list of files
pasted into every worker prompt; default: the repo's AGENTS.md and CLAUDE.md, whichever exist, in that order), rules_max_chars (default 40000), path (folders put in
front of PATH for the worker, e.g. ["~/.local/bin"]).

Worker lock: while a worker runs, the launcher keeps <scratch>/runs/worker.lock (JSON: launcher pid, worker pid, role,
ticket, stamp). It keeps one worker at a time and lets scratch.py refuse to switch machines mid-run.
"""
import json, os, pathlib, subprocess, sys

RULES_MAX_CHARS = 40000


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
    if own.is_file():
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


def pid_alive(pid):
    """True when a process with this pid is running. Never signals it (os.kill(pid, 0) TERMINATES it on Windows)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)           # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259           # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def lock_path(scratch):
    return pathlib.Path(scratch) / "runs" / "worker.lock"


def read_lock(scratch):
    """(lock dict or None, alive). alive = the launcher OR the worker named in the lock is still running.

    measured (Linux): a SIGKILLed launcher leaves its worker running (the worker has its own session so `stop` can kill
    its group), and a lock that checked only the launcher let a second dispatch start beside the orphan.
    """
    p = lock_path(scratch)
    if not p.is_file():
        return None, False
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return {"raw": p.read_text(encoding="utf-8", errors="replace")}, False
    return d, pid_alive(d.get("launcher_pid")) or pid_alive(d.get("worker_pid"))


def kill_tree(pid):
    """Kill a worker and all its children. Windows: taskkill /T /F. Elsewhere: SIGTERM to its process group (the
    launcher starts the worker in its own session, so the group is the worker's and not the planner's)."""
    if not pid_alive(pid):
        return False
    if sys.platform == "win32":
        return subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"], capture_output=True).returncode == 0
    import signal
    try:
        os.killpg(int(pid), signal.SIGTERM)
    except OSError:
        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError:
            return False
    return True
