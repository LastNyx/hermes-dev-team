#!/usr/bin/env python3
"""`scratch.py dispatch` with two REAL dev workers on a throwaway repo. CALLS A MODEL (two short Sonnet runs).

Two independent tickets (two functions in two files, each with its own test); both must PASS, run at the same time
in two pool slots, and be merged into the feature branch, where both tests then pass.

    python tests/parallel_real.py [--keep]

Last line: `<n>/<total> checks passed`. See docs/testing.md.
"""
import json, os, pathlib, re, shutil, subprocess, sys, tempfile, textwrap, time

KIT = pathlib.Path(__file__).resolve().parent.parent / "kit"
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-parallel-real-"))
os.environ.update(GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1")
PY = sys.executable
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name)
    if not ok and detail:
        print("      " + str(detail)[-3000:].replace("\n", "\n      "))


def sh(cwd, *a):
    r = subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"git {a}: {r.stderr}")
    return r.stdout.strip()


def ticket(repo, nn, mod, fn, body, expect):
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
        Blocked by: None
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


def main():
    repo = ROOT / "toy"
    repo.mkdir()
    sh(repo, "init", "-q", "-b", "main")
    sh(repo, "config", "user.email", "t@example.com"); sh(repo, "config", "user.name", "T")
    (repo / ".gitignore").write_text(".scratch/\n__pycache__/\n")
    (repo / ".team.json").write_text(json.dumps({"max_workers": 2, "setup": [{"cmd": "true"}],
                                                 "models": {"dev": "sonnet"}}))
    (repo / ".scratch/feat/issues").mkdir(parents=True)
    ticket(repo, "01", "calc", "double", "returns its number argument times two", "self.assertEqual(double(21), 42)")
    ticket(repo, "02", "text", "shout", "returns its string argument upper-cased with one `!` added",
           "self.assertEqual(shout('hi'), 'HI!')")
    sh(repo, "add", "-A"); sh(repo, "commit", "-q", "-m", "init")
    sh(repo, "checkout", "-q", "-b", "feat")
    t0 = time.time()
    r = subprocess.run([PY, str(KIT / "scratch.py"), "dispatch", "feat", "01", "02"], cwd=repo, capture_output=True,
                       text=True, timeout=900)
    took = time.time() - t0
    o = r.stdout + r.stderr
    print(o)
    check("dispatch OK, both merged", r.returncode == 0 and "DISPATCH OK 2 merged" in o, o)
    secs = [int(x) for x in re.findall(r"RESULT dev PASS .*? (\d+)s ", o)]
    check(f"both ran at once (wall {took:.0f}s, runs {secs})", len(secs) == 2 and took < sum(secs) + 30, o)
    for mod in ("calc", "text"):
        t = subprocess.run([PY, "-m", "unittest", "-q", f"test_{mod}.py"], cwd=repo, capture_output=True, text=True)
        check(f"test_{mod} passes on the merged feature branch", t.returncode == 0, t.stderr)
    check("ticket branches deleted, main untouched", not sh(repo, "branch", "--list", "feat--*")
          and sh(repo, "log", "--format=%s", "main") == "init", sh(repo, "branch"))


if __name__ == "__main__":
    try:
        main()
    finally:
        print(f"kept {ROOT}") if "--keep" in sys.argv else shutil.rmtree(ROOT, ignore_errors=True)
    print(f"{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)
