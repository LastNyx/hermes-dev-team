#!/usr/bin/env python3
"""The launcher's `guard:pushed` (kit/run-worker.py guards_after), no model.

One bare remote; clone `me` is the worker's clone, clone `mate` plays a teammate. Each case snapshots `me`, makes one
remote change (from `me` = the worker, or from `mate`), then reads the guard. Everything happens in a temp folder,
deleted at the end (--keep keeps it).

    python tests/push_guard.py [--keep]

Last line: `<n>/<total> checks passed`; exit 0 only when all pass. See docs/testing.md.
"""
import importlib.util, os, pathlib, shutil, subprocess, sys, tempfile

KIT = pathlib.Path(__file__).resolve().parent.parent / "kit"
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-pushguard-"))
os.environ.update(GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1")
results = []


def load_launcher():
    sys.path.insert(0, str(KIT))
    spec = importlib.util.spec_from_file_location("run_worker", KIT / "run-worker.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def sh(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"FAILED git {args} in {cwd}: {r.stdout}{r.stderr}")
    return r.stdout.strip()


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name)
    if not ok and detail:
        print("      " + str(detail))


def run():
    rw = load_launcher()
    bare = (ROOT / "r.git").as_posix()
    sh(ROOT, "init", "-q", "--bare", "-b", "main", bare)
    for who in ("me", "mate"):
        sh(ROOT, "clone", "-q", bare, who)
        sh(ROOT / who, "config", "user.email", f"{who}@x"); sh(ROOT / who, "config", "user.name", who)
    me, mate = ROOT / "me", ROOT / "mate"
    sh(me, "commit", "-q", "--allow-empty", "-m", "init"); sh(me, "push", "-q", "origin", "main")
    sh(me, "checkout", "-q", "-b", "feat"); sh(me, "commit", "-q", "--allow-empty", "-m", "f1")
    sh(me, "push", "-q", "origin", "feat")                     # the user pushed the feature branch (switching / PR)
    sh(me, "push", "-q", "origin", "main:old-me")              # a remote-only branch this clone tracks
    sh(mate, "fetch", "-q"); sh(mate, "checkout", "-q", "main")
    sh(mate, "push", "-q", "origin", "main:mate-a", "main:mate-b")
    sh(me, "fetch", "-q")                                      # me now tracks mate-a and mate-b too
    os.chdir(me)

    def guard(action):
        snap = rw.snapshot([])
        action()
        return [g for g in rw.guards_after(snap, []) if g.startswith("pushed")]

    def mate_commit_push(ref):
        sh(mate, "commit", "-q", "--allow-empty", "-m", "mate work"); sh(mate, "push", "-q", "origin", f"HEAD:{ref}")

    g = guard(lambda: mate_commit_push("main"))
    check("teammate pushes a new commit to main -> no guard", g == [], g)
    g = guard(lambda: sh(mate, "push", "-q", "origin", "--delete", "mate-a"))
    check("teammate deletes their own branch (this clone still tracks it) -> no guard", g == [], g)
    g = guard(lambda: (sh(me, "commit", "-q", "--allow-empty", "-m", "f2"), sh(me, "push", "-q", "origin", "feat")))
    check("worker pushes the feature branch -> guard:pushed", g == ["pushed:origin"], g)
    g = guard(lambda: sh(me, "push", "-q", "origin", "HEAD:new-branch"))
    check("worker pushes a new branch -> guard:pushed", g == ["pushed:origin"], g)
    g = guard(lambda: sh(me, "push", "-q", "origin", "--delete", "old-me"))
    check("worker deletes a remote-only branch (`push origin --delete`) -> guard:pushed", g == ["pushed:origin"], g)
    g = guard(lambda: sh(me, "push", "-q", bare, "--delete", "feat"))
    check("worker deletes the feature branch by URL (tracking ref left) -> guard:pushed", g == ["pushed:origin"], g)
    sh(me, "push", "-q", "origin", "feat")
    g = guard(lambda: sh(me, "push", "-q", bare, "--delete", "mate-b"))
    check("known gap: worker deletes a teammate's branch by URL -> not caught", g == [], g)


def main():
    cwd = os.getcwd()
    try:
        run()
    finally:
        os.chdir(cwd)
        if "--keep" not in sys.argv:
            shutil.rmtree(ROOT, onerror=lambda f, p, e: (os.chmod(p, 0o700), f(p)))
    print(f"{sum(results)}/{len(results)} checks passed")
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
