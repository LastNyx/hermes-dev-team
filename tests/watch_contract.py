#!/usr/bin/env python3
"""The file contract with hermes-dev-watch (docs/watch-contract.md): what the kit writes, parsed the way the watcher does.

No model. Drives the launcher's real writers with fixed inputs: `report()` (a dev PASS, a researcher FAIL, a Hermes
run), `stream_worker()` on a scripted Claude-shaped stream (assistant text, tool calls, a denial, user-side text) and
`write_lock()`, plus one planner line (`plannerlog.log()`, a failed Verify; planner lines have no progress log). Then reads the result with the watcher's rules (first six tab fields by position, `key=value` after,
run id `^\\d{8}-\\d{6}-[a-z]+$`, progress lines `[ Ns] text` with `say: ` / `DENIED ` / `exit!=0 ` prefixes, lock JSON)
and compares the output with the committed samples in tests/fixtures/watch/, which hermes-dev-watch's CI parses.

    python tests/watch_contract.py [--update] [--keep]

`--update` rewrites the samples after an intended format change (then bump teamcfg.RUN_FORMAT if an old watcher
would misread the new files, and note it in CHANGELOG with **watch:**). Last line: `<n>/<total> checks passed`.
"""
import contextlib, datetime, importlib.util, io, json, os, pathlib, re, shutil, sys, tempfile, types

HERE = pathlib.Path(__file__).resolve().parent
KIT = HERE.parent / "kit"
FIXTURES = HERE / "fixtures" / "watch"
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-watch-"))
results = []

# The watcher's rules (hermes-dev-watch core.py runs_log / index.html lineHTML, commit 08265b1), restated here.
RUN_ID = re.compile(r"^\d{8}-\d{6}-[a-z]+$")
LINE = re.compile(r"^\s*\[\s*(\d+)s\]\s?(.*)$")
LOCK_KEYS = ("launcher_pid", "worker_pid", "role", "ticket", "feature", "repo", "stamp")
KV_KEYS = ("turns", "list$", "denials", "feature", "ticket", "repo", "backend", "fmt")
STAMP_DEV, STAMP_RES, STAMP_HER, STAMP_PLN = "20261016-120000", "20261016-120500", "20261016-121000", "20261016-121500"


def check(name, ok, detail=None):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name)
    if not ok and detail:
        print("      " + str(detail).replace("\n", "\n      ")[-1500:])


def load_launcher():
    sys.path.insert(0, str(KIT))
    spec = importlib.util.spec_from_file_location("run_worker", KIT / "run-worker.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def clock(step=3):
    t = {"n": -step}

    def now():
        t["n"] += step
        return float(t["n"])
    return types.SimpleNamespace(time=now)


STREAM = [
    {"type": "system", "subtype": "init"},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "Reading the ticket first.\n\nThen tests."}]}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Read",
                                                   "input": {"file_path": "calc.py"}}]}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t2", "name": "Bash",
                                                   "input": {"command": "git push"}}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2", "is_error": True,
                                              "content": "This command requires approval"}]},
     "tool_result_meta": [{"id": "t2", "permission_decision": {"decision": "reject"}}]},
    {"type": "user", "message": {"content": [{"type": "text", "text": "user-side text is not logged"}]}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t3", "name": "Edit",
                                                   "input": {"file_path": "calc.py"}}]}},
    {"type": "result", "subtype": "success", "result": "RESULT: PASS done", "num_turns": 4, "usage": {}},
]


def write_samples(rw, scratch):
    """Run the kit's writers into <scratch> with fixed inputs; cwd = scratch so rel() paths are `runs/...`."""
    runs = scratch / "runs"
    runs.mkdir(parents=True)
    os.chdir(scratch)
    real_time = rw.time
    rw.time = clock()
    try:
        script = "import json,sys\nfor e in json.loads(sys.argv[1]): print(json.dumps(e), flush=True)\n"
        with contextlib.redirect_stdout(io.StringIO()):
            rw.stream_worker([sys.executable, "-c", script, json.dumps(STREAM)], runs / f"{STAMP_DEV}-dev.progress.log")
    finally:
        rw.time = real_time

    def st(stamp, role, model, ticket, feature, test):
        return {"stamp": stamp, "role": role, "model": model, "test_cmd": test, "feature": feature,
                "repo_name": "proj", "scratch": scratch.as_posix(), "ticket": ticket}
    with contextlib.redirect_stdout(io.StringIO()):
        rw.report(st(STAMP_DEV, "dev", "sonnet", "01-mul.md", "calc", "python -m pytest -q"), "PASS 1a2b3c4", 18,
                  22, (12, 30000, 150000, 2000), 0.1234, [], runs / f"{STAMP_DEV}-dev.json", "claude")
        rw.report(st(STAMP_RES, "researcher", "sonnet", None, "dev-watch", None), "FAIL guard:pushed:origin", 15, 36,
                  (None, None, None, None), None, [{"tool_name": "Bash", "tool_input": {"command": "git push"}}],
                  runs / f"{STAMP_RES}-researcher.json", "claude")
        rw.report(st(STAMP_HER, "docs", "hermes", None, "calc", None), "PASS docs updated", "-", 40,
                  (None, None, None, None), None, [], runs / f"{STAMP_HER}-docs.json", "hermes")
    import plannerlog                                      # kit/ is on sys.path (load_launcher)
    plannerlog.log(scratch, "calc", "FAIL verify 01: the sum is blank", "status", ticket="01-mul.md", repo="proj",
                   now=datetime.datetime.strptime(STAMP_PLN, "%Y%m%d-%H%M%S"))
    lock = {"path": str(runs / "worker.lock"), "launcher_pid": 4242, "role": "dev", "ticket": "01-mul.md",
            "feature": "calc", "repo": "proj", "stamp": STAMP_DEV, "fmt": rw.teamcfg.RUN_FORMAT}
    rw.write_lock(lock, worker_pid=4343)
    os.chdir(ROOT)
    return ["runs.log", f"runs/{STAMP_DEV}-dev.progress.log", "runs/worker.lock"]


