#!/usr/bin/env python3
"""Kanban view of a project's tickets and the dispatcher. `board` and `status` are READ-ONLY; only `run` writes. No model.

Usage (cwd = the project repo root, like scratch.py):
    python <kit>/kanban.py board [--feature F] [--json]   # tickets by Status: todo, in-progress, review, blocked, done
    python <kit>/kanban.py status [--json]                # dispatcher state: running, pid, workers, pause
    python <kit>/kanban.py run <feature> [--once] [--dry-run] [--interval S] [--grace S] [--drain]
                                                          # each tick: reap finished workers (K3), start ready tickets (K2)
    python <kit>/kanban.py stop [--now] [--wait S]        # graceful: no new starts; --now also stops the workers (K4)
    python <kit>/kanban.py pause [--minutes N]            # nothing starts for N minutes (default 60); running workers go on
    python <kit>/kanban.py resume                         # remove the pause
    python <kit>/kanban.py service <feature>              # PRINT the systemd-run command that starts `run` (installs nothing)

`run` (docs/open/kanban-dispatcher.md D3/D5/D6): takes runs/dispatcher.lock (refuses when that pid is alive, replaces a
stale one, removes it on exit, SIGTERM and SIGINT), needs `.team.json` max_workers >= 1 and `setup`, a clean repo on the
ticket's Branch and no live worker lock. A READY ticket (pick_ready: todo, blockers done, Seam apart from running tickets
and from each other, same repo and Branch as the running group, free slot, cap min(max_workers, 3), not paused) gets a pool
slot (parallel.prepare_slot), Status in-progress and a detached `run-worker.py dev --slot N` (stdout in
runs/kanban-slot-N.out). The scratch repo is committed once per tick that changed a Status. `--once`: one tick, workers
keep running. `--dry-run`: print `would start NN in slot K`, write nothing. Last line `KANBAN OK started N` or `KANBAN STOP
<reason>`.

FINISH side (K3, D3/D4/D6), every tick before picking: a tracked run (runs/kanban-state.json: slot, runs.log position,
start time, failures) is FINISHED when its slot lock is gone or dead and a new runs.log line for the ticket exists.
PASS + a commit on `<Branch>--NN`: merged into the feature branch (parallel.merge_ticket, one merge lock, ticket order),
`merged NN -> sha`, Status review, `NOTE parallel NN merged <sha>` in runs.log. PASS without a commit: blocked. A merge
conflict or any other FAIL or crash (no runs.log line, `FAIL no RESULT`, timeout): first one -> Status todo (re-queued,
branch kept), second -> blocked with `Dispatcher: blocked after 2 failed runs: <RESULT line>` in `## Result`. `FAIL
usage-limit`: no retry counted, ticket back to todo, runs/dispatcher.pause = the reset epoch (parsed from `resets at
5pm` / `in N minutes`, else now+30 min), `KANBAN PAUSE until <time>`, nothing starts until then. Timeout: `.team.json`
`run_seconds` (default 1800), that slot only is stopped with scratch.stop_one, then handled as a crash. Orphan: a stale
slot lock of an in-progress ticket with no new runs.log line on two ticks in a row -> blocked, never re-run, the stale
lock removed. A run adopts workers an earlier `--once` left (slot lock with a live pid of an in-progress ticket of the
feature); any other live lock refuses. SIGTERM/SIGINT: no new starts, wait up to `--grace S` (default 0) for running
workers, release the lock. `--drain`: no new starts, keep reaping until nothing runs, exit 0. When no todo ticket is left
and nothing runs: `KANBAN OK done: merged N, review N, blocked N`, exit 0 (not with --once).

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

STOP side (K4, D6): `stop` reads runs/dispatcher.lock. No lock or a dead pid: `KANBAN OK not running`. A live pid whose
/proc/<pid>/cmdline (or `ps`) does not contain `kanban.py` is never signalled: `KANBAN STOP pid N is not a kanban
dispatcher (stale lock)`, exit 1, nothing removed. Graceful (default): SIGTERM (the loop starts nothing, releases the lock;
with `run --grace S` it first waits for its workers), wait up to `--wait S` (default 60) for the lock to go, then
`KANBAN OK stopped` or `KANBAN OK draining (N workers still running)` (exit 0, not an error; workers are detached and the
next `run` adopts and merges them). `--now`: SIGTERM, then every live worker lock is stopped with scratch.stop_one (like
`scratch.py stop`), their in-progress tickets go back to `todo`, runs.log gets `NOTE kanban force-stopped NN`, one scratch
commit, `KANBAN OK force-stopped N`. `pause` writes runs/dispatcher.pause (now + N minutes); a running loop reads it every
tick. `service` writes no file: it prints the `systemd-run --user` line a planner uses (a plain background job dies with
Hermes' shell; a transient unit does not). The template `kit/systemd/hdt-kanban@.service` is the same thing as a unit file.

Exit 0, or 1 with one line `KANBAN STOP <reason>` (no .team.json, no scratch folder, no feature, a foreign pid, a
dispatcher that does not exit).
"""
import argparse, json, os, pathlib, re, shlex, signal, subprocess, sys, threading, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import teamcfg  # noqa: E402
import plannerlog as pl  # noqa: E402
import tickets as tk  # noqa: E402
import parallel  # noqa: E402
import scratch as sc  # noqa: E402
import preflight  # noqa: E402

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
            bl = tk.blocker_tokens(tk.field(text, "Blocked by") or "none")
            # a blocker in `review` is already merged into the feature branch (only the planner's review sets `done`,
            # which nothing in this loop waits for), so it releases its dependents (K5)
            open_b = tk.open_blockers(tk.field(text, "Blocked by"), p, by_num, ("done", "review"))
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
    return parallel.worker_argv(ticket_path, n)


