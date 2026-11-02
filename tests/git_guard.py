#!/usr/bin/env python3
"""The launcher's `guard:git-hooks-or-config-changed` and the researcher's source guard on a hostile repo, no model.

The hook/config fingerprint catches a new hook, a content change, a mode change (POSIX only), a removed hook and a
config edit, and nothing on an ordinary commit. On a repo whose own config runs a program from a plain `git log` or
`git diff` (gpg.program, diff.external, core.fsmonitor, a textconv driver), the researcher's source fingerprint runs no
git, so nothing of the repo's runs (0.13.3). The full prepare/finish path with these guards is in delegate_runs.py.

    python tests/git_guard.py [--keep]

Last line: `<n>/<total> checks passed`; exit 0 only when all pass. See docs/testing.md.
"""
import importlib.util, os, pathlib, shutil, stat, subprocess, sys, tempfile

KIT = pathlib.Path(__file__).resolve().parent.parent / "kit"
# resolved: on GitHub's Windows runner TEMP is an 8.3 short path (RUNNER~1) and the launcher resolves to the long one
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-gitguard-")).resolve()
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
        print("      " + str(detail).replace("\n", "\n      ")[-1500:])


def make_repo(name):
    repo = ROOT / name
    sh(ROOT, "init", "-q", "-b", "main", name)
    sh(repo, "config", "user.email", "t@x"); sh(repo, "config", "user.name", "t")
    (repo / ".team.json").write_text('{"test": "git status", "options": {"dev": "standard"}}'); (repo / ".gitignore").write_text(".scratch/\n")
    (repo / "a.txt").write_text("one\n")
    sh(repo, "add", "-A"); sh(repo, "commit", "-q", "-m", "init")
    sh(repo, "checkout", "-q", "-b", "feat")
    return repo


def hostile_repo(name, mark):
    """A repo whose own config runs `evil.sh` (it writes <mark>) from a plain log or diff: a signed commit +
    log.showSignature + gpg.program, diff.external, core.fsmonitor, a textconv driver via .git/info/attributes."""
    repo = make_repo(name)
    evil = ROOT / f"{name}-evil.sh"
    evil.write_text(f'#!/bin/sh\necho "$@" > "{mark.as_posix()}"\nexit 1\n', newline="\n")
    os.chmod(evil, 0o755)
    tree = sh(repo, "rev-parse", "HEAD^{tree}")
    body = (f"tree {tree}\nparent {sh(repo, 'rev-parse', 'HEAD')}\nauthor t <t@x> 1 +0000\ncommitter t <t@x> 1 +0000\n"
            "gpgsig -----BEGIN PGP SIGNATURE-----\n \n x\n -----END PGP SIGNATURE-----\n\nsigned\n")
    # bytes: text-mode stdin on Windows turns \n into \r\n and git rejects the commit (measured)
    c = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin"], cwd=repo, input=body.encode(),
                       capture_output=True).stdout.decode().strip()
    sh(repo, "update-ref", "HEAD", c)
    for k in ("gpg.program", "diff.external", "core.fsmonitor", "diff.x.textconv"):
        sh(repo, "config", k, evil.as_posix())
    sh(repo, "config", "log.showSignature", "true")
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text("a.txt diff=x\n")
    (repo / "a.txt").write_text("changed\n")
    return repo


def hostile_checks(rw):
    mark = ROOT / "PWNED"
    repo = hostile_repo("hostile", mark)
    raw = subprocess.run(["git", "--no-pager", "log", "-1"], cwd=repo, capture_output=True)
    check("hostile source: plain `git log` runs the repo's program (the 0.13.3 finding, so the probe is live)",
          mark.exists(), raw.stderr.decode(errors="replace")[-300:])
    mark.unlink(missing_ok=True)
    topic = ROOT / "topic-hostile"; topic.mkdir()
    (topic / "questions.md").write_text("1. x\n")
    paths = (topic / "questions.md", topic / "findings.md", repo)
    snap = rw.research_snapshot(paths)
    g = rw.research_guards(snap, paths)
    check("research guard on a hostile source: runs no git (nothing of the repo's runs), no change -> no guard",
          not mark.exists() and g == [], g)


def guard_checks(rw, repo):
    os.chdir(repo)
    hooks = pathlib.Path(sh(repo, "rev-parse", "--git-path", "hooks"))
    if not hooks.is_absolute():
        hooks = repo / hooks

    def guard(action):
        snap = rw.snapshot([])
        action()
        return [g for g in rw.guards_after(snap, []) if g.startswith("git-hooks")]

    check("no change -> no hooks/config guard", guard(lambda: None) == [])
    g = guard(lambda: (hooks / "pre-commit").write_text("#!/bin/sh\necho hi\n"))
    check("new hook file -> guard", g == ["git-hooks-or-config-changed:hooks/pre-commit"], g)
    g = guard(lambda: (hooks / "pre-commit").write_text("#!/bin/sh\necho changed\n"))
    check("hook content changed -> guard", g == ["git-hooks-or-config-changed:hooks/pre-commit"], g)
    if os.name != "nt":
        g = guard(lambda: os.chmod(hooks / "pre-commit", 0o755))
        check("hook made executable -> guard", g == ["git-hooks-or-config-changed:hooks/pre-commit"], g)
    g = guard(lambda: (hooks / "pre-commit").unlink())
    check("hook removed -> guard", g == ["git-hooks-or-config-changed:hooks/pre-commit"], g)
    g = guard(lambda: sh(repo, "config", "core.pager", "less"))
    check(".git/config changed -> guard", g == ["git-hooks-or-config-changed:config"], g)
    g = guard(lambda: (sh(repo, "commit", "-q", "--allow-empty", "-m", "work"), (repo / "a.txt").write_text("x\n")))
    check("ordinary commit and edit -> no hooks/config guard", g == [], g)
    sh(repo, "checkout", "-q", "--", "a.txt")
    return hooks


def run():
    rw = load_launcher()
    repo = make_repo("proj")
    hostile_checks(rw)
    guard_checks(rw, repo)


def main():
    cwd = os.getcwd()
    try:
        run()
    finally:
        os.chdir(cwd)
        if "--keep" not in sys.argv:
            shutil.rmtree(ROOT, onerror=lambda f, p, e: (os.chmod(p, stat.S_IWRITE | 0o700), f(p)))
        else:
            print(f"kept {ROOT}")
    print(f"{sum(results)}/{len(results)} checks passed")
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
