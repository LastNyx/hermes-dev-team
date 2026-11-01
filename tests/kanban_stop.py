#!/usr/bin/env python3
"""`kanban.py stop`, `pause`, `resume` and `service` (K4), no model.

A throwaway repo and a FAKE `claude` on PATH (outcome from the ticket's `Fake:` line, as in kanban_finish.py). Checks: stop
with no dispatcher says `KANBAN OK not running`; a graceful stop of a looping dispatcher releases the lock and leaves a
running worker alone, which the next `run` adopts and merges; `--now` stops the worker, returns its ticket to todo and logs
`NOTE kanban force-stopped NN`; a lock naming a pid that is not a kanban.py process is refused and nothing is signalled or
removed; `pause`/`resume` write and remove runs/dispatcher.pause and a loop started before `pause` starts nothing after it;
`service` prints a systemd-run line with the repo path and writes nothing (tree snapshot).

    python tests/kanban_stop.py [--keep]

Last line: `<n>/<total> checks passed`; exit 0 only when all pass.
"""
import json, os, pathlib, re, shutil, subprocess, sys, tempfile, textwrap, time

KIT = pathlib.Path(__file__).resolve().parent.parent / "kit"
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-kstop-"))
BIN = ROOT / "bin"
os.environ.update(GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1", PATH=f"{BIN}{os.pathsep}{os.environ['PATH']}")
os.environ.pop("ANTHROPIC_API_KEY", None)
sys.path.insert(0, str(KIT))
import kanban  # noqa: E402
results = []

FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, pathlib, re, signal, subprocess, sys, time
a = sys.argv[1:]
if a[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True})); sys.exit(0)
prompt = a[a.index("-p") + 1]
ticket = pathlib.Path(re.search(r"ticket:\s*(\S+)", prompt).group(1))
spec = re.search(r"^Fake:\s*(.+)$", ticket.read_text(), re.M).group(1).split()
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(f"start {ticket.name}\n")
def done(msg):
    print(json.dumps({"type": "result", "subtype": "success", "result": msg, "num_turns": 3,
                      "usage": {"input_tokens": 1, "output_tokens": 1}, "total_cost_usd": 0, "permission_denials": []}))
    sys.exit(0)
time.sleep(float(os.environ.get("FAKE_SLEEP", "1")))
if spec[0] == "slow":
    time.sleep(120)
if spec[0] == "die":
    os.kill(os.getppid(), signal.SIGKILL); sys.exit(1)
if spec[0] == "limit":
    done("Claude AI usage limit reached. Your limit resets in 20 minutes.")
if spec[0] == "fail":
    done("could not\nRESULT: FAIL nope")
if spec[0] == "nocommit":
    done("nothing to do\nRESULT: PASS built nothing")
path, word = spec[1], spec[2]
p = pathlib.Path(path); p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(word + "\n")
subprocess.run(["git", "add", path], check=True)
subprocess.run(["git", "commit", "-q", "-m", f"{ticket.stem} built"], check=True)
done("did it\nRESULT: PASS built " + path)
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


def ticket(repo, nn, fake, seam=None, status="todo", blocked="None"):
    (repo / ".scratch/feat/issues" / f"{nn}-t.md").write_text(textwrap.dedent(f"""\
        # {nn}: t

        Lane: full
        Branch: feat
        Status: {status}
        Round: 0
        Blocked by: {blocked}
        Seam: {seam or f"`src/{nn}.py`"}
        Test command: true
        Fake: {fake}

        ## Result
        """))


def field(repo, nn, name="Status"):
    return re.search(rf"^{name}:\s*(.+)$", (repo / ".scratch/feat/issues" / f"{nn}-t.md").read_text(), re.M).group(1).strip()


def body(repo, nn):
    return (repo / ".scratch/feat/issues" / f"{nn}-t.md").read_text()


