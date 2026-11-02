#!/usr/bin/env python3
"""E2E: resume, force-stop and machine switching (kit/scratch.py + the worker lock), single-repo layout.

Two clones of one toy repo act as machines A and B (a third, C, checks a clean fast-forward); local bare repos are
the remotes. No model: the test plays the delegate child between `run-worker.py prepare` and `finish` (and fakes a
running child's Hermes manifest for the wait/stop part). Needs git and Python 3. Everything happens in a temp folder,
deleted at the end (--keep keeps it).

    python tests/resume_e2e.py [--keep]

Last line: `<n>/<total> checks passed`; exit 0 only when all pass. See docs/testing.md.
"""
import json, os, pathlib, shutil, subprocess, sys, tempfile, time

KIT = (pathlib.Path(__file__).resolve().parent.parent / "kit").as_posix()
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-resume-"))
HH = ROOT / "hermes-home"
ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
       "HERMES_HOME": HH.as_posix()}
results = []


def sh(cwd, *args, ok=True):
    r = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, env=ENV, encoding="utf-8", errors="replace")
    if ok and r.returncode:
        raise SystemExit(f"FAILED {args} in {cwd}: {r.stdout}{r.stderr}")
    return r


def kit(cwd, script, *args, stdin=None):
    r = subprocess.run([sys.executable, "-u", f"{KIT}/{script}", *args], cwd=cwd, capture_output=True, text=True,
                       env=ENV, encoding="utf-8", errors="replace", input=stdin)
    return r.returncode, (r.stdout + r.stderr).strip()


def prepare(cwd, ticket):
    rc, out = kit(cwd, "run-worker.py", "prepare", "dev", f"ticket: {ticket}")
    rid = next((l.split()[1] for l in out.splitlines() if l.startswith("PREPARED ")), None)
    return rc, out, rid


def running_child(rid):
    """What Hermes writes while a delegate child runs: a manifest naming the run in its goal, a fresh transcript."""
    d = HH / "cache/delegation/live/deleg_test"; d.mkdir(parents=True, exist_ok=True)
    (d / "task-0.log").write_text("12:00 tool | -> terminal(ls)\n")
    (d / "manifest.json").write_text(json.dumps({"started": time.strftime("%Y-%m-%d %H:%M:%S"), "tasks": [
        {"index": 0, "goal": f"[hdt] {rid}: dev worker", "log": str(d / "task-0.log"), "status": "running"}]}))


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"\n      {detail}" if detail and not cond else ""), flush=True)


def last(out):
    return out.strip().splitlines()[-1] if out.strip() else ""


def indent(out):
    return "\n".join("        " + l for l in out.splitlines())


TICKET = """# {n}: {title}

Lane: quick
Branch: feat-calc
Status: todo
Round: 0
Blocked by: None
Terms: calc
Seam: calc.py function {fn}
Test command: python -m unittest -q
Verify: None

## What to build

Add a function `{fn}(a, b)` to calc.py that returns {what}. Add one unittest for it in test_calc.py.

## Acceptance criteria

- [ ] `{fn}(2, 3)` returns {ex}

## Result
"""


