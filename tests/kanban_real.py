#!/usr/bin/env python3
"""`kanban.py run` with REAL dev workers on a throwaway repo. CALLS A MODEL (three short Sonnet runs).

Three tickets on one feature branch, max_workers 2: 01 and 02 are independent (separate Seams) and must run at the
same time; 03 is `Blocked by: 01`, uses 01's function and must not start before 01 is merged. Drives
`python kit/kanban.py run feat --interval 2` as a subprocess (hard 600 s timeout, stdin closed), then checks the
ticket files, runs.log, the feature branch, the locks, worktrees and `board --json`.

    python tests/kanban_real.py [--keep]

Last line: `<n>/<total> checks passed`; exit 0 only when all pass. See docs/testing.md.
"""
import json, os, pathlib, re, shutil, subprocess, sys, tempfile, textwrap, time

KIT = pathlib.Path(__file__).resolve().parent.parent / "kit"
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-kanban-real-"))
os.environ.update(GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1")
PY = sys.executable
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name)
    if not ok and detail:
        print("      " + str(detail)[-3000:].replace("\n", "\n      "))


def sh(cwd, *a, ok=False):
    r = subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)
    if r.returncode and not ok:
        raise SystemExit(f"git {a}: {r.stderr}")
    return r.stdout.strip()


def write_ticket(repo, nn, mod, fn, body, expect, blocked="None"):
    (repo / f"test_{mod}.py").write_text(textwrap.dedent(f"""\
        import unittest
        from {mod} import {fn}
        class T(unittest.TestCase):
            def test_it(self):
                {expect}
        """))
    (repo / ".scratch/feat/issues" / f"{nn}-{fn}.md").write_text(textwrap.dedent(f"""\
        # {nn}: {fn}

        Lane: full
        Branch: feat
        Status: todo
        Round: 0
        Blocked by: {blocked}
        Terms: none
        Seam: `{mod}.py`
        Test command: {PY} -m unittest -q test_{mod}.py
        Verify: None

        ## What to build

        Create `{mod}.py` with a function `{fn}` that {body}. The test `test_{mod}.py` already exists; make it pass.

        ## Acceptance criteria

        - [ ] `{PY} -m unittest -q test_{mod}.py` passes

        ## Result
        """))


def status(repo, nn):
    f = next((repo / ".scratch/feat/issues").glob(f"{nn}-*.md"))
    return re.search(r"^Status:\s*(.+)$", f.read_text(), re.M).group(1).strip()


def stamp(s):
    return time.mktime(time.strptime(s, "%Y%m%d-%H%M%S"))


