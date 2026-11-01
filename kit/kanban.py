#!/usr/bin/env python3
"""Kanban view of a project's tickets and the dispatcher. `board` and `status` are READ-ONLY; only `run` writes. No model.

Usage (cwd = the project repo root, like scratch.py):
    python <kit>/kanban.py board [--feature F] [--json]   # tickets by Status: todo, in-progress, review, blocked, done
    python <kit>/kanban.py status [--json]                # dispatcher state: running, pid, workers, pause
    python <kit>/kanban.py run <feature> [--once] [--dry-run] [--interval S]
                                                          # START side (K2): each tick start ready tickets in pool slots

`run` (docs/open/kanban-dispatcher.md D3/D5/D6): takes runs/dispatcher.lock (refuses when that pid is alive, replaces a
stale one, removes it on exit, SIGTERM and SIGINT), needs `.team.json` max_workers >= 1 and `setup`, a clean repo on the
ticket's Branch and no live worker lock. A READY ticket (pick_ready: todo, blockers done, Seam apart from running tickets
and from each other, same repo and Branch as the running group, free slot, cap min(max_workers, 3), not paused) gets a pool
slot (parallel.prepare_slot), Status in-progress and a detached `run-worker.py dev --slot N` (stdout in
runs/kanban-slot-N.out). The scratch repo is committed once per tick that changed a Status. `--once`: one tick, workers
keep running. `--dry-run`: print `would start NN in slot K`, write nothing. Last line `KANBAN OK started N` or `KANBAN STOP
<reason>`. Finish handling (merge, review/blocked, retry, pause, timeout, orphans) is K3 and not here yet.

Files read (all under the scratch folder; `run` writes dispatcher.lock, K3 writes dispatcher.pause):
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
import argparse, json, os, pathlib, re, signal, subprocess, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import teamcfg  # noqa: E402
import plannerlog as pl  # noqa: E402
import tickets as tk  # noqa: E402
import parallel  # noqa: E402
import scratch as sc  # noqa: E402

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


# ------------------------------------------------------------------ run: start side (K2)

def ticket_key(t):
    m = re.match(r"(\d+)([a-z]*)", str(t.get("number") or ""), re.I)
    return (int(m.group(1)), m.group(2).lower()) if m else (10 ** 9, str(t.get("number")))


def pick_ready(tickets, running, max_workers, paused_until, now):
    """Tickets to start now, in ticket-number order. Plain dicts (the `collect` shape): tickets need number, status,
    blockers_open, seam_paths, repo, branch; running needs seam_paths, repo, branch. None when nothing may start."""
    if paused_until is not None and paused_until > now:
        return []
    free = min(int(max_workers), HARD_CAP) - len(running)
    if free <= 0:
        return []
    group = (running[0].get("repo"), running[0].get("branch")) if running else None
    picked = []
    for t in sorted(tickets, key=ticket_key):
        if len(picked) >= free:
            break
        if t.get("status") != "todo" or t.get("blockers_open") or not t.get("branch"):
            continue
        if group is None:
            group = (t.get("repo"), t.get("branch"))      # v1: one repo + feature branch at a time
        if (t.get("repo"), t.get("branch")) != group:
            continue
        me = "\0candidate"
        others = [(f"r{i}", r.get("seam_paths") or []) for i, r in enumerate(running)] + \
                 [(f"p{i}", p.get("seam_paths") or []) for i, p in enumerate(picked)]
        if any(a == me for a, _, _ in parallel.seam_conflicts([(me, t.get("seam_paths") or []), *others])):
            continue
        picked.append(t)
    return picked


def lock_file(scratch):
    return scratch / "runs" / "dispatcher.lock"


def acquire_lock(scratch, feature):
    """Create runs/dispatcher.lock atomically. Returns None or the pid of a live dispatcher; a stale lock is replaced."""
    p = lock_file(scratch)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"pid": os.getpid(), "started": time.strftime("%Y-%m-%d %H:%M"), "feature": feature})
    for _ in range(3):
        try:
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                d = None
            pid = d.get("pid") if isinstance(d, dict) else None
            if teamcfg.pid_alive(pid):
                return pid
            try:
                p.unlink()
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        return None
    return -1


def release_lock(scratch):
    p = lock_file(scratch)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict) and d.get("pid") == os.getpid():
            p.unlink()
    except (OSError, ValueError):
        pass


def worker_argv(ticket_path, n):
    """Same command line as parallel.run_worker builds."""
    return [sys.executable, "-u", str(parallel.KIT / "run-worker.py"), "dev", f"ticket: {pathlib.Path(ticket_path).as_posix()}",
            "--slot", str(n)]


def used_slots(scratch, children):
    used = {n for n, (_, p, _) in children.items() if p.poll() is None}
    for d, alive in worker_locks(scratch):
        m = re.search(r"slot-(\d+)\.lock$", str(d.get("_path", "")))
        if alive and m:
            used.add(int(m.group(1)))
    return used


def check_run(a, say=print):
    """Preconditions, like scratch.py dispatch. Returns (tc, scratch, repos, cap) or raises Stop."""
    tc, scratch, repos = sc.ctx()
    cfg = tc["cfg"]
    if tc["source"] is None:
        raise Stop("no .team.json found (run it from the project repo root)")
    if not scratch.is_dir():
        raise Stop(f"no scratch folder {scratch.as_posix()}")
    if not (scratch / a.feature / "issues").is_dir():
        raise Stop(f"no tickets in {(scratch / a.feature / 'issues').as_posix()}")
    try:
        mw = int(cfg.get("max_workers") or 0)
    except (TypeError, ValueError):
        mw = 0
    if mw < 1:
        raise Stop(".team.json has no max_workers >= 1 (set it, at most 3)")
    if not cfg.get("setup"):
        raise Stop(".team.json has no `setup` (the commands that install dependencies in a fresh worktree)")
    if a.interval <= 0:
        raise Stop("--interval must be a positive number of seconds")
    return tc, scratch, repos, min(mw, HARD_CAP)


def open_tickets(scratch, feature, repos):
    cols = collect(scratch, [feature])
    out = []
    for status in ("todo", "in-progress"):
        for t in cols[status]:
            r = t["repo"]
            t["repo"] = r if r and not r.startswith("<") else (next(iter(repos)) if len(repos) == 1 else None)
            out.append(t)
    return out


def check_tree(scratch, feature, repos):
    """Repo clean, on the Branch of some open ticket. Returns {repo name: current branch}."""
    cur, tickets = {}, open_tickets(scratch, feature, repos)
    for t in tickets:
        if t["repo"] not in repos:
            raise Stop(f"{t['name']}: repo {t['repo']} is not one of {', '.join(repos) or 'the repos'}")
    for rn in {t["repo"] for t in tickets}:
        cur[rn] = sc.git(repos[rn], "branch", "--show-current")
        if sc.dirty(repos[rn]):
            raise Stop(f"{rn} has uncommitted changes")
    if tickets and not any(t["branch"] == cur[t["repo"]] for t in tickets):
        t = tickets[0]
        raise Stop(f"{t['repo']} is not on {t['branch']}")
    return cur


def tick(a, tc, scratch, repos, cap, children, cur, bad, say=print):
    """One pass: pick, prepare the slot, mark in-progress, start. Returns the list of started ticket numbers."""
    fd = scratch / a.feature
    tickets = [t for t in open_tickets(scratch, a.feature, repos) if t["branch"] == cur.get(t["repo"])]
    running = [t for t in tickets if t["status"] == "in-progress"]
    todo = [t for t in tickets if t["status"] == "todo" and t["name"] not in bad]
    pp = scratch / "runs" / "dispatcher.pause"
    paused = None
    try:
        paused = float(pp.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pass
    picks = pick_ready(todo, running, cap, paused, time.time())
    used, started, changed = used_slots(scratch, children), [], False
    for t in picks:
        free = [n for n in range(1, cap + 1) if n not in used]
        if not free:
            break
        n = free[0]
        used.add(n)
        if a.dry_run:
            say(f"would start {t['number']} in slot {n}")
            started.append(t["number"])
            continue
        repo, br = repos[t["repo"]], t["branch"]
        base = parallel.out(repo, "rev-parse", "HEAD")
        slot, err, note = parallel.prepare_slot(repo, n, parallel.ticket_branch(br, t["number"]), base or "HEAD",
                                                tc["cfg"]["setup"], scratch / "runs")
        if err or not base:
            say(f"  {t['number']}: slot {n} not ready: {err or 'cannot read the branch tip'}; skipped")
            bad.add(t["name"])
            continue
        path = fd / "issues" / t["name"]
        text, crlf = sc.pl.read(path)
        new = sc.set_status(text, "in-progress")
        if new is None:
            say(f"  {t['number']}: no Status line; skipped")
            bad.add(t["name"])
            continue
        sc.pl.write(path, new, crlf)
        env = {**os.environ, "HDT_CONFIG": str(tc["source"]), "HDT_SLOT_OF": t["repo"], "PYTHONUNBUFFERED": "1"}
        try:
            out = open(scratch / "runs" / f"kanban-slot-{n}.out", "ab")
            proc = subprocess.Popen(worker_argv(path, n), cwd=slot, env=env, stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, start_new_session=True)
            out.close()
        except OSError as e:
            sc.pl.write(path, text, crlf)
            say(f"  {t['number']}: cannot start the worker: {e}; skipped")
            bad.add(t["name"])
            continue
        children[n] = (t, proc, time.time())
        changed = True
        started.append(t["number"])
        say(f"started {t['number']} in slot {n} ({parallel.ticket_branch(br, t['number'])} @ {base[:8]}, pid {proc.pid})"
            + (f" [{note}]" if note else ""))
    if changed:
        sha = sc.commit_scratch(scratch, f"dispatch {' '.join(started)} (kanban)")
        if sha:
            say(f"  scratch committed {sha}")
    return started


def cmd_run(a):
    tc, scratch, repos, cap = check_run(a)
    cur = check_tree(scratch, a.feature, repos)
    st = dispatcher_status(tc, scratch)
    if st["running"] and not a.dry_run:
        raise Stop(f"a dispatcher is already running (pid {st['pid']})")
    live = [d for d, al in worker_locks(scratch) if al]
    if live:
        raise Stop(f"a worker is running ({live[0].get('role')} {live[0].get('ticket') or '-'}); "
                   "it is not one of this dispatcher's")
    children, bad, started, stop = {}, set(), 0, []
    if a.dry_run:
        n = len(tick(a, tc, scratch, repos, cap, children, cur, bad))
        print(f"KANBAN OK started 0 (dry run: would start {n})")
        return 0
    other = acquire_lock(scratch, a.feature)
    if other is not None:
        raise Stop(f"a dispatcher is already running (pid {other})" if other > 0 else "cannot take the dispatcher lock")
    old = {s: signal.signal(s, lambda *_: stop.append(1)) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        while True:
            started += len(tick(a, tc, scratch, repos, cap, children, cur, bad))
            if a.once or stop:
                break
            deadline = time.time() + a.interval
            while time.time() < deadline and not stop:
                time.sleep(0.2)
            if stop:
                break
    finally:
        for s, h in old.items():
            signal.signal(s, h)
        release_lock(scratch)
    print(f"KANBAN OK started {started}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("board"); p.add_argument("--feature"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("status"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("run"); p.add_argument("feature"); p.add_argument("--once", action="store_true")
    p.add_argument("--dry-run", action="store_true"); p.add_argument("--interval", type=float, default=10)
    a = ap.parse_args()
    try:
        return {"board": cmd_board, "status": cmd_status, "run": cmd_run}[a.cmd](a)
    except Stop as e:
        print(f"KANBAN STOP {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
