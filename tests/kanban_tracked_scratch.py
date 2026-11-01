#!/usr/bin/env python3
"""`kanban.py` with `.scratch` TRACKED inside the project repo (not its own git repo), no model.

The dispatcher's status edits rewrite tracked ticket files; they must not count as a dirty repo. Uses the fake `claude` of
tests/kanban_finish.py. Checks: a full `run` (tickets 01, 02 independent, 03 blocked by 01) ends `KANBAN OK done: merged 3,
review 3, blocked 0`; the ticket commits are on the feature branch; the repo was dirty only under .scratch while it ran and
no merge waited; a second run starts with a scratch-only change; a modified non-scratch file still refuses the run and
still holds a merge; `repo_dirty` unit checks (scratch inside, outside, path with a space).

    python tests/kanban_tracked_scratch.py [--keep]

Last line: `<n>/<total> checks passed`; exit 0 only when all pass.
"""
import json, os, pathlib, re, shutil, subprocess, sys, tempfile, textwrap, time

KIT = pathlib.Path(__file__).resolve().parent.parent / "kit"
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-ktrk-"))
BIN = ROOT / "bin"
os.environ.update(GIT_TERMINAL_PROMPT="0", PYTHONDONTWRITEBYTECODE="1", PATH=f"{BIN}{os.pathsep}{os.environ['PATH']}")
os.environ.pop("ANTHROPIC_API_KEY", None)
sys.path.insert(0, str(KIT))
import kanban  # noqa: E402
results = []

FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, pathlib, re, signal, subprocess, sys, time
a = sys.argv[1:]
if a[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True})); sys.exit(0)
prompt = a[a.index("-p") + 1]
ticket = pathlib.Path(re.search(r"ticket:\s*(\S+)", prompt).group(1))
spec = re.search(r"^Fake:\s*(.+)$", ticket.read_text(), re.M).group(1).split()
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(f"start {ticket.name}\n")
def done(msg):
    print(json.dumps({"type": "result", "subtype": "success", "result": msg, "num_turns": 3,
                      "usage": {"input_tokens": 1, "output_tokens": 1}, "total_cost_usd": 0, "permission_denials": []}))
    sys.exit(0)
time.sleep(float(os.environ.get("FAKE_SLEEP", "1")))
if spec[0] == "slow":
    time.sleep(120)
if spec[0] == "die":
    os.kill(os.getppid(), signal.SIGKILL); sys.exit(1)
if spec[0] == "limit":
    done("Claude AI usage limit reached. Your limit resets in 20 minutes.")
if spec[0] == "fail":
    done("could not\nRESULT: FAIL nope")
if spec[0] == "nocommit":
    done("nothing to do\nRESULT: PASS built nothing")
