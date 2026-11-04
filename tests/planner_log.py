#!/usr/bin/env python3
"""The planner's trail (kit/plannerlog.py via scratch.py status and decide). No model calls.

    python tests/planner_log.py [--keep]

Builds a throwaway repo with a scratch repo and four tickets, then drives `status` and `decide` and checks what they
write (ticket, decisions.md, runs.log, scratch commits); with HDW_CORE pointing at hermes-dev-watch's core.py, the
watcher's own reader on the planner lines. Last line: `<n>/<total> checks passed`. See docs/testing.md.
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
    check("status: Status rewritten", code == 0 and re.search(r"^Status: review$", ticket("01-add.md"), re.M), out)
    f = planner_lines()[-1]
    check("status: one planner runs.log line (role, model, NOTE verdict, turns=-, secs, action, ticket, fmt last)",
          (f[1], f[2], f[3], f[4], kv(f).get("action"), kv(f).get("ticket"), f[-1].startswith("fmt="))
          == ("planner", "planner", "NOTE status 01 review", "turns=-", "status", "01-add.md", True), f)
    check("status: run id fits the watcher's pattern; no progress log (field 10 is `-`)",
          re.match(r"^\d{8}-\d{6}-[a-z]+$", f"{f[0]}-{f[1]}") and f[9] == "-"
          and not list((SCRATCH / "runs").glob("*-planner.progress.log")), f)
    check("status: the scratch repo got a commit", sh(SCRATCH, "git", "log", "-1", "--format=%s") == "01 review",
          sh(SCRATCH, "git", "log", "--oneline", "-3"))

    code, out = kit("status", "x", "02", "review")
    check("status: a ticket with a Verify line moves without --verify (no refusal)",
          code == 0 and "Status: review" in ticket("02-page.md"), out)
    n = len(planner_lines())
    code, out = kit("status", "x", "02", "review", "--verify", "maybe")
    check("refuse: --verify needs PASS:/FAIL:/SKIP: (nothing written)", code == 1 and len(planner_lines()) == n, out)
    code, out = kit("status", "x", "02", "review", "--verify", "FAIL: the sum is blank")
    t2 = ticket("02-page.md")
    f = planner_lines()[-1]
    check("status --verify FAIL: allowed without a fix ticket; `Planner Verify:` under ## Result",
          code == 0 and "Planner Verify: FAIL the sum is blank" in t2, out + "\n" + t2)
    check("verify line: verdict `FAIL verify 02: ...`, action=status",
          f[3] == "FAIL verify 02: the sum is blank" and kv(f).get("action") == "status", f)
    code, out = kit("status", "x", "04", "review", "--verify", "SKIP: needs the real DB, runs after review")
    check("SKIP is recorded", code == 0 and planner_lines()[-1][3].startswith("SKIP verify 04"), out)
    code, out = kit("status", "x", "02", "done", "--verify", "PASS: sum shows")
    check("PASS to done", code == 0 and "Status: done" in ticket("02-page.md")
          and planner_lines()[-1][3] == "PASS verify 02: sum shows", out)
    code, out = kit("status", "x", "02", "nope")
    check("refuse: unknown status", code == 1 and "STATUS STOP" in last(out), out)
    code, out = kit("status", "x", "fix", "review", "--verify", "PASS: x")
    check("refuse: unknown ticket", code == 1 and "STATUS STOP" in last(out), out)
    for gone in (["fix", "x", "05-fix-a", "--for", "02", "x"], ["rewrite", "x", "03", "why"],
                 ["status", "x", "03", "review", "--step", "x"]):
        code, out = kit(*gone)
        check(f"removed in 0.14.0: `{'--step' if gone[0] == 'status' else gone[0]}`",
              code == 2, out[-200:])

    # ---- decide
    code, out = kit("decide", "x", "user: skip the DB check until staging exists")
    f = planner_lines()[-1]
    check("decide: decisions.md line plus a `NOTE decide:` planner line (action=decide), one commit",
          code == 0 and f[3] == "NOTE decide: user: skip the DB check until staging exists"
          and kv(f).get("action") == "decide" and sh(SCRATCH, "git", "status", "--short") == "", (out, f))

    # ---- no WARNs any more
    t3 = FEAT / "issues" / "03-more.md"
    kit("status", "x", "03", "in-progress")
    t3.write_text(t3.read_text(encoding="utf-8").replace("Add three.", "Add three, or none."), encoding="utf-8")
    sh(SCRATCH, "git", "commit", "-qam", "hand edit")
    _, out = kit("x", script="tickets.py")
    code, out2 = kit("resume", "x")
    check("tickets.py and resume print no planner WARNs (rewrite detection removed)",
          "changed after" not in out and "changed after" not in out2 and code == 0, out + out2)

    # ---- the secs field and unique ids
    rows = [f for f in planner_lines() if f[1] == "planner"]
    ids = [f"{f[0]}-{f[1]}" for f in rows]
    check("every planner line has a unique run id (stamps move on within one second)", len(ids) == len(set(ids)), ids)
    check("first planner line 0s; later ones the time since the previous (0..120s here)",
          rows[0][5] == "0s" and all(re.fullmatch(r"\d+s", f[5]) and int(f[5][:-1]) <= 120 for f in rows),
          [f[5] for f in rows])

    core = os.environ.get("HDW_CORE")
    if core and pathlib.Path(core).is_file():
        spec = importlib.util.spec_from_file_location("hdw_core", core)
        c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)
        runs = c.runs_log(SCRATCH)
        mine = {k: v for k, v in runs.items() if k.endswith("-planner")}
        check("hermes-dev-watch reads every planner line", len(mine) == len(rows), (len(mine), len(rows)))
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