def setup_repo(name, cfg):
    repo = ROOT / name
    repo.mkdir()
    sh(repo, "init", "-q", "-b", "main")
    sh(repo, "config", "user.email", "t@example.com"); sh(repo, "config", "user.name", "T")
    (repo / ".gitignore").write_text(".scratch/\n")
    (repo / "shared.txt").write_text("base\n")
    (repo / ".team.json").write_text(json.dumps(cfg))
    sh(repo, "add", "-A"); sh(repo, "commit", "-q", "-m", "init")
    sh(repo, "checkout", "-q", "-b", "feat")
    (repo / ".scratch/feat/issues").mkdir(parents=True)
    sh(repo / ".scratch", "init", "-q", "-b", "main")
    sh(repo / ".scratch", "config", "user.email", "t@example.com"); sh(repo / ".scratch", "config", "user.name", "T")
    (repo / ".scratch/.gitignore").write_text("runs/\n")
    return repo


def commit_tickets(repo):
    sh(repo / ".scratch", "add", "-A")
    subprocess.run(["git", "commit", "-q", "-m", "tickets"], cwd=repo / ".scratch", capture_output=True)


def run(repo, *args, timeout=90):
    r = subprocess.run([sys.executable, str(KIT / "kanban.py"), "run", "feat", *args], cwd=repo, capture_output=True,
                       text=True, timeout=timeout)
    if os.environ.get("KF_DEBUG"):
        print("   >>> run", args, "\n" + "\n".join("   | " + l for l in (r.stdout + r.stderr).splitlines()))
    return r.returncode, r.stdout + r.stderr


def live(repo):
    return [d for d, al in kanban.worker_locks(repo / ".scratch") if al]


def wait_idle(repo, runs_lines=None, secs=40):
    """No live worker lock (and, when given, that many dev lines in runs.log)."""
    end = time.time() + secs
    while time.time() < end:
        n = sum(1 for l in (repo / ".scratch/runs.log").read_text().splitlines() if "\tdev\t" in l) \
            if (repo / ".scratch/runs.log").is_file() else 0
        if not live(repo) and (runs_lines is None or n >= runs_lines):
            time.sleep(0.5)
            return True
        time.sleep(0.3)
    return False


def dev_lines(repo):
    p = repo / ".scratch/runs.log"
    return [l for l in p.read_text().splitlines() if "\tdev\t" in l] if p.is_file() else []


def kill_all(repo):
    for _ in range(4):
        for d, alive in kanban.teamcfg.read_locks(repo / ".scratch"):
            for k in ("worker_pid", "launcher_pid"):
                if d.get(k) and alive:
                    kanban.teamcfg.kill_tree(d[k])
        if not live(repo):
            break
        time.sleep(0.5)


def reset_scratch(repo):
    kill_all(repo)
    time.sleep(1.5)                                   # a launcher killed before its worker pid was written
    for n in (1, 2, 3):
        w = repo.parent / f"{repo.name}.w{n}"
        if w.exists():
            subprocess.run(["git", "worktree", "remove", "--force", str(w)], cwd=repo, capture_output=True)
    subprocess.run(["git", "worktree", "prune"], cwd=repo, capture_output=True)
    for b in sh(repo, "branch", "--list", "feat--*", "--format=%(refname:short)").split():
        sh(repo, "branch", "-D", b)
    for f in (repo / ".scratch/runs").glob("slot-*"):
        f.unlink()
    for f in (repo / ".scratch/feat/issues").glob("*.md"):
        f.unlink()
    for f in (repo / ".scratch/runs").glob("*"):
        if f.is_file():
            f.unlink()
    (repo / ".scratch/runs.log").unlink(missing_ok=True)
    commit_tickets(repo)


def kb(repo, *args, timeout=90):
    r = subprocess.run([sys.executable, str(KIT / "kanban.py"), *args], cwd=repo, capture_output=True, text=True,
                       timeout=timeout)
    return r.returncode, (r.stdout + r.stderr)


def start_loop(repo, *extra):
    return subprocess.Popen([sys.executable, str(KIT / "kanban.py"), "run", "feat", "--interval", "1", *extra], cwd=repo,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def wait_for(cond, secs=20):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.2)
    return False


def snapshot(root):
    out = {}
    for p in sorted(root.rglob("*")):
        if ".git" in p.parts:
            continue
        try:
            out[p.relative_to(root).as_posix()] = (p.stat().st_mtime_ns, p.stat().st_size) if p.is_file() else None
        except OSError:
            pass
    return out


