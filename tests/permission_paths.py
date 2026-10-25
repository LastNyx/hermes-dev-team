#!/usr/bin/env python3
"""The launcher writes every path rule absolute, so a `cd` in an allowed command cannot move it (0.15.0).

No model by default: `main()` runs for real in a throwaway repo with the worker replaced, and the settings it would hand
to `claude -p` are checked. `--real` adds one `claude -p` run (Haiku) with those settings: an allowed `cd sub && ls`,
then an edit to the scratch folder (must work) and to a package.json at the root and in the subfolder (both must be
denied). Measured before the fix: after the `cd`, the scratch edit was denied and the root package.json edit allowed.

    python tests/permission_paths.py [--real] [--keep]

Last line: `<n>/<total> checks passed`; exit 0 only when all pass. See docs/testing.md.
"""
import contextlib, importlib.util, io, json, os, pathlib, shutil, subprocess, sys, tempfile

KIT = pathlib.Path(__file__).resolve().parent.parent / "kit"
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-permpaths-"))
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


def load_launcher():
    sys.path.insert(0, str(KIT))
    spec = importlib.util.spec_from_file_location("run_worker", KIT / "run-worker.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def make_repo():
    repo = ROOT / "proj"
    sh(ROOT, "init", "-q", "-b", "main", "proj")
    sh(repo, "config", "user.email", "t@x"); sh(repo, "config", "user.name", "t")
    (repo / "sub").mkdir()
    (repo / "sub" / "c.txt").write_text("a\n")
    (repo / "package.json").write_text("{}\n"); (repo / "sub" / "package.json").write_text("{}\n")
    (repo / ".team.json").write_text(json.dumps({"test": [{"name": "root", "cmd": "git status --short"},
                                                          {"name": "sub", "cmd": "git status --short", "cwd": "sub"}]}))
    (repo / ".gitignore").write_text(".scratch/\n")
    sh(repo, "add", "-A"); sh(repo, "commit", "-q", "-m", "init"); sh(repo, "checkout", "-q", "-b", "feat")
    issues = repo / ".scratch" / "f" / "issues"; issues.mkdir(parents=True)
    (issues / "01-x.md").write_text("# 01: x\n\nStatus: in-progress\nTest command: cd sub && ls\n\n## Result\n")
    (repo / ".scratch" / "notes.md").write_text("a\n")
    return repo


def settings_for(rw, repo, role, prompt):
    """Run main() with the worker replaced; return (settings, prompt) the claude command line carried."""
    seen = {}

    def fake(cmd, progress_path, env=None, lock=None):
        seen["settings"] = json.loads(cmd[cmd.index("--settings") + 1])
        seen["prompt"], seen["env"] = cmd[2], env
        pathlib.Path(progress_path).write_text("")
        return {"type": "result", "subtype": "success", "result": "RESULT: PASS ok", "num_turns": 1, "usage": {}}
    rw.stream_worker = fake
    argv, cwd = sys.argv, os.getcwd()
    sys.argv = ["run-worker.py", role, prompt, "--backend", "claude"]
    os.chdir(repo)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            rw.main()
    finally:
        sys.argv = argv; os.chdir(cwd)
    return seen


def main():
    real, keep = "--real" in sys.argv, "--keep" in sys.argv
    try:
        if not shutil.which("claude"):
            print("SKIP: claude CLI not on PATH (main() refuses before the worker starts)"); return 0
        rw = load_launcher()
        repo = make_repo()
        root = rw.perm_path(repo.resolve())
        dev = settings_for(rw, repo, "dev", "ticket: .scratch/f/issues/01-x.md")
        rules = dev["settings"]["permissions"]["allow"] + dev["settings"]["permissions"]["deny"]
        rel = [r for r in rules if "(./" in r or "(**/" in r]
        check("dev: no rule keeps a `./` or `**/` path (they follow the shell's cd)", not rel, rel)
        check("dev: Edit is granted on the repo by absolute path", f"Edit({root}/**)" in rules, rules)
        check("dev: .git and package.json denials are absolute",
              f"Edit({root}/.git/**)" in rules and f"Edit({root}/**/package.json)" in rules, rules)
        check("dev: the ticket's Test command is granted as written", "Bash(cd sub && ls)" in rules, rules)
        check("dev: the claude env keeps the shell in the repo between calls",
              (dev["env"] or {}).get("CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR") == "1")
        rev = settings_for(rw, repo, "reviewer", "feature: .scratch/f round: 1")
        allow = rev["settings"]["permissions"]["allow"]
        suite = [r for r in allow if "suites.py" in r]
        check("reviewer: the full-suite command is kit/suites.py --feature f, not the config's raw commands",
              len(suite) == 1 and suite[0].endswith("suites.py --feature f)") and "suites.py --feature f" in rev["prompt"],
              allow)
        check("reviewer: Edit on the scratch folder is absolute", f"Edit({root}/.scratch/**)" in allow, allow)

        if real:
            s = dev["settings"]
            prompt = (f"Step 1: run this Bash command exactly: cd sub && ls\nStep 2: with the Edit tool, append a line "
                      f"`b` to {repo.as_posix()}/.scratch/notes.md\nStep 3: with the Edit tool, change {{}} to "
                      f'{{"a": 1}} in {repo.as_posix()}/package.json\nStep 4: the same in '
                      f"{repo.as_posix()}/sub/package.json\nReport each step.")
            env = {**os.environ, **{k: v for k, v in (dev["env"] or {}).items() if k.startswith("CLAUDE_")}}
            r = subprocess.run(["claude", "-p", prompt, "--settings", json.dumps(s), "--setting-sources", "",
                                "--tools", "Read,Edit,Write,Glob,Grep,Bash", "--model", "haiku", "--max-turns", "10",
                                "--output-format", "json"], cwd=repo, capture_output=True, text=True, env=env,
                               timeout=300)
            d = json.loads(r.stdout or "{}")
            denied = [(x["tool_name"], (x.get("tool_input") or {}).get("file_path") or "") for x in
                      d.get("permission_denials") or []]
            check("real: after `cd sub`, the scratch edit is allowed",
                  (repo / ".scratch" / "notes.md").read_text().strip().endswith("b"), denied)
            check("real: after `cd sub`, the root package.json edit is denied",
                  (repo / "package.json").read_text().strip() == "{}", denied)
            check("real: after `cd sub`, sub/package.json is denied",
                  (repo / "sub" / "package.json").read_text().strip() == "{}", denied)
    finally:
        if keep:
            print(f"kept {ROOT}")
        else:
            shutil.rmtree(ROOT, ignore_errors=True)
    print(f"{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