def used_slots(scratch, children, fin=None):
    used = {n for n, (_, p, _) in children.items() if p.poll() is None}
    if fin:
        used |= {e["slot"] for e in fin.state.values() if e.get("running") and e.get("slot")}
    for d, alive in worker_locks(scratch):
        m = re.search(r"slot-(\d+)\.lock$", str(d.get("_path", "")))
        if alive and m:
            used.add(int(m.group(1)))
    return used


def check_run(a, say=print):
    """Preconditions, like scratch.py dispatch. Returns (tc, scratch, repos, cap) or raises Stop."""
    tc, scratch, repos = sc.ctx()
    cfg = tc["cfg"]
    if not preflight.has_config(tc):
        raise Stop("no .team.json found (run it from the project repo root)")
    if not scratch.is_dir():
        raise Stop(f"no scratch folder {scratch.as_posix()}")
    if not (scratch / a.feature / "issues").is_dir():
        raise Stop(f"no tickets in {(scratch / a.feature / 'issues').as_posix()}")
    mw = preflight.max_workers(cfg)
    if mw < 1:
        raise Stop(".team.json has no max_workers >= 1 (set it, at most 3)")
    if not preflight.has_setup(cfg):
        raise Stop(".team.json has no `setup` (the commands that install dependencies in a fresh worktree)")
    if a.interval <= 0:
        raise Stop("--interval must be a positive number of seconds")
    if a.grace < 0:
        raise Stop("--grace must not be negative")
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


def repo_dirty(repo, scratch):
    """`git status --short` of `repo` without the scratch folder's own paths: a scratch folder tracked inside the project
    repo is rewritten by the dispatcher's status edits, which must not count as a dirty repo."""
    try:
        rel = pathlib.Path(scratch).resolve().relative_to(pathlib.Path(repo).resolve()).as_posix()
    except ValueError:
        rel = None                                       # scratch outside the repo: nothing to exclude
    if rel in (None, "."):
        return sc.dirty(repo)
    return sc.git(repo, "status", "--short", "--", ".", f":(exclude,literal){rel}") or ""


def check_tree(scratch, feature, repos):
    """Repo clean, on the Branch of some open ticket. Returns {repo name: current branch}."""
    cur, tickets = {}, open_tickets(scratch, feature, repos)
    for t in tickets:
        if t["repo"] not in repos:
            raise Stop(f"{t['name']}: repo {t['repo']} is not one of {', '.join(repos) or 'the repos'}")
    for rn in {t["repo"] for t in tickets}:
        cur[rn] = sc.git(repos[rn], "branch", "--show-current")
        if not preflight.clean(repo_dirty(repos[rn], scratch)):
            raise Stop(f"{rn} has uncommitted changes")
    if tickets and not any(preflight.on_branch(cur[t["repo"]], t["branch"]) for t in tickets):
        t = tickets[0]
        raise Stop(f"{t['repo']} is not on {t['branch']}")
    return cur

