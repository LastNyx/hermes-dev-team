#!/usr/bin/env python3
"""`scratch.py check-run begin|end`: the launcher's guards around the planner's own trivial fix, no model (0.16.0).

A throwaway repo with a bare remote. A clean fix passes; a push, an edited package.json, a dirty tree, a commit on main
and a subject ending like a dev's ticket commit each FAIL; `begin` refuses main, a dirty tree and a missing `begin`.

    python tests/check_run.py [--keep]

Last line: `<n>/<total> checks passed`; exit 0 only when all pass. See docs/testing.md.
"""
import os, pathlib, shutil, subprocess, sys, tempfile

KIT = pathlib.Path(__file__).resolve().parent.parent / "kit"
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-checkrun-"))
os.environ.update(GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1")
results = []


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


def cr(repo, phase):
    r = subprocess.run([sys.executable, str(KIT / "scratch.py"), "check-run", phase], cwd=repo, capture_output=True,
                       text=True)
    return r.returncode, (r.stdout + r.stderr).strip().splitlines()[-1]


def commit(repo, name, text, msg):
    (repo / name).write_text(text); sh(repo, "add", name); sh(repo, "commit", "-q", "-m", msg)


def main():
    keep = "--keep" in sys.argv
    try:
        bare = ROOT / "remote.git"
        sh(ROOT, "init", "-q", "--bare", "-b", "main", str(bare))
        repo = ROOT / "proj"
        sh(ROOT, "init", "-q", "-b", "main", "proj")
        sh(repo, "config", "user.email", "t@x"); sh(repo, "config", "user.name", "t")
        (repo / ".team.json").write_text('{"test": "git status"}'); (repo / ".gitignore").write_text(".scratch/\n")
        (repo / "package.json").write_text("{}\n"); (repo / "a.txt").write_text("1\n")
        sh(repo, "add", "-A"); sh(repo, "commit", "-q", "-m", "init")
        sh(repo, "remote", "add", "origin", str(bare)); sh(repo, "push", "-q", "origin", "main")

        rc, line = cr(repo, "begin")
        check("begin refuses main", rc == 1 and "STOP on main" in line, line)
        sh(repo, "checkout", "-q", "-b", "feat"); sh(repo, "push", "-q", "origin", "feat")
        rc, line = cr(repo, "end")
        check("end without begin refuses", rc == 1 and "no `check-run begin`" in line, line)
        (repo / "a.txt").write_text("dirty\n")
        rc, line = cr(repo, "begin")
        check("begin refuses a dirty tree", rc == 1 and "dirty" in line, line)
        sh(repo, "checkout", "-q", "--", "a.txt")

        cases = [
            ("a clean fix and commit passes", lambda: commit(repo, "a.txt", "2\n", "fix: label"), 0, "CHECK-RUN OK 1"),
            ("a push fails pushed", lambda: (commit(repo, "a.txt", "3\n", "fix"), sh(repo, "push", "-q", "origin", "feat")),
             1, "pushed:origin"),
            ("an edited package.json fails test-config-changed",
             lambda: commit(repo, "package.json", '{"a": 1}\n', "fix: deps"), 1, "test-config-changed:package.json"),
            ("a dirty tree fails", lambda: (repo / "a.txt").write_text("x\n"), 1, "tree-dirty"),
            ("a subject ending like a ticket commit fails", lambda: commit(repo, "a.txt", "5\n", "fix (02-x.md)"), 1,
             "subject-ends-like-a-ticket"),
            ("a commit on main fails main-moved",
             lambda: (sh(repo, "checkout", "-q", "main"), commit(repo, "b.txt", "m\n", "on main"),
                      sh(repo, "checkout", "-q", "feat")), 1, "main-moved"),
        ]
        for name, act, want_rc, want in cases:
            sh(repo, "checkout", "-q", "--", "."); subprocess.run(["git", "clean", "-qfd"], cwd=repo)
            rc, line = cr(repo, "begin")
            if rc:
                check(name + " (begin)", False, line); continue
            act()
            rc, line = cr(repo, "end")
            check(name, rc == want_rc and want in line, line)
    finally:
        if keep:
            print(f"kept {ROOT}")
        else:
            shutil.rmtree(ROOT, ignore_errors=True)
    print(f"{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
