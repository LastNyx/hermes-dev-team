#!/usr/bin/env python3
"""The EXISTING-PROJECTS.md steps, run as written: a project that used the kit before 0.11.0 (a .scratch/ with
tickets, runs/ and an old runs.log, no git repo) on machine A, and a stale .scratch/ copy on machine B. No model calls.

    python tests/existing_project.py [--keep]

Last line: `<n>/<total> checks passed`; exit 0 only when all pass. See docs/testing.md.
"""
import os, pathlib, shutil, subprocess, sys, tempfile

KIT = (pathlib.Path(__file__).resolve().parent.parent / "kit").as_posix()
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-existing-"))
ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "PYTHONDONTWRITEBYTECODE": "1"}
results = []


def sh(cwd, *a):
    r = subprocess.run(list(a), cwd=cwd, capture_output=True, text=True, env=ENV)
    if r.returncode:
        raise SystemExit(f"{a}: {r.stderr}")
    return r.stdout.strip()


def kit(cwd, *a):
    r = subprocess.run([sys.executable, f"{KIT}/scratch.py", *a], cwd=cwd, capture_output=True, text=True, env=ENV)
    return r.returncode, (r.stdout + r.stderr).strip()


def check(n, c, d=""):
    results.append(bool(c))
    print(("PASS  " if c else "FAIL  ") + n + ("" if c else "\n" + d), flush=True)


def old_scratch(repo, status):
    """What a pre-0.11.0 project has: tickets, batches.md, runs/ files, a runs.log line without ticket=."""
    s = repo / ".scratch"
    (s / "f1/issues").mkdir(parents=True)
    (s / "f1/issues/01-a.md").write_text("# 01\n\nBranch: feat\nStatus: done\n")
    (s / "f1/issues/02-b.md").write_text(f"# 02\n\nBranch: feat\nStatus: {status}\n")
    (s / "f1/batches.md").write_text("1: 01,02 base:\n")
    (s / "runs").mkdir()
    (s / "runs/20261001-100000-dev.json").write_text("{}")
    (s / "runs.log").write_text("20261001-100000\tdev\tsonnet\tPASS x\tturns=5\t9s\tout=1\tlist$=0.1\tdenials=0\t"
                                ".scratch/runs/20261001-100000-dev.json\tfeature=f1\n")
    with open(repo / ".git/info/exclude", "a") as f:     # orchestrate step 1 before 0.11.0
        f.write(".scratch/\n")


def run():
    for n in ("product", "scratch"):
        sh(ROOT, "git", "init", "-q", "--bare", "-b", "main", f"{n}.git")
    A = ROOT / "A/app"; A.parent.mkdir()
    sh(A.parent, "git", "clone", "-q", (ROOT / "product.git").as_posix(), "app")
    (A / "a.txt").write_text("a"); (A / ".gitignore").write_text("__pycache__/\n")
    sh(A, "git", "add", "-A"); sh(A, "git", "commit", "-qm", "init"); sh(A, "git", "push", "-q", "origin", "main")
    sh(A, "git", "checkout", "-qb", "feat")
    (A / "b.txt").write_text("b"); sh(A, "git", "add", "-A"); sh(A, "git", "commit", "-qm", "b (02-b.md)")
    sh(A, "git", "push", "-q", "-u", "origin", "feat")
    old_scratch(A, "in-progress")

    # guide step 2: the remote is reachable without a prompt
    r = subprocess.run(["git", "ls-remote", (ROOT / "scratch.git").as_posix()], capture_output=True, env=ENV)
    check("step 2: ls-remote on the new remote works (empty is fine)", r.returncode == 0)
    # guide step 3: init on the machine that has the newest scratch
    rc, out = kit(A, "init", "--remote", (ROOT / "scratch.git").as_posix())
    check("step 3: init on an existing scratch commits it", rc == 0 and "committed existing scratch files" in out, out)
    tracked = sh(A / ".scratch", "git", "ls-files").split()
    check("  tickets, batches.md and the old runs.log are tracked; runs/ is not",
          {"f1/issues/01-a.md", "f1/batches.md", "runs.log", ".gitignore"} <= set(tracked)
          and not any(t.startswith("runs/") for t in tracked), str(tracked))
    excl = (A / ".git/info/exclude").read_text()
    check("  exclude not duplicated (old `.scratch/` line already ignores it)", excl.count("scratch") == 1, excl)
    check("  product tree still clean", sh(A, "git", "status", "--short") == "")
    rc, out = kit(A, "resume")
    check("step 4: resume works with an old runs.log (no ticket= field)",
          rc == 0 and "02-b.md in-progress | last run: no run logged | VERIFY" in out, out)
    rc, out = kit(A, "switch")
    check("step 5: switch pushes scratch + feat", rc == 0 and "scratch:" in out and "app:feat" in out, out)

    # machine B already has an old, stale .scratch copy (it ran workers before 0.11.0)
    B = ROOT / "B/app"; B.parent.mkdir()
    sh(B.parent, "git", "clone", "-q", (ROOT / "product.git").as_posix(), "app")
    old_scratch(B, "todo")
    rc, out = kit(B, "init", "--clone", (ROOT / "scratch.git").as_posix())
    check("other machine: --clone refuses over an existing .scratch (nothing lost)",
          rc == 1 and "already has files" in out and "Status: todo" in (B / ".scratch/f1/issues/02-b.md").read_text(), out)
    # guide: move it aside, clone, compare
    (B / ".scratch").rename(B / ".scratch.old")
    rc, out = kit(B, "init", "--clone", (ROOT / "scratch.git").as_posix())
    check("  after moving it aside to .scratch.old, --clone works",
          rc == 0 and "Status: in-progress" in (B / ".scratch/f1/issues/02-b.md").read_text(), out)
    st = sh(B, "git", "status", "--short")
    check("  .scratch.old shows as untracked in the product repo (the guide says to exclude or delete it)",
          ".scratch.old" in st, st)
    with open(B / ".git/info/exclude", "a") as f:
        f.write("/.scratch.old/\n")
    rc, out = kit(B, "resume")
    check("  resume on B checks out feat and reports VERIFY", rc == 0 and "VERIFY" in out
          and sh(B, "git", "branch", "--show-current") == "feat", out)

    # the mistake the guide warns about: plain init (not --clone) on a second machine with its own old scratch
    C = ROOT / "C/app"; C.parent.mkdir()
    sh(C.parent, "git", "clone", "-q", (ROOT / "product.git").as_posix(), "app")
    old_scratch(C, "todo")
    kit(C, "init", "--remote", (ROOT / "scratch.git").as_posix())
    rc, out = kit(C, "resume")
    check("wrong way: plain init on a second machine -> resume DIVERGED, nothing merged",
          rc == 1 and "DIVERGED" in out and
          sh(C / ".scratch", "git", "log", "--merges", "--oneline") == "", out)


def main():
    try:
        run()
    finally:
        if "--keep" not in sys.argv:
            shutil.rmtree(ROOT, onerror=lambda f, p, e: (os.chmod(p, 0o700), f(p)))
    print(f"{sum(results)}/{len(results)} checks passed")
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
