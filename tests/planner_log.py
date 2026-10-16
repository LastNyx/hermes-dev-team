#!/usr/bin/env python3
"""The planner's own trail (kit/plannerlog.py, scratch.py status/fix/rewrite/decide, tickets.py WARNs). No model calls.

    python tests/planner_log.py [--keep]

Builds a throwaway repo with a scratch repo and four tickets, then drives the commands the planner runs and checks
what they write (ticket, batches.md, decisions.md, runs.log, progress log, scratch commits) and what they refuse.
Ends with the reviewer prompt line (run-worker.py `planner_fixes`) and, with HDW_CORE pointing at hermes-dev-watch's
core.py, the watcher's own reader on the planner lines. Last line: `<n>/<total> checks passed`. See docs/testing.md.
"""
import importlib.util, json, os, pathlib, re, shutil, subprocess, sys, tempfile

KIT = pathlib.Path(__file__).resolve().parent.parent / "kit"
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-planner-log-")).resolve()
ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "PYTHONDONTWRITEBYTECODE": "1"}
results = []

HEADER = """# {n}: {title}

Lane: full
Branch: feature/x
Status: {status}
Round: 0
Blocked by: {blocked}
Terms: thing
Seam: `calc.add`
Test command: python -m unittest -q
Verify: {verify}

## What to build

{what}

## Acceptance criteria

- [ ] it works

## Result
"""


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else "\n      " + str(detail)[-1500:]), flush=True)


def sh(cwd, *a):
    r = subprocess.run(list(a), cwd=cwd, capture_output=True, text=True, env=ENV)
    if r.returncode:
        raise SystemExit(f"{a}: {r.stderr}")
    return r.stdout.strip()


def kit(*a, script="scratch.py"):
    r = subprocess.run([sys.executable, str(KIT / script), *a], cwd=REPO, capture_output=True, text=True, env=ENV,
                       encoding="utf-8")
    return r.returncode, (r.stdout + r.stderr).strip()


def last(out):
    return out.splitlines()[-1] if out else ""


def planner_lines():
    p = SCRATCH / "runs.log"
    return [l.split("\t") for l in p.read_text(encoding="utf-8").splitlines()] if p.is_file() else []


def kv(f):
    return dict(x.split("=", 1) for x in f[4:] if "=" in x)


def ticket(name):
    return (FEAT / "issues" / name).read_text(encoding="utf-8")


def setup():
    global REPO, SCRATCH, FEAT
    REPO = ROOT / "proj"
    REPO.mkdir()
    sh(REPO, "git", "init", "-q", "-b", "main")
    (REPO / ".team.json").write_text(json.dumps({"test": "python -m unittest -q"}))
    sh(REPO, "git", "add", "-A"); sh(REPO, "git", "commit", "-q", "-m", "init")
    sh(REPO, "git", "checkout", "-q", "-b", "feature/x")
    SCRATCH = REPO / ".scratch"
    FEAT = SCRATCH / "x"
    (FEAT / "issues").mkdir(parents=True)
    t = {"01-add.md": ("Add", "in-progress", "None", "None", "Add two numbers."),
         "02-page.md": ("Page", "in-progress", "None", "planner, in a browser: the sum shows", "Show the sum."),
         "03-more.md": ("More", "todo", "02", "planner, in a browser: three numbers", "Add three."),
         "04-later.md": ("Later", "todo", "None", "needs the real DB", "Store it.")}
    for name, (title, st, bl, ver, what) in t.items():
        (FEAT / "issues" / name).write_text(HEADER.format(n=name[:2], title=title, status=st, blocked=bl, verify=ver,
                                                          what=what), encoding="utf-8")
    (FEAT / "batches.md").write_text("1: 01,02 base: abc1234\n2: 03,04\n", encoding="utf-8")
    code, out = kit("init")
    check("setup: scratch init", code == 0, out)