def main():
    BIN.mkdir()
    (BIN / "claude").write_text(FAKE_CLAUDE); (BIN / "claude").chmod(0o755)
    os.environ["FAKE_LOG"] = str(ROOT / "fake.log")
    cfg = {"test": "true", "max_workers": 2, "setup": [{"cmd": "true"}], "run_seconds": 300}
    repo = setup_repo("app", cfg)
    scratch = repo / ".scratch"
    runs = scratch / "runs"
    lockp = runs / "dispatcher.lock"
    pausep = runs / "dispatcher.pause"

    # --- not running
    runs.mkdir(parents=True, exist_ok=True)
    rc, o = kb(repo, "stop")
    check("stop with no dispatcher: 'KANBAN OK not running', exit 0", rc == 0 and o.strip() == "KANBAN OK not running", o)
    lockp.write_text(json.dumps({"pid": 2 ** 22 + 12345, "started": "2026-01-01 00:00", "feature": "feat"}))
    rc, o = kb(repo, "stop", "--now")
    check("stop with a dead-pid lock: 'KANBAN OK not running'", rc == 0 and o.strip() == "KANBAN OK not running", o)
    lockp.unlink()

    # --- graceful stop leaves a running worker alone; the next run adopts it
    ticket(repo, "01", "write src/01.py A"); commit_tickets(repo)
    os.environ["FAKE_SLEEP"] = "6"
    p = start_loop(repo)
    check("setup: the loop holds the lock and has a live worker",
          wait_for(lambda: lockp.is_file() and len(live(repo)) == 1), "")
    rc, o = kb(repo, "stop", "--wait", "20")
    out, _ = p.communicate(timeout=30)
    check("graceful stop: exits 0 and says 'draining (1 workers still running)' or 'stopped'",
          rc == 0 and ("draining (1 workers still running)" in o or o.strip() == "KANBAN OK stopped") and "KANBAN OK" in o, o)
    check("graceful stop: the dispatcher exited 0 and released the lock", p.returncode == 0 and not lockp.exists(), out)
    check("graceful stop: the worker is still alive and its ticket in-progress",
          len(live(repo)) == 1 and field(repo, "01") == "in-progress")
    wait_idle(repo, 1)
    rc, o = run(repo, "--interval", "1")
    check("the next `run` adopts the finished worker and merges it (K3)",
          "merged 01" in o and field(repo, "01") == "review", o)
    rc, o = kb(repo, "stop")
    check("stop after the dispatcher ended: not running", o.strip() == "KANBAN OK not running", o)

    # --- --now
    reset_scratch(repo)
    ticket(repo, "02", "slow"); commit_tickets(repo)
    os.environ["FAKE_SLEEP"] = "1"
    p = start_loop(repo)
    check("setup: slow worker running", wait_for(lambda: lockp.is_file() and len(live(repo)) == 1, 30))
    time.sleep(2)
    rc, o = kb(repo, "stop", "--now", "--wait", "30")
    out, _ = p.communicate(timeout=30)
    check("--now: 'KANBAN OK force-stopped 1', exit 0, no live worker, lock gone",
          rc == 0 and "KANBAN OK force-stopped 1" in o and not live(repo) and not lockp.exists(), o + out)
    check("--now: the ticket is back to todo (not in-progress)", field(repo, "02") == "todo", body(repo, "02"))
    log = (scratch / "runs.log").read_text()
    check("--now: runs.log has 'NOTE kanban force-stopped 02'", "NOTE kanban force-stopped 02" in log, log)
    check("--now: the stopped run is logged force-stopped and the scratch tree is committed",
          "force-stopped" in "\n".join(dev_lines(repo)) and not sh(scratch, "status", "--porcelain"),
          sh(scratch, "status", "--porcelain"))

    # --- foreign pid
    reset_scratch(repo)
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        lockp.write_text(json.dumps({"pid": sleeper.pid, "started": "2026-01-01 00:00", "feature": "feat"}))
        for args in (("stop",), ("stop", "--now")):
            rc, o = kb(repo, *args)
            check(f"{' '.join(args)}: a lock naming another process is refused, exit 1",
                  rc == 1 and f"KANBAN STOP pid {sleeper.pid} is not a kanban dispatcher (stale lock)" in o, o)
        time.sleep(0.5)
        check("the foreign process was not signalled and the lock was not removed",
              sleeper.poll() is None and lockp.is_file())
    finally:
        sleeper.kill(); sleeper.wait()
        lockp.unlink(missing_ok=True)

    # --- pause / resume
    t0 = time.time()
    rc, o = kb(repo, "pause", "--minutes", "5")
    check("pause --minutes 5 writes now+300s and prints one KANBAN OK line",
          rc == 0 and len(o.strip().splitlines()) == 1 and o.startswith("KANBAN OK paused")
          and 295 < float(pausep.read_text()) - t0 < 310, o)
    st = json.loads(kb(repo, "status", "--json")[1])
    check("status --json shows paused_until", st["paused_until"] and 295 < st["paused_until"] - t0 < 310, st)
    rc, o = kb(repo, "resume")
    check("resume removes the pause file and prints one KANBAN OK line",
          rc == 0 and not pausep.exists() and o.strip() == "KANBAN OK resumed", o)
    rc, o = kb(repo, "pause")
    check("pause default is 60 minutes", 3590 < float(pausep.read_text()) - time.time() <= 3600, o)
    kb(repo, "resume")
    rc, o = kb(repo, "resume")
    check("resume when not paused: 'KANBAN OK not paused', exit 0", rc == 0 and o.strip() == "KANBAN OK not paused", o)

    # --- a loop started before `pause` starts nothing after it (03 is still running, a slot is free, 04 is new and ready)
    reset_scratch(repo)
    ticket(repo, "03", "write src/03.py C"); commit_tickets(repo)
    os.environ["FAKE_SLEEP"] = "5"
    p = start_loop(repo)
    check("setup: 03 started by the loop", wait_for(lambda: field(repo, "03") == "in-progress" and len(live(repo)) == 1, 30))
    kb(repo, "pause", "--minutes", "10")
    ticket(repo, "04", "write src/04.py D"); commit_tickets(repo)
    time.sleep(4)
    check("loop started before `pause`: ready ticket 04 stays todo with a free slot, nothing new starts",
          field(repo, "04") == "todo" and len(live(repo)) <= 1 and p.poll() is None
          and not any("ticket=04" in l for l in dev_lines(repo)), body(repo, "04"))
    kb(repo, "resume")
    check("after `resume` the same loop starts 04", wait_for(lambda: field(repo, "04") != "todo", 30))
    rc, o = kb(repo, "stop", "--wait", "40")
    out, _ = p.communicate(timeout=60)
    check("the loop ends on stop with the lock released", p.returncode == 0 and not lockp.exists(), out)
    wait_idle(repo)

    # --- service prints and writes nothing
    before = snapshot(ROOT)
    rc, o = kb(repo, "service", "feat")
    line = o.splitlines()[0] if o else ""
    check("service prints a systemd-run --user line with the repo path, the kit's kanban.py and the feature",
          rc == 0 and line.startswith("systemd-run --user --collect --unit=hdt-kanban-app-feat ")
          and f"--working-directory={repo}" in line and "--setenv=PATH=" in line
          and str(KIT / "kanban.py") in line and line.endswith(" run feat"), o)
    check("service writes nothing (tree snapshot unchanged)", snapshot(ROOT) == before)
    rc, o = kb(repo, "service", "nope")
    check("service for an unknown feature: KANBAN STOP, exit 1", rc == 1 and o.startswith("KANBAN STOP no feature nope"), o)
    unit = (KIT / "systemd" / "hdt-kanban@.service").read_text()
    check("the unit template has the required lines",
          all(x in unit for x in ("Restart=no", "KillMode=process", "TimeoutStopSec=90", 'kanban.py" run "$HDT_FEATURE"'))
          and "WorkingDirectory" not in "\n".join(l for l in unit.splitlines() if not l.startswith("#"))
          and "/home/" not in unit)
    kill_all(repo)


if __name__ == "__main__":
    try:
        main()
    finally:
        if "--keep" in sys.argv:
            print(f"kept {ROOT}")
        else:
            try:
                kill_all(ROOT / "app")
            except Exception:
                pass
            shutil.rmtree(ROOT, ignore_errors=True)
    print(f"{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)
