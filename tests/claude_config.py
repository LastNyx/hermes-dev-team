#!/usr/bin/env python3
"""2.0.0 config refusal and `kit/bg.py`, no model.

Checks: a `.team.json` with 1.0.0's `options` (or an old hermes key) makes `run-worker.py` refuse with
`ERROR .team.json has old keys`, prints a replacement with `models` mapped from the options (fast -> haiku, standard ->
sonnet, deep -> opus) and no old keys, and starts nothing (no lock, no runs.log line). `bg.py start` refuses any script but
run-worker.py and `scratch.py dispatch`; it starts a run-worker run detached (process mode, and systemd mode when a user
systemd is there), `wait` returns the worker's RESULT line and `BG DONE`, `status` lists the job. A fake `claude` on PATH
stands in for the model.

    python tests/claude_config.py [--keep]

Last line: `<n>/<total> checks passed`; exit 0 only when all pass.
"""
import json, os, pathlib, re, shutil, subprocess, sys, tempfile, textwrap

KIT = pathlib.Path(__file__).resolve().parent.parent / "kit"
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-ccfg-"))
BIN = ROOT / "bin"
os.environ.update(GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1", PATH=f"{BIN}{os.pathsep}{os.environ['PATH']}")
os.environ.pop("ANTHROPIC_API_KEY", None)
results = []

FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, pathlib, subprocess, sys
a = sys.argv[1:]
if a[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True})); sys.exit(0)
import re
t = re.search(r"ticket:\s*(\S+)", a[a.index("-p") + 1])
tp = pathlib.Path(t.group(1)) if t else None
m = re.search(r"^Out:\s*(\S+)", tp.read_text(), re.M) if tp and tp.is_file() else None
out = m.group(1) if m else "out.txt"
pathlib.Path(out).write_text("done\n")
subprocess.run(["git", "add", out], check=True)
subprocess.run(["git", "commit", "-q", "-m", f"build it ({tp.name if tp else '01-a.md'})"], check=True)
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "1", "name": "Bash",
                  "input": {"command": "true"}}]}}), flush=True)
print(json.dumps({"type": "result", "subtype": "success", "result": "ok\nRESULT: PASS built", "num_turns": 2,
                  "usage": {"input_tokens": 1, "output_tokens": 1}, "total_cost_usd": 0, "permission_denials": []}))
