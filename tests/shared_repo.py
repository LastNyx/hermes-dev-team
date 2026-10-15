#!/usr/bin/env python3
"""E2E: a SHARED repo, where a teammate (no kit, no tests) keeps pushing to main while the user runs the kit.

One bare remote; clone `me` runs the kit, clone `mate` plays the teammate. Makes 3 real `claude -p` runs (Sonnet:
2 dev, 1 reviewer; about $0.40 list). Also covers, with no model: the lock when the launcher is SIGKILLed (orphaned
worker), sync-main (dry run, refusal mid-batch, clean merge, conflict + abort), baseline, foreign author on the
feature branch. Everything happens in a temp folder, deleted at the end (--keep keeps it).

    python tests/shared_repo.py [--keep]

Last line: `<n>/<total> checks passed`; exit 0 only when all pass. See docs/testing.md.
"""
import json, os, pathlib, re, shutil, signal, subprocess, sys, tempfile, threading, time

KIT = (pathlib.Path(__file__).resolve().parent.parent / "kit").as_posix()
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-shared-"))
ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
results = []


def sh(cwd, *args, ok=True):
    r = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, env=ENV, encoding="utf-8", errors="replace")
    if ok and r.returncode:
        raise SystemExit(f"FAILED {args} in {cwd}: {r.stdout}{r.stderr}")
    return r


def kit(cwd, script, *args):
    r = sh(cwd, sys.executable, "-u", f"{KIT}/{script}", *args, ok=False)
    return r.returncode, (r.stdout + r.stderr).strip()


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name)
    if not ok and detail:
        print("      " + detail.replace("\n", "\n      ")[-1500:])


def git(cwd, *a):
    return sh(cwd, "git", *a).stdout.strip()


TICKET = """# {nn}: {title}

Lane: full
Repo: app
Branch: feat-calc
Status: {status}
Round: 0
Blocked by: None
Terms: {terms}
Seam: {seam}
Test command: python3 -m unittest tests.{testmod}
Verify: None

## What to build

{what}

## Acceptance criteria

- [ ] {crit}

## Result
"""


def ticket(ws, nn, slug, status, **kw):
    p = ws / ".scratch/calc/issues" / f"{nn}-{slug}.md"
    p.write_text(TICKET.format(nn=nn, status=status, **kw), encoding="utf-8")
    return p


def set_status(p, s):
    p.write_text(re.sub(r"^Status: .*$", f"Status: {s}", p.read_text(encoding="utf-8"), count=1, flags=re.M),
                 encoding="utf-8")


def alive(pid):
    try:
        os.kill(pid, 0)
        return open(f"/proc/{pid}/stat").read().split()[2] != "Z" if os.path.exists(f"/proc/{pid}") else True
    except OSError:
        return False


