#!/usr/bin/env python3
"""Read-only git for workers (kit/git-ro.py) and the launcher's `guard:git-hooks-or-config-changed`.

No model: git-ro.py allows log/diff/show and refuses `--output` (and abbreviations), `--ext-diff`, `--no-index`,
`--textconv`, a diff path outside the repo, other subcommands; on a repo whose own config runs a program (gpg.program,
diff.external, core.fsmonitor, a textconv driver) neither git-ro.py nor the launcher's history.txt runs it (0.13.3), and
a subfolder source gets no history; every role's settings lose their raw `git log/diff/show` grants; the hook/config
fingerprint catches a new hook, a mode change (POSIX only), a config edit, and nothing on an untouched repo; the
launcher's `main()` with a scripted worker FAILs a run that writes a hook and passes one that does not; the researcher
runs without Bash and gets history.txt, written only once the worker lock is held.

`--real` adds 4 real `claude -p` calls (Sonnet, about $0.55): the old rule `Bash(git log:*)` lets `git log --output`
write a file (the 0.13.1 finding), the new settings deny it and the wrapper refuses it, and the wrapper's plain
`log -1 --format=%h` works.

    python tests/git_guard.py [--keep] [--real]

Last line: `<n>/<total> checks passed`; exit 0 only when all pass. See docs/testing.md.
"""
import contextlib, copy, importlib.util, io, json, os, pathlib, shutil, stat, subprocess, sys, tempfile

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


def ro(cwd, *args):
    r = subprocess.run([sys.executable, str(KIT / "git-ro.py"), *args], cwd=cwd, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name)
    if not ok and detail:
        print("      " + str(detail).replace("\n", "\n      ")[-1500:])


def make_repo(name):
    repo = ROOT / name
    sh(ROOT, "init", "-q", "-b", "main", name)
    sh(repo, "config", "user.email", "t@x"); sh(repo, "config", "user.name", "t")
    (repo / ".team.json").write_text('{"test": "git status"}'); (repo / ".gitignore").write_text(".scratch/\n")
    (repo / "a.txt").write_text("one\n")
    sh(repo, "add", "-A"); sh(repo, "commit", "-q", "-m", "init")
    sh(repo, "checkout", "-q", "-b", "feat")
    return repo


def wrapper_checks(repo):
    rc, out = ro(repo, "log", "-1", "--format=%s")
    check("git-ro: log -1 --format=%s works", rc == 0 and out == "init", out)
    (repo / "a.txt").write_text("two\n")
    rc, out = ro(repo, "diff", "--stat")
    check("git-ro: diff --stat works", rc == 0 and "a.txt" in out, out)
    sh(repo, "checkout", "-q", "--", "a.txt")
    rc, out = ro(repo, "show", "-s", "--format=%s", "HEAD")
    check("git-ro: show -s works", rc == 0 and out == "init", out)
    target = ROOT / "written-by-git.txt"
    for args in (["log", "-1", f"--output={target}"], ["log", "-1", "--output", str(target)],
                 ["log", "-1", f"--outp={target}"], ["diff", f"--output={target}"],
                 ["show", "-s", f"--output={target}", "HEAD"]):
        rc, out = ro(repo, *args)
        check(f"git-ro refuses {' '.join(args[:3]).replace(str(target), '<path>')}",
              rc == 2 and "GIT-RO STOP" in out and not target.exists(), out)
    for args, want in ((["diff", "--ext-diff"], "refused"), (["diff", "--no-index", "a.txt", "b.txt"], "refused"),
                       (["diff", str(ROOT / "outside.txt")], "outside the repo"), (["status"], "only log")):
        if args[-1].endswith("outside.txt"):
            (ROOT / "outside.txt").write_text("x\n")
        rc, out = ro(repo, *args)
        check(f"git-ro refuses {' '.join(a if 'outside' not in a else '<outside path>' for a in args)}",
              rc == 2 and want in out, out)


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
    for args in (["log", "-1"], ["log", "-1", "--format=%G?"], ["diff"], ["diff", "--stat"], ["show", "-s"]):
        mark.unlink(missing_ok=True)
        rc, out = ro(repo, *args)
        check(f"git-ro {' '.join(args)} on a hostile repo: nothing of the repo's runs", not mark.exists(), out[-300:])
    rc, out = ro(repo, "diff", "--textc")
    check("git-ro refuses --textc (an abbreviation of --textconv)", rc == 2 and "refused" in out, out)
    rc, out = ro(repo, "diff", "--text", "--stat")
    check("git-ro allows --text (a different option)", rc == 0 and "a.txt" in out, out)
    mark.unlink(missing_ok=True)
    topic = ROOT / "topic-hostile"; topic.mkdir()
    hist = rw.research_history(repo, topic)
    check("history.txt of a hostile source: written, and nothing of the repo's runs",
          not mark.exists() and "signed" in hist.read_text(), hist.read_text()[:300])
    sub = repo / "pkg"; sub.mkdir()
    hist = rw.research_history(sub, topic)
    check("history.txt of a subfolder: no history (not the enclosing repo's under the subfolder's name)",
          "no history" in hist.read_text() and "signed" not in hist.read_text(), hist.read_text()[:300])