# ------------------------------------------------------------------ run: finish side (K3)

BOOT_GRACE = 30          # seconds a just-started worker may have no lock yet before "no lock, no result" is a crash
RESET_RE = re.compile(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d\d))?\s*([ap]m)?", re.I)
IN_RE = re.compile(r"\bin\s+(\d+)\s*(minute|min|hour|hr)s?\b", re.I)


def parse_reset(text, now):
    """Epoch a usage limit resets, from 'resets at 5pm' / 'resets 17:30' / 'in 20 minutes'; now + 30 min otherwise."""
    m = IN_RE.search(text or "")
    if m:
        return now + int(m.group(1)) * (3600 if m.group(2).lower().startswith("h") else 60)
    m = RESET_RE.search(text or "")
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if h < 24 and mi < 60:
            t = time.localtime(now)
            e = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, h, mi, 0, 0, 0, -1))
            return e if e > now else e + 86400
    return now + 1800


def stamp_epoch(stamp):
    try:
        return time.mktime(time.strptime(stamp, "%Y%m%d-%H%M%S"))
    except (ValueError, TypeError):
        return None


class Finisher:
    """Reaps the workers of one feature (docs/open/kanban-dispatcher.md D3, D4, D6). State per running ticket lives in
    runs/kanban-state.json so a later `run` adopts what an earlier one started."""

    def __init__(self, a, tc, scratch, repos, children, say=print):
        self.a, self.tc, self.scratch, self.repos, self.children, self.say = a, tc, scratch, repos, children, say
        self.fd = scratch / a.feature
        self.merge_lock = threading.Lock()
        self.path = scratch / "runs" / "kanban-state.json"
        d = {}
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        d = d if isinstance(d, dict) else {}
        self.state = d.get("running") if isinstance(d.get("running"), dict) else {}
        self.extra = d.get("extra") if isinstance(d.get("extra"), dict) else {}
        try:
            self.run_seconds = float(tc["cfg"].get("run_seconds") or 1800)
        except (TypeError, ValueError):
            self.run_seconds = 1800.0
        self.merged, self.notes, self.waiting = 0, [], set()

    # -- bookkeeping
    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"running": self.state, "extra": self.extra}), encoding="utf-8")

    @staticmethod
    def mine(runs, name):
        return [f for f in runs if len(f) > 6 and f[1] != "planner" and f"ticket={name}" in f[6:]]

    def track(self, name, slot, base, outoff):
        self.state[name] = {"slot": slot, "base": base, "stamp": None, "started": time.time(), "out": outoff,
                            "orphan": 0}
        self.save()

    def new_lines(self, e, runs, name):
        lines = self.mine(runs, name)
        if e.get("base") is not None:
            return lines[e["base"]:]
        return [f for f in lines if f[0] == e.get("stamp")] if e.get("stamp") else []

    def lock_of(self, name, locks):
        return next(((d, al) for d, al in locks if pathlib.PurePath(str(d.get("ticket") or "")).name == name), (None, False))

    def ticket(self, name):
        p = self.fd / "issues" / name
        return p if p.is_file() else None

    # -- ticket edits
    def set(self, name, status, note=None):
        p = self.ticket(name)
        if not p:
            return
        text, crlf = sc.pl.read(p)
        new = sc.set_status(text, status)
        if new is not None:
            text = new
        if note:
            text = sc.pl.append(text, note)
        sc.pl.write(p, text, crlf)

    def finish_entry(self, name, status, note=None):
        self.set(name, status, note)
        e = self.state.pop(name, None)
        if e and e.get("slot") in self.children:
            self.children.pop(e["slot"], None)
        self.save()
        self.notes.append(f"{name.split('-')[0]} {status}")

    # -- decisions
    def failure(self, name, runs, line, reason, logged):
        nn = sc.pl.num(name)
        fails = sum(1 for f in self.mine(runs, name) if not f[3].startswith("PASS") and "usage-limit" not in f[3])
        if not logged:
            self.extra[name] = self.extra.get(name, 0) + 1
        total = fails + self.extra.get(name, 0)
        last = line or f"RESULT dev FAIL {reason}"
        if total >= 2:
            self.say(f"blocked {nn}: {reason} (failed {total} times)")
            self.finish_entry(name, "blocked", f"Dispatcher: blocked after 2 failed runs: {last}")
        else:
            self.say(f"requeued {nn}: {reason} (failure {total} of 2; branch kept)")
            self.finish_entry(name, "todo", f"Dispatcher: run failed ({reason}); re-queued once: {last}")

    def usage_limit(self, name, f, ent):
        text = " ".join(f)
        for fld in f:
            if fld.endswith(".json") or "/" in fld:
                for cand in (pathlib.Path(fld), self.scratch.parent / fld):
                    try:
                        text += " " + str(json.loads(cand.read_text(encoding="utf-8")).get("result") or "")
                        break
                    except (OSError, ValueError, AttributeError):
                        continue
        out = self.scratch / "runs" / f"kanban-slot-{ent.get('slot')}.out"
        try:
            with open(out, "rb") as fh:
                fh.seek(ent.get("out") or 0)
                text += " " + fh.read().decode("utf-8", "replace")
        except OSError:
            pass
        until = parse_reset(text, time.time())
        pp = self.scratch / "runs" / "dispatcher.pause"
        pp.parent.mkdir(parents=True, exist_ok=True)
        pp.write_text(str(int(until)), encoding="utf-8")
        self.say(f"KANBAN PAUSE until {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(until))}")
        self.say(f"requeued {sc.pl.num(name)}: usage limit (no retry counted)")
        self.finish_entry(name, "todo")

    def merge(self, name, ent, f, runs):
        """True when the ticket is settled, False when it must wait for a clean feature tree."""
        text, _ = sc.pl.read(self.ticket(name))
        rn = tk.field(text, "Repo")
        if not rn or rn.startswith("<"):
            rn = next(iter(self.repos)) if len(self.repos) == 1 else rn
        br, nn = tk.field(text, "Branch"), sc.pl.num(name)
        if not ent.get("slot"):                                  # a serial `run-worker.py dev` worked on the branch itself
            self.say(f"reviewed {nn}: serial worker, nothing to merge")
            self.finish_entry(name, "review")
            return True
        repo = self.repos[rn]
        if sc.git(repo, "branch", "--show-current") != br or repo_dirty(repo, self.scratch):
            if name not in self.waiting:
                self.say(f"  {nn}: finished, but {rn} is not clean on {br}; merge waits")
            self.waiting.add(name)
            return False
        self.waiting.discard(name)
        tb = parallel.ticket_branch(br, nn)
        slot = parallel.slot_path(repo, ent["slot"]) if ent.get("slot") else None
        if slot and slot.exists():
            sc.git(slot, "checkout", "-q", "--detach")           # free the ticket branch for the merge and `branch -d`
        with self.merge_lock:
            state, detail = parallel.merge_ticket(repo, br, tb, name)
        if state == "merged":
            self.merged += 1
            self.say(f"merged {nn} -> {detail}")
            sc.pl.log(self.scratch, self.a.feature, f"NOTE parallel {nn} merged {detail}", "merge", ticket=None,
                      repo=rn)
            self.finish_entry(name, "review", f"Dispatcher: merged {tb} into {br} -> {detail}")
        elif state == "conflict":
            self.say(f"  {nn}: FAIL conflict merging into {br} ({', '.join(detail)}); branch {tb} kept")
            self.failure(name, runs, f"RESULT dev FAIL conflict merging {tb} into {br}", "merge conflict", False)
        else:
            self.say(f"  {nn}: PASS but no commit on {tb}; nothing merged")
            self.finish_entry(name, "blocked", "Dispatcher: PASS but no commit")
        return True

    def stop_slot(self, lock):
        sc.stop_one(lock)
        end = time.time() + 15
        while time.time() < end and teamcfg.read_lock_file(lock["_path"])[1]:
            time.sleep(0.3)

    # -- one pass
    def reap(self):
        """Decide the state of every running ticket of the feature. Sets self.notes when a Status changed."""
        self.notes = []
        cols = collect(self.scratch, [self.a.feature])
        inprog = {t["name"] for t in cols["in-progress"]}
        for name in list(self.state):
            if name not in inprog:
                self.state.pop(name)                        # the planner moved it: not ours any more
        runs, locks, now = read_runs(self.scratch), worker_locks(self.scratch), time.time()
        for name in sorted(inprog):
            if name not in self.state:                      # adopted: in-progress with nothing tracking it
                d, _ = self.lock_of(name, locks)
                m = re.search(r"slot-(\d+)\.lock$", str((d or {}).get("_path", "")))
                self.state[name] = {"slot": int(m.group(1)) if m else None, "base": None if d else len(self.mine(runs, name)),
                                    "stamp": (d or {}).get("stamp"), "started": stamp_epoch((d or {}).get("stamp")) or now,
                                    "out": None, "orphan": 0}
        for name in sorted(self.state, key=lambda n: ticket_key({"number": sc.pl.num(n)})):
            if not self.ticket(name):
                self.state.pop(name); continue
            e = self.state[name]
            d, alive = self.lock_of(name, locks)
            if d:
                e["stamp"] = d.get("stamp") or e.get("stamp")
                m = re.search(r"slot-(\d+)\.lock$", str(d.get("_path", "")))
                if m and not e.get("slot"):
                    e["slot"] = int(m.group(1))
            proc = self.children.get(e.get("slot"))
            proc_alive = bool(proc and proc[1].poll() is None and proc[0]["name"] == name)
            new = self.new_lines(e, runs, name)
            if alive or proc_alive:
                if now - e["started"] > self.run_seconds and d and alive:
                    self.say(f"  {sc.pl.num(name)}: timed out after {int(now - e['started'])}s; stopping slot {e.get('slot')}")
                    self.stop_slot(d)
                    runs = read_runs(self.scratch)
                    new = self.new_lines(e, runs, name)
                    self.failure(name, runs, f"RESULT dev {new[-1][3]}" if new else None, "timed out", bool(new)
                                 and not new[-1][3].startswith("PASS"))
                continue
            if new:
                if d and not alive:
                    pathlib.Path(d["_path"]).unlink(missing_ok=True)
                f = new[-1]
                word = parallel.verdict_word(f"RESULT {f[1]} {f[3]}")
                if word == "PASS":
                    if not self.merge(name, e, f, runs):
                        continue
                elif "usage-limit" in f[3]:
                    self.usage_limit(name, f, e)
                else:
                    self.failure(name, runs, f"RESULT {f[1]} {f[3]}", "FAIL", True)
                continue
            if d:                                                   # dead lock, no result
                e["orphan"] = e.get("orphan", 0) + 1
                if e["orphan"] >= 2:
                    pathlib.Path(d["_path"]).unlink(missing_ok=True)   # stale per teamcfg (launcher and worker gone)
                    self.say(f"blocked {sc.pl.num(name)}: orphaned (no live worker, no result)")
                    self.finish_entry(name, "blocked", "Dispatcher: orphaned (no live worker, no result)")
                continue
            if proc or now - e["started"] > BOOT_GRACE:             # no lock, no result: the worker is gone
                out = self.scratch / "runs" / f"kanban-slot-{e.get('slot')}.out"
                last = next((l for l in reversed(tail(out, 40)) if l.startswith("RESULT ")), None)
                self.failure(name, runs, last or "RESULT dev FAIL no RESULT (worker gone, no runs.log line)",
                             "worker gone, no result", False)
        self.save()

    def running(self):
        return sorted(self.state)

    def counts(self):
        cols = collect(self.scratch, [self.a.feature])
        return len(cols["review"]), len(cols["blocked"])


