#!/usr/bin/env python3
"""`run-worker.py prepare` / `finish` around a FAKED delegate child (1.0.0), no model.

A real child can only be started from the Hermes chat, so here the test does what the child would (commit, edit, push,
write a skill) between prepare and finish, and pipes in the answer the child would give. HERMES_HOME points at a temp
folder, so the skills guard and the child lookup (`cache/delegation/live/*/manifest.json`) never read the user's own.

Checks: refusals (no `options`, old backend keys, max_workers without setup, main branch, unknown role, --web on
another role, ticket of another repo); the DELEGATE line (goal tag, option, output_schema) and the brief (role prompt,
tool rules, project rules with an @import, absolute repo, test command); one run at a time; finish parses the schema
JSON, a fenced JSON, a raw `RESULT:` line, a FAIL, no answer; guards main-moved, branch-changed, history-rewritten,
test-config-changed, skills-changed, pushed, git hooks; usage limit; the child found by its goal tag gives model,
transcript and status (interrupted child -> FAIL); `resume` finishes an interrupted run (D12) and waits on a running
one; `stop` refuses a running child without --force and finishes it with it; researcher guards.

    python tests/delegate_runs.py [--keep]

Last line: `<n>/<total> checks passed`; exit 0 only when all pass. See docs/testing.md.
"""
import json, os, pathlib, shutil, subprocess, sys, tempfile, time

KIT = pathlib.Path(__file__).resolve().parent.parent / "kit"
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-delegate-")).resolve()
HH = ROOT / "hermes-home"
os.environ.update(GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1", HERMES_HOME=str(HH))
for k in ("HDT_CONFIG", "HDT_SLOT_OF", "HDT_MAIN_CHECKOUT"):
    os.environ.pop(k, None)
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name)
    if not ok and detail:
        print("      " + str(detail)[-2000:].replace("\n", "\n      "))


def sh(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"FAILED git {args} in {cwd}: {r.stdout}{r.stderr}")
    return r.stdout.strip()


def kit(cwd, script, *args, stdin=None):
    r = subprocess.run([sys.executable, str(KIT / script), *args], cwd=cwd, capture_output=True, text=True,
                       input=stdin, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout + r.stderr).strip()


def last(out):
    return out.strip().splitlines()[-1] if out.strip() else ""


CFG = {"test": "true", "options": {"dev": "standard", "reviewer": "standard", "docs": "fast", "researcher": "fast"}}


def make_repo(name, cfg=None, remote=False):
    repo = ROOT / name
    if remote:
        bare = ROOT / f"{name}.git"
        sh(ROOT, "init", "-q", "--bare", "-b", "main", str(bare))
    sh(ROOT, "init", "-q", "-b", "main", name)
    sh(repo, "config", "user.email", "t@x"); sh(repo, "config", "user.name", "t")
    (repo / ".team.json").write_text(json.dumps(cfg if cfg is not None else CFG))
    (repo / ".gitignore").write_text(".scratch/\n")
    (repo / "a.txt").write_text("one\n")
    (repo / "package.json").write_text("{}\n")
    sh(repo, "add", "-A"); sh(repo, "commit", "-q", "-m", "init")
    if remote:
        sh(repo, "remote", "add", "origin", str(ROOT / f"{name}.git")); sh(repo, "push", "-q", "origin", "main")
    sh(repo, "checkout", "-q", "-b", "feat")
    (repo / ".scratch/f/issues").mkdir(parents=True)
    (repo / ".scratch/f/issues/01-x.md").write_text("# 01: x\n\nBranch: feat\nStatus: in-progress\n"
                                                    "Test command: python -c pass\n\n## What to build\n\nx\n")
    return repo


def prepare(repo, *args, role="dev", prompt="ticket: .scratch/f/issues/01-x.md"):
    rc, out = kit(repo, "run-worker.py", "prepare", role, prompt, *args)
    task = next((json.loads(l[9:]) for l in out.splitlines() if l.startswith("DELEGATE ")), None)
    rid = task["goal"].split()[1].rstrip(":") if task else None
    return rc, out, task, rid


def finish(repo, rid, answer):
    return kit(repo, "run-worker.py", "finish", rid, stdin=answer)


def pass_answer(repo, extra=None):
    return json.dumps({"verdict": "PASS", "summary": "built it", "sha": sh(repo, "rev-parse", "--short", "HEAD"),
                       "not_checked": "nothing", **(extra or {})})


def commit(repo, name="b.txt", text="x\n", msg="x (01-x.md)"):
    (repo / name).write_text(text); sh(repo, "add", name); sh(repo, "commit", "-q", "-m", msg)