def main():
    repo = ROOT / "toy"
    repo.mkdir()
    sh(repo, "init", "-q", "-b", "main")
    sh(repo, "config", "user.email", "t@example.com"); sh(repo, "config", "user.name", "T")
    (repo / ".gitignore").write_text(".scratch/\n__pycache__/\n")
    (repo / "package.json").write_text('{"name": "toy"}\n')
    (repo / ".team.json").write_text(json.dumps({"max_workers": 2, "setup": [{"cmd": "true"}],
                                                 "models": {"dev": "sonnet"}}))
    (repo / ".scratch/feat/issues").mkdir(parents=True)
    sh(repo / ".scratch", "init", "-q", "-b", "main")
    sh(repo / ".scratch", "config", "user.email", "t@example.com"); sh(repo / ".scratch", "config", "user.name", "T")
    (repo / ".scratch/.gitignore").write_text("runs/\n")
    write_ticket(repo, "01", "calc", "double", "returns its number argument times two", "self.assertEqual(double(21), 42)")
    write_ticket(repo, "02", "text", "shout", "returns its string argument upper-cased with one `!` added",
                 "self.assertEqual(shout('hi'), 'HI!')")
    write_ticket(repo, "03", "quad", "quadruple",
                 "returns its number argument times four by calling `double` from `calc.py` twice (import it, do not copy it)",
                 "self.assertEqual(quadruple(5), 20)", blocked="01")
    sh(repo, "add", "-A"); sh(repo, "commit", "-q", "-m", "init")
    sh(repo, "checkout", "-q", "-b", "feat")
    sh(repo / ".scratch", "add", "-A"); sh(repo / ".scratch", "commit", "-q", "-m", "tickets")
    main_before = sh(repo, "rev-parse", "main")
    t0 = time.time()
    try:
        r = subprocess.run([PY, str(KIT / "kanban.py"), "run", "feat", "--interval", "2"], cwd=repo, capture_output=True,
                           text=True, timeout=600, stdin=subprocess.DEVNULL)
        rc, o = r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired as e:
        rc, o = -1, "TIMEOUT " + str(e.stdout or "") + str(e.stderr or "")
        subprocess.run([PY, str(KIT / "kanban.py"), "stop", "--now"], cwd=repo, capture_output=True)
    took = time.time() - t0
    print(o)
    last = o.strip().splitlines()[-1] if o.strip() else ""
    check(f"run exits 0 with the done line (wall {took:.0f}s)", rc == 0 and last.startswith("KANBAN OK done:"), o)

    runs = pathlib.Path(repo / ".scratch/runs.log")
    lines = [l.split("\t") for l in runs.read_text().splitlines() if "\tdev\t" in l] if runs.is_file() else []
    by = {}
    for f in lines:
        m = re.search(r"ticket=(\d+)-", "\t".join(f))
        if m:
            by[m.group(1)] = (stamp(f[0]), stamp(f[0]) + int(re.search(r"(\d+)s", f[5]).group(1)), f)   # stamp = run start
    check("three dev runs logged, all PASS", len(by) == 3 and all(v[2][3].startswith("PASS") for v in by.values()), lines)
    if len(by) == 3:
        s1, e1, s2, e2 = by["01"][0], by["01"][1], by["02"][0], by["02"][1]
        check("01 and 02 ran at the same time", s1 < e2 and s2 < e1, f"01 {s1}-{e1}, 02 {s2}-{e2}")
        check("03 started only after 01 finished and merged", by["03"][0] >= e1 and
              o.find("merged 01") != -1 and o.find("merged 01") < o.find("started 03"), f"03 start {by['03'][0]}, 01 end {e1}\n{o}")
    else:
        check("01 and 02 ran at the same time", False, "runs missing")
        check("03 started only after 01 finished", False, "runs missing")
    check("all three tickets are review", all(status(repo, n) == "review" for n in ("01", "02", "03")),
          [status(repo, n) for n in ("01", "02", "03")])
    log = sh(repo, "log", "--format=%s", "feat")
    check("feature branch has the three ticket commits", all(re.search(rf"\b{n}-\w+\.md|{n}", log) for n in ("01", "02", "03"))
          and len(log.splitlines()) >= 4, log)
    for mod in ("calc", "text", "quad"):
        t = subprocess.run([PY, "-m", "unittest", "-q", f"test_{mod}.py"], cwd=repo, capture_output=True, text=True)
        check(f"test_{mod} passes on the feature branch", t.returncode == 0, t.stderr)
    check("dispatcher.lock removed", not (repo / ".scratch/runs/dispatcher.lock").exists())
    wt = sh(repo, "worktree", "list", "--porcelain")
    # the pool slot worktrees (toy.w1, toy.w2) stay by design: detached, unlocked, clean, and no worker lock is left
    slots = [w for w in wt.split("\n\n") if "branch refs/heads/feat--" in w or "locked" in w]
    check("no feat--NN branches, no locked worktrees, no slot or worker locks, slot worktrees clean",
          not sh(repo, "branch", "--list", "feat--*") and not slots
          and not list((repo / ".scratch/runs").glob("slot-*.lock")) and not list((repo / ".scratch/runs").glob("worker*.lock"))
          and all(not sh(repo.parent / f"toy.w{n}", "status", "--porcelain") for n in (1, 2)), wt)
    b = subprocess.run([PY, str(KIT / "kanban.py"), "board", "--json"], cwd=repo, capture_output=True, text=True)
    try:
        j = json.loads(b.stdout)
        cols = j["columns"]
        n_rev, n_run = len(cols["review"]), len(cols["in-progress"]) + len([w for w in j["dispatcher"]["workers"] if w["alive"]])
        txt = json.dumps({k: len(v) for k, v in cols.items()})
    except (ValueError, KeyError):
        n_rev, n_run, txt = None, None, b.stdout + b.stderr
    check("board --json: 3 in review, none running", n_rev == 3 and not n_run, txt)
    check("guards ran (fmt=1, no GUARD failure) and package.json untouched",
          len(lines) == 3 and all("fmt=1" in f for f in lines) and "GUARD" not in runs.read_text()
          and (repo / "package.json").read_text() == '{"name": "toy"}\n'
          and sh(repo, "diff", "--name-only", "main", "feat", "--", "package.json", ".team.json") == "", lines)
    check("no push, main unmoved", not sh(repo, "remote") and sh(repo, "rev-parse", "main") == main_before)


if __name__ == "__main__":
    try:
        main()
    finally:
        print(f"kept {ROOT}") if "--keep" in sys.argv else shutil.rmtree(ROOT, ignore_errors=True)
    print(f"{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)