def tick(a, tc, scratch, repos, cap, children, cur, bad, say=print, fin=None, start=True):
    """One pass: reap finished workers (K3, when `fin`), then pick, prepare the slot, mark in-progress, start.
    Returns the list of started ticket numbers."""
    if fin:
        fin.reap()
    if not start:
        if fin and fin.notes:
            sha = sc.commit_scratch(scratch, f"finish {' '.join(fin.notes)} (kanban)")
            if sha:
                say(f"  scratch committed {sha}")
        return []
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
    used, started, changed = used_slots(scratch, children, fin), [], False
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
        runs_before = len(fin.mine(read_runs(scratch), t["name"])) if fin else 0
        out_file = scratch / "runs" / f"kanban-slot-{n}.out"
        out_off = out_file.stat().st_size if out_file.is_file() else 0
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
        if fin:
            fin.track(t["name"], n, runs_before, out_off)
        changed = True
        started.append(t["number"])
        say(f"started {t['number']} in slot {n} ({parallel.ticket_branch(br, t['number'])} @ {base[:8]}, pid {proc.pid})"
            + (f" [{note}]" if note else ""))
    if changed or (fin and fin.notes):
        parts = ([f"finish {' '.join(fin.notes)}"] if fin and fin.notes else []) + \
                ([f"dispatch {' '.join(started)}"] if started else [])
        sha = sc.commit_scratch(scratch, f"{'; '.join(parts)} (kanban)")
        if sha:
            say(f"  scratch committed {sha}")
    return started


