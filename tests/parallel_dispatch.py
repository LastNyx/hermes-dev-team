#!/usr/bin/env python3
"""`scratch.py dispatch`: parallel dev workers in pool worktrees, no model (0.17.0, docs/open/parallel-workers.md).

A throwaway repo and a FAKE `claude` on PATH (it answers `auth status`, then plays a dev run: reads its ticket's
`Fake:` line, writes a file, commits with the dev's subject and prints a stream-json result). Checks: Seam overlap
refused, max_workers and setup required, two tickets run at the same time in two slots and both merge, the setup runs
once per slot, a ticket branch is deleted after its merge, a merge conflict is reported and its branch kept, a usage
limit stops the queue, a slot left mid-group is merged by `resume`, and a serial run is refused while a slot runs.

    python tests/parallel_dispatch.py [--keep]

Last line: `<n>/<total> checks passed`; exit 0 only when all pass. See docs/testing.md.
"""
import json, os, pathlib, shutil, subprocess, sys, tempfile, textwrap, time

KIT = pathlib.Path(__file__).resolve().parent.parent / "kit"
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-parallel-"))
BIN = ROOT / "bin"
os.environ.update(GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1", PATH=f"{BIN}{os.pathsep}{os.environ['PATH']}")
os.environ.pop("ANTHROPIC_API_KEY", None)
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
start = time.time()
log = pathlib.Path(os.environ["FAKE_LOG"])
with open(log, "a") as f:
    f.write(f"start {ticket.name} {start:.2f} {os.getcwd()}\n")
time.sleep(float(os.environ.get("FAKE_SLEEP", "2")))
def done(msg):
    with open(log, "a") as f:
        f.write(f"end {ticket.name} {time.time():.2f}\n")
    print(json.dumps({"type": "result", "subtype": "success", "result": msg, "num_turns": 3,
                      "usage": {"input_tokens": 1, "output_tokens": 1}, "total_cost_usd": 0, "permission_denials": []}))
    sys.exit(0)
if spec[0] == "limit":
    done("Claude AI usage limit reached. Your limit resets at 5pm.")
if spec[0] == "hang":
    time.sleep(3600)
path, word = spec[1], spec[2]
p = pathlib.Path(path); p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(word + "\n")
subprocess.run(["git", "add", path], check=True)
subprocess.run(["git", "commit", "-q", "-m", f"{ticket.stem} built ({ticket.name})"], check=True)
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


def ticket(repo, nn, slug, seam, fake, blocked="None"):
    (repo / ".scratch/feat/issues" / f"{nn}-{slug}.md").write_text(textwrap.dedent(f"""\
        # {nn}: {slug}

        Lane: full
        Branch: feat
        Status: todo
        Round: 0
        Blocked by: {blocked}
        Seam: {seam}
        Test command: true
        Fake: {fake}

        ## Result
        """))


def scratch(repo, *args, env=None, timeout=120):
    r = subprocess.run([sys.executable, str(KIT / "scratch.py"), *args], cwd=repo, capture_output=True, text=True,
                       env={**os.environ, **(env or {})}, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr)


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
    return repo


def main():
    BIN.mkdir()
    (BIN / "claude").write_text(FAKE_CLAUDE); (BIN / "claude").chmod(0o755)
    log = ROOT / "fake.log"
    os.environ["FAKE_LOG"] = str(log)
    cfg = {"test": "true", "max_workers": 2, "setup": [{"cmd": "echo x >> ../setup-count.txt"}]}
    repo = setup_repo("app", cfg)
    setup_count = ROOT / "setup-count.txt"

    # --- refusals, no worker started
    ticket(repo, "01", "a", "`src/a.py`", "write src/a.py A")
    ticket(repo, "02", "b", "`src/b.py`", "write src/b.py B")
    ticket(repo, "03", "c", "`src/` (folder)", "write src/c.py C")
    rc, o = scratch(repo, "dispatch", "feat", "01", "03", "--dry-run")
    check("overlapping Seams refused (folder vs file)", rc == 1 and "overlap" in o and "STOP" in o, o)
    rc, o = scratch(repo, "dispatch", "feat", "01", "02", "03")
    check("more tickets than max_workers refused", rc == 1 and "max_workers is 2" in o, o)
    ticket(repo, "04", "d", "`src/d.py`", "write src/d.py D", blocked="01")
    rc, o = scratch(repo, "dispatch", "feat", "01", "04", "--dry-run")
    check("a ticket blocked by one in the group refused", rc == 1 and "blocked by 01" in o, o)
    rc, o = scratch(repo, "dispatch", "feat", "01", "02", "--dry-run")
    check("dry run of separate Seams passes", rc == 0 and "DISPATCH OK dry run" in o, o)
    norepo = setup_repo("nosetup", {"test": "true", "max_workers": 2})
    ticket(norepo, "01", "a", "`a.py`", "write a.py A"); ticket(norepo, "02", "b", "`b.py`", "write b.py B")
    rc, o = scratch(norepo, "dispatch", "feat", "01", "02")
    check("no setup in .team.json refused", rc == 1 and "no `setup`" in o, o)

    # --- the happy path: two at once, both merged
    t0 = time.time()
    rc, o = scratch(repo, "dispatch", "feat", "01", "02", env={"FAKE_SLEEP": "4"})
    took = time.time() - t0
    check("dispatch 01 02 OK", rc == 0 and "DISPATCH OK 2 merged" in o, o)
    text = log.read_text() if log.is_file() else ""
    starts = [l.split() for l in text.splitlines() if l.startswith("start")]
    ends = [l.split() for l in text.splitlines() if l.startswith("end")]
    overlap = len(starts) == 2 and len(ends) == 2 and max(float(s[2]) for s in starts) < min(float(e[2]) for e in ends)
    check(f"both workers ran at the same time (wall {took:.0f}s for two 4s runs)", overlap, text)
    check("each worker ran in its own slot worktree", {pathlib.Path(s[3]).name for s in starts} == {"app.w1", "app.w2"},
          str(starts))
    check("both tickets merged into feat (--no-ff, in ticket order)",
          (repo / "src/a.py").is_file() and (repo / "src/b.py").is_file()
          and sh(repo, "log", "--merges", "--format=%s", "-2").splitlines()[-1].startswith("Merge feat--01"), o)
    check("merged ticket branches deleted", not sh(repo, "branch", "--list", "feat--*"), sh(repo, "branch"))
    check("setup ran once per slot", setup_count.read_text().count("x") == 2, setup_count.read_text())
    check("runs.log has one dev line per ticket, repo named as the main checkout",
          sum(1 for l in (repo / ".scratch/runs.log").read_text().splitlines()
              if "\tdev\t" in l and "\trepo=app\t" in l) == 2, (repo / ".scratch/runs.log").read_text())
    check("no lock left behind", not list((repo / ".scratch/runs").glob("*.lock")))
    check("main untouched", sh(repo, "log", "--format=%s", "main") == "init", sh(repo, "log", "--oneline", "main"))

    # --- conflict: both write shared.txt (Seams name different files, so the kit cannot see it)
    ticket(repo, "05", "e", "`src/e.py`", "write shared.txt EEE")
    ticket(repo, "06", "f", "`src/f.py`", "write shared.txt FFF")
    rc, o = scratch(repo, "dispatch", "feat", "05", "06", env={"FAKE_SLEEP": "1"})
    check("conflict: first merged, second FAIL conflict, branch kept",
          rc == 0 and "05: merged" in o and "06: FAIL conflict" in o and "with failures: 06" in o
          and sh(repo, "branch", "--list", "feat--06") != "" and (repo / "shared.txt").read_text() == "EEE\n", o)
    check("the aborted merge left the tree clean", sh(repo, "status", "--porcelain") == "", sh(repo, "status"))
    check("setup not repeated when the lockfiles did not change", setup_count.read_text().count("x") == 2)

    # --- usage limit stops the queue; the other ticket still merges
    ticket(repo, "07", "g", "`src/g.py`", "limit")
    ticket(repo, "08", "h", "`src/h.py`", "write src/h.py H")
    rc, o = scratch(repo, "dispatch", "feat", "07", "08", env={"FAKE_SLEEP": "1"})
    check("usage limit: FAIL usage-limit, DISPATCH STOP, the other ticket merged",
          rc == 1 and "FAIL usage-limit" in o and "usage limit hit" in o and "08: merged" in o, o)

    # --- crash mid-group, then resume merges what finished (D12)
    ticket(repo, "09", "i", "`src/i.py`", "write src/i.py I")
    ticket(repo, "10", "j", "`src/j.py`", "hang")
    p = subprocess.Popen([sys.executable, str(KIT / "scratch.py"), "dispatch", "feat", "09", "10"], cwd=repo,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         env={**os.environ, "FAKE_SLEEP": "1"}, start_new_session=True)
    deadline = time.time() + 60
    while time.time() < deadline and "end 09-i.md" not in log.read_text():
        time.sleep(0.5)
    time.sleep(1.5)                                              # 09's launcher writes its RESULT and exits
    rc, o = scratch(repo, "dispatch", "feat", "01", "02", "--dry-run")
    rcs, os_ = subprocess.run([sys.executable, str(KIT / "run-worker.py"), "dev", "ticket: .scratch/feat/issues/01-a.md"],
                              cwd=repo, capture_output=True, text=True).returncode, ""
    check("while a slot runs, a serial run and a new dispatch are refused", rc == 1 and rcs == 2, o)
    os.killpg(p.pid, 9)                                          # the planner side dies: dispatch + its launchers
    p.wait(timeout=30)
    rc, o = scratch(repo, "resume", "feat")
    check("resume sees the orphaned worker and stops", rc == 1 and "orphaned worker" in o, o)
    rc, o = scratch(repo, "stop", "--wait", "20")
    check("stop kills the orphaned slot worker", rc == 0 and "STOP OK" in o, o)
    check("after the crash: 09 is finished on its branch but not merged",
          sh(repo, "branch", "--list", "feat--09") != "" and not (repo / "src/i.py").exists())
    rc, o = scratch(repo, "resume", "feat")
    check("resume merges the finished ticket branch (D12)",
          "feat--09: finished before the interruption; merged" in o and (repo / "src/i.py").is_file(), o)
    check("resume leaves the unfinished one for redispatch", "feat--10: no dev commit" in o, o)


if __name__ == "__main__":
    try:
        main()
    finally:
        if "--keep" in sys.argv:
            print(f"kept {ROOT}")
        else:
            shutil.rmtree(ROOT, ignore_errors=True)
    print(f"{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)