def run(repo_fn, action, answer=None):
    """prepare -> action (the child's work) -> finish. Returns (rc, finish output)."""
    repo = repo_fn
    rc, out, task, rid = prepare(repo)
    if rc:
        return rc, out
    action(repo)
    return finish(repo, rid, answer if answer is not None else pass_answer(repo))


def fake_child(run_id, status="completed", log_text=None, model="claude-sonnet-x", age=0):
    d = HH / "cache/delegation/live" / f"deleg_{run_id[-12:].replace('-', '')}"
    d.mkdir(parents=True, exist_ok=True)
    log = d / "task-0.log"
    log.write_text(log_text if log_text is not None else
                   "=== transcript ===\n12:00:00 final    | status=completed duration=42.5s summary: done\n"
                   "12:00:01 final    | end status=completed exit_reason=completed\n")
    if age:
        os.utime(log, (time.time() - age, time.time() - age))
    started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - age - 60))
    (d / "manifest.json").write_text(json.dumps({
        "delegation_id": d.name, "started": started, "model": None,
        "tasks": [{"index": 0, "goal": f"[hdt] {run_id}: dev worker", "log": str(log), "status": status,
                   "model": model}]}))
    return log


def refusals():
    for cfg, want, name in (({"test": "true"}, "add an `options` entry for dev", "no options"),
                            ({**CFG, "backend": "claude", "models": {"dev": "sonnet"}}, "remove old key(s) backend, models",
                             "old backend keys"),
                            ({**CFG, "max_workers": 2}, "`max_workers` needs a `setup`", "max_workers without setup")):
        repo = make_repo(f"ref-{name.replace(' ', '-')}", cfg)
        rc, out, task, _ = prepare(repo)
        check(f"prepare refuses: {name} (and prints the replacement JSON)",
              rc == 2 and want in out and '"options"' in out and "ERROR .team.json not set up" in last(out)
              and task is None and not (repo / ".scratch/runs/worker.lock").exists(), out)
    repo = make_repo("ref-main")
    sh(repo, "checkout", "-q", "main")
    rc, out, _, _ = prepare(repo)
    check("prepare refuses on main", rc == 2 and "refusing to run on main" in out, out)
    rc, out, _, _ = prepare(repo, role="tester")
    check("prepare refuses an unknown role", rc == 2 and "unknown role" in out, out)
    sh(repo, "checkout", "-q", "feat")
    rc, out, _, _ = prepare(repo, "--web")
    check("prepare refuses --web on dev", rc == 2 and "researcher only" in out, out)
    t = repo / ".scratch/f/issues/02-y.md"
    t.write_text("# 02\n\nRepo: other\nBranch: feat\nStatus: todo\n")
    rc, out, _, _ = prepare(repo, prompt=f"ticket: {t}")
    check("prepare refuses a ticket for another repo", rc == 2 and "for repo other" in out, out)


