#!/usr/bin/env python3
"""`kanban.py run` start side (K2), no model.

pick_ready unit cases with plain dicts, then a throwaway repo with a FAKE `claude` on PATH (copied from
parallel_dispatch.py): --dry-run writes nothing, --once starts the ready non-overlapping tickets up to the cap and marks
them in-progress, an open blocker is not started, a second run refuses while the lock pid lives, a stale lock is
replaced, the lock is gone after --once and after SIGTERM of a looping run, and dirty tree / wrong branch / no
max_workers are refused.

    python tests/kanban_run.py [--keep]

Last line: `<n>/<total> checks passed`; exit 0 only when all pass.
"""
import json, os, pathlib, shutil, signal, subprocess, sys, tempfile, textwrap, time

KIT = pathlib.Path(__file__).resolve().parent.parent / "kit"
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-krun-"))
BIN = ROOT / "bin"
os.environ.update(GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1", PATH=f"{BIN}{os.pathsep}{os.environ['PATH']}")
os.environ.pop("ANTHROPIC_API_KEY", None)
sys.path.insert(0, str(KIT))
import kanban  # noqa: E402
results = []

FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, pathlib, re, subprocess, sys, time
a = sys.argv[1:]
if a[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True})); sys.exit(0)
prompt = a[a.index("-p") + 1]
ticket = pathlib.Path(re.search(r"ticket:\s*(\S+)", prompt).group(1))
text = ticket.read_text()
spec = re.search(r"^Fake:\s*(.+)$", text, re.M).group(1).split()
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(f"start {ticket.name} {os.getcwd()}\n")
time.sleep(float(os.environ.get("FAKE_SLEEP", "2")))
path, word = spec[1], spec[2]
p = pathlib.Path(path); p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(word + "\n")
subprocess.run(["git", "add", path], check=True)
subprocess.run(["git", "commit", "-q", "-m", f"{ticket.stem} built"], check=True)
print(json.dumps({"type": "result", "subtype": "success", "result": "did it\nRESULT: PASS built " + path, "num_turns": 3,
                  "usage": {"input_tokens": 1, "output_tokens": 1}, "total_cost_usd": 0, "permission_denials": []}))