def setup():
    for bare in ("product.git", "scratch.git"):
        sh(ROOT, "git", "init", "-q", "--bare", "-b", "main", bare)
    a = ROOT / "A" / "calc"
    a.parent.mkdir()
    sh(a.parent, "git", "clone", "-q", (ROOT / "product.git").as_posix(), "calc")
    (a / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (a / "test_calc.py").write_text("import unittest\nfrom calc import add\n\n\nclass T(unittest.TestCase):\n"
                                    "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n")
    (a / ".team.json").write_text(json.dumps({"test": "python -m unittest -q", "options": {"dev": "standard"}}))
    (a / ".gitignore").write_text("__pycache__/\n")
    sh(a, "git", "add", "-A"); sh(a, "git", "commit", "-q", "-m", "init"); sh(a, "git", "push", "-q", "origin", "main")
    sh(a, "git", "checkout", "-q", "-b", "feat-calc")
    return a


def run():
    a = setup()
    sc = a / ".scratch"
    # ---- machine A: scratch repo, tickets, a decision
    rc, out = kit(a, "scratch.py", "init", "--remote", (ROOT / "scratch.git").as_posix())
    check("A init: OK + .scratch/ excluded", rc == 0 and "INIT OK" in last(out) and
          "/.scratch/" in (a / ".git/info/exclude").read_text(), out)
    check("A product tree ignores the nested scratch repo", sh(a, "git", "status", "--short").stdout.strip() == "")
    iss = sc / "f1" / "issues"; iss.mkdir(parents=True)
    (iss / "01-mul.md").write_text(TICKET.format(n="01", title="multiply", fn="mul", what="a * b", ex="6"))
    (iss / "02-sub.md").write_text(TICKET.format(n="02", title="subtract", fn="sub", what="a - b", ex="-1"))
    (iss / "03-neg.md").write_text(TICKET.format(n="03", title="negate sum", fn="negsum", what="-(a + b)", ex="-5"))
    rc, out = kit(a, "tickets.py", "f1")
    check("tickets.py accepts the tickets", rc == 0, out)
    rc, out = kit(a, "scratch.py", "commit", "tickets: f1")
    check("A scratch commit", rc == 0 and "COMMIT OK" in out and "nothing" not in out, out)
    rc, out = kit(a, "scratch.py", "decide", "f1", "skip Verify on every ticket (toy repo)")
    check("A decide is committed", rc == 0 and "committed" in out, out)
    tracked = sh(sc, "git", "ls-files").stdout.split()
    check("scratch repo tracks tickets + decisions, not runs/", ".gitignore" in tracked and
          "f1/decisions.md" in tracked and not any(t.startswith("runs/") for t in tracked), str(tracked))

    # ---- dev run on 01: prepare, the child's work (played here), finish
    t1 = iss / "01-mul.md"; t1.write_text(t1.read_text().replace("Status: todo", "Status: in-progress"))
    kit(a, "scratch.py", "commit", "01 in-progress")
    rc, out, rid = prepare(a, ".scratch/f1/issues/01-mul.md")
    check("A prepare 01", rc == 0 and rid, out)
    (a / "calc.py").write_text((a / "calc.py").read_text() + "\n\ndef mul(a, b):\n    return a * b\n")
    sh(a, "git", "commit", "-qam", "multiply (01-mul.md)")
    sha = sh(a, "git", "rev-parse", "--short", "HEAD").stdout.strip()
    rc, out = kit(a, "run-worker.py", "finish", rid, stdin=json.dumps({"verdict": "PASS", "summary": "mul", "sha": sha}))
    check("A dev 01 PASS", rc == 0 and f"RESULT dev PASS {sha}" in out, last(out))
    log = (sc / "runs.log").read_text()
    check("runs.log line carries ticket=01-mul.md", "\tticket=01-mul.md" in log, log[-300:])
    check("worker lock removed after the run", not (sc / "runs/worker.lock").exists())
    t1.write_text(t1.read_text().replace("Status: in-progress", "Status: done"))
    kit(a, "scratch.py", "commit", "01 done")

    # ---- dev on 02 with a running child: second prepare, switch and resume must wait; then stop
    t2 = iss / "02-sub.md"; t2.write_text(t2.read_text().replace("Status: todo", "Status: in-progress"))
    kit(a, "scratch.py", "commit", "02 in-progress")
    rc, out, rid2 = prepare(a, ".scratch/f1/issues/02-sub.md")
    running_child(rid2)
    lk = sc / "runs/worker.lock"
    rc, out, _ = prepare(a, ".scratch/f1/issues/03-neg.md")
    check("second prepare refused while a worker runs", rc == 2 and "still running" in out, last(out))
    rc, out = kit(a, "scratch.py", "switch")
    check("switch refused while a run is open", rc == 1 and "SWITCH STOP a worker run is not finished" in out, last(out))
    rc, out = kit(a, "scratch.py", "resume")
    check("resume during a run says wait", rc == 0 and "worker running" in out, last(out))
    rc, out = kit(a, "scratch.py", "stop")
    check("stop refuses while the child runs (the planner stops the child first)", rc == 1 and "still running" in out,
          out)
    rc, stop_out = kit(a, "scratch.py", "stop", "--force")
    print("      stop said:\n" + indent(stop_out))
    check("stop --force OK, the run finished as FAIL force-stopped",
          rc == 0 and "STOP OK" in last(stop_out) and "RESULT dev FAIL force-stopped" in stop_out, stop_out)
    check("lock gone after stop", not lk.exists())
    dec = (sc / "f1/decisions.md").read_text()
    check("decisions.md has the force-stopped line, committed", "force-stopped dev 02-sub.md" in dec and
          sh(sc, "git", "status", "--short").stdout.strip() == "", dec)
    check("ticket 02 still in-progress", "Status: in-progress" in t2.read_text())

    # ---- a half-done edit in the tree (what a stopped worker can leave): switch STOPs, resume ASKs
    (a / "calc.py").write_text((a / "calc.py").read_text() + "\n\ndef sub(a, b):\n    return a -\n")
    rc, out = kit(a, "scratch.py", "switch")
    check("switch STOPs on uncommitted product changes", rc == 1 and "uncommitted" in last(out), out)
    rc, out = kit(a, "scratch.py", "resume")
    check("resume: in-progress + dirty tree -> ASK + STOP", rc == 1 and "ASK the user" in out, out)
    sh(a, "git", "checkout", "--", "."); sh(a, "git", "clean", "-fdq")      # the user's choice: discard
    rc, out = kit(a, "scratch.py", "switch")
    check("switch OK pushes scratch + feature branch", rc == 0 and "SWITCH OK" in last(out) and
          "feat-calc" in out and "scratch:" in out, out)
    main_log = sh(ROOT / "product.git", "git", "log", "--oneline", "main").stdout.strip().splitlines()
    check("main on the remote unchanged (init only)", len(main_log) == 1, str(main_log))

    # ---- machine B
    b = ROOT / "B" / "calc"; b.parent.mkdir()
    sh(b.parent, "git", "clone", "-q", (ROOT / "product.git").as_posix(), "calc")
    rc, out = kit(b, "scratch.py", "init", "--clone", (ROOT / "scratch.git").as_posix())
    check("B init --clone", rc == 0 and "INIT OK" in last(out) and (b / ".scratch/f1/issues/02-sub.md").is_file(), out)
    check("B product tree ignores scratch", sh(b, "git", "status", "--short").stdout.strip() == "")
    rc, out = kit(b, "scratch.py", "resume")
    print("      B resume:\n" + indent(out))
    check("B resume checks out feat-calc", sh(b, "git", "branch", "--show-current").stdout.strip() == "feat-calc", out)
    check("B resume: 02 -> REDISPATCH", rc == 0 and "02-sub.md in-progress" in out and "REDISPATCH" in out, out)
    check("B resume shows the decisions", "skip Verify" in out and "force-stopped" in out, out)
    check("B has the dev commit from A", "(01-mul.md)" in sh(b, "git", "log", "--format=%s").stdout)

    # ---- crash after the commit: in-progress ticket with a matching commit -> VERIFY
    bt3 = b / ".scratch/f1/issues/03-neg.md"
    bt3.write_text(bt3.read_text().replace("Status: todo", "Status: in-progress"))
    (b / "calc.py").write_text((b / "calc.py").read_text() + "\n\ndef negsum(a, b):\n    return -(a + b)\n")
    sh(b, "git", "commit", "-qam", "negate sum (03-neg.md)")
    rc, out = kit(b, "scratch.py", "resume")
    check("resume: in-progress with a commit -> VERIFY", "03-neg.md in-progress" in out and "VERIFY" in out, out)

    # ---- divergence: B pushes, A has its own scratch commit
    rc, out = kit(b, "scratch.py", "switch")
    check("B switch OK", rc == 0 and "SWITCH OK" in last(out), out)
    kit(a, "scratch.py", "decide", "f1", "made on A after the switch (diverges)")
    rc, out = kit(a, "scratch.py", "resume")
    check("A resume STOPs on a diverged scratch repo, no merge", rc == 1 and "DIVERGED" in out and
          sh(a / ".scratch", "git", "log", "--merges", "--oneline").stdout.strip() == "", out)
    rc, out = kit(a, "scratch.py", "switch")
    check("A switch with a diverged scratch: push rejected, never forced", rc == 1 and "rejected" in out, out)

    # ---- clean fast-forward on a third machine
    c = ROOT / "C" / "calc"; c.parent.mkdir()
    sh(c.parent, "git", "clone", "-q", (ROOT / "product.git").as_posix(), "calc")
    kit(c, "scratch.py", "init", "--clone", (ROOT / "scratch.git").as_posix())
    kit(c, "scratch.py", "resume")
    kit(b, "scratch.py", "decide", "f1", "B again")
    kit(b, "scratch.py", "switch")
    rc, out = kit(c, "scratch.py", "resume")
    check("C resume fast-forwards the scratch repo", rc == 0 and "fast-forwarded" in out and
          "B again" in (c / ".scratch/f1/decisions.md").read_text(), out)


def main():
    print(f"temp folder: {ROOT.as_posix()}")
    try:
        run()
    finally:
        if "--keep" not in sys.argv:
            shutil.rmtree(ROOT, onerror=lambda f, p, e: (os.chmod(p, 0o700), f(p)))
    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
