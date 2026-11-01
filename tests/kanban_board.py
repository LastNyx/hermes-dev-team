#!/usr/bin/env python3
"""`kanban.py board` and `status` (read-only), no model.

A throwaway project (git repo, .team.json, one feature with 6 tickets in every status), a runs.log, one live slot
lock (this process's pid), one stale lock (dead pid) and a progress log. Checks: column counts, ready flags, open
blockers, retries, last result, slot/progress of the running ticket, a stale lock not shown as running, one JSON
document, column headers, dispatcher status files, nothing written, STOP outside a project.

    python tests/kanban_board.py [--keep]

Last line: `<n>/<total> checks passed`; exit 0 only when all pass.
"""
import json, os, pathlib, shutil, subprocess, sys, tempfile, textwrap, time

KIT = pathlib.Path(__file__).resolve().parent.parent / "kit"
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-kanban-"))
os.environ.update(GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1")
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name)
    if not ok and detail:
        print("      " + str(detail)[-2500:].replace("\n", "\n      "))


def sh(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"FAILED git {args}: {r.stdout}{r.stderr}")


def kanban(cwd, *args):
    r = subprocess.run([sys.executable, str(KIT / "kanban.py"), *args], cwd=cwd, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def ticket(repo, nn, slug, status, blocked="None", seam="`src/a.py`"):
    (repo / ".scratch/feat/issues" / f"{nn}-{slug}.md").write_text(textwrap.dedent(f"""\
        # {nn}: {slug} title

        Lane: full
        Branch: feat
        Status: {status}
        Round: 0
        Blocked by: {blocked}
        Seam: {seam}
        Test command: true

        ## Result
        """))


def snapshot(root):
    snap = {}
    for p in sorted(root.rglob("*")):
        if ".git" in p.parts:
            continue
        snap[p.relative_to(root).as_posix()] = (p.read_bytes() if p.is_file() else None, p.stat().st_mtime_ns if p.is_file() else 0)
    return snap


def runline(stamp, role, verdict, name):
    return "\t".join([stamp, role, "sonnet", verdict, "turns=3", "5s", "out=1", "list$=0", "denials=0", "-",
                      "feature=feat", "test=-", "in=1", "cw=0", "cr=0", "repo=app", "backend=claude",
                      f"ticket={name}", "fmt=1"])


def main():
    repo = ROOT / "app"
    repo.mkdir()
    sh(repo, "init", "-q", "-b", "main")
    sh(repo, "config", "user.email", "t@example.com"); sh(repo, "config", "user.name", "T")
    (repo / ".team.json").write_text(json.dumps({"test": "true", "max_workers": 5}))
    (repo / ".gitignore").write_text(".scratch/\n")
    (repo / "x.txt").write_text("x\n")
    sh(repo, "add", "-A"); sh(repo, "commit", "-q", "-m", "init")
    scratch = repo / ".scratch"
    (scratch / "feat/issues").mkdir(parents=True)
    ticket(repo, "01", "base", "done")
    ticket(repo, "02", "second", "todo", blocked="01", seam="`src/b.py`")            # ready
    ticket(repo, "03", "third", "todo", blocked="01, 04")                             # 04 not done: not ready
    ticket(repo, "04", "fourth", "in-progress", seam="`src/d.py`")                    # running in slot 1
    ticket(repo, "05", "fifth", "review")
    ticket(repo, "06", "sixth", "blocked", blocked="None")
    (scratch / "runs").mkdir()
    name4 = "04-fourth.md"
    (scratch / "runs.log").write_text("\n".join([
        runline("20261101-100000", "dev", "PASS abc1234", "01-base.md"),
        runline("20261101-101000", "dev", "FAIL no RESULT line (error)", name4),
        runline("20261101-102000", "dev", "PASS def5678", name4),
        runline("20261101-103000", "dev", "FAIL tests red", "06-sixth.md"),
        "20261101-103500\tplanner\tplanner\tNOTE status 04 in-progress\tturns=-\t0s\tout=None\tlist$=None\tdenials=0\t-\t"
        f"feature=feat\ttest=-\tin=None\tcw=None\tcr=None\trepo=-\tbackend=-\tticket={name4}\taction=status\tfmt=1",
    ]) + "\n")
    stamp = "20261101-104000"
    (scratch / "runs" / f"{stamp}-dev.progress.log").write_text(
        "\n".join(f"  [{i:>4}s] Edit file{i}" for i in range(1, 6)) + "\n")
    me = os.getpid()
    (scratch / "runs/slot-1.lock").write_text(json.dumps(
        {"launcher_pid": me, "worker_pid": me, "role": "dev", "ticket": name4, "feature": "feat", "stamp": stamp}))
    (scratch / "runs/slot-2.lock").write_text(json.dumps(
        {"launcher_pid": 2 ** 22 + 12345, "worker_pid": 2 ** 22 + 12346, "role": "dev", "ticket": "03-third.md",
         "feature": "feat", "stamp": "20261101-090000"}))

    before = snapshot(repo)
    rc, o, e = kanban(repo, "board", "--json")
    try:
        doc = json.loads(o)
    except ValueError:
        doc = {}
    check("board --json is one valid document", rc == 0 and set(doc) == {"feature", "columns", "dispatcher"}, o + e)
    cols = doc.get("columns", {})
    by = {n: t for c in cols.values() for t in c for n in [t["number"]]}
    counts = {k: len(v) for k, v in cols.items()}
    check("column counts", counts == {"todo": 2, "in-progress": 1, "review": 1, "blocked": 1, "done": 1}, counts)
    check("ready: blockers done -> ready", by["02"]["ready"] is True and by["02"]["blockers_open"] == [], by["02"])
    check("ready: open blocker -> not ready", by["03"]["ready"] is False and by["03"]["blockers_open"] == ["04"], by["03"])
    check("blocked_by lists numbers", by["03"]["blocked_by"] == ["01", "04"] and by["01"]["blocked_by"] == [], by["03"])
    check("done/review/blocked are never ready", not any(by[n]["ready"] for n in ("01", "04", "05", "06")))
    check("seam_paths parsed", by["02"]["seam_paths"] == ["src/b.py"], by["02"])
    check("title, repo, branch", by["02"]["title"] == "02: second title" and by["02"]["branch"] == "feat"
          and by["02"]["name"] == "02-second.md", by["02"])
    check("retries counts only earlier non-PASS runs", by["04"]["retries"] == 1 and by["01"]["retries"] == 0
          and by["06"]["retries"] == 0, (by["04"], by["01"]))
    check("last_result is the last run, planner lines ignored",
          by["04"]["last_result"] == "RESULT dev PASS def5678" and by["06"]["last_result"] == "RESULT dev FAIL tests red"
          and by["02"]["last_result"] is None, by["04"])
    check("running ticket has slot, started and progress tail",
          by["04"]["slot"] == 1 and by["04"]["started"] == stamp
          and by["04"]["progress_tail"] == [f"  [{i:>4}s] Edit file{i}" for i in (3, 4, 5)], by["04"])
    check("stale lock is not shown as running", by["03"]["slot"] is None and by["03"]["started"] is None
          and by["03"]["progress_tail"] == [])
    check("a ticket with no lock has no slot/progress", by["02"]["slot"] is None and by["02"]["progress_tail"] == [])
    d = doc.get("dispatcher", {})
    check("dispatcher: not running, max_workers capped at 3", d.get("running") is False and d.get("pid") is None
          and d.get("max_workers") == 3 and d.get("paused_until") is None, d)
    live = {w["lock"]: w["alive"] for w in d.get("workers", [])}
    check("dispatcher workers list live and stale locks", live == {"slot-1.lock": True, "slot-2.lock": False}, d)

    rc, o, e = kanban(repo, "board")
    check("plain output has the five column headers", rc == 0 and all(h in o for h in (
        "== Todo (2) ==", "== In progress (1) ==", "== Review (1) ==", "== Blocked (1) ==", "== Done (1) ==")), o)
    check("plain line: blockers, last result and progress",
          "  03 03: third title [" in o and "blocked-by: 01,04" in o and "| last: RESULT dev PASS def5678" in o
          and "    > [   5s] Edit file5" in o, o)
    rc, o, e = kanban(repo, "board", "--feature", "feat", "--json")
    check("--feature restricts", rc == 0 and json.loads(o)["feature"] == "feat")
    rc, o, e = kanban(repo, "board", "--feature", "nope")
    check("unknown feature: STOP, exit 1", rc == 1 and o.startswith("KANBAN STOP") and o.count("\n") == 1, o)

    # status: dispatcher files
    rc, o, e = kanban(repo, "status", "--json")
    s = json.loads(o)
    check("status --json without dispatcher files", rc == 0 and s["running"] is False and s["paused_until"] is None, o)
    (scratch / "runs/dispatcher.lock").write_text(json.dumps({"pid": me, "started": "2026-11-01 10:00", "feature": "feat"}))
    until = int(time.time()) + 600
    (scratch / "runs/dispatcher.pause").write_text(f"{until}\n")
    before2 = snapshot(repo)
    rc, o, e = kanban(repo, "status", "--json")
    s = json.loads(o)
    check("status reads a live dispatcher lock and the pause file", s["running"] and s["pid"] == me
          and s["started"] == "2026-11-01 10:00" and s["feature"] == "feat" and s["paused_until"] == until, o)
    rc, o, e = kanban(repo, "status")
    check("dispatcher.lock is not listed as a worker", [w["lock"] for w in s["workers"]] == ["slot-1.lock", "slot-2.lock"], s)
    rc, o, e = kanban(repo, "status")
    check("status plain text", rc == 0 and f"running pid {me}" in o and "paused until" in o and "slot-2.lock" in o
          and "stale" in o, o)
    (scratch / "runs/dispatcher.lock").write_text(json.dumps({"pid": 2 ** 22 + 99, "started": "x", "feature": "feat"}))
    (scratch / "runs/dispatcher.pause").write_text(f"{int(time.time()) - 10}\n")
    rc, o, e = kanban(repo, "status", "--json")
    s = json.loads(o)
    check("dead dispatcher pid is not running; past pause ignored", s["running"] is False and s["pid"] is None
          and s["paused_until"] is None, o)
    (scratch / "runs/dispatcher.lock").write_text("not json")
    rc, o, e = kanban(repo, "status", "--json")
    check("garbled dispatcher lock does not crash", rc == 0 and json.loads(o)["running"] is False, o + e)
    (scratch / "runs/dispatcher.lock").unlink(); (scratch / "runs/dispatcher.pause").unlink()

    # read-only
    after = snapshot(repo)
    check("nothing written in the project (tree snapshot equal)", before == after,
          [k for k in set(before) | set(after) if before.get(k) != after.get(k)])
    del before2

    # outside a project
    empty = ROOT / "empty"; empty.mkdir()
    rc, o, e = kanban(empty, "board")
    check("outside a project: KANBAN STOP, exit 1", rc == 1 and o.startswith("KANBAN STOP") and o.count("\n") == 1, o + e)
    bare = ROOT / "noscratch"; bare.mkdir(); (bare / ".team.json").write_text("{}")
    rc, o, e = kanban(bare, "status")
    check("no scratch folder: KANBAN STOP", rc == 1 and o.startswith("KANBAN STOP no scratch folder"), o + e)
    nofeat = ROOT / "nofeat"; (nofeat / ".scratch").mkdir(parents=True); (nofeat / ".team.json").write_text("{}")
    rc, o, e = kanban(nofeat, "board")
    check("no feature folder: KANBAN STOP", rc == 1 and o.startswith("KANBAN STOP no feature"), o + e)


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