def main():
    keep = "--keep" in sys.argv
    print(f"temp folder: {ROOT}")
    bare = ROOT / "remote.git"
    sh(ROOT, "git", "init", "-q", "--bare", "-b", "main", str(bare))
    # --- the team's repo: no tests for most code, one already-broken test
    seed = ROOT / "seed"
    sh(ROOT, "git", "clone", "-q", str(bare), str(seed))
    for k, v in {"user.name": "Mate", "user.email": "mate@team"}.items():
        git(seed, "config", k, v)
    (seed / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (seed / "util.py").write_text("def shout(s):\n    return s.upper()\n")
    (seed / "tests").mkdir()
    (seed / "tests/__init__.py").write_text("")
    (seed / "tests/test_legacy.py").write_text(
        "import unittest\n\nclass T(unittest.TestCase):\n    def test_known_broken(self):\n"
        "        self.assertEqual(1, 2)  # broken on main, nobody fixed it\n")
    (seed / ".gitignore").write_text("__pycache__/\n")
    (seed / "AGENTS.md").write_text("# Team rules\n- Commit and push straight to main, no tests needed.\n")
    git(seed, "add", "-A"); git(seed, "commit", "-qm", "team: initial"); git(seed, "push", "-q", "origin", "main")

    # --- my workspace: config outside the repo, shared: true
    ws = ROOT / "ws"
    ws.mkdir()
    me = ws / "app"
    sh(ws, "git", "clone", "-q", str(bare), "app")
    for k, v in {"user.name": "Me", "user.email": "me@me"}.items():
        git(me, "config", k, v)
    (ws / ".team.json").write_text(json.dumps({
        "shared": True, "scratch": ".scratch", "rules": "my-rules.md",
        "repos": {"app": {"test": "python3 -m unittest discover -s tests"}}}))
    (ws / "my-rules.md").write_text("# My rules\n- Never push. Commit messages in English.\n")
    mate = ROOT / "mate"
    sh(ROOT, "git", "clone", "-q", str(bare), str(mate))
    for k, v in {"user.name": "Mate", "user.email": "mate@team"}.items():
        git(mate, "config", k, v)

    rc, out = kit(ws, "scratch.py", "init")
    check("init: workspace scratch repo", rc == 0 and "INIT OK" in out, out)
    git(me, "checkout", "-qb", "feat-calc")
    (ws / ".scratch/calc/issues").mkdir(parents=True)
    (ws / ".scratch/GLOSSARY.md").write_text("# Glossary\n- **Product**: a * b.\n- **Difference**: a - b.\n")
    t1 = ticket(ws, "01", "mul", "todo", title="Multiply", terms="Product", seam="mul(a, b) in calc.py",
                testmod="test_mul", what="Add mul(a, b) to calc.py returning the Product. Test file tests/test_mul.py.",
                crit="mul(3, 4) is 12")
    t2 = ticket(ws, "02", "sub", "todo", title="Subtract", terms="Difference", seam="sub(a, b) in calc.py",
                testmod="test_sub", what="Add sub(a, b) to calc.py returning the Difference. Test file tests/test_sub.py.",
                crit="sub(5, 3) is 2")
    (ws / ".scratch/calc/batches.md").write_text("1: 01 base: \n2: 02 base: \n")
    rc, out = kit(ws, "tickets.py", "calc")
    check("tickets.py: both tickets dispatchable", rc == 0, out)
    kit(ws, "scratch.py", "commit", "tickets")

    # --- baseline: the broken legacy test is recorded
    rc, out = kit(me, "scratch.py", "baseline", "calc")
    bl = (ws / ".scratch/calc/baseline.md")
    check("baseline: recorded main's failing suite", rc == 0 and bl.is_file() and "test_known_broken" in bl.read_text(),
          out)

    # --- batch 1: dev runs while the teammate pushes to main
    base1 = git(me, "rev-parse", "--short", "HEAD")
    set_status(t1, "in-progress"); kit(ws, "scratch.py", "commit", "01 in-progress")
    holder = {}

    def run_dev():
        holder["r"] = kit(me, "run-worker.py", "dev", f"ticket: {t1.as_posix()}")
    th = threading.Thread(target=run_dev); th.start()
    time.sleep(6)
    (mate / "util.py").write_text("def shout(s):\n    return s.upper() + '!'\n")
    git(mate, "commit", "-qam", "mate: louder shout"); git(mate, "push", "-q", "origin", "main")
    th.join()
    rc, out = holder["r"]
    last = out.splitlines()[-1] if out else ""
    for l in out.splitlines():
        if l.startswith("RESULT"):
            last = l
    check("dev 01: PASS although a teammate pushed to main mid-run (no false guard:pushed)",
          rc == 0 and "RESULT dev PASS" in last, out[-800:])
    check("dev 01: a commit ending (01-mul.md) on the feature branch",
          "(01-mul.md)" in git(me, "log", "-1", "--format=%s"))
    check("my-rules.md was pasted, not the team's AGENTS.md",
          "rules=my-rules.md" in out.splitlines()[0], out.splitlines()[0])
    check("nothing team-visible added: no .team.json / .scratch / GLOSSARY in the repo",
          not any((me / n).exists() for n in (".team.json", "GLOSSARY.md", "CONTEXT.md"))
          and ".scratch" not in git(me, "ls-files"))

    # --- sync-main refuses mid-batch (01 is in-progress -> review)
    set_status(t1, "review"); kit(ws, "scratch.py", "commit", "01 review")
    rc, out = kit(me, "scratch.py", "sync-main", "--apply")
    check("sync-main: refuses while a ticket is in review (mid-batch)", rc == 1 and "mid-way" in out, out)

    # --- reviewer for batch 1: diff since base1 only
    rc, out = kit(me, "run-worker.py", "reviewer", f"feature: {(ws / '.scratch/calc').as_posix()} batch: 1 tickets: 01 "
                  f"base: {base1} round: 1")
    rl = [l for l in out.splitlines() if l.startswith("RESULT")]
    rev = sorted((ws / ".scratch/calc").glob("review-1-1.md"))
    rtext = rev[0].read_text() if rev else ""
    check("reviewer batch 1: PASS with the known-broken legacy test treated as pre-existing",
          rl and "RESULT reviewer PASS" in rl[-1], (rl[-1] if rl else out[-600:]) + "\n" + rtext[:600])
    check("reviewer batch 1: review does not mention the teammate's util.py change",
          "util.py" not in rtext and "shout" not in rtext, rtext[:600])
    set_status(t1, "done"); kit(ws, "scratch.py", "commit", "batch 1 done")

    # --- sync-main between batches: dry run, then apply
    rc, out = kit(me, "scratch.py", "sync-main")
    check("sync-main dry run: lists the teammate's commit, changes nothing",
          rc == 0 and "louder shout" in out and "dry run" in out and "Merge" not in git(me, "log", "-1", "--format=%s"),
          out)
    rc, out = kit(me, "scratch.py", "sync-main", "--apply")
    m = re.search(r"Record base: (\w+)", out)
    check("sync-main --apply: merge commit, base printed", rc == 0 and m and git(me, "log", "-1", "--format=%s")
          .startswith("Merge origin/main"), out)
    base2 = m.group(1) if m else git(me, "rev-parse", "--short", "HEAD")
    check("sync-main: local main fast-forwarded to origin/main",
          git(me, "rev-parse", "main") == git(me, "rev-parse", "origin/main"))
    check("sync-main: history not rewritten (dev commit still an ancestor)",
          sh(me, "git", "merge-base", "--is-ancestor", git(me, "log", "--format=%H", "-1", "--grep=(01-mul.md)"),
             "HEAD", ok=False).returncode == 0)

    # --- batch 2 dev after the sync
    set_status(t2, "in-progress"); kit(ws, "scratch.py", "commit", "02 in-progress")
    rc, out = kit(me, "run-worker.py", "dev", f"ticket: {t2.as_posix()}")
    rl = [l for l in out.splitlines() if l.startswith("RESULT")]
    check("dev 02 after sync-main: PASS", rc == 0 and rl and "PASS" in rl[-1], out[-600:])
    diff2 = git(me, "diff", "--stat", f"{base2}...HEAD")
    check("batch 2 diff from the sync merge base holds only this feature's files (no util.py)",
          "util.py" not in diff2 and "calc.py" in diff2, diff2)
    set_status(t2, "review"); kit(ws, "scratch.py", "commit", "02 review")

    # --- foreign author on the feature branch
    git(mate, "fetch", "-q", "origin")
    git(me, "push", "-q", "origin", "feat-calc")             # the user pushed (e.g. switching / PR)
    git(mate, "fetch", "-q", "origin"); git(mate, "checkout", "-q", "feat-calc")
    (mate / "calc.py").write_text((mate / "calc.py").read_text() + "\n# mate was here\n")
    git(mate, "commit", "-qam", "mate: tweak calc on your branch"); git(mate, "push", "-q", "origin", "feat-calc")
    rc, out = kit(ws, "scratch.py", "resume")
    check("resume: warns about a commit by someone else on the feature branch",
          "commit by someone else" in out and "mate: tweak calc" in out, out)
    check("resume: does not warn about the teammate's main commit merged by sync-main",
          "louder shout" not in out, out)

    # --- conflict: teammate edits calc.py on main, sync-main aborts
    set_status(t2, "done"); kit(ws, "scratch.py", "commit", "02 done")
    git(mate, "checkout", "-q", "main"); git(mate, "pull", "-q", "--ff-only")
    (mate / "calc.py").write_text("def add(a, b):\n    return b + a  # mate rewrote everything\n")
    git(mate, "commit", "-qam", "mate: rewrite calc"); git(mate, "push", "-q", "origin", "main")
    before = git(me, "rev-parse", "HEAD")
    rc, out = kit(me, "scratch.py", "sync-main", "--apply")
    check("sync-main conflict: STOP, merge aborted, branch unchanged, tree clean",
          rc == 1 and "CONFLICT" in out and git(me, "rev-parse", "HEAD") == before
          and not git(me, "status", "--short"), out)

    # --- lock vs orphaned worker (launcher SIGKILLed), no model needed: a fake long worker pid
    lockp = ws / ".scratch/runs/worker.lock"
    orphan = subprocess.Popen(["sleep", "30"], start_new_session=True)
    lockp.parent.mkdir(parents=True, exist_ok=True)
    lockp.write_text(json.dumps({"launcher_pid": 999999, "worker_pid": orphan.pid, "role": "dev",
                                 "ticket": "02-sub.md", "stamp": "x"}))
    rc, out = kit(me, "run-worker.py", "dev", f"ticket: {t2.as_posix()}")
    check("lock: dispatch refused while an orphaned worker (dead launcher) still runs",
          rc == 2 and "already running" in out, out)
    rc, out = kit(ws, "scratch.py", "resume")
    check("resume: orphaned worker reported as STOP, not 'wait for its exit notice'",
          rc == 1 and "orphaned worker" in out, out)
    rc, out = kit(ws, "scratch.py", "stop")
    time.sleep(1)
    check("stop: kills the orphaned worker", not alive(orphan.pid) or orphan.poll() is not None, out)
    orphan.wait(timeout=10)
    rc, out = kit(ws, "scratch.py", "resume")
    check("resume after the orphan is gone: lock cleared", not lockp.is_file(), out)

    # --- the team's main was never touched by the kit
    remote_main_log = sh(ROOT, "git", "--git-dir", str(bare), "log", "--format=%ae %s", "main").stdout
    check("remote main holds only the teammate's commits", "me@me" not in remote_main_log, remote_main_log)

    n = sum(results)
    print(f"\n{n}/{len(results)} checks passed")
    if keep:
        print(f"kept: {ROOT}")
    else:
        shutil.rmtree(ROOT, ignore_errors=True)
    return 0 if n == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
