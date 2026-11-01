#!/usr/bin/env python3
"""Kanban view of a project's tickets and of the dispatcher. READ-ONLY: this file never writes in the project. No model.

Usage (cwd = the project repo root, like scratch.py):
    python <kit>/kanban.py board [--feature F] [--json]   # tickets by Status: todo, in-progress, review, blocked, done
    python <kit>/kanban.py status [--json]                # dispatcher state: running, pid, workers, pause

Files read (all under the scratch folder; K2/K3 write the two dispatcher files, here they are only read):
    <scratch>/<feature>/issues/NN-*.md      tickets (header lines Status, Branch, Repo, Blocked by, Seam)
    <scratch>/runs.log                      one tab-separated line per run: stamp, role, model, verdict, turns=, secs, ..., ticket=<file>.md
        e.g. 20261016-120000<TAB>dev<TAB>sonnet<TAB>PASS 1a2b3c4<TAB>turns=18<TAB>22s<TAB>...<TAB>ticket=01-mul.md<TAB>fmt=1
    <scratch>/runs/slot-N.lock, worker.lock live worker locks (teamcfg.read_locks; JSON launcher_pid, worker_pid, role, ticket, stamp)
    <scratch>/runs/<stamp>-<role>.progress.log   progress of a running worker
    <scratch>/runs/dispatcher.lock          JSON {"pid": <int>, "started": "<YYYY-MM-DD HH:MM>", "feature": "<name>"}
                                            running = the pid is alive; a missing file or dead pid = not running
    <scratch>/runs/dispatcher.pause         a single epoch number (seconds): the queue is paused until then; a past
                                            epoch or a missing/garbled file = not paused

`board` last_result is rebuilt from the ticket's last non-planner runs.log line as `RESULT <role> <verdict>`.
retries = non-PASS runs of the ticket before its last run.

Exit 0, or 1 with one line `KANBAN STOP <reason>` (no .team.json, no scratch folder, no feature).
"""
import argparse, json, pathlib, re, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import teamcfg  # noqa: E402
import plannerlog as pl  # noqa: E402
import tickets as tk  # noqa: E402
import parallel  # noqa: E402

COLUMNS = [("todo", "Todo"), ("in-progress", "In progress"), ("review", "Review"), ("blocked", "Blocked"),
           ("done", "Done")]
HARD_CAP = 3


class Stop(Exception):
    pass


def project():
    tc = teamcfg.load()
    if not tc["source"]:
        raise Stop("no .team.json found (run it from the project repo root)")
    scratch = tc["scratch"]
    if not scratch.is_dir():
        raise Stop(f"no scratch folder {scratch.as_posix()}")
    return tc, scratch


def features(scratch, only=None):
    if only:
        if not (scratch / only / "issues").is_dir():
            raise Stop(f"no feature {only} (no tickets in {(scratch / only / 'issues').as_posix()})")
        return [only]
    found = sorted(p.name for p in scratch.iterdir() if (p / "issues").is_dir())
    if not found:
        raise Stop(f"no feature folder with issues/ in {scratch.as_posix()}")
    return found


def read_runs(scratch):
    """[(fields list)] of runs.log, oldest first."""
    log = scratch / "runs.log"
    if not log.is_file():
        return []
    return [l.split("\t") for l in log.read_text(encoding="utf-8", errors="replace").splitlines() if "\t" in l]


def run_info(runs, name):
    """(last_result, retries) for a ticket file name; planner lines are not runs of the ticket."""
    mine = [f for f in runs if len(f) > 6 and f[1] != "planner" and f"ticket={name}" in f[6:]]
    if not mine:
        return None, 0
    last = mine[-1]
    retries = sum(1 for f in mine[:-1] if not f[3].startswith("PASS"))
    return f"RESULT {last[1]} {last[3]}", retries


def worker_locks(scratch):
    """[(lock, alive)] of worker locks only: runs/dispatcher.lock matches read_locks' *.lock glob but is not a worker."""
    return [(d, alive) for d, alive in teamcfg.read_locks(scratch)
            if d and "raw" not in d and pathlib.PurePath(d.get("_path", "")).name != "dispatcher.lock"]


def live_locks(scratch):
    return [d for d, alive in worker_locks(scratch) if alive]


def tail(path, n=3):
    try:
        return [l.rstrip() for l in path.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()][-n:]
    except OSError:
        return []