def adoptable(scratch, feature, repos):
    """Refusal text for the first live worker lock this dispatcher cannot adopt, else None. Adoptable: a slot lock (or a
    worker.lock of role dev) whose ticket is an in-progress ticket of this feature."""
    mine = {t["name"] for t in collect(scratch, [feature])["in-progress"]}
    for d, alive in worker_locks(scratch):
        if not alive:
            continue
        name = pathlib.PurePath(str(d.get("ticket") or "")).name
        is_slot = re.search(r"slot-\d+\.lock$", str(d.get("_path", ""))) is not None
        in_feature = (scratch / feature / "issues" / name).is_file() if name else False
        if in_feature and name in mine and (is_slot or d.get("role") == "dev"):
            continue
        return f"a worker is running ({d.get('role')} {d.get('ticket') or '-'}); it is not one of this feature's"
    return None


def cmd_run(a):
    tc, scratch, repos, cap = check_run(a)
    cur = check_tree(scratch, a.feature, repos)
    st = dispatcher_status(tc, scratch)
    if st["running"] and not a.dry_run:
        raise Stop(f"a dispatcher is already running (pid {st['pid']})")
    why = adoptable(scratch, a.feature, repos)
    if why:
        raise Stop(why)
    children, bad, started, stop = {}, set(), 0, []
    if a.dry_run:
        n = len(tick(a, tc, scratch, repos, cap, children, cur, bad))
        print(f"KANBAN OK started 0 (dry run: would start {n})")
        return 0
    other = acquire_lock(scratch, a.feature)
    if other is not None:
        raise Stop(f"a dispatcher is already running (pid {other})" if other > 0 else "cannot take the dispatcher lock")
    old = {s: signal.signal(s, lambda *_: stop.append(1)) for s in (signal.SIGTERM, signal.SIGINT)}
    fin = Finisher(a, tc, scratch, repos, children)
    label = "drained" if a.drain else "done"

    def nap(secs):
        deadline = time.time() + secs
        while time.time() < deadline and not stop:
            time.sleep(0.2)

    try:
        while True:
            started += len(tick(a, tc, scratch, repos, cap, children, cur, bad, fin=fin,
                                start=not a.drain and not stop))
            if a.once or stop:
                break
            todo = [t for t in collect(scratch, [a.feature])["todo"] if t["name"] not in bad and t["ready"]]
            if not fin.running() and (a.drain or not todo):
                rv, bl = fin.counts()
                print(f"KANBAN OK {label}: merged {fin.merged}, review {rv}, blocked {bl}")
                return 0
            nap(a.interval)
            if stop:
                break
        if stop and a.grace > 0:
            end = time.time() + a.grace
            while fin.running() and time.time() < end:
                tick(a, tc, scratch, repos, cap, children, cur, bad, fin=fin, start=False)
                time.sleep(0.5)
    finally:
        for s, h in old.items():
            signal.signal(s, h)
        release_lock(scratch)
    print(f"KANBAN OK started {started}")
    return 0