def settings_checks(rw):
    raw = []
    for role in ("dev", "reviewer", "docs", "researcher"):
        s = json.loads((KIT / "roles" / f"{role}.json").read_text(encoding="utf-8"))
        subs = rw.read_only_git(s)
        left = [r for r in s["permissions"]["allow"] if rw.RAW_GIT_READ.match(r)]
        wrapped = [r for r in s["permissions"]["allow"] if "git-ro.py" in r]
        raw.append((role, subs, left, wrapped))
    check("every role: no raw `Bash(git log|diff|show...)` grant left after read_only_git",
          all(not left for _, _, left, _ in raw), raw)
    check("dev, reviewer, docs get the wrapper for the subcommands they had (researcher none)",
          [(r, s) for r, s, _, w in raw if len(w) == len(s)] == [(r, s) for r, s, _, _ in raw]
          and dict((r, s) for r, s, _, _ in raw) == {"dev": ["diff", "log"], "reviewer": ["diff", "log", "show"],
                                                     "docs": ["diff", "log"], "researcher": []}, raw)


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


def launcher_checks(rw, repo, hooks):
    os.chdir(repo)
    seen = {}

    def launch(role, prompt, action):
        def fake_worker(cmd, progress_path, env=None, lock=None):
            seen["cmd"] = cmd
            pathlib.Path(progress_path).write_text("")
            action()
            return {"type": "result", "subtype": "success", "result": "RESULT: PASS done", "num_turns": 1,
                    "usage": {}, "_exit": 0}
        rw.stream_worker = fake_worker
        argv = sys.argv
        sys.argv = ["run-worker.py", role, prompt, "--backend", "claude"]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = rw.main()
        finally:
            sys.argv = argv
        lines = buf.getvalue().strip().splitlines()
        return rc, next((l for l in reversed(lines) if l.startswith("RESULT")), buf.getvalue()[-300:])

    if not shutil.which("claude"):
        print("SKIP launcher checks: claude CLI not on PATH (main() refuses before the worker starts)")
        return
    rc, line = launch("dev", "do the ticket", lambda: None)
    s = json.loads(seen["cmd"][seen["cmd"].index("--settings") + 1])
    allow = s["permissions"]["allow"]
    check("launcher: dev PASS, settings carry git-ro and no raw git log/diff",
          rc == 0 and "PASS" in line and any("git-ro.py log:*" in r for r in allow)
          and not any(rw.RAW_GIT_READ.match(r) for r in allow), (line, allow))
    check("launcher: the dev prompt names the wrapper command", "git-ro.py" in seen["cmd"][2], seen["cmd"][2][-400:])
    rc, line = launch("dev", "do the ticket", lambda: (hooks / "post-commit").write_text("#!/bin/sh\n"))
    check("launcher: a hook written mid-run -> RESULT dev FAIL guard:git-hooks-or-config-changed, exit 1",
          rc == 1 and "FAIL" in line and "git-hooks-or-config-changed:hooks/post-commit" in line, line)
    (hooks / "post-commit").unlink()

    src = make_repo("source")
    os.chdir(repo)
    topic = repo / ".scratch" / "research" / "t1"
    topic.mkdir(parents=True)
    (topic / "questions.md").write_text("# Research: t1\n\n1. What is in a.txt?\n")
    rc, line = launch("researcher", f"questions: {topic / 'questions.md'} out: {topic / 'findings.md'} source: {src}",
                      lambda: None)
    tools = seen["cmd"][seen["cmd"].index("--tools") + 1]
    s = json.loads(seen["cmd"][seen["cmd"].index("--settings") + 1])
    hist = topic / "history.txt"
    check("launcher: researcher runs without the Bash tool and with no Bash grant",
          "Bash" not in tools.split(",") and not any(r.startswith("Bash") for r in s["permissions"]["allow"]),
          (tools, s["permissions"]["allow"]))
    check("launcher: researcher gets history.txt (Read granted, named in the prompt)",
          hist.is_file() and "init" in hist.read_text() and any("history.txt" in r for r in s["permissions"]["allow"])
          and str(hist.as_posix()) in seen["cmd"][2], (hist.read_text() if hist.is_file() else "missing"))
    hist.write_text("KEEP\n")
    lock = rw.teamcfg.lock_path(repo / ".scratch")
    lock.write_text(json.dumps({"launcher_pid": os.getpid(), "role": "dev", "stamp": "x"}))   # a live run holds it
    rc, line = launch("researcher", f"questions: {topic / 'questions.md'} out: {topic / 'findings.md'} source: {src}",
                      lambda: None)
    lock.unlink()
    check("launcher: researcher refused by a held lock leaves history.txt alone",
          rc == 2 and "already running" in line and hist.read_text() == "KEEP\n", (line, hist.read_text()[:80]))


