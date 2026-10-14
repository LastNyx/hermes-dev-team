#!/usr/bin/env python3
"""E2E: resume, force-stop and machine switching (kit/scratch.py + the worker lock), single-repo layout.

Two clones of one toy repo act as machines A and B (a third, C, checks a clean fast-forward); local bare repos are
the remotes. Makes 2 real `claude -p` dev runs (Sonnet, about $0.20 list price, about 1 minute in total).
Needs: `claude` logged in, git, Python 3. Everything happens in a temp folder, deleted at the end (--keep keeps it).

    python tests/resume_e2e.py [--keep]

Last line: `<n>/<total> checks passed`; exit 0 only when all pass. See docs/testing.md.
"""
import json, os, pathlib, shutil, subprocess, sys, tempfile, time

KIT = (pathlib.Path(__file__).resolve().parent.parent / "kit").as_posix()
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-resume-"))
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
    (a / ".team.json").write_text(json.dumps({"test": "python -m unittest -q"}))
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

    # ---- real dev run on 01
    t1 = iss / "01-mul.md"; t1.write_text(t1.read_text().replace("Status: todo", "Status: in-progress"))
    kit(a, "scratch.py", "commit", "01 in-progress")
    t0 = time.time()
    rc, out = kit(a, "run-worker.py", "dev", "ticket: .scratch/f1/issues/01-mul.md")
    check(f"A dev 01 PASS ({round(time.time() - t0)} s)", rc == 0 and "RESULT dev PASS" in out, last(out))
    log = (sc / "runs.log").read_text()
    check("runs.log line carries ticket=01-mul.md", "\tticket=01-mul.md" in log, log[-300:])
    check("worker lock removed after the run", not (sc / "runs/worker.lock").exists())
    subj = sh(a, "git", "log", "-1", "--format=%s").stdout.strip()
    check("dev commit subject ends with (01-mul.md)", subj.endswith("(01-mul.md)"), subj)
    t1.write_text(t1.read_text().replace("Status: in-progress", "Status: done"))
    kit(a, "scratch.py", "commit", "01 done")

    # ---- dev on 02 in the background: second dispatch, switch and resume must wait; then force-stop
    t2 = iss / "02-sub.md"; t2.write_text(t2.read_text().replace("Status: todo", "Status: in-progress"))
    kit(a, "scratch.py", "commit", "02 in-progress")
    bg = subprocess.Popen([sys.executable, "-u", f"{KIT}/run-worker.py", "dev", "ticket: .scratch/f1/issues/02-sub.md"],
                          cwd=a, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=ENV,
                          encoding="utf-8", errors="replace")
    lk = sc / "runs/worker.lock"
    for _ in range(60):
        time.sleep(1)
        if lk.exists() and "worker_pid" in lk.read_text():
            break
    time.sleep(8)                                    # let the worker start working
    rc, out = kit(a, "run-worker.py", "dev", "ticket: .scratch/f1/issues/03-neg.md")
    check("second dispatch refused while a worker runs", rc == 2 and "already running" in out, last(out))
    rc, out = kit(a, "scratch.py", "switch")
    check("switch refused while a worker runs", rc == 1 and "SWITCH STOP a worker is running" in out, last(out))
    rc, out = kit(a, "scratch.py", "resume")
    check("resume during a run says wait", rc == 0 and "worker running" in out, last(out))
    rc, stop_out = kit(a, "scratch.py", "stop")
    bg_out = bg.communicate(timeout=120)[0]
    print("      stop said:\n" + indent(stop_out))
    check("stop OK", rc == 0 and "STOP OK" in last(stop_out), stop_out)
    check("launcher RESULT is FAIL force-stopped", "RESULT dev FAIL force-stopped" in bg_out, last(bg_out))
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