# ------------------------------------------------------------------ stop, pause, resume, service (K4)

proc_cmdline = teamcfg.proc_cmdline                          # shared with scratch.py (teamcfg.dispatcher_alive)
read_dispatcher = teamcfg.read_dispatcher


def wait_until(cond, secs):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.2)
    return cond()


def cmd_stop(a):
    tc, scratch = project()
    if a.wait < 0:
        raise Stop("--wait must not be negative")
    d = read_dispatcher(scratch)
    pid = d.get("pid") if d else None
    if not teamcfg.pid_alive(pid):
        print("KANBAN OK not running")
        return 0
    pid = int(pid)
    if pid == os.getpid() or "kanban.py" not in (proc_cmdline(pid) or ""):
        print(f"KANBAN STOP pid {pid} is not a kanban dispatcher (stale lock)")
        return 1
    feature = d.get("feature")
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as e:
        raise Stop(f"cannot signal dispatcher pid {pid}: {e}")
    gone = lambda: not lock_file(scratch).exists() or not teamcfg.pid_alive(pid)
    if not a.now:
        ok = wait_until(gone, a.wait)
        n = len(live_locks(scratch))
        print(f"KANBAN OK draining ({n} workers still running)" if n or not ok else "KANBAN OK stopped")
        return 0
    stopped = []
    for lock, alive in worker_locks(scratch):
        if not alive:
            continue
        sc.stop_one(lock)
        wait_until(lambda lk=lock: not teamcfg.read_lock_file(lk["_path"])[1], 15)
        stopped.append(lock)
    if not wait_until(gone, max(a.wait, 1)):
        print(f"KANBAN STOP dispatcher pid {pid} is still running after {a.wait:g}s")
        return 1
    names = []
    for lock in stopped:
        name = pathlib.PurePath(str(lock.get("ticket") or "")).name
        names.append(name)
        for f in (feature, lock.get("feature")):
            path = scratch / f / "issues" / name if f and name else None
            if path and path.is_file():
                text, crlf = sc.pl.read(path)
                if tk.field(text, "Status") == "in-progress":
                    new = sc.set_status(text, "todo")
                    if new is not None:
                        sc.pl.write(path, new, crlf)
                break
    log_feature = feature if feature and (scratch / feature).is_dir() else None
    for name in names:
        if log_feature and name:
            sc.pl.log(scratch, log_feature, f"NOTE kanban force-stopped {sc.pl.num(name)}", "stop", ticket=None)
    if names and sc.commit_scratch(scratch, f"force-stopped {' '.join(sc.pl.num(n) for n in names if n)} (kanban)"):
        pass
    print(f"KANBAN OK force-stopped {len(stopped)}")
    return 0