def brief_and_lock():
    repo = make_repo("brief")
    (repo / "AGENTS.md").write_text("Rule A: always say hi.\n@docs/extra.md\n")
    (repo / "docs").mkdir(); (repo / "docs/extra.md").write_text("Rule B: imported.\n")
    sh(repo, "add", "-A"); sh(repo, "commit", "-q", "-m", "rules")
    rc, out, task, rid = prepare(repo)
    brief = repo / f".scratch/runs/{rid}.brief.md"
    check("prepare: DELEGATE line with the goal tag, option from .team.json and the output schema",
          rc == 0 and task and task["goal"].startswith(f"[hdt] {rid}:") and task["option"] == "standard"
          and task["output_schema"]["required"] == ["verdict", "summary"] and last(out).startswith(f"PREPARED {rid}"),
          out)
    text = brief.read_text() if brief.is_file() else ""
    check("brief: role prompt, tool rules, project rules with the @import inlined, absolute repo, test command",
          "# dev" in text and "## Tool rules" in text and "Rule A" in text and "Rule B: imported" in text
          and f"Repo root: {repo.as_posix()}" in text and "exactly: python -c pass" in text
          and "skill_manage" in text and "Protected files" in text, text[-1500:])
    rc2, out2, _, _ = prepare(repo)
    check("a second prepare is refused while the first is not finished",
          rc2 == 2 and "prepared and not finished" in out2, out2)
    commit(repo)
    rc, out = finish(repo, rid, pass_answer(repo))
    check("finish: schema JSON PASS -> RESULT dev PASS <sha>, exit 0, Not checked shown, lock gone",
          rc == 0 and last(out).startswith(f"RESULT dev PASS {sh(repo, 'rev-parse', '--short', 'HEAD')}")
          and "Not checked: nothing" in out and not (repo / ".scratch/runs/worker.lock").exists(), out)
    line = (repo / ".scratch/runs.log").read_text().splitlines()[-1].split("\t")
    check("runs.log: fixed positions 1-6, backend=delegate enforce=prompt-only, ticket, fmt",
          line[1] == "dev" and line[3].startswith("PASS") and line[4].startswith("turns=") and line[5].endswith("s")
          and "backend=delegate" in line and "enforce=prompt-only" in line and "ticket=01-x.md" in line
          and "fmt=1" in line, line)
    rc, out = finish(repo, rid, pass_answer(repo))
    check("finish twice is refused", rc == 2 and "already finished" in out, out)
    rc, out, task, rid = prepare(repo, "--option", "deep")
    check("--option overrides .team.json", rc == 0 and task["option"] == "deep", out)
    rc, out = finish(repo, rid, "Here you go:\n```json\n" + pass_answer(repo) + "\n```")
    check("finish: a fenced JSON answer is read", rc == 0 and "RESULT dev PASS" in last(out), out)
    rc, out, task, rid = prepare(repo)
    rc, out = finish(repo, rid, "worked hard\nRESULT: FAIL tests do not run")
    check("finish: a raw `RESULT: FAIL` line -> FAIL with its reason, exit 1",
          rc == 1 and "RESULT dev FAIL tests do not run" in last(out), out)
    rc, out, task, rid = prepare(repo)
    rc, out = finish(repo, rid, "")
    check("finish: no answer -> FAIL no answer", rc == 1 and "FAIL no answer" in last(out), out)
    rc, out, task, rid = prepare(repo)
    rc, out = finish(repo, rid, json.dumps({"verdict": "FAIL", "summary": "Claude AI usage limit reached"}))
    check("finish: a usage limit -> FAIL usage-limit", rc == 1 and "FAIL usage-limit" in last(out), out)


def guards():
    repo = make_repo("guards", remote=True)
    hooks = repo / ".git" / "hooks"
    cases = [
        ("main moved", lambda r: (sh(r, "checkout", "-q", "main"), commit(r, "m.txt", msg="on main"),
                                  sh(r, "checkout", "-q", "feat")), "main-moved"),
        ("branch changed", lambda r: sh(r, "checkout", "-q", "-b", "other"), "branch-changed"),
        ("history rewritten", lambda r: sh(r, "commit", "-q", "--amend", "--allow-empty", "-m", "amended"),
         "history-rewritten"),
        ("protected file edited", lambda r: commit(r, "package.json", '{"scripts": {}}\n'),
         "test-config-changed:package.json"),
        ("git hook written", lambda r: (hooks / "pre-commit").write_text("#!/bin/sh\n"),
         "git-hooks-or-config-changed:hooks/pre-commit"),
        ("pushed", lambda r: (commit(r, "p.txt"), sh(r, "push", "-q", "origin", "feat")), "pushed:origin"),
        ("skill edited", lambda r: (HH / "skills/team/SKILL.md").write_text("changed\n"), "skills-changed:team/SKILL.md"),
    ]
    (HH / "skills/team").mkdir(parents=True, exist_ok=True)
    (HH / "skills/team/SKILL.md").write_text("orig\n")
    (HH / "skills/.curator_ledger.jsonl").write_text("x\n")
    for name, action, want in cases:
        sh(repo, "checkout", "-q", "feat")
        rc, out = run(repo, action)
        check(f"guard: {name} -> FAIL guard:{want.split(':')[0]}",
              rc == 1 and "FAIL guard:" in last(out) and want in last(out) and "| PASS" in last(out), out)
        (hooks / "pre-commit").unlink(missing_ok=True)
        if name == "branch changed":
            sh(repo, "checkout", "-q", "feat")
    rc, out = run(repo, lambda r: ((HH / "skills/.curator_ledger.jsonl").write_text("y\n"), commit(r, "ok.txt")))
    check("guard: a dot file in the skills folder (curator ledger) is ignored; a plain commit passes",
          rc == 0 and "RESULT dev PASS" in last(out), out)


