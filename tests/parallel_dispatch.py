#!/usr/bin/env python3
"""`scratch.py dispatch` + `merge`: parallel dev runs in pool worktrees, no model (docs/open/parallel-workers.md;
delegate children since 1.0.0).

A throwaway repo; the test plays each delegate child (reads its ticket's `Fake:` line, writes a file in its slot,
commits with the dev's subject) and finishes its run. Checks: Seam overlap refused, max_workers and setup required,
dispatch prepares one run per slot (DELEGATE lines, slot locks) and refuses a serial run meanwhile, merge refuses
while a run is unfinished, both tickets merge in order, setup runs once per slot, a ticket branch is deleted after its
merge, a merge conflict is reported and its branch kept, a usage limit stops the queue, a child that changes the main
checkout FAILs, and an interrupted group is finished by `resume` and merged by `merge`.

    python tests/parallel_dispatch.py [--keep]

Last line: `<n>/<total> checks passed`; exit 0 only when all pass. See docs/testing.md.
"""
import json, os, pathlib, re, shutil, subprocess, sys, tempfile, textwrap

KIT = pathlib.Path(__file__).resolve().parent.parent / "kit"
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-parallel-")).resolve()
os.environ.update(GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1", HERMES_HOME=str(ROOT / "hermes-home"))
results = []
OPTS = {"dev": "standard", "reviewer": "standard", "docs": "fast", "researcher": "standard"}

def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name)
    if not ok and detail:
        print("      " + str(detail)[-2500:].replace("\n", "\n      "))


def sh(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"FAILED git {args}: {r.stdout}{r.stderr}")
    return r.stdout.strip()


def ticket(repo, nn, slug, seam, fake, blocked="None"):
    (repo / ".scratch/feat/issues" / f"{nn}-{slug}.md").write_text(textwrap.dedent(f"""\
        # {nn}: {slug}

        Lane: full
        Branch: feat
        Status: todo
        Round: 0
        Blocked by: {blocked}
        Seam: {seam}
        Test command: true
        Fake: {fake}

        ## Result
        """))


def scratch(repo, *args, env=None, timeout=120):
    r = subprocess.run([sys.executable, str(KIT / "scratch.py"), *args], cwd=repo, capture_output=True, text=True,
                       env={**os.environ, **(env or {})}, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr)


def setup_repo(name, cfg):
    repo = ROOT / name
    repo.mkdir()
    sh(repo, "init", "-q", "-b", "main")
    sh(repo, "config", "user.email", "t@example.com"); sh(repo, "config", "user.name", "T")
    (repo / ".gitignore").write_text(".scratch/\n")
    (repo / "shared.txt").write_text("base\n")
    (repo / ".team.json").write_text(json.dumps(cfg))
    sh(repo, "add", "-A"); sh(repo, "commit", "-q", "-m", "init")
    sh(repo, "checkout", "-q", "-b", "feat")
    (repo / ".scratch/feat/issues").mkdir(parents=True)
    return repo


def child(task, answer=None):
    """Play one delegate child: find its slot and ticket in the brief, do the ticket's `Fake:` spec, finish the run."""
    rid = task["goal"].split()[1].rstrip(":")
    brief = pathlib.Path(re.search(r"(\S+\.brief\.md)", task["goal"]).group(1)).read_text().split("## Your task")[-1]
    slot = pathlib.Path(re.search(r"Repo root: (\S+) \(absolute\)", brief).group(1))
    tp = pathlib.Path(re.search(r"ticket: (\S+)", brief).group(1))
    spec = re.search(r"^Fake:\s*(.+)$", tp.read_text(), re.M).group(1).split()
    if spec[0] == "limit":
        answer = json.dumps({"verdict": "FAIL", "summary": "Claude AI usage limit reached. Your limit resets at 5pm."})
    elif spec[0] == "write":
        path, word = spec[1], spec[2]
        p = slot / path; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(word + "\n")
        sh(slot, "add", path); sh(slot, "commit", "-q", "-m", f"{tp.stem} built ({tp.name})")
        answer = answer or json.dumps({"verdict": "PASS", "summary": "built", "sha": sh(slot, "rev-parse", "--short",
                                                                                           "HEAD")})
    elif spec[0] == "touch-main":                               # a child that writes in the planner's checkout
        main = pathlib.Path(re.search(r"Scratch folder: (\S+)/\.scratch", brief).group(1))
        (main / "stray.txt").write_text("x\n")
        answer = json.dumps({"verdict": "PASS", "summary": "done"})
    r = subprocess.run([sys.executable, str(KIT / "run-worker.py"), "finish", rid], cwd=slot, capture_output=True,
                       text=True, input=answer or "")
    return r.returncode, r.stdout


