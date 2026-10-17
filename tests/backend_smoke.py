#!/usr/bin/env python3
"""Smoke test per backend: one trivial dev ticket on a throwaway repo, through the real launcher. CALLS A MODEL.

Run it on demand: after a change to kit/run-worker.py or kit/backends.py, on a new machine, or before relying on a
backend you have not used for a while (the hermes fallback can rot unnoticed while every run uses claude).

    python tests/backend_smoke.py [claude] [hermes] [--keep]     # default: every backend that is ready here

A backend that is not set up here (claude not logged in, no hermes worker profile) is reported SKIP with the reason,
not FAIL. For each one that runs: RESULT dev PASS, exit 0, one commit ending `(01-double.md)`, the test passes,
main unmoved, a runs.log line with `backend=<name>`. About a minute and a few cents of list price per backend.
Last line: `<n>/<total> checks passed`.
"""
import os, pathlib, shutil, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
KIT = HERE.parent / "kit"
sys.path.insert(0, str(KIT))
import backends  # noqa: E402

ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-smoke-")).resolve()
ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "PYTHONDONTWRITEBYTECODE": "1"}
results = []

TICKET = """# 01: double

Lane: quick
Branch: feature/smoke
Status: in-progress
Round: 0
Blocked by: None
Terms: double
Seam: `calc.double`
Test command: {py} -m unittest -q test_calc.py
Verify: None

## What to build

`calc.double(n)` returns n times two. Write `test_calc.py` with one unittest for it first.

## Acceptance criteria

- [ ] `double(4)` is 8

## Result
"""


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "\n      " + str(detail)[-1200:]), flush=True)


def sh(cwd, *a):
    return subprocess.run(list(a), cwd=cwd, capture_output=True, text=True, env=ENV).stdout.strip()


def make_repo(name):
    repo = ROOT / name
    repo.mkdir()
    py = pathlib.Path(sys.executable).as_posix()
    sh(repo, "git", "init", "-q", "-b", "main")
    sh(repo, "git", "config", "user.email", "smoke@example.invalid"); sh(repo, "git", "config", "user.name", "smoke")
    (repo / "calc.py").write_text('"""Tiny calculator."""\n')
    (repo / ".gitignore").write_text(".scratch/\n__pycache__/\n")
    (repo / ".team.json").write_text(f'{{"test": "{py} -m unittest -q test_calc.py", "models": {{"dev": "sonnet"}}, '
                                     f'"effort": "low"}}\n')
    sh(repo, "git", "add", "-A"); sh(repo, "git", "commit", "-q", "-m", "init")
    sh(repo, "git", "checkout", "-q", "-b", "feature/smoke")
    issues = repo / ".scratch" / "smoke" / "issues"
    issues.mkdir(parents=True)
    (issues / "01-double.md").write_text(TICKET.format(py=py))
    return repo


def smoke(be):
    if be is backends.Claude and not be.ready():
        print("SKIP claude: not on PATH or not logged in (`claude auth status`)"); return
    if be is backends.Hermes:
        err = be.setup_error({})
        if err:
            print(f"SKIP hermes: {err}"); return
    repo = make_repo(be.name)
    main_before = sh(repo, "git", "rev-parse", "main")
    r = subprocess.run([sys.executable, "-u", str(KIT / "run-worker.py"), "dev",
                        "ticket: .scratch/smoke/issues/01-double.md", "--backend", be.name, "--max-turns", "25"],
                       cwd=repo, capture_output=True, text=True, env=ENV, encoding="utf-8", errors="replace")
    out = r.stdout + r.stderr
    line = next((l for l in reversed(out.splitlines()) if l.startswith("RESULT")), out[-400:])
    check(f"{be.name}: RESULT dev PASS, exit 0", r.returncode == 0 and line.startswith("RESULT dev PASS"), line)
    subj = sh(repo, "git", "log", "-1", "--format=%s")
    check(f"{be.name}: one commit ending (01-double.md)", subj.endswith("(01-double.md)")
          and sh(repo, "git", "rev-list", "--count", "main..HEAD") == "1", subj)
    t = subprocess.run([sys.executable, "-m", "unittest", "-q", "test_calc.py"], cwd=repo, capture_output=True,
                       text=True, env=ENV)
    check(f"{be.name}: the test passes", t.returncode == 0, t.stderr[-400:])
    check(f"{be.name}: main unmoved, tree clean", sh(repo, "git", "rev-parse", "main") == main_before
          and sh(repo, "git", "status", "--short") == "", sh(repo, "git", "status", "--short"))
    log = (repo / ".scratch" / "runs.log").read_text(encoding="utf-8") if (repo / ".scratch" / "runs.log").is_file() else ""
    check(f"{be.name}: runs.log line with backend={be.name}", f"backend={be.name}" in log, log[-300:])


def main():
    want = [a for a in sys.argv[1:] if not a.startswith("--")] or list(backends.BACKENDS)
    try:
        for name in want:
            if name not in backends.BACKENDS:
                print(f"unknown backend {name} ({', '.join(backends.BACKENDS)})"); return 2
            smoke(backends.BACKENDS[name])
    finally:
        if "--keep" in sys.argv:
            print(f"kept {ROOT}")
        else:
            shutil.rmtree(ROOT, ignore_errors=True)
    print(f"{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