'''


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name)
    if not ok and detail:
        print("      " + str(detail)[-2500:].replace("\n", "\n      "))


def sh(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"FAILED git {args}: {r.stdout}{r.stderr}")
    return r.stdout.strip()


def ticket(repo, nn, seam, status="todo", blocked="None", branch="feat", fake=None):
    (repo / ".scratch/feat/issues" / f"{nn}-t.md").write_text(textwrap.dedent(f"""\
        # {nn}: t

        Lane: full
        Branch: {branch}
        Status: {status}
        Round: 0
        Blocked by: {blocked}
        Seam: {seam}
        Test command: true
        Fake: {fake or f"write src/{nn}.py W{nn}"}

        ## Result
        """))


def status_of(repo, nn):
    import re
    return re.search(r"^Status:\s*(\S+)", (repo / ".scratch/feat/issues" / f"{nn}-t.md").read_text(), re.M).group(1)


def setup_repo(name, cfg):
    repo = ROOT / name
    repo.mkdir()
    sh(repo, "init", "-q", "-b", "main")
    sh(repo, "config", "user.email", "t@example.com"); sh(repo, "config", "user.name", "T")
    (repo / ".gitignore").write_text(".scratch/\n")
    (repo / ".team.json").write_text(json.dumps(cfg))
    sh(repo, "add", "-A"); sh(repo, "commit", "-q", "-m", "init")
    sh(repo, "checkout", "-q", "-b", "feat")
    (repo / ".scratch/feat/issues").mkdir(parents=True)
    sh(repo / ".scratch", "init", "-q", "-b", "main")
    sh(repo / ".scratch", "config", "user.email", "t@example.com"); sh(repo / ".scratch", "config", "user.name", "T")
    (repo / ".scratch/.gitignore").write_text("runs/\n")
    return repo


def run(repo, *args, timeout=60):
    r = subprocess.run([sys.executable, str(KIT / "kanban.py"), "run", "feat", *args], cwd=repo, capture_output=True,
                       text=True, timeout=timeout)
    return r.returncode, r.stdout + r.stderr


def snapshot(root):
    snap = {}
    for p in sorted(root.rglob("*")):
        if ".git" in p.parts:
            continue
        snap[p.relative_to(root).as_posix()] = (p.read_bytes() if p.is_file() else None, p.stat().st_mtime_ns)
    return snap


def stop_workers(repo):
    for d, alive in kanban.teamcfg.read_locks(repo / ".scratch"):
        for k in ("worker_pid", "launcher_pid"):
            if d.get(k) and alive:
                kanban.teamcfg.kill_tree(d[k])


def wait_gone(pids, secs=30):
    end = time.time() + secs
    while time.time() < end and any(kanban.teamcfg.pid_alive(p) for p in pids):
        time.sleep(0.3)


def T(n, status="todo", seam=("a.py",), blockers=(), repo="app", branch="feat"):
    return {"number": n, "status": status, "seam_paths": list(seam), "blockers_open": list(blockers), "repo": repo,
            "branch": branch, "name": f"{n}-t.md"}


def numbers(ts):
    return [t["number"] for t in ts]


def unit():
    pr = kanban.pick_ready
    now = 1000.0
    check("pick_ready: todo tickets with separate Seams, in number order",
          numbers(pr([T("03", seam=["c.py"]), T("01", seam=["a.py"]), T("02", seam=["b.py"])], [], 3, None, now))
          == ["01", "02", "03"])
    check("pick_ready: only Status todo", numbers(pr([T("01", "review"), T("02", "done", seam=["b.py"]),
                                                     T("03", "blocked", seam=["c.py"]), T("04", seam=["d.py"])],
                                                    [], 3, None, now)) == ["04"])
    check("pick_ready: a ticket with an open blocker waits, one whose blockers are done starts",
          numbers(pr([T("01", blockers=["00"]), T("02", seam=["b.py"])], [], 3, None, now)) == ["02"])
    run1 = [T("01", "in-progress", seam=["src/"])]
    check("pick_ready: Seam overlap with a running ticket (folder vs file) waits",
          numbers(pr([T("02", seam=["src/x.py"]), T("03", seam=["lib/y.py"])], run1, 3, None, now)) == ["03"])
    check("pick_ready: a candidate with no Seam path waits while something runs",
          numbers(pr([T("02", seam=[])], run1, 3, None, now)) == [])
    check("pick_ready: overlap between two candidates starts only the lower number",
          numbers(pr([T("01", seam=["src/a.py"]), T("02", seam=["src/"]), T("03", seam=["lib/z.py"])], [], 3, None, now))
          == ["01", "03"])
    check("pick_ready: cap = max_workers minus running",
          numbers(pr([T("02", seam=["b.py"]), T("03", seam=["c.py"]), T("04", seam=["d.py"])],
                     [T("01", "in-progress", seam=["a.py"])], 2, None, now)) == ["02"])
    check("pick_ready: max_workers 1 starts one", len(pr([T("01"), T("02", seam=["b.py"])], [], 1, None, now)) == 1)
    check("pick_ready: hard cap 3 even when max_workers is 9",
          len(pr([T(f"0{i}", seam=[f"{i}.py"]) for i in range(1, 6)], [], 9, None, now)) == 3)
    check("pick_ready: nothing when every slot is busy",
          pr([T("04", seam=["d.py"])], [T("01", "in-progress", seam=["a.py"]), T("02", "in-progress", seam=["b.py"])],
             2, None, now) == [])
    check("pick_ready: nothing while paused, starts again once the pause is over",
          pr([T("01")], [], 3, now + 60, now) == [] and numbers(pr([T("01")], [], 3, now - 1, now)) == ["01"])
    check("pick_ready: another Branch than the running group waits",
          numbers(pr([T("02", seam=["b.py"], branch="other"), T("03", seam=["c.py"])],
                     [T("01", "in-progress", seam=["a.py"])], 3, None, now)) == ["03"])
    check("pick_ready: another Repo than the running group waits",
          numbers(pr([T("02", seam=["b.py"], repo="web"), T("03", seam=["c.py"])],
                     [T("01", "in-progress", seam=["a.py"])], 3, None, now)) == ["03"])
    check("pick_ready: with nothing running the first ticket picks the group's Branch",
          numbers(pr([T("01", branch="x"), T("02", seam=["b.py"], branch="feat"), T("03", seam=["c.py"], branch="x")],
                     [], 3, None, now)) == ["01", "03"])
    check("pick_ready: ticket order is numeric (2 before 10), 02b after 02",
          numbers(pr([T("10", seam=["j.py"]), T("02b", seam=["b2.py"]), T("2", seam=["b.py"])], [], 3, None, now))
          == ["2", "02b", "10"] or numbers(pr([T("10", seam=["j.py"]), T("02b", seam=["b2.py"]), T("02", seam=["b.py"])],
                                              [], 3, None, now)) == ["02", "02b", "10"])


def main():
    unit()
    BIN.mkdir()
    (BIN / "claude").write_text(FAKE_CLAUDE); (BIN / "claude").chmod(0o755)
    os.environ["FAKE_LOG"] = str(ROOT / "fake.log")
    os.environ["FAKE_SLEEP"] = "3"
    cfg = {"test": "true", "max_workers": 2, "setup": [{"cmd": "true"}]}
    repo = setup_repo("app", cfg)
    scratch = repo / ".scratch"
    ticket(repo, "01", "`src/a.py`"); ticket(repo, "02", "`src/b.py`"); ticket(repo, "03", "`src/c.py`")
    ticket(repo, "04", "`lib/d.py`", blocked="01"); ticket(repo, "05", "`src/`")
    sh(scratch, "add", "-A"); sh(scratch, "commit", "-q", "-m", "tickets")
    scratch_head = sh(scratch, "rev-parse", "HEAD")

    # --- refusals and dry run
    before = snapshot(ROOT)
    rc, o = run(repo, "--dry-run")
    check("--dry-run lists the tickets it would start", rc == 0 and "would start 01 in slot 1" in o
          and "would start 02 in slot 2" in o and "would start 03" not in o and o.strip().splitlines()[-1].startswith("KANBAN OK"), o)
    check("--dry-run wrote nothing (tree snapshot, no lock, no status change)", snapshot(ROOT) == before
          and not (scratch / "runs/dispatcher.lock").exists())

    # --- the real start
    rc, o = run(repo, "--once")
    last = o.strip().splitlines()[-1]
    check("--once starts exactly 01 and 02 (cap 2; 03 waits, 04 blocked, 05 overlaps)",
          rc == 0 and last == "KANBAN OK started 2" and "started 01 in slot 1" in o and "started 02 in slot 2" in o, o)
    check("started tickets are in-progress, the others untouched",
          [status_of(repo, n) for n in ("01", "02", "03", "04", "05")] == ["in-progress"] * 2 + ["todo"] * 3)
    check("the dispatcher lock is gone after --once", not (scratch / "runs/dispatcher.lock").exists())
    check("one scratch commit holds the status changes", sh(scratch, "rev-parse", "HEAD") != scratch_head
          and sh(scratch, "log", "-1", "--format=%s") == "dispatch 01 02 (kanban)" and not sh(scratch, "status", "--porcelain"),
          sh(scratch, "log", "--oneline"))
    deadline = time.time() + 30
    while time.time() < deadline and len(list((scratch / "runs").glob("slot-*.lock"))) < 2:
        time.sleep(0.3)
    locks = {pathlib.Path(d["_path"]).name: pathlib.PurePath(d["ticket"]).name for d, _ in kanban.worker_locks(scratch)}
    check("workers keep running after --once, one per slot, detached",
          locks == {"slot-1.lock": "01-t.md", "slot-2.lock": "02-t.md"}, locks)
    check("the main checkout stays on feat and clean", sh(repo, "branch", "--show-current") == "feat"
          and sh(repo, "status", "--porcelain") == "")
    check("slots are sibling worktrees on ticket branches",
          sh(ROOT / "app.w1", "branch", "--show-current") == "feat--01" and sh(ROOT / "app.w2", "branch", "--show-current") == "feat--02")
    rc, o = run(repo, "--once")
    check("a run while workers of an earlier run are live is refused (not ours)", rc == 1 and "KANBAN STOP" in o, o)
    pids = [d.get("worker_pid") or d.get("launcher_pid") for d, _ in kanban.worker_locks(scratch)]
    stop_workers(repo); wait_gone(pids)
    time.sleep(1)

    # --- dispatcher lock
    for p in (scratch / "runs").glob("*.lock"):
        p.unlink()
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    (scratch / "runs/dispatcher.lock").write_text(json.dumps({"pid": sleeper.pid, "started": "2026-11-01 10:00", "feature": "feat"}))
    ticket(repo, "06", "`src/f.py`"); sh(scratch, "add", "-A"); sh(scratch, "commit", "-q", "-m", "t6")
    rc, o = run(repo, "--once")
    check("a second run while the lock's pid is alive refuses", rc == 1 and o.strip().splitlines()[-1]
          == f"KANBAN STOP a dispatcher is already running (pid {sleeper.pid})", o)
    check("the refused run left the live lock alone",
          json.loads((scratch / "runs/dispatcher.lock").read_text())["pid"] == sleeper.pid and status_of(repo, "03") == "todo")
    sleeper.kill(); sleeper.wait()
    rc, o = run(repo, "--once")
    check("a stale lock (dead pid) is replaced and the run goes on", rc == 0 and "KANBAN OK started" in o.splitlines()[-1], o)
    check("the lock is removed after that --once too", not (scratch / "runs/dispatcher.lock").exists())
    pids = [d.get("worker_pid") or d.get("launcher_pid") for d, _ in kanban.worker_locks(scratch)]
    stop_workers(repo); wait_gone(pids)

    # --- SIGTERM of a looping run removes the lock
    for p in (scratch / "runs").glob("*.lock"):
        p.unlink()
    for nn in ("01", "02", "03", "04", "05", "06"):
        (scratch / f"feat/issues/{nn}-t.md").unlink()
    sh(scratch, "add", "-A"); sh(scratch, "commit", "-q", "-m", "clear")
    ticket(repo, "07", "`src/g.py`", blocked="99")           # nothing ready: the loop idles
    ticket(repo, "99", "`src/h.py`", status="review")
    sh(scratch, "add", "-A"); sh(scratch, "commit", "-q", "-m", "idle")
    p = subprocess.Popen([sys.executable, str(KIT / "kanban.py"), "run", "feat", "--interval", "1"], cwd=repo,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lock = scratch / "runs/dispatcher.lock"
    deadline = time.time() + 20
    while time.time() < deadline and not lock.exists():
        time.sleep(0.1)
    d = json.loads(lock.read_text()) if lock.exists() else {}
    check("a looping run holds the lock with its pid, start time and feature",
          d.get("pid") == p.pid and d.get("feature") == "feat" and d.get("started"), d)
    time.sleep(1.5)
    p.send_signal(signal.SIGTERM)
    try:
        out, _ = p.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        p.kill(); out, _ = p.communicate()
    check("SIGTERM: lock removed and a clean exit 0", p.returncode == 0 and not lock.exists()
          and out.strip().splitlines()[-1] == "KANBAN OK started 0", (p.returncode, out))
    p = subprocess.Popen([sys.executable, str(KIT / "kanban.py"), "run", "feat", "--interval", "1"], cwd=repo,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.time() + 20
    while time.time() < deadline and not lock.exists():
        time.sleep(0.1)
    p.send_signal(signal.SIGINT)
    try:
        out, _ = p.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        p.kill(); out, _ = p.communicate()
    check("SIGINT: lock removed and a clean exit 0", p.returncode == 0 and not lock.exists(), (p.returncode, out))

    # --- refusals
    (repo / "dirty.txt").write_text("x")
    rc, o = run(repo, "--once")
    check("a dirty repo is refused", rc == 1 and "KANBAN STOP" in o and "uncommitted" in o and not lock.exists(), o)
    (repo / "dirty.txt").unlink()
    sh(repo, "checkout", "-q", "main")
    rc, o = run(repo, "--once")
    check("a repo not on the ticket's Branch is refused", rc == 1 and "is not on feat" in o and not lock.exists(), o)
    sh(repo, "checkout", "-q", "feat")
    (repo / ".team.json").write_text(json.dumps({"test": "true", "setup": [{"cmd": "true"}]}))
    rc, o = run(repo, "--once")
    check("no max_workers in .team.json is refused", rc == 1 and "max_workers" in o and "KANBAN STOP" in o, o)
    (repo / ".team.json").write_text(json.dumps({"test": "true", "max_workers": 2}))
    rc, o = run(repo, "--once")
    check("no setup in .team.json is refused", rc == 1 and "`setup`" in o, o)
    check("refusals started nothing", status_of(repo, "07") == "todo" and not list((scratch / "runs").glob("slot-*.lock")))
    stop_workers(repo)


if __name__ == "__main__":
    try:
        main()
    finally:
        if "--keep" in sys.argv:
            print(f"kept {ROOT}")
        else:
            shutil.rmtree(ROOT, ignore_errors=True)
            for w in ("app.w1", "app.w2", "app.w3"):
                shutil.rmtree(ROOT / w, ignore_errors=True)
    print(f"{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)
