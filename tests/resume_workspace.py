#!/usr/bin/env python3
"""Resume and switching in the multi-repo workspace layout (two repos, scratch in the workspace). No model calls.

    python tests/resume_workspace.py [--keep]

Last line: `<n>/<total> checks passed`; exit 0 only when all pass. See docs/testing.md.
"""
import json, os, pathlib, shutil, subprocess, sys, tempfile

KIT = (pathlib.Path(__file__).resolve().parent.parent / "kit").as_posix()
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-resume-ws-"))
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


def machine(m):
    w = ROOT / m; w.mkdir()
    (w / ".team.json").write_text(json.dumps({"scratch": ".scratch",
                                              "repos": {"api": {"test": "x"}, "web": {"test": "y"}}}))
    return w


def run():
    for n in ("api", "web", "scratch"):
        sh(ROOT, "git", "init", "-q", "--bare", "-b", "main", f"{n}.git")
    A = machine("A")
    for i, n in ((1, "api"), (2, "web")):
        sh(A, "git", "clone", "-q", (ROOT / f"{n}.git").as_posix(), n)
        (A / n / "f.txt").write_text(n)
        sh(A / n, "git", "add", "-A"); sh(A / n, "git", "commit", "-qm", "init")
        sh(A / n, "git", "push", "-q", "origin", "main")
        sh(A / n, "git", "checkout", "-qb", "feat-x")
        if n == "api":                               # api's ticket is finished, web's is not
            (A / n / "g.txt").write_text("work"); sh(A / n, "git", "add", "-A")
            sh(A / n, "git", "commit", "-qm", f"{n} work (0{i}-{n}.md)")
    rc, out = kit(A, "init", "--remote", (ROOT / "scratch.git").as_posix())
    check("workspace init from the workspace root", rc == 0 and "INIT OK" in out, out)
    iss = A / ".scratch/fx/issues"; iss.mkdir(parents=True)
    for i, n in ((1, "api"), (2, "web")):
        (iss / f"0{i}-{n}.md").write_text(f"# 0{i}\n\nRepo: {n}\nBranch: feat-x\nStatus: in-progress\n")
    rc, out = kit(A / "api", "commit", "tickets")
    check("commit from inside a workspace repo", rc == 0 and "COMMIT OK" in out and "nothing" not in out, out)
    rc, out = kit(A, "switch")
    check("switch pushes scratch + both feature branches",
          rc == 0 and "api:feat-x" in out and "web:feat-x" in out and "scratch:" in out, out)
    B = machine("B")
    for n in ("api", "web"):
        sh(B, "git", "clone", "-q", (ROOT / f"{n}.git").as_posix(), n)
    rc, out = kit(B, "init", "--clone", (ROOT / "scratch.git").as_posix())
    check("B clones the scratch repo into the workspace", rc == 0 and (B / ".scratch/fx/issues/01-api.md").is_file(), out)
    rc, out = kit(B, "resume")
    print("\n".join("      " + l for l in out.splitlines()))
    check("B resume checks out feat-x in both repos",
          sh(B / "api", "git", "branch", "--show-current") == "feat-x"
          and sh(B / "web", "git", "branch", "--show-current") == "feat-x", out)
    check("B resume: api ticket VERIFY (commit found), web ticket REDISPATCH",
          rc == 0 and "01-api.md in-progress" in out and "VERIFY" in out.split("01-api.md")[1].split("\n")[0]
          and "REDISPATCH" in out.split("02-web.md")[1].split("\n")[0], out)


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