'''


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name)
    if not ok and detail:
        print("      " + str(detail)[-2000:].replace("\n", "\n      "))


def sh(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"FAILED git {args}: {r.stdout}{r.stderr}")
    return r.stdout.strip()


def kit(repo, script, *args, env=None):
    r = subprocess.run([sys.executable, str(KIT / script), *args], cwd=repo, capture_output=True, text=True,
                       timeout=300, env=env)
    return r.returncode, r.stdout + r.stderr


def project(name, cfg):
    repo = ROOT / name
    repo.mkdir()
    sh(repo, "init", "-q", "-b", "main")
    sh(repo, "config", "user.email", "t@example.com"); sh(repo, "config", "user.name", "T")
    (repo / ".gitignore").write_text(".scratch/\n")
    (repo / ".team.json").write_text(json.dumps(cfg))
    sh(repo, "add", "-A"); sh(repo, "commit", "-q", "-m", "init")
    sh(repo, "checkout", "-q", "-b", "feat")
    (repo / ".scratch/feat/issues").mkdir(parents=True)
    (repo / ".scratch/feat/issues/01-a.md").write_text(textwrap.dedent("""\
        # 01: a

        Branch: feat
        Status: in-progress
        Blocked by: None
        Test command: true

        ## Result
        """))
    return repo


def refusal():
    for name, cfg, want in (("opts", {"test": "true", "options": {"dev": "standard", "docs": "fast", "reviewer": "deep"}},
                             {"dev": "sonnet", "docs": "haiku", "reviewer": "opus"}),
                            ("herm", {"test": "true", "backend": "hermes", "hermes_profile": "w"}, {"dev": "sonnet"})):
        repo = project(name, cfg)
        rc, o = kit(repo, "run-worker.py", "dev", "ticket: .scratch/feat/issues/01-a.md")
        check(f"{name}: refused with ERROR .team.json has old keys, exit 2",
              rc == 2 and "RESULT dev ERROR .team.json has old keys" in o.splitlines()[-1], o)
        block = o[o.index("replacement"):o.index("RESULT dev ERROR")] if "replacement" in o else ""
        try:
            fix = json.loads("\n".join(l[4:] for l in block.splitlines()[1:]))
        except ValueError:
            fix = {}
        check(f"{name}: replacement has models {want} and no old key",
              all(fix.get("models", {}).get(r) == m for r, m in want.items())
              and not set(fix) & {"options", "backend", "hermes_profile"} and fix.get("test") == "true", block)
        runs = repo / ".scratch/runs"
        check(f"{name}: nothing started (no lock, no runs.log, no commit)",
              not (runs / "worker.lock").exists() and not (repo / ".scratch/runs.log").exists()
              and sh(repo, "rev-list", "--count", "main..HEAD") == "0", o)


def systemd_ok():
    if sys.platform != "linux" or not shutil.which("systemd-run"):
        return False
    r = subprocess.run(["systemctl", "--user", "is-system-running"], capture_output=True, text=True)
    return r.stdout.strip() in ("running", "degraded")


def bg():
    repo = project("bg", {"test": "true", "models": {"dev": "sonnet"}})
    rc, o = kit(repo, "bg.py", "start", "suites.py", "--feature", "feat")
    check("bg: refuses a script that is not run-worker.py / scratch.py dispatch", rc == 1 and "BG STOP" in o, o)
    rc, o = kit(repo, "bg.py", "start", "scratch.py", "resume")
    check("bg: refuses scratch.py other than dispatch", rc == 1 and "BG STOP" in o, o)
    modes = [("process", {**os.environ, "HDT_BG_NO_SYSTEMD": "1"})]
    if systemd_ok():
        modes.append(("systemd", dict(os.environ)))
    else:
        print("INFO  no user systemd here: systemd mode not run")
    for mode, env in modes:
        if mode == "systemd":                                  # second run: reset the branch to before the first
            sh(repo, "reset", "-q", "--hard", "main")
        rc, o = kit(repo, "bg.py", "start", "run-worker.py", "dev", "ticket: .scratch/feat/issues/01-a.md", env=env)
        m = re.search(r"BG OK (\S+) started \((\w+)", o)
        check(f"bg {mode}: BG OK started in {mode} mode", rc == 0 and m and m.group(2) == mode, o)
        if not m:
            continue
        rc, o = kit(repo, "bg.py", "wait", m.group(1), "--timeout", "120", env=env)
        lines = o.strip().splitlines()
        check(f"bg {mode}: wait prints the RESULT dev PASS line, then BG DONE",
              any(l.startswith("RESULT dev PASS") for l in lines) and lines[-1].startswith(f"BG DONE {m.group(1)}"), o)
        check(f"bg {mode}: exit code read from the rc file", lines[-1].endswith("exit=0"), o)
        check(f"bg {mode}: the worker's commit is on the branch, lock gone",
              sh(repo, "log", "-1", "--format=%s").endswith("(01-a.md)")
              and not (repo / ".scratch/runs/worker.lock").exists(), sh(repo, "log", "-1", "--format=%s"))
    rc, o = kit(repo, "bg.py", "status")
    check("bg: status lists the job(s) as done", rc == 0 and " done " in o and o.strip().endswith("job(s)"), o)

    # a parallel group through bg.py: two tickets, separate Seams, fake workers write their own file
    repo = project("bgpar", {"test": "true", "models": {"dev": "sonnet"}, "max_workers": 2, "setup": [{"cmd": "true"}]})
    for nn, f in (("02", "b.txt"), ("03", "c.txt")):
        (repo / f".scratch/feat/issues/{nn}-t.md").write_text(textwrap.dedent(f"""\
            # {nn}: t

            Branch: feat
            Status: todo
            Blocked by: None
            Seam: `{f}`
            Test command: true
            Out: {f}

            ## Result
            """))
    env = {**os.environ, "HDT_BG_NO_SYSTEMD": "1"}
    rc, o = kit(repo, "bg.py", "start", "scratch.py", "dispatch", "feat", "02", "03", env=env)
    m = re.search(r"BG OK (\S+) started", o)
    check("bg dispatch: started", rc == 0 and m, o)
    if m:
        rc, o = kit(repo, "bg.py", "wait", m.group(1), "--timeout", "200", env=env)
        check("bg dispatch: wait shows DISPATCH OK 2 merged, both files on feat",
              "DISPATCH OK 2 merged" in o and (repo / "b.txt").is_file() and (repo / "c.txt").is_file(), o)


def main():
    BIN.mkdir()
    (BIN / "claude").write_text(FAKE_CLAUDE); (BIN / "claude").chmod(0o755)
    refusal()
    bg()


if __name__ == "__main__":
    try:
        main()
    finally:
        if "--keep" in sys.argv:
            print(f"kept {ROOT}")
        else:
            shutil.rmtree(ROOT, ignore_errors=True)
    print(f"{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if results and all(results) else 1)