def tasks_of(out):
    return [json.loads(l[len("DELEGATE "):]) for l in out.splitlines() if l.startswith("DELEGATE ")]


def main():
    cfg = {"test": "true", "options": OPTS, "max_workers": 2, "setup": [{"cmd": "echo x >> ../setup-count.txt"}]}
    repo = setup_repo("app", cfg)
    setup_count = ROOT / "setup-count.txt"

    # --- refusals, nothing prepared
    ticket(repo, "01", "a", "`src/a.py`", "write src/a.py A")
    ticket(repo, "02", "b", "`src/b.py`", "write src/b.py B")
    ticket(repo, "03", "c", "`src/` (folder)", "write src/c.py C")
    rc, o = scratch(repo, "dispatch", "feat", "01", "03", "--dry-run")
    check("overlapping Seams refused (folder vs file)", rc == 1 and "overlap" in o and "STOP" in o, o)
    rc, o = scratch(repo, "dispatch", "feat", "01", "02", "03")
    check("more tickets than max_workers refused", rc == 1 and "max_workers is 2" in o, o)
    ticket(repo, "04", "d", "`src/d.py`", "write src/d.py D", blocked="01")
    rc, o = scratch(repo, "dispatch", "feat", "01", "04", "--dry-run")
    check("a ticket blocked by one in the group refused", rc == 1 and "blocked by 01" in o, o)
    rc, o = scratch(repo, "dispatch", "feat", "01", "02", "--dry-run")
    check("dry run of separate Seams passes", rc == 0 and "DISPATCH OK dry run" in o, o)
    norepo = setup_repo("nosetup", {"test": "true", "options": OPTS, "max_workers": 2})
    ticket(norepo, "01", "a", "`a.py`", "write a.py A"); ticket(norepo, "02", "b", "`b.py`", "write b.py B")
    rc, o = scratch(norepo, "dispatch", "feat", "01", "02")
    check("no setup in .team.json refused", rc == 1 and "no `setup`" in o, o)

    # --- the happy path: prepared, played, merged
    rc, o = scratch(repo, "dispatch", "feat", "01", "02")
    ts = tasks_of(o)
    check("dispatch 01 02: two DELEGATE lines, one slot lock each, option from .team.json",
          rc == 0 and "DISPATCH OK 2 runs prepared" in o and len(ts) == 2 and all(t["option"] == "standard" for t in ts)
          and sorted(p.name for p in (repo / ".scratch/runs").glob("*.lock")) == ["slot-1.lock", "slot-2.lock"], o)
    briefs = [re.search(r"Repo root: (\S+) ", pathlib.Path(re.search(r"(\S+\.brief\.md)", t["goal"]).group(1))
                        .read_text()).group(1) for t in ts]
    check("each run's brief points at its own slot worktree", [pathlib.Path(b).name for b in briefs] == ["app.w1", "app.w2"],
          briefs)
    rcs = subprocess.run([sys.executable, str(KIT / "run-worker.py"), "prepare", "dev",
                          "ticket: .scratch/feat/issues/03-c.md"], cwd=repo, capture_output=True, text=True)
    check("while the group is open, a serial prepare is refused", rcs.returncode == 2, rcs.stdout)
    rc, o = scratch(repo, "merge", "feat")
    check("merge refuses while a run is unfinished", rc == 1 and "not finished yet" in o, o)
    outs = [child(t) for t in ts]
    check("both children finish PASS", all(rc == 0 and "RESULT dev PASS" in out for rc, out in outs), outs)
    rc, o = scratch(repo, "merge", "feat")
    check("merge: both tickets merged into feat (--no-ff, in ticket order)",
          rc == 0 and "MERGE OK 2 merged" in o and (repo / "src/a.py").is_file() and (repo / "src/b.py").is_file()
          and sh(repo, "log", "--merges", "--format=%s", "-2").splitlines()[-1].startswith("Merge feat--01"), o)
    check("merged ticket branches deleted", not sh(repo, "branch", "--list", "feat--*"), sh(repo, "branch"))
    check("setup ran once per slot", setup_count.read_text().count("x") == 2, setup_count.read_text())
    check("runs.log has one dev line per ticket, repo named as the main checkout",
          sum(1 for l in (repo / ".scratch/runs.log").read_text().splitlines()
              if "\tdev\t" in l and "\trepo=app\t" in l) == 2, (repo / ".scratch/runs.log").read_text())
    check("no lock left behind", not list((repo / ".scratch/runs").glob("*.lock")))
    check("main untouched", sh(repo, "log", "--format=%s", "main") == "init", sh(repo, "log", "--oneline", "main"))

    # --- conflict: both write shared.txt (Seams name different files, so the kit cannot see it)
    ticket(repo, "05", "e", "`src/e.py`", "write shared.txt EEE")
    ticket(repo, "06", "f", "`src/f.py`", "write shared.txt FFF")
    rc, o = scratch(repo, "dispatch", "feat", "05", "06")
    [child(t) for t in tasks_of(o)]
    rc, o = scratch(repo, "merge", "feat")
    check("conflict: first merged, second FAIL conflict, branch kept",
          rc == 0 and "05: merged" in o and "06: FAIL conflict" in o and "with failures: 06" in o
          and sh(repo, "branch", "--list", "feat--06") != "" and (repo / "shared.txt").read_text() == "EEE\n", o)
    check("the aborted merge left the tree clean", sh(repo, "status", "--porcelain") == "", sh(repo, "status"))
    check("setup not repeated when the lockfiles did not change", setup_count.read_text().count("x") == 2)

    # --- usage limit stops the queue; the other ticket still merges
    ticket(repo, "07", "g", "`src/g.py`", "limit")
    ticket(repo, "08", "h", "`src/h.py`", "write src/h.py H")
    rc, o = scratch(repo, "dispatch", "feat", "07", "08")
    [child(t) for t in tasks_of(o)]
    rc, o = scratch(repo, "merge", "feat")
    check("usage limit: FAIL usage-limit, MERGE STOP, the other ticket merged",
          rc == 1 and "FAIL usage-limit" in o and "usage limit hit" in o and "08: merged" in o, o)

    # --- a child that writes in the main checkout instead of its slot
    ticket(repo, "09", "i", "`src/i.py`", "touch-main")
    ticket(repo, "10", "j", "`src/j.py`", "write src/j.py J")
    rc, o = scratch(repo, "dispatch", "feat", "09", "10")
    outs = [child(t) for t in tasks_of(o)]
    check("a child that changes the main checkout -> FAIL guard:main-checkout-changed for every run open then "
          "(the kit cannot tell which child did it)",
          all(rc == 1 and "main-checkout-changed" in out for rc, out in outs), outs)
    (repo / "stray.txt").unlink()
    rc, o = scratch(repo, "merge", "feat")
    check("merge: FAILed tickets are kept, nothing merged", rc == 0 and "09: not merged" in o and "10: not merged" in o, o)

    # --- interrupted group (/new): one child finished its work but nobody ran finish; resume finishes, merge merges
    ticket(repo, "11", "k", "`src/k.py`", "write src/k.py K")
    ticket(repo, "12", "l", "`src/l.py`", "none")
    rc, o = scratch(repo, "dispatch", "feat", "11", "12")
    ts = tasks_of(o)
    slot1 = ROOT / "app.w1"
    (slot1 / "src").mkdir(exist_ok=True); (slot1 / "src/k.py").write_text("K\n")
    sh(slot1, "add", "src/k.py"); sh(slot1, "commit", "-q", "-m", "11-k built (11-k.md)")
    for lk in (repo / ".scratch/runs").glob("slot-*.lock"):      # age the locks past the dispatch window
        d = json.loads(lk.read_text()); d["prepared"] -= 3600; lk.write_text(json.dumps(d))
    rc, o = scratch(repo, "resume", "feat")
    check("resume: both runs INTERRUPTED and finished, merge suggested",
          o.count("INTERRUPTED") == 2 and "scratch.py merge feat" in o and not list((repo / ".scratch/runs").glob("*.lock")),
          o)
    rc, o = scratch(repo, "merge", "feat")
    check("merge after an interruption: FAIL runs are not merged (redispatch them), branches kept",
          rc == 0 and "11: not merged" in o and "12: not merged" in o and sh(repo, "branch", "--list", "feat--11"), o)


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