def children():
    repo = make_repo("child")
    rc, out, task, rid = prepare(repo)
    commit(repo)
    log = fake_child(rid)
    rc, out = finish(repo, rid, pass_answer(repo))
    line = (repo / ".scratch/runs.log").read_text().splitlines()[-1].split("\t")
    check("finish finds the child by its goal tag: model, duration and transcript in runs.log",
          rc == 0 and line[2] == "claude-sonnet-x" and line[5] == "42s" and f"transcript={log}" in line, line)
    rc, out, task, rid = prepare(repo)
    fake_child(rid, status="interrupted", log_text="x\n")
    rc, out = finish(repo, rid, pass_answer(repo))
    check("an interrupted child -> FAIL child interrupted even with a PASS answer",
          rc == 1 and "FAIL child interrupted" in last(out), out)
    rc, out, task, rid = prepare(repo)
    fake_child(rid, status="running", log_text="12:00 tool | -> terminal(ls)\n")
    rc, out = kit(repo, "scratch.py", "resume")
    check("resume with a running child: waits, finishes nothing",
          rc == 0 and "worker running" in last(out) and (repo / ".scratch/runs/worker.lock").exists(), out)
    rc, out = kit(repo, "scratch.py", "stop")
    check("stop refuses while the child runs (stop the child first)", rc == 1 and "still running" in out, out)
    rc, out = kit(repo, "scratch.py", "stop", "--force")
    check("stop --force finishes the run as force-stopped, guards checked, lock gone",
          rc == 0 and "FAIL force-stopped" in out and "STOP OK" in last(out)
          and not (repo / ".scratch/runs/worker.lock").exists(), out)
    rc, out, task, rid = prepare(repo)
    fake_child(rid, status="running", log_text="12:00 tool | -> terminal(ls)\n", age=3 * 3600)
    commit(repo, "late.txt")
    rc, out = kit(repo, "scratch.py", "resume")
    check("resume: a child silent for 2 h+ -> INTERRUPTED, finished with guards, ticket left for VERIFY",
          rc == 0 and "INTERRUPTED 01-x.md" in out and "FAIL interrupted" in out and "VERIFY" in out
          and not (repo / ".scratch/runs/worker.lock").exists(), out)
    rc, out, task, rid = prepare(repo)
    fake_child(rid, status="running")                         # the manifest lags; the transcript says it ended
    rc, out = kit(repo, "scratch.py", "resume")
    check("resume: a child whose transcript ended but finish never ran -> INTERRUPTED and finished",
          "INTERRUPTED 01-x.md" in out and "child done" in out, out)


def researcher():
    repo = make_repo("res")
    src = ROOT / "src-lib"; src.mkdir(); (src / "lib.py").write_text("x = 1\n")
    topic = repo / ".scratch/research/t1"; topic.mkdir(parents=True)
    (topic / "questions.md").write_text("1. what is x?\n")
    prompt = f"questions: {topic / 'questions.md'} out: {topic / 'findings.md'} source: {src}"
    sh(repo, "checkout", "-q", "main")
    for name, action, rc_want, want in (
            ("writes only the findings file -> PASS (allowed on main)", lambda: (topic / "findings.md").write_text("f\n"),
             0, "RESULT researcher PASS"),
            ("edits the source -> FAIL guard:source-changed", lambda: (src / "lib.py").write_text("x = 2\n"),
             1, "source-changed:lib.py"),
            ("writes a second topic file -> FAIL guard:topic-folder-changed",
             lambda: (topic / "notes.md").write_text("n\n"), 1, "topic-folder-changed:notes.md"),
            ("a write inside node_modules (skipped) -> PASS",
             lambda: ((src / "node_modules").mkdir(exist_ok=True), (src / "node_modules/p.js").write_text("y\n")),
             0, "RESULT researcher PASS")):
        rc, out, task, rid = prepare(repo, role="researcher", prompt=prompt)
        if rc:
            check(f"researcher {name}", False, out); continue
        action()
        rc, out = finish(repo, rid, json.dumps({"verdict": "PASS", "summary": "1/1 answered"}))
        check(f"researcher {name}", rc == rc_want and want in last(out), out)
        (topic / "notes.md").unlink(missing_ok=True)
    rc, out, task, rid = prepare(repo, "--web", role="researcher", prompt=prompt)
    brief = (repo / f".scratch/runs/{rid}.brief.md").read_text() if rid else ""
    check("researcher --web: option `fast`, `web: on` in the brief", rc == 0 and task["option"] == "fast"
          and "web: on" in brief, out)
    finish(repo, rid, json.dumps({"verdict": "PASS", "summary": "ok"}))


def main():
    cwd = os.getcwd()
    try:
        refusals()
        brief_and_lock()
        guards()
        children()
        researcher()
    finally:
        os.chdir(cwd)
        if "--keep" in sys.argv:
            print(f"kept {ROOT}")
        else:
            shutil.rmtree(ROOT, ignore_errors=True)
    print(f"{sum(results)}/{len(results)} checks passed")
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
