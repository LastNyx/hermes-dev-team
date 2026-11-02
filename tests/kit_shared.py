#!/usr/bin/env python3
"""Shared kit helpers, no model.

Checks: `tickets.open_blockers` (done/review/todo/in-progress blockers, lettered ids, fix tickets, `None`, unknown id);
`scratch.py dispatch` accepts a ticket whose blocker is in `review` (merged) and refuses one in `todo`.

    python tests/kit_shared.py [--keep]

Last line: `<n>/<total> checks passed`; exit 0 only when all pass.
"""
import json, os, pathlib, shutil, subprocess, sys, tempfile, textwrap

KIT = pathlib.Path(__file__).resolve().parent.parent / "kit"
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-kshared-"))
os.environ.update(GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1", HERMES_HOME=str(ROOT / "hermes-home"))
sys.path.insert(0, str(KIT))
import tickets as tk  # noqa: E402
results = []

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


def project(name):
    repo = ROOT / name
    repo.mkdir()
    sh(repo, "init", "-q", "-b", "main")
    sh(repo, "config", "user.email", "t@example.com"); sh(repo, "config", "user.name", "T")
    (repo / ".gitignore").write_text(".scratch/\n")
    (repo / ".team.json").write_text(json.dumps({"test": "true", "options": {"dev": "standard"}, "max_workers": 2,
                                                  "setup": [{"cmd": "true"}]}))
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
    check("dispatch: 02 + 03 really prepared", rc == 0 and "DISPATCH OK 2 runs prepared" in o, o)


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
