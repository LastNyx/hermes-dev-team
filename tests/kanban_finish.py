#!/usr/bin/env python3
"""`kanban.py run` finish side (K3), no model.

A throwaway repo and a FAKE `claude` on PATH whose outcome comes from the ticket's `Fake:` line (`write PATH WORD`
commits a file, `nocommit` passes without a commit, `fail`, `limit` (usage limit, resets in 20 minutes), `slow`,
`die` (kills its launcher with SIGKILL: a dead lock and no runs.log line)). Each `run --once` is one tick, so every case
steps the dispatcher by hand. Checks: PASS merges into the feature branch and sets review, two PASS in one tick merge in
ticket order, a conflict follows the failure path, an empty PASS blocks, FAIL -> todo -> FAIL -> blocked with the
Dispatcher line, usage-limit writes the pause file without a retry and nothing starts while it holds (and starts again once
it has passed), a timeout stops only that slot and counts as a crash, an orphan is blocked after two ticks and never
re-run, a worker left by `--once` is adopted, `--drain` exits when idle, the loop ends with the done line.

    python tests/kanban_finish.py [--keep]

Last line: `<n>/<total> checks passed`; exit 0 only when all pass.
"""
import json, os, pathlib, re, shutil, subprocess, sys, tempfile, textwrap, time

KIT = pathlib.Path(__file__).resolve().parent.parent / "kit"
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-kfin-"))
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


