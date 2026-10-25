#!/usr/bin/env python3
"""kit/suites.py and `scratch.py baseline` with several test suites, no model (0.15.0).

A throwaway repo with two Python "suites" (one in a subfolder): every suite runs even when the first fails, failing test
names are read from unittest, pytest, vitest and jest output (fixed samples captured from the real runners), baseline
failures are not counted, a new one is, and `baseline` records the names `suites.py` reads back. The old single-string
`test` still works.

    python tests/suite_runner.py [--keep]

Last line: `<n>/<total> checks passed`; exit 0 only when all pass. See docs/testing.md.
"""
import json, os, pathlib, shutil, subprocess, sys, tempfile

KIT = pathlib.Path(__file__).resolve().parent.parent / "kit"
sys.path.insert(0, str(KIT))
import suites  # noqa: E402

ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-suites-"))
os.environ.update(GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1")
results = []
PY = pathlib.Path(sys.executable).as_posix()

# Samples cut from real runner output (vitest 3, pytest 8, jest 29, unittest on Python 3.14).
SAMPLES = {
    "vitest": (" FAIL  a.test.js > strip > shows close on hover\nAssertionError: expected 1 to be 2\n"
               " FAIL  a.test.js > top level fails\n Test Files  1 failed (1)\n",
               ["a.test.js > strip > shows close on hover", "a.test.js > top level fails"]),
    "pytest": ("F.\nFAILED test_x.py::test_a - assert 0\n1 failed, 1 passed in 0.01s\n", ["test_x.py::test_a"]),
    "jest": ("FAIL ./a.test.js\n  \u25cf Grp \u203a name one\n\n    expect(received).toBe(expected)\n",
             ["./a.test.js", "Grp \u203a name one"]),
    "unittest": ("ERROR: test_b (test_windows.WinTests.test_b)\n------\nFAIL: test_a (pkg.T.test_a)\n",
                 ["pkg.T.test_a", "test_windows.WinTests.test_b"]),
}


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name)
    if not ok and detail:
        print("      " + str(detail)[-1500:])


def sh(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"FAILED git {args}: {r.stdout}{r.stderr}")
    return r.stdout.strip()


def run(cwd, *args):
    r = subprocess.run([sys.executable, *args], cwd=cwd, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def unit_test(folder, name, ok):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"test_{name}.py").write_text(
        f"import unittest\n\nclass T(unittest.TestCase):\n    def test_{name}(self):\n        self.assertTrue({ok})\n")


def main():
    keep = "--keep" in sys.argv
    try:
        for runner, (text, want) in SAMPLES.items():
            got = suites.failing_names(text)
            check(f"names: {runner} output -> {want}", got == want, got)
        check("names: ANSI colour codes are ignored",
              suites.failing_names("\x1b[31m FAIL \x1b[39m a.test.js > x\n") == ["a.test.js > x"])
        check("config: a string is one suite run from the repo root",
              suites.suites({"test": "npm test"}) == [("test", "npm test", ".")])
        check("config: a list needs `cmd` in every suite", isinstance(suites.suites({"test": [{"name": "a"}]}), str))
        check("config: duplicate suite names are refused",
              isinstance(suites.suites({"test": [{"cmd": "a", "name": "x"}, {"cmd": "b", "name": "x"}]}), str))

        repo = ROOT / "proj"
        sh(ROOT, "init", "-q", "-b", "main", "proj")
        sh(repo, "config", "user.email", "t@x"); sh(repo, "config", "user.name", "t")
        unit_test(repo / "tests", "old_broken", False)          # fails on main: the baseline
        unit_test(repo / "ui" / "tests", "ui_ok", True)
        cfg = {"test": [{"name": "py", "cmd": f"{PY} -m unittest discover -s tests"},
                        {"name": "ui", "cmd": f"{PY} -m unittest discover -s tests", "cwd": "ui"}]}
        (repo / ".team.json").write_text(json.dumps(cfg))
        (repo / ".gitignore").write_text(".scratch/\n__pycache__/\n")
        sh(repo, "add", "-A"); sh(repo, "commit", "-q", "-m", "init"); sh(repo, "checkout", "-q", "-b", "feat")
        rc, out = run(repo, str(KIT / "scratch.py"), "init")
        (repo / ".scratch" / "f").mkdir(parents=True)
        rc, out = run(repo, str(KIT / "scratch.py"), "baseline", "f")
        base = (repo / ".scratch" / "f" / "baseline.md")
        text = base.read_text() if base.is_file() else ""
        check("baseline: runs every suite and records each exit code", rc == 0 and "py=1,ui=0" in out, out)
        check("baseline: records the failing test by name for suites.py",
              "- py: test_old_broken.T.test_old_broken" in text, text[-600:])

        rc, out = run(repo, str(KIT / "suites.py"), "--feature", "f")
        last = out.splitlines()[-1]
        check("suites: a baseline-only failure passes, and the second suite still ran",
              rc == 0 and "py FAIL (1 baseline, 0 new)" in last and "ui PASS" in last, out)
        rc, out = run(repo, str(KIT / "suites.py"))
        check("suites: without --feature the same failure is new", rc == 1 and "py FAIL (0 baseline, 1 new)" in out, out)

        unit_test(repo / "ui" / "tests", "ui_new", False)        # a new failure in the subfolder suite
        rc, out = run(repo, str(KIT / "suites.py"), "--feature", "f")
        last = out.splitlines()[-1]
        check("suites: a new failure in the second suite fails and is named",
              rc == 1 and "ui FAIL (0 baseline, 1 new)" in last and "test_ui_new.T.test_ui_new" in out, out)
        (repo / "ui" / "tests" / "test_ui_new.py").unlink()

        (repo / ".team.json").write_text(json.dumps({"test": [{"name": "boom", "cmd": "exit 3"}]}))
        rc, out = run(repo, str(KIT / "suites.py"), "--feature", "f")
        check("suites: a suite that fails with no readable names counts as new",
              rc == 1 and "boom FAIL (exit 3, no readable test names)" in out, out)
        (repo / ".team.json").write_text(json.dumps({"test": f"{PY} -m unittest discover -s ui/tests"}))
        rc, out = run(repo, str(KIT / "suites.py"))
        check("suites: the old single-string `test` still runs", rc == 0 and "test PASS" in out, out)
    finally:
        if keep:
            print(f"kept {ROOT}")
        else:
            shutil.rmtree(ROOT, ignore_errors=True)
    print(f"{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
