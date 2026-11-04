#!/usr/bin/env python3
"""Shared kit helpers, no model.

Checks: `tickets.open_blockers` (done/review/todo/in-progress blockers, lettered ids, fix tickets, `None`, unknown id);
`scratch.py dispatch` accepts a ticket whose blocker is in `review` (merged) and refuses one in `todo`; `scratch.py resume`
removes a stale slot lock; `dispatch` refuses while a worker lock is live.

    python tests/kit_shared.py [--keep]

Last line: `<n>/<total> checks passed`; exit 0 only when all pass.
"""
import hashlib, json, os, pathlib, shutil, subprocess, sys, tempfile, textwrap

KIT = pathlib.Path(__file__).resolve().parent.parent / "kit"
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-kshared-"))
BIN = ROOT / "bin"
os.environ.update(GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1", PATH=f"{BIN}{os.pathsep}{os.environ['PATH']}")
os.environ.pop("ANTHROPIC_API_KEY", None)
sys.path.insert(0, str(KIT))
import tickets as tk  # noqa: E402
results = []

FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, pathlib, re, subprocess, sys
a = sys.argv[1:]
if a[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True})); sys.exit(0)
ticket = pathlib.Path(re.search(r"ticket:\s*(\S+)", a[a.index("-p") + 1]).group(1))
path, word = re.search(r"^Fake:\s*(.+)$", ticket.read_text(), re.M).group(1).split()[1:3]
p = pathlib.Path(path); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(word + "\n")
subprocess.run(["git", "add", path], check=True)
subprocess.run(["git", "commit", "-q", "-m", f"{ticket.stem} built"], check=True)
print(json.dumps({"type": "result", "subtype": "success", "result": "did it\nRESULT: PASS built " + path, "num_turns": 3,
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


def ticket(repo, nn, slug, fake, status="todo", blocked="None", seam=None):
    (repo / ".scratch/feat/issues" / f"{nn}-{slug}.md").write_text(textwrap.dedent(f"""\
        # {nn}: {slug}

        Lane: full
        Branch: feat
        Status: {status}
        Round: 0
        Blocked by: {blocked}
        Seam: {seam or f'`src/{slug}.py`'}
        Test command: true
        Fake: {fake}

        ## Result
        """))


def kit(repo, script, *args):
    r = subprocess.run([sys.executable, str(KIT / script), *args], cwd=repo, capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout + r.stderr


def snapshot(repo):
    """Everything resume could touch: refs, current branch, every file (repo + scratch + runs) by content hash."""
    h = hashlib.sha256()
    for p in sorted(repo.rglob("*")):
        rel = p.relative_to(repo)
        if p.is_file() and (rel.parts[0] != ".git" or rel.parts[-1] == "HEAD" or "refs" in rel.parts):
            h.update(str(rel).encode() + p.read_bytes())
    return sh(repo, "branch", "--show-current"), sh(repo, "for-each-ref"), h.hexdigest()


def project(name):
    repo = ROOT / name
    repo.mkdir()
    sh(repo, "init", "-q", "-b", "main")
    sh(repo, "config", "user.email", "t@example.com"); sh(repo, "config", "user.name", "T")
    (repo / ".gitignore").write_text(".scratch/\n")
    (repo / ".team.json").write_text(json.dumps({"test": "true", "max_workers": 2, "setup": [{"cmd": "true"}]}))
    sh(repo, "add", "-A"); sh(repo, "commit", "-q", "-m", "init")
    sh(repo, "checkout", "-q", "-b", "feat")
    (repo / ".scratch/feat/issues").mkdir(parents=True)
    return repo


def unit():
    f = lambda s: {"Status": s}
    P = pathlib.Path
    by = {"1": [(P("01-a.md"), f("done"))], "2": [(P("02-b.md"), f("review"))], "3": [(P("03-c.md"), f("todo"))],
          "4": [(P("04-d.md"), f("in-progress"))], "2b": [(P("02b-e.md"), f("todo"))],
          "5": [(P("05-x.md"), f("done")), (P("05-fix-x.md"), f("todo"))]}
    w = P("09-w.md")
    ob = lambda v, ds=("done",): tk.open_blockers(v, w, by, ds)
    check("open_blockers: done blocker is closed", ob("01") == [])
    check("open_blockers: review is open for ('done',) and closed for ('done','review')",
          ob("02") == ["02"] and ob("02", ("done", "review")) == [])
    check("open_blockers: todo and in-progress stay open even with review allowed",
          ob("03, 04", ("done", "review")) == ["03", "04"])
    check("open_blockers: lettered id 02b is its own ticket, not 02", ob("02b", ("done", "review")) == ["02b"]
          and ob("02, 02b", ("done", "review")) == ["02b"])
    check("open_blockers: a fix ticket (05-fix-x) not done keeps blocker 05 open; done fix would close it",
          ob("05") == ["05"] and tk.open_blockers("05", w, {"5": [(P("05-x.md"), f("done")), (P("05-fix-x.md"), f("done"))]}, ("done",)) == [])
    check("open_blockers: a fix ticket blocked by its own id waits only for the main ticket",
          tk.open_blockers("05", P("05-fix-x.md"), by, ("done",)) == [])
    check("open_blockers: None / empty / unknown id", ob("None") == [] and ob("none yet") == [] and ob("") == []
          and ob(None) == [] and ob("77") == ["77"])


def main():
    BIN.mkdir()
    (BIN / "claude").write_text(FAKE_CLAUDE); (BIN / "claude").chmod(0o755)
    unit()

    # --- dispatch: a blocker in `review` is merged, so it releases its dependent
    repo = project("app")
    ticket(repo, "01", "a", "write src/a.py A", status="review")
    ticket(repo, "02", "b", "write src/b.py B", blocked="01")
    ticket(repo, "03", "c", "write src/c.py C")
    ticket(repo, "04", "d", "write src/d.py D", blocked="05")
    ticket(repo, "05", "e", "write src/e.py E")
    rc, o = kit(repo, "scratch.py", "dispatch", "feat", "02", "03", "--dry-run")
    check("dispatch: ticket 02 blocked by 01 in review is accepted", rc == 0 and "DISPATCH OK dry run" in o, o)
    rc, o = kit(repo, "scratch.py", "dispatch", "feat", "04", "03", "--dry-run")
    check("dispatch: a blocker in todo still refuses", rc == 1 and "blocked by 05" in o, o)
    rc, o = kit(repo, "scratch.py", "dispatch", "feat", "02", "03")
    check("dispatch: 02 + 03 really run and merge", rc == 0 and "DISPATCH OK 2 merged" in o
          and (repo / "src/b.py").is_file(), o)

    # --- resume: a stale slot lock (launcher and worker gone) is removed; dispatch then runs
    repo = project("stale")
    ticket(repo, "01", "a", "write src/a.py A", status="in-progress")
    ticket(repo, "02", "b", "write src/b.py B")
    ticket(repo, "03", "c", "write src/c.py C")
    runs = repo / ".scratch/runs"; runs.mkdir(parents=True)
    (runs / "slot-1.lock").write_text(json.dumps({"launcher_pid": 2 ** 22 + 1, "worker_pid": 2 ** 22 + 2, "role": "dev",
                                                  "ticket": "01-a.md", "stamp": "20260101-000000"}))
    sh(repo, "branch", "feat--01")
    rc, o = kit(repo, "scratch.py", "resume", "feat")
    check("resume: stale worker lock removed, in-progress reported",
          "stale worker lock" in o and not (runs / "slot-1.lock").exists() and "01-a.md in-progress" in o, o)
    check("resume: no kanban/dispatcher wording left (2.0.0)", "dispatcher" not in o and "kanban" not in o, o)
    rc, o = kit(repo, "scratch.py", "dispatch", "feat", "02", "03", "--dry-run")
    check("dispatch after resume: dry run OK", rc == 0 and "DISPATCH OK dry run" in o, o)
    # a live lock (this test's own pid stands in for a launcher) refuses dispatch
    (runs / "worker.lock").write_text(json.dumps({"launcher_pid": os.getpid(), "role": "dev", "ticket": "01-a.md",
                                                  "stamp": "20260101-000001"}))
    rc, o = kit(repo, "scratch.py", "dispatch", "feat", "02", "03", "--dry-run")
    check("dispatch refuses while a worker lock is live", rc == 1 and "a worker is running" in o, o)
    (runs / "worker.lock").unlink()

if __name__ == "__main__":
    try:
        main()
    finally:
        if "--keep" in sys.argv:
            print(f"kept {ROOT}")
        else:
            shutil.rmtree(ROOT, ignore_errors=True)
    print(f"{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)