path, word = spec[1], spec[2]
p = pathlib.Path(path); p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(word + "\n")
subprocess.run(["git", "add", path], check=True)
subprocess.run(["git", "commit", "-q", "-m", f"{ticket.stem} built"], check=True)
done("did it\nRESULT: PASS built " + path)
'''


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


def init_repo(path, name="app"):
    path.mkdir(parents=True, exist_ok=True)
    sh(path, "init", "-q", "-b", "main")
    sh(path, "config", "user.email", "t@example.com"); sh(path, "config", "user.name", "T")
    return path


def ticket(repo, nn, fake, blocked="None", status="todo"):
    (repo / ".scratch/feat/issues" / f"{nn}-t.md").write_text(textwrap.dedent(f"""\
        # {nn}: t

        Lane: full
        Branch: feat
        Status: {status}
        Round: 0
        Blocked by: {blocked}
        Seam: `src/{nn}.py`
        Test command: true
        Fake: {fake}

        ## Result
        """))


def field(repo, nn, name="Status"):
    return re.search(rf"^{name}:\s*(.+)$", (repo / ".scratch/feat/issues" / f"{nn}-t.md").read_text(), re.M).group(1).strip()


def run(repo, *args, timeout=120):
    r = subprocess.run([sys.executable, str(KIT / "kanban.py"), "run", "feat", *args], cwd=repo, capture_output=True,
                       text=True, timeout=timeout)
    return r.returncode, r.stdout + r.stderr


def kill_all(repo):
    for _ in range(4):
        for d, alive in kanban.teamcfg.read_locks(repo / ".scratch"):
            for k in ("worker_pid", "launcher_pid"):
                if d.get(k) and alive:
                    kanban.teamcfg.kill_tree(d[k])
        time.sleep(0.3)


def main():
    BIN.mkdir()
    (BIN / "claude").write_text(FAKE_CLAUDE); (BIN / "claude").chmod(0o755)
    os.environ["FAKE_LOG"] = str(ROOT / "fake.log")
    os.environ["FAKE_SLEEP"] = "1"

    # --- the project: .scratch committed IN the project repo, on the feature branch
    repo = init_repo(ROOT / "app")
    (repo / ".gitignore").write_text(".scratch/runs/\n.scratch/runs.log\n")
    (repo / "shared.txt").write_text("base\n")
    (repo / ".team.json").write_text(json.dumps({"test": "true", "max_workers": 2, "setup": [{"cmd": "true"}]}))
    sh(repo, "add", "-A"); sh(repo, "commit", "-q", "-m", "init")
    sh(repo, "checkout", "-q", "-b", "feat")
    (repo / ".scratch/feat/issues").mkdir(parents=True)
    ticket(repo, "01", "write src/01.py A"); ticket(repo, "02", "write src/02.py B")
    ticket(repo, "03", "write src/03.py C", blocked="01")
    sh(repo, "add", "-A"); sh(repo, "commit", "-q", "-m", "tickets")
    check("setup: .scratch is tracked in the project repo and is not a repo of its own",
          sh(repo, "ls-files", ".scratch/feat/issues").count("\n") == 2 and not (repo / ".scratch/.git").exists())

    # --- (a)(c) full loop; sample `git status` while it runs
    p = subprocess.Popen([sys.executable, str(KIT / "kanban.py"), "run", "feat", "--interval", "1"], cwd=repo,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    seen, end = set(), time.time() + 120
    while p.poll() is None and time.time() < end:
        r = subprocess.run(["git", "status", "--short"], cwd=repo, capture_output=True, text=True)
        seen.update(r.stdout.splitlines())
        time.sleep(0.2)
    if p.poll() is None:
        p.kill()
    out = p.communicate()[0]
    check("(a) run ends 'KANBAN OK done: merged 3, review 3, blocked 0' with exit 0, all tickets review",
          p.returncode == 0 and out.strip().splitlines()[-1] == "KANBAN OK done: merged 3, review 3, blocked 0"
          and all(field(repo, n) == "review" for n in ("01", "02", "03")), out)
    check("(b) the three ticket commits are on the feature branch",
          all(re.search(rf"^{n}-t built$", sh(repo, "log", "feat", "--format=%s"), re.M) for n in ("01", "02", "03")),
          sh(repo, "log", "--oneline", "--graph"))
    # src/ lines: a merge in flight. Without the fix the first merge waits forever, so require all three merges too.
    check("(c) while it ran the repo was dirty only under .scratch (tickets M) and no merge waited",
          seen and all(l[3:].startswith((".scratch/", "src/")) for l in seen)
          and any(l.startswith(" M") and "issues/" in l for l in seen)
          and "merged 01" in out and "merged 03" in out and "merge waits" not in out, f"{sorted(seen)}\n{out}")

    # --- (d) second run: one ticket back to todo by hand, scratch-only change, still starts
    sh(repo, "add", "-A"); sh(repo, "commit", "-q", "-m", "state")
    ticket(repo, "01", "write src/01b.py D")                                  # status todo again
    (repo / ".scratch/feat/note.txt").write_text("planner note\n")            # untracked scratch file too
    rc, o = run(repo, "--interval", "1")
    check("(d) a second run with scratch-only changes starts and finishes",
          rc == 0 and "started 01" in o and "uncommitted" not in o and (repo / "src/01b.py").is_file()
          and o.strip().splitlines()[-1].startswith("KANBAN OK done"), o)

    # --- (e) a non-scratch tracked file refuses the run, and holds a merge
    sh(repo, "add", "-A"); sh(repo, "commit", "-q", "-m", "state2")
    ticket(repo, "01", "write src/01c.py E")
    (repo / "shared.txt").write_text("edited\n")
    rc, o = run(repo, "--once")
    check("(e) preflight refuses 'app has uncommitted changes' when a non-scratch file is modified",
          rc != 0 and "app has uncommitted changes" in o, o)
    scratch = repo / ".scratch"
    check("(e) the finish-path test (repo_dirty) still sees the non-scratch file",
          kanban.repo_dirty(repo, scratch).strip() == "M shared.txt", kanban.repo_dirty(repo, scratch))
    sh(repo, "checkout", "--", "shared.txt")

    # --- (f) helper units
    a = init_repo(ROOT / "unit a")
    (a / "x.txt").write_text("1\n"); (a / "sc dir").mkdir(); (a / "sc dir/t.md").write_text("1\n")
    sh(a, "add", "-A"); sh(a, "commit", "-q", "-m", "i")
    (a / "sc dir/t.md").write_text("2\n")
    check("(f) scratch inside the repo, path with a space: its changes are excluded",
          kanban.repo_dirty(a, a / "sc dir") == "", kanban.repo_dirty(a, a / "sc dir"))
    check("(f) a change outside the scratch folder is still reported",
          (a / "x.txt").write_text("2\n") and kanban.repo_dirty(a, a / "sc dir") == "M x.txt",
          kanban.repo_dirty(a, a / "sc dir"))
    out_dir = ROOT / "elsewhere"; out_dir.mkdir()
    check("(f) scratch outside the repo: nothing is excluded",
          kanban.repo_dirty(a, out_dir) == kanban.sc.dirty(a) != "", kanban.repo_dirty(a, out_dir))
    check("(f) scratch is a prefix lookalike (sc dir2): not excluded",
          (a / "sc dir2").mkdir() is None and (a / "sc dir2/f").write_text("z")
          and "sc dir2/" in kanban.repo_dirty(a, a / "sc dir"), kanban.repo_dirty(a, a / "sc dir"))


if __name__ == "__main__":
    try:
        main()
    finally:
        if "--keep" in sys.argv:
            print(f"kept {ROOT}")
        else:
            try:
                kill_all(ROOT / "app")
            except Exception:
                pass
            shutil.rmtree(ROOT, ignore_errors=True)
    print(f"{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)