def main():
    BIN.mkdir()
    (BIN / "claude").write_text(FAKE_CLAUDE); (BIN / "claude").chmod(0o755)
    os.environ["FAKE_LOG"] = str(ROOT / "fake.log")
    cfg = {"test": "true", "max_workers": 3, "setup": [{"cmd": "true"}], "run_seconds": 6}
    repo = setup_repo("app", cfg)
    scratch = repo / ".scratch"
    runs = scratch / "runs"

    # --- unit: reset time parsing
    now = 1_000_000.0
    check("parse_reset: 'in 20 minutes' and 'in 2 hours'", kanban.parse_reset("limit, try in 20 minutes", now) == now + 1200
          and kanban.parse_reset("in 2 hours", now) == now + 7200)
    e = kanban.parse_reset("Your limit resets at 5pm", now)
    check("parse_reset: 'resets at 5pm' is in the future, within a day, on the hour",
          now < e <= now + 86400 and time.localtime(e).tm_hour == 17 and time.localtime(e).tm_min == 0, e)
    check("parse_reset: unknown text is now + 30 min", kanban.parse_reset("boom", now) == now + 1800)

    # --- PASS merges and sets review; two PASS in one tick merge in ticket order
    ticket(repo, "01", "write src/01.py A"); ticket(repo, "02", "write src/02.py B"); commit_tickets(repo)
    rc, o = run(repo, "--once")
    check("tick 1 starts 01 and 02", rc == 0 and "started 01" in o and "started 02" in o, o)
    check("both workers finish", wait_idle(repo, 2))
    head0 = sh(repo, "rev-parse", "HEAD")
    rc, o = run(repo, "--once")
    check("the next tick prints 'merged NN -> sha' for both, in ticket order",
          rc == 0 and re.search(r"merged 01 -> \w+.*\n(.*\n)*.*merged 02 -> \w+", o), o)
    check("both are review, files on feat, merge commits in ticket order, branches deleted",
          field(repo, "01") == "review" and field(repo, "02") == "review" and (repo / "src/01.py").is_file()
          and (repo / "src/02.py").is_file()
          and sh(repo, "log", "--merges", "--format=%s", f"{head0}..HEAD").splitlines()[::-1][0].startswith("Merge feat--01")
          and sh(repo, "log", "--merges", "--format=%s", f"{head0}..HEAD").splitlines()[0].startswith("Merge feat--02")
          and not sh(repo, "branch", "--list", "feat--*"), sh(repo, "log", "--oneline", "--graph"))
    check("a NOTE parallel NN merged line is in runs.log and the scratch commit holds the changes",
          sum(1 for l in (scratch / "runs.log").read_text().splitlines() if "NOTE parallel 0" in l and " merged " in l) == 2
          and not sh(scratch, "status", "--porcelain") and "finish" in sh(scratch, "log", "-1", "--format=%s"),
          sh(scratch, "log", "--oneline"))
    check("the ticket records the merge sha", "Dispatcher: merged feat--01 into feat" in body(repo, "01"))

    # --- conflict: first merges, second takes the failure path (todo, branch kept), second time blocked
    reset_scratch(repo)
    ticket(repo, "03", "write shared.txt CCC"); ticket(repo, "04", "write shared.txt DDD"); commit_tickets(repo)
    run(repo, "--once"); wait_idle(repo, 4 if False else None)
    wait_idle(repo, len(dev_lines(repo)) if len(dev_lines(repo)) >= 2 else 2)
    rc, o = run(repo, "--once")
    check("conflict: 03 merged, 04 FAIL conflict, branch feat--04 kept, tree clean, 04 back to todo",
          "merged 03" in o and "FAIL conflict" in o and field(repo, "03") == "review"
          and sh(repo, "branch", "--list", "feat--04") != "" and sh(repo, "status", "--porcelain") == ""
          and (repo / "shared.txt").read_text() == "CCC\n", o)
    check("04 was requeued by the retry rule", field(repo, "04") in ("todo", "in-progress"), o)
    kill_all(repo); wait_idle(repo)

    # --- empty PASS -> blocked
    reset_scratch(repo)
    ticket(repo, "05", "nocommit"); commit_tickets(repo)
    run(repo, "--once"); wait_idle(repo, 1)
    rc, o = run(repo, "--once")
    check("PASS without a commit: blocked with 'PASS but no commit'",
          field(repo, "05") == "blocked" and "PASS but no commit" in body(repo, "05"), o + body(repo, "05"))

    # --- FAIL -> todo -> (re-run) FAIL -> blocked with the Dispatcher line
    reset_scratch(repo)
    ticket(repo, "06", "fail"); commit_tickets(repo)
    run(repo, "--once"); wait_idle(repo, 1)
    rc, o = run(repo, "--once")            # reap: first failure -> todo, and the same tick starts it again
    check("first FAIL: ticket re-queued (todo, then started again in the same tick), branch kept",
          "requeued 06" in o and "started 06" in o and sh(repo, "branch", "--list", "feat--06") != "", o)
    wait_idle(repo, 2)
    rc, o = run(repo, "--once")
    check("second FAIL: blocked, with the Dispatcher line in ## Result",
          field(repo, "06") == "blocked" and "Dispatcher: blocked after 2 failed runs: RESULT dev FAIL" in body(repo, "06")
          and "started 06" not in o, o + body(repo, "06"))
    n = len(dev_lines(repo))
    rc, o = run(repo, "--once")
    check("a blocked ticket is never started again", "started" not in o.replace("started 0", "") and len(dev_lines(repo)) == n, o)

    # --- usage limit: pause file, no retry counted, nothing starts while paused, resumes after
    reset_scratch(repo)
    ticket(repo, "07", "limit"); ticket(repo, "08", "write src/08.py H", seam="`src/other.py`"); commit_tickets(repo)
    sh(scratch, "config", "user.name", "T")
    # only 07 first: block 08 until later
    ticket(repo, "08", "write src/08.py H", seam="`src/other.py`", blocked="07"); commit_tickets(repo)
    run(repo, "--once"); wait_idle(repo, 1)
    t0 = time.time()
    rc, o = run(repo, "--once")
    pause = runs / "dispatcher.pause"
    check("usage-limit: 'KANBAN PAUSE until', pause file = reset epoch (~now+20 min), ticket back to todo",
          "KANBAN PAUSE until" in o and pause.is_file() and 1150 < float(pause.read_text()) - t0 < 1300
          and field(repo, "07") == "todo", o)
    check("usage-limit is not a retry: no Dispatcher line, nothing started while paused",
          "Dispatcher" not in body(repo, "07") and "started" not in o.replace("started 0", ""), o)
    n = len(dev_lines(repo))
    rc, o = run(repo, "--once")
    check("still paused on the next tick: nothing started", "started 07" not in o and len(dev_lines(repo)) == n
          and field(repo, "07") == "todo", o)
    pause.write_text(str(int(time.time()) - 5))
    rc, o = run(repo, "--once")
    check("a pause in the past: the queue starts again, and 07 still has its two tries (not blocked)",
          "started 07" in o and field(repo, "07") == "in-progress", o)
    kill_all(repo); wait_idle(repo)
    pause.unlink(missing_ok=True)

    # --- timeout: only the slow slot is stopped, crash rule applies
    reset_scratch(repo)
    ticket(repo, "09", "slow"); ticket(repo, "10", "write src/10.py J"); commit_tickets(repo)
    run(repo, "--once")
    time.sleep(2)
    wait_idle_end = time.time() + 20
    while time.time() < wait_idle_end and len(live(repo)) > 1:
        time.sleep(0.3)
    time.sleep(5)                                         # past run_seconds (6s) for 09
    rc, o = run(repo, "--once")
    check("timeout: 09 stopped and treated as a crash (requeued), 10 untouched and merged",
          "timed out" in o and "merged 10" in o and re.search(r"requeued 09: timed out|requeued 09: FAIL", o)
          and field(repo, "10") == "review", o)
    check("only that slot was stopped (10's lock left no foreign kill; 09's run is a force-stopped runs.log line)",
          any("force-stopped" in l and "ticket=09-t.md" in l for l in dev_lines(repo)), "\n".join(dev_lines(repo)))
    kill_all(repo); wait_idle(repo)

    # --- orphan: dead lock, in-progress, no runs line; blocked on the second tick, never re-run
    reset_scratch(repo)
    ticket(repo, "11", "die"); commit_tickets(repo)
    run(repo, "--once")
    time.sleep(4)
    stale = [d for d, al in kanban.worker_locks(scratch) if not al]
    check("setup: the worker left a dead lock and no runs.log line", len(stale) == 1 and not dev_lines(repo), stale)
    rc, o = run(repo, "--once")
    check("orphan tick 1: still in-progress, not re-run", field(repo, "11") == "in-progress" and "started 11" not in o, o)
    rc, o = run(repo, "--once")
    check("orphan tick 2: blocked 'orphaned (no live worker, no result)', stale lock removed, never re-run",
          field(repo, "11") == "blocked" and "Dispatcher: orphaned (no live worker, no result)" in body(repo, "11")
          and "started 11" not in o and not list(runs.glob("slot-*.lock")), o)

    # --- crash with no lock and no runs.line: retry rule
    reset_scratch(repo)
    ticket(repo, "12", "die"); commit_tickets(repo)
    run(repo, "--once"); time.sleep(4)
    for f in runs.glob("slot-*.lock"):
        f.unlink()
    st = json.loads((runs / "kanban-state.json").read_text())
    for e in st["running"].values():
        e["started"] -= 120                              # past the boot grace of a just-started worker
    (runs / "kanban-state.json").write_text(json.dumps(st))
    rc, o = run(repo, "--once")
    check("crash (no lock, no runs.log line): re-queued once", "requeued 12" in o and "worker gone" in o, o)
    kill_all(repo); wait_idle(repo)

    # --- adoption of a worker left by --once, then the loop ends with the done line
    reset_scratch(repo)
    ticket(repo, "13", "write src/13.py M"); commit_tickets(repo)
    os.environ["FAKE_SLEEP"] = "4"
    run(repo, "--once")
    end = time.time() + 15
    while time.time() < end and not live(repo):
        time.sleep(0.2)
    check("setup: the --once worker is alive after its dispatcher exited", len(live(repo)) == 1)
    rc, o = run(repo, "--interval", "1")
    last = o.strip().splitlines()[-1]
    check("a new run adopts it, merges it and ends with the done line",
          rc == 0 and "merged 13" in o and last == "KANBAN OK done: merged 1, review 1, blocked 0"
          and field(repo, "13") == "review" and not (runs / "dispatcher.lock").exists(), o)
    os.environ["FAKE_SLEEP"] = "1"

    # --- --drain: no new starts, exits 0 once nothing runs
    reset_scratch(repo)
    ticket(repo, "14", "write src/14.py N"); ticket(repo, "15", "write src/15.py O"); commit_tickets(repo)
    run(repo, "--once", "--dry-run")
    r0 = subprocess.run([sys.executable, str(KIT / "kanban.py"), "run", "feat", "--drain"], cwd=repo, capture_output=True,
                        text=True, timeout=60)
    check("--drain with nothing running exits 0 at once and starts nothing",
          r0.returncode == 0 and "started" not in r0.stdout.replace("started 0", "") and field(repo, "14") == "todo"
          and r0.stdout.strip().splitlines()[-1].startswith("KANBAN OK drained"), r0.stdout + r0.stderr)
    ticket(repo, "14", "write src/14.py N", status="todo"); commit_tickets(repo)
    run(repo, "--once")
    ticket_state = (field(repo, "14"), field(repo, "15"))
    ticket(repo, "16", "write src/16.py P"); commit_tickets(repo)
    r1 = subprocess.run([sys.executable, str(KIT / "kanban.py"), "run", "feat", "--drain", "--interval", "1"], cwd=repo,
                        capture_output=True, text=True, timeout=90)
    check("--drain reaps the running workers, starts nothing new, exits 0",
          r1.returncode == 0 and "merged 14" in r1.stdout and "merged 15" in r1.stdout and field(repo, "16") == "todo"
          and "started 16" not in r1.stdout, r1.stdout + r1.stderr)

    # --- SIGTERM with --grace waits for the workers
    reset_scratch(repo)
    ticket(repo, "17", "write src/17.py Q"); commit_tickets(repo)
    os.environ["FAKE_SLEEP"] = "3"
    p = subprocess.Popen([sys.executable, str(KIT / "kanban.py"), "run", "feat", "--interval", "1", "--grace", "30"],
                         cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(2.5)
    p.send_signal(15)
    out, _ = p.communicate(timeout=60)
    check("SIGTERM with --grace: waits for the worker, merges it, releases the lock",
          p.returncode == 0 and "merged 17" in out and not (runs / "dispatcher.lock").exists(), out)
    os.environ["FAKE_SLEEP"] = "1"
    reset_scratch(repo)
    ticket(repo, "18", "write src/18.py R"); commit_tickets(repo)
    os.environ["FAKE_SLEEP"] = "3"
    p = subprocess.Popen([sys.executable, str(KIT / "kanban.py"), "run", "feat", "--interval", "1"],
                         cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(2)
    p.send_signal(15)
    out, _ = p.communicate(timeout=30)
    check("SIGTERM without --grace: exits at once, the worker keeps running (adoptable)",
          p.returncode == 0 and len(live(repo)) == 1 and field(repo, "18") == "in-progress", out)
    wait_idle(repo, 1)
    rc, o = run(repo, "--once")
    check("the next run adopts and merges it", "merged 18" in o and field(repo, "18") == "review", o)
    kill_all(repo)


if __name__ == "__main__":
    try:
        main()
    finally:
        if "--keep" in sys.argv:
            print(f"kept {ROOT}")
        else:
            kill_all_dirs = list(ROOT.glob("app"))
            for r in kill_all_dirs:
                try:
                    kill_all(r)
                except Exception:
                    pass
            shutil.rmtree(ROOT, ignore_errors=True)
    print(f"{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)