def collect(scratch, feats):
    runs, locks = read_runs(scratch), live_locks(scratch)
    out = {s: [] for s, _ in COLUMNS}
    for feat in feats:
        files = sorted((scratch / feat / "issues").glob("*.md"))
        texts = {p: p.read_text(encoding="utf-8", errors="replace") for p in files}
        fields = {p: {"Status": tk.field(t, "Status")} for p, t in texts.items()}
        by_num = {}
        for p in files:
            by_num.setdefault(tk.number(p), []).append((p, fields[p]))
        for p in files:
            text = texts[p]
            status = fields[p]["Status"]
            if status not in out:
                continue
            bl = re.findall(r"\b\d+[a-z]?\b", tk.field(text, "Blocked by") or "", re.I) \
                if not (tk.field(text, "Blocked by") or "none").lower().startswith("none") else []
            open_b = [b for b in bl if not tk.blocker_done(tk.ticket_id(b), p, by_num)]
            lock = next((d for d in locks if pathlib.PurePath(str(d.get("ticket") or "")).name == p.name), None)
            prog = []
            if lock and lock.get("stamp") and lock.get("role"):
                prog = tail(scratch / "runs" / f"{lock['stamp']}-{lock['role']}.progress.log")
            slot = None
            if lock:
                m = re.search(r"slot-(\d+)\.lock$", str(lock.get("_path", "")))
                slot = int(m.group(1)) if m else 0
            last, retries = run_info(runs, p.name)
            title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), p.stem)
            out[status].append({
                "feature": feat, "number": pl.num(p.name), "name": p.name, "title": title,
                "repo": tk.field(text, "Repo"), "branch": tk.field(text, "Branch"),
                "blocked_by": bl, "blockers_open": open_b,
                "ready": status == "todo" and not open_b,
                "seam_paths": parallel.seam_paths(tk.field(text, "Seam")),
                "status": status, "last_result": last, "retries": retries,
                "slot": slot, "started": lock.get("stamp") if lock else None, "progress_tail": prog})
    return out


def dispatcher_status(tc, scratch):
    runs = scratch / "runs"
    d, running = None, False
    p = runs / "dispatcher.lock"
    if p.is_file():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            d = d if isinstance(d, dict) else None
        except ValueError:
            d = None
        running = bool(d) and teamcfg.pid_alive(d.get("pid"))
    paused = None
    pp = runs / "dispatcher.pause"
    if pp.is_file():
        try:
            e = float(pp.read_text(encoding="utf-8").strip())
            paused = e if e > time.time() else None
        except ValueError:
            paused = None
    try:
        mw = int(tc["cfg"].get("max_workers") or 1)
    except (TypeError, ValueError):
        mw = 1
    workers = [{"role": l.get("role"), "ticket": l.get("ticket"), "pid": l.get("worker_pid") or l.get("launcher_pid"),
                "alive": alive, "lock": pathlib.PurePath(l.get("_path", "")).name}
               for l, alive in worker_locks(scratch)]
    return {"running": running, "pid": d.get("pid") if running else None,
            "started": d.get("started") if running else None, "feature": d.get("feature") if running else None,
            "max_workers": max(1, min(HARD_CAP, mw)), "workers": workers, "paused_until": paused}


def cmd_board(a):
    tc, scratch = project()
    feats = features(scratch, a.feature)
    cols = collect(scratch, feats)
    st = dispatcher_status(tc, scratch)
    if a.json:
        print(json.dumps({"feature": a.feature, "columns": cols, "dispatcher": st}, indent=2))
        return 0
    for key, label in COLUMNS:
        print(f"== {label} ({len(cols[key])}) ==")
        for t in cols[key]:
            line = f"  {t['number']} {t['title']} [{t['repo'] or '-'} {t['branch'] or '-'}]"
            if t["blocked_by"]:
                line += " blocked-by: " + ",".join(t["blocked_by"])
            if t["last_result"]:
                line += " | last: " + t["last_result"][:80]
            print(line)
            for l in t["progress_tail"]:
                print("    > " + l.strip())
    return 0


def cmd_status(a):
    tc, scratch = project()
    st = dispatcher_status(tc, scratch)
    if a.json:
        print(json.dumps(st, indent=2))
        return 0
    print("dispatcher: " + (f"running pid {st['pid']} since {st['started']} feature {st['feature']}"
                            if st["running"] else "not running"))
    print(f"max_workers: {st['max_workers']}")
    if st["paused_until"]:
        print("paused until " + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st["paused_until"])))
    for w in st["workers"]:
        print(f"  worker {w['lock']}: {w['role']} {w['ticket'] or '-'} pid {w['pid']} {'alive' if w['alive'] else 'stale'}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("board"); p.add_argument("--feature"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("status"); p.add_argument("--json", action="store_true")
    a = ap.parse_args()
    try:
        return {"board": cmd_board, "status": cmd_status}[a.cmd](a)
    except Stop as e:
        print(f"KANBAN STOP {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