def run():
    setup()
    # ---- status
    code, out = kit("status", "x", "01", "review")
    check("status: a ticket with `Verify: None` moves without --verify; Status rewritten, comment kept",
          code == 0 and re.search(r"^Status: review$", ticket("01-add.md"), re.M), out)
    f = planner_lines()[-1]
    check("status: one planner runs.log line (role, model, NOTE verdict, turns=-, secs, action, ticket, fmt last)",
          (f[1], f[2], f[3], f[4], kv(f).get("action"), kv(f).get("ticket"), f[-1].startswith("fmt="))
          == ("planner", "planner", "NOTE status 01 review", "turns=-", "status", "01-add.md", True), f)
    check("status: run id fits the watcher's pattern; progress log written", re.match(r"^\d{8}-\d{6}-[a-z]+$",
          f"{f[0]}-{f[1]}") and (SCRATCH / f[9]).is_file() and "status 01 in-progress -> review" in
          (SCRATCH / f[9]).read_text(encoding="utf-8"), f)
    check("status: the scratch repo got a commit", sh(SCRATCH, "git", "log", "-1", "--format=%s") == "01 review",
          sh(SCRATCH, "git", "log", "--oneline", "-3"))

    code, out = kit("status", "x", "02", "review")
    check("refuse: a ticket with a Verify line to review without --verify (STOP, file unchanged)",
          code == 1 and "STATUS STOP" in last(out) and "Status: in-progress" in ticket("02-page.md"), out)
    n = len(planner_lines())
    code, out = kit("status", "x", "02", "review", "--verify", "maybe")
    check("refuse: --verify needs PASS:/FAIL:/SKIP:", code == 1 and len(planner_lines()) == n, out)
    code, out = kit("status", "x", "02", "review", "--step", "x")
    check("refuse: --step without --verify", code == 1, out)
    code, out = kit("status", "x", "02", "review", "--verify", "FAIL: the sum is blank")
    check("refuse: FAIL to review with no planner fix ticket for it", code == 1 and "fix" in last(out)
          and "Status: in-progress" in ticket("02-page.md") and len(planner_lines()) == n, out)
    code, out = kit("status", "x", "02", "done", "--verify", "FAIL: the sum is blank")
    check("refuse: FAIL to done", code == 1, out)

    # ---- fix
    code, out = kit("fix", "x", "Bad_Name", "--for", "02", "x")
    check("refuse: fix ticket name must be NN-fix-slug", code == 1, out)
    code, out = kit("fix", "x", "01-fix-sum", "--for", "02", "x")
    check("refuse: a fix ticket may not take another ticket's number", code == 1 and "taken" in out, out)
    code, out = kit("fix", "x", "05-fix-blank-sum", "--for", "02", "the sum is blank after\tload\n(seen in a browser)")
    fx = ticket("05-fix-blank-sum.md") if (FEAT / "issues" / "05-fix-blank-sum.md").is_file() else ""
    check("fix: creates the ticket (Status todo, Round 1, Blocked by 02, header copied, Found by the planner)",
          code == 0 and all(s in fx for s in ("Status: todo", "Round: 1", "Blocked by: 02", "Seam: `calc.add`",
                                               "Test command: python -m unittest -q",
                                               "Found by the planner: the sum is blank after load (seen in a browser)")),
          out + "\n" + fx)
    check("fix: added to the batch that lists 02, base kept",
          (FEAT / "batches.md").read_text(encoding="utf-8").splitlines()[0] == "1: 01,02,05 base: abc1234",
          (FEAT / "batches.md").read_text(encoding="utf-8"))
    dec = (FEAT / "decisions.md").read_text(encoding="utf-8")
    f = planner_lines()[-1]
    check("fix: a decisions.md line and a `NOTE fix 05 for 02` planner line (whitespace folded, one line)",
          "planner fix ticket 05-fix-blank-sum.md for 02-page.md" in dec and f[3].startswith("NOTE fix 05 for 02: ")
          and "\t" not in f[3] and kv(f).get("action") == "fix", (dec, f))
    check("fix: committed", "fix ticket" in sh(SCRATCH, "git", "log", "-1", "--format=%s"))
    code, out = kit("fix", "x", "05-fix-blank-sum", "--for", "02", "again")
    check("refuse: the fix ticket exists already", code == 1, out)

    code, out = kit("status", "x", "02", "review", "--verify", "FAIL: the sum is blank", "--step", "loaded the page",
                    "--step", "typed 2 and 3")
    t2 = ticket("02-page.md")
    f = planner_lines()[-1]
    prog = (SCRATCH / f[9]).read_text(encoding="utf-8")
    check("status --verify FAIL to review once a planner fix ticket names it; record + steps in the ticket",
          code == 0 and "Status: review" in t2 and "Planner Verify: FAIL the sum is blank\n- loaded the page" in t2,
          out + "\n" + t2)
    check("verify line: verdict `FAIL verify 02: ...`, action=verify; progress has say: steps then the change",
          f[3] == "FAIL verify 02: the sum is blank" and kv(f).get("action") == "verify"
          and re.findall(r"\] (.*)", prog) == ["say: loaded the page", "say: typed 2 and 3",
                                               "status 02 in-progress -> review"], (f, prog))
    code, out = kit("status", "x", "02", "done")
    check("refuse: done while its last record is FAIL and no new --verify", code == 1, out)
    code, out = kit("status", "x", "02", "done", "--verify", "PASS: sum shows")
    check("refuse: done while a fix ticket naming it is open", code == 1 and "05-fix-blank-sum.md" in out, out)

    # ---- the reviewer prompt line
    sys.path.insert(0, str(KIT))
    spec = importlib.util.spec_from_file_location("run_worker", KIT / "run-worker.py")
    rw = importlib.util.module_from_spec(spec); spec.loader.exec_module(rw)
    cwd = os.getcwd(); os.chdir(REPO)
    try:
        own = rw.planner_fixes("feature: .scratch/x batch: 1 tickets: 01,02,05 base: abc1234 round: 1")
        none = rw.planner_fixes("feature: .scratch/x batch: 1 tickets: 01,02 base: abc1234 round: 1")
    finally:
        os.chdir(cwd)
    check("reviewer prompt: names planner-written fix tickets in the batch, and only those",
          own == ["05-fix-blank-sum.md"] and none == [], (own, none))

    # ---- finish 05, then 02 can be done
    for st, extra in (("in-progress", []), ("review", []), ("done", [])):
        code, out = kit("status", "x", "05", st, *extra)
    check("fix ticket 05 (Verify copied: `planner, in a browser`) needs --verify too", code == 1, out)
    code, out = kit("status", "x", "05", "review", "--verify", "PASS: the sum shows after load")
    code2, out2 = kit("status", "x", "05", "done")
    check("a last PASS record lets the ticket move on without a second --verify", code == 0 and code2 == 0,
          out + "\n" + out2)
    code, out = kit("status", "x", "02", "done", "--verify", "PASS: sum shows")
    check("done once its fix tickets are done", code == 0 and "Status: done" in ticket("02-page.md"), out)

    # ---- SKIP, rewrite, decide
    code, out = kit("status", "x", "04", "review", "--verify", "SKIP: needs the real DB, runs after review")
    check("SKIP is recorded and lets it move", code == 0 and planner_lines()[-1][3].startswith("SKIP verify 04"), out)
    code, out = kit("status", "x", "03", "in-progress")
    t3 = FEAT / "issues" / "03-more.md"
    t3.write_text(t3.read_text(encoding="utf-8").replace("Add three.", "Add three, or none."), encoding="utf-8")
    sh(SCRATCH, "git", "commit", "-qam", "hand edit")
    code, out = kit("x", script="tickets.py")
    check("tickets.py WARNs on a ticket changed after dispatch with no rewrite record",
          "WARN 03-more.md: changed after it was dispatched" in out, out)
    code, out = kit("resume", "x")
    check("resume shows the same WARN (and stays OK)", code == 0 and "WARN 03-more.md: changed after" in out, out)
    code, out = kit("rewrite", "x", "03", "criteria were wrong: zero numbers allowed")
    _, out2 = kit("x", script="tickets.py")
    check("rewrite: `Rewritten by the planner:` line, NOTE rewrite line, and the WARN is gone",
          code == 0 and "Rewritten by the planner: criteria were wrong" in ticket("03-more.md")
          and planner_lines()[-1][3].startswith("NOTE rewrite 03") and "changed after" not in out2, out + out2)
    code, out = kit("decide", "x", "user: skip the DB check until staging exists")
    f = planner_lines()[-1]
    check("decide: decisions.md line plus a `NOTE decide:` planner line, one commit",
          code == 0 and f[3] == "NOTE decide: user: skip the DB check until staging exists"
          and sh(SCRATCH, "git", "status", "--short") == "", (out, f))

    (FEAT / "issues" / "06-fix-hand.md").write_text(HEADER.format(n="06", title="hand", status="todo", blocked="02",
                                                                  verify="None", what="x"), encoding="utf-8")
    _, out = kit("x", script="tickets.py")
    check("tickets.py WARNs on a fix ticket with no planner or review origin",
          "WARN 06-fix-hand.md: a fix ticket with neither" in out, out)
    (FEAT / "review-1-1.md").write_text("# Review\n\n- 02: blank. Fix ticket 06.\n", encoding="utf-8")
    _, out = kit("x", script="tickets.py")
    check("... and not once a review file names it", "WARN 06-fix-hand" not in out, out)

    # ---- the secs field and unique ids
    rows = [f for f in planner_lines() if f[1] == "planner"]
    ids = [f"{f[0]}-{f[1]}" for f in rows]
    check("every planner line has a unique run id (stamps move on within one second)", len(ids) == len(set(ids)), ids)
    check("first planner line 0s; later ones the time since the previous (0..120s here)",
          rows[0][5] == "0s" and all(re.fullmatch(r"\d+s", f[5]) and int(f[5][:-1]) <= 120 for f in rows), [f[5] for f in rows])

    core = os.environ.get("HDW_CORE")
    if core and pathlib.Path(core).is_file():
        spec = importlib.util.spec_from_file_location("hdw_core", core)
        c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)
        runs = c.runs_log(SCRATCH)
        mine = {k: v for k, v in runs.items() if k.endswith("-planner")}
        p = c.progress(SCRATCH, ids[ids.index(next(i for i, f in zip(ids, rows) if f[3].startswith("FAIL")))], 0)
        check("hermes-dev-watch reads every planner line and a planner progress log",
              len(mine) == len(rows) and "say: loaded the page" in (p.get("text") or ""), (len(mine), len(rows), p))
    else:
        print("INFO  HDW_CORE not set: hermes-dev-watch's reader not run on the planner lines")


def main():
    try:
        run()
    finally:
        if "--keep" in sys.argv:
            print(f"kept {ROOT}")
        else:
            shutil.rmtree(ROOT, ignore_errors=True)
    print(f"{sum(results)}/{len(results)} checks passed")
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