def lf(p):
    return p.read_bytes().replace(b"\r\n", b"\n")


def runs_log_checks(rw, scratch):
    lines = lf(scratch / "runs.log").decode().splitlines()
    rows = []
    for line in lines:
        f = line.split("\t")
        kv = dict(x.split("=", 1) for x in f[4:] if "=" in x)
        rows.append((f, kv))
    check("runs.log: 4 lines (3 workers, 1 planner), each with at least 6 tab fields", len(rows) == 4 and all(len(f) >= 6 for f, _ in rows),
          lines)
    check("runs.log: run id `<stamp>-<role>` matches the watcher's pattern",
          all(RUN_ID.match(f"{f[0]}-{f[1]}") for f, _ in rows), [f[:2] for f, _ in rows])
    check("runs.log: fixed positions: model f[2], verdict f[3], f[4] = turns=, f[5] = <secs>s",
          [(f[2], f[3].split()[0], f[4].startswith("turns="), f[5]) for f, _ in rows]
          == [("sonnet", "PASS", True, "22s"), ("sonnet", "FAIL", True, "36s"), ("hermes", "PASS", True, "40s"),
              ("planner", "FAIL", True, "0s")],
          [f[:6] for f, _ in rows])
    check("runs.log: every key the watcher reads is present", all(all(k in kv for k in KV_KEYS) for _, kv in rows),
          [sorted(set(KV_KEYS) - set(kv)) for _, kv in rows])
    check(f"runs.log: fmt={rw.teamcfg.RUN_FORMAT} on every line, as the last field",
          all(f[-1] == f"fmt={rw.teamcfg.RUN_FORMAT}" for f, _ in rows), [f[-1] for f, _ in rows])
    check("runs.log: list$ parses as a float or is None, ticket/feature `-` when absent",
          float(rows[0][1]["list$"]) == 0.1234 and rows[1][1]["list$"] == "None" and rows[1][1]["ticket"] == "-",
          [kv for _, kv in rows])
    pf, pkv = rows[3]
    check("runs.log: planner line: role and model `planner`, turns=-, action=status, no progress log (field 10 `-`)",
          (pf[1], pf[2], pkv.get("turns"), pkv.get("action"), pkv.get("ticket"), pf[9]) == ("planner", "planner", "-",
          "status", "01-mul.md", "-"), pf)


def progress_checks(scratch):
    lines = lf(scratch / "runs" / f"{STAMP_DEV}-dev.progress.log").decode().splitlines()
    m = [LINE.match(l) for l in lines]
    check("progress: every line is `[ Ns] text`", lines and all(m), lines)
    texts = [x.group(2) for x in m if x]
    check("progress: assistant text -> one `say: ` line, whitespace folded",
          [t for t in texts if t.startswith("say: ")] == ["say: Reading the ticket first. Then tests."], texts)
    check("progress: a denial -> `DENIED Bash: ...`", any(t.startswith("DENIED Bash: ") for t in texts), texts)
    check("progress: tool calls -> `<Tool> <arg>` (Read, Bash, Edit)",
          [t.split(" ", 1)[0] for t in texts if not t.startswith(("say: ", "DENIED "))] == ["Read", "Bash", "Edit"],
          texts)
    check("progress: user-side text is not logged", not any("user-side" in t for t in texts), texts)


def lock_checks(rw, scratch):
    d = json.loads((scratch / "runs" / "worker.lock").read_text(encoding="utf-8"))
    check("lock: JSON with every field the watcher reads, plus fmt",
          all(k in d for k in LOCK_KEYS) and d.get("fmt") == rw.teamcfg.RUN_FORMAT, d)
    check("lock: `<stamp>-<role>` is a valid run id and names the progress log",
          RUN_ID.match(f"{d['stamp']}-{d['role']}") and (scratch / "runs" / f"{d['stamp']}-{d['role']}.progress.log")
          .is_file(), d)
    check("lock: the name is runs/*.lock", (scratch / "runs" / "worker.lock").is_file())


def role_checks():
    roles = sorted(p.stem for p in (KIT / "roles").glob("*.json"))
    check("every role name in kit/roles/*.json fits the watcher's `[a-z]+` (a `fix-dev` role would vanish)",
          roles and all(re.fullmatch(r"[a-z]+", r) for r in roles), roles)


def fixture_checks(scratch, files):
    if "--update" in sys.argv:
        for name in files:
            dst = FIXTURES / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(lf(scratch / name))
        print(f"updated {len(files)} samples in {FIXTURES.relative_to(HERE.parent).as_posix()}")
    stale = [n for n in files if not (FIXTURES / n).is_file() or lf(FIXTURES / n) != lf(scratch / n)]
    check("tests/fixtures/watch/ matches what the kit writes now (else: --update, and tell hermes-dev-watch)",
          not stale, stale)


def main():
    cwd = os.getcwd()
    try:
        rw = load_launcher()
        scratch = ROOT / ".scratch"
        files = write_samples(rw, scratch)
        runs_log_checks(rw, scratch)
        progress_checks(scratch)
        lock_checks(rw, scratch)
        role_checks()
        fixture_checks(scratch, files)
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