def cmd_pause(a):
    tc, scratch = project()
    if a.minutes <= 0:
        raise Stop("--minutes must be a positive number")
    until = int(time.time() + a.minutes * 60)
    pp = scratch / "runs" / "dispatcher.pause"
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_text(str(until), encoding="utf-8")
    print(f"KANBAN OK paused until {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(until))}")
    return 0


def cmd_resume(a):
    tc, scratch = project()
    pp = scratch / "runs" / "dispatcher.pause"
    had = pp.is_file()
    pp.unlink(missing_ok=True)
    print("KANBAN OK resumed" if had else "KANBAN OK not paused")
    return 0


def cmd_service(a):
    """Print, never install or write."""
    tc, scratch = project()
    if not (scratch / a.feature / "issues").is_dir():
        raise Stop(f"no feature {a.feature} (no tickets in {(scratch / a.feature / 'issues').as_posix()})")
    repo = tc["base"]
    safe = lambda x: re.sub(r"[^A-Za-z0-9_-]+", "-", x).strip("-") or "x"
    unit = f"hdt-kanban-{safe(pathlib.Path(repo).name)}-{safe(a.feature)}"
    q = shlex.quote
    print(" ".join(["systemd-run", "--user", "--collect", f"--unit={unit}", f"--working-directory={q(str(repo))}",
                    "--property=KillMode=process", q("--setenv=PATH=" + os.environ.get("PATH", "")), "python3",
                    q(str(pathlib.Path(__file__).resolve())), "run", q(a.feature)]))
    print(f"KANBAN OK printed (nothing installed); stop it with `kanban.py stop`, see it with `systemctl --user status {unit}`")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("board"); p.add_argument("--feature"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("status"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("run"); p.add_argument("feature"); p.add_argument("--once", action="store_true")
    p.add_argument("--dry-run", action="store_true"); p.add_argument("--interval", type=float, default=10)
    p.add_argument("--grace", type=float, default=0); p.add_argument("--drain", action="store_true")
    p = sub.add_parser("stop"); p.add_argument("--now", action="store_true"); p.add_argument("--wait", type=float, default=60)
    p = sub.add_parser("pause"); p.add_argument("--minutes", type=float, default=60)
    sub.add_parser("resume")
    p = sub.add_parser("service"); p.add_argument("feature")
    a = ap.parse_args()
    try:
        return {"board": cmd_board, "status": cmd_status, "run": cmd_run, "stop": cmd_stop, "pause": cmd_pause,
                "resume": cmd_resume, "service": cmd_service}[a.cmd](a)
    except Stop as e:
        print(f"KANBAN STOP {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