def claude_run(cwd, settings, command):
    r = subprocess.run(["claude", "-p", f"Run this one Bash command exactly as written, then say what it printed: "
                        f"{command}", "--tools", "Bash", "--settings", json.dumps(settings), "--setting-sources", "",
                        "--model", "sonnet", "--max-turns", "3", "--output-format", "json",
                        "--no-session-persistence"], cwd=cwd, capture_output=True, text=True, timeout=300)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"result": r.stdout[-500:] + r.stderr[-500:]}


def real_checks(rw, repo):
    os.chdir(repo)
    base = {"permissions": {"defaultMode": "dontAsk", "allow": ["Read(./**)", "Bash(git log:*)"],
                            "deny": ["Edit(./.git/**)"]}}
    old_target = repo / ".git" / "notes-old.txt"
    d = claude_run(repo, base, f"git log -1 --format=format:hello --output={old_target.as_posix()}")
    check("real, old rule `Bash(git log:*)`: `git log --output` into .git/ is allowed and writes the file",
          old_target.is_file(), d.get("result", "")[-300:])
    new = copy.deepcopy(base)
    rw.read_only_git(new)
    new_target = repo / ".git" / "notes-new.txt"
    d1 = claude_run(repo, new, f"git log -1 --format=format:hello --output={new_target.as_posix()}")
    d2 = claude_run(repo, new, f"{rw.git_ro_cmd()} log -1 --format=format:hello --output={new_target.as_posix()}")
    check("real, new settings: plain git log denied, wrapper refuses --output, no file written",
          not new_target.exists() and (d1.get("permission_denials") or "denied" in d1.get("result", "").lower())
          and "GIT-RO STOP" in json.dumps(d2), (d1.get("result", "")[-200:], d2.get("result", "")[-200:]))
    head = sh(repo, "log", "-1", "--format=%h")
    d3 = claude_run(repo, new, f"{rw.git_ro_cmd()} log -1 --format=%h")
    check("real, new settings: the wrapper's plain log works", head in d3.get("result", "")
          and not d3.get("permission_denials"), d3.get("result", "")[-200:])
    cost = sum(x.get("total_cost_usd") or 0 for x in (d, d1, d2, d3))
    print(f"      real runs: list ${cost:.2f}")


def run():
    rw = load_launcher()
    repo = make_repo("proj")
    wrapper_checks(repo)
    hostile_checks(rw)
    settings_checks(rw)
    hooks = guard_checks(rw, repo)
    launcher_checks(rw, repo, hooks)
    if "--real" in sys.argv:
        real_checks(rw, make_repo("proj-real"))


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
