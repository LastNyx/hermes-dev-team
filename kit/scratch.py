#!/usr/bin/env python3
"""Planner state that survives a new session or a switch to another machine. No model is called.

The scratch folder (tickets, batches.md, reviews, decisions.md, drift.md, dev-notes.md, runs.log) is its own small git
repo, separate from the product repo: `<repo>/.scratch/` for a single repo (the product repo already ignores it), the
workspace scratch folder for a multi-repo workspace. `runs/` (result JSON, progress logs, prompts, the worker lock)
stays out of it. Nothing here merges, rebases, resets, stashes or force-pushes.

Usage (cwd = the project repo, or the workspace root / any of its repos):
    python <kit>/scratch.py init [--remote URL]     # git init the scratch folder (once per project); commits what exists
    python <kit>/scratch.py init --clone URL        # other machine: clone the scratch repo into the scratch folder
    python <kit>/scratch.py resume [feature]        # step 0: ff-only pull of scratch + feature branch(es), state report
    python <kit>/scratch.py commit "<message>"      # commit the scratch repo (after every Status change)
    python <kit>/scratch.py decide <feature> "<text>"  # one line in <feature>/decisions.md, committed
    python <kit>/scratch.py status <feature> <NN> <status> [--verify "PASS|FAIL|SKIP: <text>"]
                                                    # set a ticket's Status (never by hand); records the Verify
    python <kit>/scratch.py stop                    # force-stop the running worker; logs it in decisions.md
    python <kit>/scratch.py switch                  # push scratch + feature branch(es) for the other machine
    python <kit>/scratch.py sync-main [--apply]     # shared repo, between batches: merge origin/main into the branch
    python <kit>/scratch.py baseline <feature>      # before the first ticket: record what already fails on main
    python <kit>/scratch.py check-run begin|end     # trivial lane: the launcher's guards around the planner's own fix
    python <kit>/scratch.py dispatch <feature> NN NN [--dry-run]
                                                    # parallel dev workers in pool worktrees, merged in order (0.17.0)

`decide` and `status` also write a `planner` line to runs.log (plannerlog.py), so the planner's own actions show in
hermes-dev-watch next to the workers'.

Last line: `<COMMAND> OK ...` (exit 0) or `<COMMAND> STOP <reason>` (exit 1): a STOP needs the user.
"""
import argparse, datetime, json, os, pathlib, re, subprocess, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import teamcfg  # noqa: E402
import plannerlog as pl  # noqa: E402
import suites  # noqa: E402
import parallel  # noqa: E402

ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
GITIGNORE = "# per-run files: result JSON, progress logs, prompts, the worker lock (runs.log is kept)\nruns/\n"
MAIN = ("main", "master")


def git(cwd, *args, check=False):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=ENV,
                       encoding="utf-8", errors="replace")
    if check and r.returncode:
        raise SystemExit(f"{' '.join(args[:2])} failed in {cwd}: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip() if r.returncode == 0 else None


def is_repo_root(p):
    p = pathlib.Path(p)
    return (p / ".git").exists()


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def ctx():
    tc = teamcfg.load()
    if tc["repos"]:
        repos = {n: p for n, p in tc["repos"].items() if is_repo_root(p)}
    else:
        top = git(pathlib.Path.cwd(), "rev-parse", "--show-toplevel")
        repos = {pathlib.Path(top).name: pathlib.Path(top).resolve()} if top else {}
    return tc, tc["scratch"], repos


def dirty(cwd):
    return git(cwd, "status", "--short") or ""


def commit_scratch(scratch, msg):
    """Commit everything in the scratch repo. Returns the short sha, or None when there was nothing to commit."""
    if not is_repo_root(scratch):
        return None
    if not dirty(scratch):
        return None
    git(scratch, "add", "-A", check=True)
    git(scratch, "commit", "-q", "-m", msg, check=True)
    return git(scratch, "rev-parse", "--short", "HEAD")


def has_origin(cwd):
    return "origin" in (git(cwd, "remote") or "").split()


def sync(cwd, branch, label):
    """Fetch origin and fast-forward `branch` (the checked-out one). Returns (ok, message)."""
    if not has_origin(cwd):
        return True, f"{label}: no remote, local only"
    if git(cwd, "fetch", "-q", "origin") is None:
        return True, f"{label}: fetch failed (offline or no access), not synced"
    remote = f"origin/{branch}"
    if git(cwd, "rev-parse", "--verify", "-q", remote) is None:
        return True, f"{label}: {remote} does not exist yet"
    counts = git(cwd, "rev-list", "--left-right", "--count", f"HEAD...{remote}")
    if not counts:
        return False, f"{label}: cannot compare HEAD with {remote}"
    ahead, behind = (int(x) for x in counts.split())
    if ahead and behind:
        return False, (f"{label}: {branch} and {remote} have DIVERGED ({ahead} local, {behind} remote commits); "
                       f"not merging. local: {git(cwd, 'log', '--oneline', '-3', f'{remote}..HEAD')!r} "
                       f"remote: {git(cwd, 'log', '--oneline', '-3', f'HEAD..{remote}')!r}")
    if behind:
        if dirty(cwd):
            return False, f"{label}: {behind} new remote commit(s) but the tree is dirty; not pulling"
        git(cwd, "merge", "-q", "--ff-only", remote, check=True)
        return True, f"{label}: fast-forwarded {behind} commit(s) from {remote}"
    return True, f"{label}: up to date" + (f" ({ahead} local commit(s) not pushed)" if ahead else "")


def field(text, key):
    m = re.search(rf"^{re.escape(key)}:[ \t]*(.*?)[ \t]*$", text, re.M)
    return m.group(1).split("  #")[0].strip().strip("`").strip() if m else None


def tickets(feature_dir):
    out = []
    for t in sorted((feature_dir / "issues").glob("*.md")):
        text = t.read_text(encoding="utf-8")
        out.append({"path": t, "name": t.name, "status": field(text, "Status"), "branch": field(text, "Branch"),
                    "repo": field(text, "Repo")})
    return out


def runs_for(scratch, name):
    log = scratch / "runs.log"
    if not log.is_file():
        return []
    return [l for l in log.read_text(encoding="utf-8").splitlines() if f"\tticket={name}" in l]


def commit_for(repo, name):
    """Commits on the current branch (not on main) whose subject ends with `(<ticket file name>)` (dev role's format)."""
    base = next((b for b in MAIN if git(repo, "rev-parse", "--verify", "-q", b)), None)
    rng = [f"{base}..HEAD"] if base else ["HEAD"]
    out = git(repo, "log", "--format=%h %s", "--fixed-strings", f"--grep=({name})", *rng) or ""
    return [l for l in out.splitlines() if l.rstrip().endswith(f"({name})")]


def main_ref(repo):
    """The name of the main branch: main or master, whichever exists locally or on origin."""
    for b in MAIN:
        if git(repo, "rev-parse", "--verify", "-q", b) or git(repo, "rev-parse", "--verify", "-q", f"origin/{b}"):
            return b
    return None


def foreign_commits(repo):
    """Non-merge commits on the feature branch (not on main or origin/main) whose author email is not this user's."""
    me, mb = (git(repo, "config", "user.email") or "").lower(), main_ref(repo)
    if not mb:
        return []
    nots = [r for r in (mb, f"origin/{mb}") if git(repo, "rev-parse", "--verify", "-q", r)]
    out = git(repo, "log", "--no-merges", "--format=%h %ae %s", "HEAD", *[f"^{r}" for r in nots]) or ""
    return [l for l in out.splitlines() if l.split(" ", 2)[1].lower() != me]


def open_tickets_on(scratch, repo_name, branch, single):
    """Tickets of features on this branch whose Status is in-progress or review (a batch is mid-way)."""
    busy = []
    for fd in sorted(p for p in scratch.iterdir() if (p / "issues").is_dir()) if scratch.is_dir() else []:
        for t in tickets(fd):
            if t["branch"] != branch or (not single and t["repo"] != repo_name):
                continue
            if t["status"] in ("in-progress", "review"):
                busy.append(f"{fd.name}/{t['name']} {t['status']}")
    return busy


# ---------------------------------------------------------------- commands

def cmd_sync_main(a):
    """Shared repos: bring teammates' new main commits into the feature branch, only between batches.

    Merge (never rebase: no history rewrite, so the launcher's guards and pushed branches stay valid). The merge commit
    is the next batch's `base:`, so the reviewer's `git diff <base>...HEAD` shows only this feature's later commits.
    The local main is fast-forwarded too, so `git merge-base HEAD main` (batch 1, docs) excludes teammates' work.
    On a conflict the merge is aborted and nothing changes: the user resolves it.
    """
    tc, scratch, repos = ctx()
    lock, alive = teamcfg.read_lock(scratch)
    if alive:
        print("SYNC STOP a worker is running; wait for its exit notice"); return 1
    single = not tc["repos"]
    stop, report = [], []
    for name, repo in repos.items():
        br, mb = git(repo, "branch", "--show-current"), main_ref(repo)
        if not mb or not br or br in MAIN:
            report.append(f"{name}: on {br or 'detached HEAD'}, nothing to sync"); continue
        if not has_origin(repo):
            report.append(f"{name}: no remote, nothing to sync"); continue
        if git(repo, "fetch", "-q", "origin") is None:
            stop.append(f"{name}: fetch failed (offline or no access)"); continue
        new = git(repo, "rev-list", "--count", f"HEAD..origin/{mb}") or "0"
        if new == "0":
            report.append(f"{name} {br}: origin/{mb} has nothing new"); continue
        log = git(repo, "log", "--oneline", "-5", f"HEAD..origin/{mb}") or ""
        report.append(f"{name} {br}: origin/{mb} has {new} new commit(s):\n" + "\n".join("      " + l for l in log.splitlines()))
        busy = open_tickets_on(scratch, name, br, single)
        if busy:
            stop.append(f"{name}: a batch is mid-way ({', '.join(busy[:4])}); sync only when every ticket is todo/done/blocked")
            continue
        if dirty(repo):
            stop.append(f"{name}: dirty tree; not merging"); continue
        if not a.apply:
            continue
        r = subprocess.run(["git", "-C", str(repo), "merge", "--no-ff", "--no-edit", "-m",
                            f"Merge origin/{mb} into {br}", f"origin/{mb}"], capture_output=True, text=True, env=ENV)
        if r.returncode:
            files = git(repo, "diff", "--name-only", "--diff-filter=U") or ""
            git(repo, "merge", "--abort")
            stop.append(f"{name}: CONFLICT merging origin/{mb}, merge aborted, branch unchanged; the user resolves it. "
                        f"Files: {', '.join(files.split()) or '?'}"); continue
        head = git(repo, "rev-parse", "--short", "HEAD")
        if git(repo, "fetch", "-q", "origin", f"{mb}:{mb}") is None:     # fast-forward local main (not checked out)
            report.append(f"{name}: WARN local {mb} could not be fast-forwarded; pass base explicitly")
        report.append(f"{name} {br}: merged origin/{mb} -> {head}. Record base: {head} for the next batch")
    for r in report:
        print("  " + r)
    if stop:
        print("SYNC STOP " + "; ".join(stop)); return 1
    print("SYNC OK" + ("" if a.apply else " (dry run; --apply merges)"))
    return 0


def cmd_baseline(a):
    """Run the full test command before any feature commit and keep its result in <feature>/baseline.md.

    In a shared repo main may already fail (code without tests, known-broken suites). The reviewer and dev treat what is
    listed there as pre-existing, not as their findings.
    """
    tc, scratch, repos = ctx()
    plan = suites.suites(tc["cfg"])
    cmd = None if isinstance(plan, str) else plan
    fd = scratch / a.feature
    if not fd.is_dir():
        print(f"BASELINE STOP no feature folder {fd.as_posix()}"); return 1
    repo = pathlib.Path.cwd().resolve()
    top = git(repo, "rev-parse", "--show-toplevel")
    if not top or not cmd:
        print("BASELINE STOP run it from the repo root, with a `test` command in the config"); return 1
    repo = pathlib.Path(top)
    mb = main_ref(repo)
    ahead = git(repo, "rev-list", "--count", f"{mb}..HEAD") if mb else None
    if ahead not in ("0", None) and not a.force:
        print(f"BASELINE STOP the branch already has {ahead} commit(s) beyond {mb}; run it before the first ticket "
              "(or --force to record the current state)"); return 1
    results = [suites.run_suite(repo, n, c, d, a.timeout) for n, c, d in cmd]
    late = [r["name"] for r in results if r["code"] is None]
    if late:
        print(f"BASELINE STOP suite(s) {', '.join(late)} ran longer than {a.timeout}s; pass --timeout <seconds>"); return 1
    head = git(repo, "rev-parse", "--short", "HEAD")
    body = [f"# Baseline: {a.feature} ({repo.name})", "",
            f"Before the first ticket, at {head}. Failures below already existed on {mb}; they are not this feature's "
            "findings. A NEW failure is.", ""]
    for r in results:
        tail = "\n".join(r["tail"].splitlines()[-a.lines:])
        body += [f"### {r['name']}: `{r['cmd']}` in `{r['cwd']}` exited {r['code']}", "", "```text", tail, "```", ""]
    p = fd / ("baseline.md" if len(repos) <= 1 else f"baseline-{repo.name}.md")
    p.write_text("\n".join(body) + "\n" + suites.baseline_block(results), encoding="utf-8")
    codes = ",".join(f"{r['name']}={r['code']}" for r in results)
    sha = commit_scratch(scratch, f"baseline: {a.feature} {codes}")
    named = sum(len(r["failing"]) for r in results)
    print(f"BASELINE OK {codes}, {named} failing test(s) recorded -> {p.relative_to(scratch).as_posix()}"
          + (f" committed {sha}" if sha else ""))
    return 0



def cmd_init(a):
    tc, scratch, repos = ctx()
    if a.clone:
        if scratch.exists() and any(scratch.iterdir()):
            print(f"INIT STOP {scratch.as_posix()} already has files; move them away or run init without --clone")
            return 1
        r = subprocess.run(["git", "clone", "-q", a.clone, str(scratch)], capture_output=True, text=True, env=ENV)
        if r.returncode:
            print(f"INIT STOP clone failed: {r.stderr.strip()}"); return 1
    else:
        scratch.mkdir(parents=True, exist_ok=True)
        if not is_repo_root(scratch):
            git(scratch, "init", "-q", "-b", "main", check=True)
            print(f"  git init {scratch.as_posix()}")
        gi = scratch / ".gitignore"
        if not gi.is_file() or "runs/" not in gi.read_text(encoding="utf-8"):
            with open(gi, "a", encoding="utf-8") as f:
                f.write(GITIGNORE)
        if a.remote:
            cur = git(scratch, "remote", "get-url", "origin")
            if cur and cur != a.remote:
                print(f"INIT STOP origin is already {cur}; not changing it"); return 1
            if not cur:
                git(scratch, "remote", "add", "origin", a.remote, check=True)
                print(f"  remote origin = {a.remote}")
        sha = commit_scratch(scratch, "scratch: init")
        if sha:
            print(f"  committed existing scratch files: {sha}")
    # the product repo must never track the scratch folder (it holds its own .git)
    for name, repo in repos.items():
        if scratch == repo or repo in scratch.parents:
            if git(repo, "check-ignore", "-q", str(scratch)) is None:
                excl = repo / ".git" / "info" / "exclude"
                excl.parent.mkdir(parents=True, exist_ok=True)
                with open(excl, "a", encoding="utf-8") as f:
                    f.write(f"\n/{scratch.relative_to(repo).as_posix()}/\n")
                print(f"  {name}: added {scratch.relative_to(repo).as_posix()}/ to .git/info/exclude")
    remote = git(scratch, "remote", "get-url", "origin")
    print(f"INIT OK scratch repo {scratch.as_posix()}" + (f" remote {remote}" if remote else
          " (no remote: `switch` cannot carry it to another machine until you add one)"))
    return 0


def cmd_commit(a):
    _, scratch, _ = ctx()
    if not is_repo_root(scratch):
        print("COMMIT OK scratch is not a git repo (run `scratch.py init`); nothing committed"); return 0
    sha = commit_scratch(scratch, a.message)
    print(f"COMMIT OK {sha}" if sha else "COMMIT OK nothing to commit")
    return 0


def add_decision(scratch, feature, text, commit=True):
    d = scratch / feature
    if not d.is_dir():
        raise SystemExit(f"DECIDE STOP no feature folder {d.as_posix()}")
    p = d / "decisions.md"
    new = not p.is_file()
    with open(p, "a", encoding="utf-8") as f:
        if new:
            f.write(f"# Decisions: {feature}\n\nChat-made rules the files cannot show, one line each, newest last.\n\n")
        f.write(f"- {now()} {' '.join(text.split())}\n")
    return commit_scratch(scratch, f"decision: {feature}: {text[:60]}") if commit else None


def cmd_decide(a):
    _, scratch, repos = ctx()
    add_decision(scratch, a.feature, a.text, commit=False)
    rid = pl.log(scratch, a.feature, f"NOTE decide: {a.text}", "decide", repo=one_repo(repos))
    sha = commit_scratch(scratch, f"decision: {a.feature}: {a.text[:60]}")
    print(f"DECIDE OK {a.feature}/decisions.md, {rid}" + (f" committed {sha}" if sha else " (scratch not a git repo)"))
    return 0


# ---------------------------------------------------------------- check-run (trivial lane)

def launcher():
    """kit/run-worker.py as a module (its file name has a hyphen): the guards live there, shared with every worker."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_worker", pathlib.Path(__file__).resolve().parent / "run-worker.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def cmd_check_run(a):
    """The launcher's after-run guards around the planner's own trivial fix (no worker, no allow-list).

    `begin` snapshots main, branch, HEAD, remote refs, hooks/config and protected files into runs/check-run.json; `end`
    runs the same `guards_after` a worker gets, plus a clean tree, and lists the commits made in between. A check after
    the fact cannot stop a push; it makes one visible.
    """
    tc, scratch, repos = ctx()
    top = git(pathlib.Path.cwd(), "rev-parse", "--show-toplevel")
    if not top:
        print("CHECK-RUN STOP run it from the repo"); return 1
    repo = pathlib.Path(top).resolve()
    if pathlib.Path.cwd().resolve() != repo:
        os.chdir(repo)                                         # guards_after reads the cwd's git
    rw = launcher()
    protected = rw.PROTECTED + list(tc["cfg"].get("protected") or [])
    snap_file = scratch / "runs" / f"check-run-{repo.name}.json"
    if a.phase == "begin":
        held, alive = teamcfg.read_lock(scratch)
        if alive:
            print(f"CHECK-RUN STOP a worker is running ({held.get('role')} {held.get('ticket') or '-'}); wait for it")
            return 1
        branch = git(repo, "branch", "--show-current")
        if branch in MAIN:
            print(f"CHECK-RUN STOP on {branch}; fix on the feature branch"); return 1
        if dirty(repo):
            print("CHECK-RUN STOP the tree is dirty; commit or ask the user first"); return 1
        snap = rw.snapshot(protected)
        snap_file.parent.mkdir(parents=True, exist_ok=True)
        snap_file.write_text(json.dumps({k: sorted(v) if isinstance(v, set) else v for k, v in snap.items()}),
                             encoding="utf-8")
        print(f"CHECK-RUN OK begin on {branch} at {snap['head'][:8]}; make the fix, run the full suites, commit, then "
              "`check-run end`")
        return 0
    if not snap_file.is_file():
        print("CHECK-RUN STOP no `check-run begin` for this repo"); return 1
    snap = json.loads(snap_file.read_text(encoding="utf-8"))
    for k in ("tracking", "locals"):
        snap[k] = set(snap.get(k) or [])
    # JSON turned the (mode, sha) tuples into lists; git_state() compares tuples
    snap["git_state"] = {k: tuple(v) if isinstance(v, list) else v for k, v in (snap.get("git_state") or {}).items()}
    guards = rw.guards_after(snap, protected)
    if dirty(repo):
        guards.append("tree-dirty")
    commits = (git(repo, "log", "--format=%h %s", f"{snap['head']}..HEAD") or "").splitlines()
    ticket_like = [c for c in commits if re.search(r"\(\S+\.md\)$", c)]
    if ticket_like:
        guards.append("subject-ends-like-a-ticket:" + ticket_like[0].split()[0])   # resume would read it as a dev's
    for c in commits:
        print("  " + c)
    snap_file.unlink()
    if guards:
        print(f"CHECK-RUN FAIL {','.join(guards)} | {len(commits)} commit(s)"); return 1
    print(f"CHECK-RUN OK {len(commits)} commit(s), main unmoved, nothing pushed, no protected file changed, tree clean")
    return 0


# ---------------------------------------------------------------- status (plannerlog.py)

def one_repo(repos, ticket_text=None):
    r = pl.tk.field(ticket_text or "", "Repo")
    if r and not r.startswith("<"):
        return r
    return next(iter(repos), None) if len(repos) == 1 else None


def feature_dir(scratch, feature, cmd):
    fd = scratch / feature
    if not (fd / "issues").is_dir():
        print(f"{cmd} STOP no tickets in {(fd / 'issues').as_posix()}")
        return None
    return fd


def set_status(text, new):
    """Replace the value of the `Status:` line, keeping a trailing `# comment`."""
    def sub(m):
        rest = m.group(1)
        i = rest.find("  #")
        return "Status: " + new + (" " * max(2, i - len(new)) + rest[i:].lstrip() if i >= 0 else "")
    out, n = re.subn(r"^Status:[ \t]*(.*)$", sub, text, count=1, flags=re.M)
    return out if n else None


def cmd_status(a):
    _, scratch, repos = ctx()
    fd = feature_dir(scratch, a.feature, "STATUS")
    if not fd:
        return 1
    if a.status not in pl.STATUSES:
        print(f"STATUS STOP unknown status {a.status} ({', '.join(sorted(pl.STATUSES))})"); return 1
    t, why = pl.find_ticket(fd, a.ticket)
    if not t:
        print(f"STATUS STOP {why}"); return 1
    text, crlf = pl.read(t)
    old, nn = pl.tk.field(text, "Status"), pl.num(t.name)
    verdict = word = None
    if a.verify is not None:
        m = pl.VERIFY_RE.match(a.verify)
        if not m:
            print('STATUS STOP --verify must be "PASS: <what was checked>", "FAIL: <what failed>" or '
                  '"SKIP: <why not now>"'); return 1
        word, vtext = m.group(1), pl.fold(m.group(2))
        verdict = f"{word} verify {nn}: {vtext}"
    new = set_status(text, a.status)
    if new is None:
        print(f"STATUS STOP {t.name} has no Status: line"); return 1
    if word:
        new = pl.append(new, f"Planner Verify: {word} {vtext}")
    pl.write(t, new, crlf)
    rid = pl.log(scratch, a.feature, verdict or f"NOTE status {nn} {a.status}", "status", ticket=t.name,
                 repo=one_repo(repos, text))
    sha = commit_scratch(scratch, f"{nn} {a.status}" + (f" (verify {word})" if word else ""))
    print(f"STATUS OK {t.name} {old} -> {a.status}" + (f", verify {word}" if word else "") + f", {rid}"
          + (f" committed {sha}" if sha else ""))
    return 0


def cmd_stop(a):
    tc, scratch, repos = ctx()
    locks = teamcfg.read_locks(scratch)
    if not locks:
        print("STOP OK no worker running"); return 0
    live = [l for l, al in locks if al]
    for l, al in locks:
        if not al:
            pathlib.Path(l["_path"]).unlink(missing_ok=True)
    if not live:
        print(f"STOP OK no worker running (stale lock from {locks[0][0].get('stamp')}, its launcher is gone)")
        return 0
    for lock in live[1:]:                                      # parallel slots: stop every one, report the first
        stop_one(lock)
    lock = live[0]
    stop_one(lock)
    t0 = time.time()
    while time.time() - t0 < a.wait and teamcfg.read_lock(scratch)[1]:
        time.sleep(1)
    if teamcfg.read_lock(scratch)[1]:
        print(f"STOP STOP the launcher (pid {lock.get('launcher_pid')}) is still running after {a.wait}s"); return 1
    last = [l for l in ((scratch / "runs.log").read_text(encoding="utf-8").splitlines()
                        if (scratch / "runs.log").is_file() else []) if l.startswith(lock.get("stamp", "?"))]
    if last:
        print("  runs.log: " + " | ".join(last[-1].split("\t")[1:4]))
    feature, ticket = lock.get("feature") or "-", lock.get("ticket") or "-"
    sha = None
    if feature != "-" and (scratch / feature).is_dir():
        sha = add_decision(scratch, feature, f"force-stopped {lock.get('role')} {ticket} (run {lock.get('stamp')}); "
                                             "ticket left in-progress")
    repo = repos.get(lock.get("repo") or "") or next(iter(repos.values()), None)
    tree = dirty(repo) if repo else ""
    print("  product tree: " + ("clean" if not tree else "DIRTY:\n" + "\n".join("    " + l for l in tree.splitlines())))
    print(f"STOP OK {lock.get('role')} {ticket} stopped" + (f"; decision committed {sha}" if sha else "")
          + ("; next resume asks you about the dirty tree" if tree else "; next resume redispatches it"))
    return 0


def stop_one(lock):
    lock["stop_requested"] = now()
    pathlib.Path(lock["_path"]).write_text(json.dumps({k: v for k, v in lock.items() if k != "_path"}),
                                           encoding="utf-8")
    target = lock.get("worker_pid") or lock.get("launcher_pid")
    print(f"  killed {lock.get('role')} {lock.get('ticket') or '-'} worker pid {target}: {teamcfg.kill_tree(target)}")


def cmd_resume(a):
    tc, scratch, repos = ctx()
    msgs, stop = [], []
    if not scratch.is_dir():
        print(f"RESUME STOP no scratch folder {scratch.as_posix()} (other machine: `scratch.py init --clone URL`)")
        return 1
    lock, alive = teamcfg.read_lock(scratch)
    if alive:
        if not teamcfg.pid_alive(lock.get("launcher_pid")):
            print(f"RESUME STOP orphaned worker: {lock.get('role')} {lock.get('ticket') or '-'} since "
                  f"{lock.get('stamp')} still runs but its launcher is gone (no exit notice will come); ask the user, "
                  "then `scratch.py stop`"); return 1
        print(f"RESUME OK worker running: {lock.get('role')} {lock.get('ticket') or '-'} since {lock.get('stamp')}; "
              "wait for its exit notice"); return 0
    for lk, _ in teamcfg.read_locks(scratch):                  # all stale here (a live one returned above)
        msgs.append(f"stale worker lock from {lk.get('stamp')} ({lk.get('role')} {lk.get('ticket')}): the "
                    "launcher died; removed")
        pathlib.Path(lk["_path"]).unlink(missing_ok=True)
    if is_repo_root(scratch):
        sha = commit_scratch(scratch, "resume: uncommitted scratch state")
        if sha:
            msgs.append(f"scratch: committed leftover changes {sha}")
        ok, m = sync(scratch, git(scratch, "branch", "--show-current"), "scratch")
        (msgs if ok else stop).append(m)
    else:
        msgs.append("scratch: not a git repo (run `scratch.py init` to carry it between machines)")
    if stop:
        for m in msgs + stop:
            print("  " + m)
        print("RESUME STOP scratch repo diverged; show the user both sides"); return 1

    features = [a.feature] if a.feature else sorted(p.name for p in scratch.iterdir() if (p / "issues").is_dir())
    report = []
    for feat in features:
        fd = scratch / feat
        if not (fd / "issues").is_dir():
            stop.append(f"no tickets in {fd.as_posix()}/issues"); continue
        ts = tickets(fd)
        # the feature branch per repo, from the tickets' Branch / Repo lines
        wanted = {}
        for t in ts:
            if t["branch"] and not t["branch"].startswith("<"):
                rn = t["repo"] if t["repo"] and not t["repo"].startswith("<") else next(iter(repos), None)
                wanted.setdefault(rn, t["branch"])
        for rn, br in wanted.items():
            repo = repos.get(rn)
            if repo is None:
                stop.append(f"{feat}: repo {rn} not found"); continue
            cur = git(repo, "branch", "--show-current")
            if cur != br:
                exists = git(repo, "rev-parse", "--verify", "-q", br) or (
                    has_origin(repo) and git(repo, "fetch", "-q", "origin") is not None
                    and git(repo, "rev-parse", "--verify", "-q", f"origin/{br}"))
                if not exists:
                    msgs.append(f"{rn}: branch {br} does not exist yet (step 1 creates it)"); continue
                if dirty(repo):
                    stop.append(f"{rn}: on {cur} with a dirty tree; cannot check out {br}"); continue
                git(repo, "checkout", "-q", br, check=True)      # creates a tracking branch from origin/<br> if needed
                msgs.append(f"{rn}: checked out {br} (was {cur})")
            ok, m = sync(repo, br, f"{rn} {br}")
            (msgs if ok else stop).append(m)
            for f in foreign_commits(repo):
                msgs.append(f"WARN {rn} {br}: commit by someone else on the feature branch: {f} "
                            "(one feature branch per person; show the user)")
        for rn, br in wanted.items():                          # D12: parallel ticket branches left by a crash
            repo = repos.get(rn)
            if repo is not None and git(repo, "branch", "--show-current") == br:
                pending_ticket_branches(repo, br, ts, msgs, stop)
        counts = {}
        for t in ts:
            counts[t["status"]] = counts.get(t["status"], 0) + 1
        report.append(f"{feat}: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items(), key=lambda x: str(x[0]))))
        for t in ts:
            if t["status"] != "in-progress":
                continue
            rn = t["repo"] if t["repo"] and not t["repo"].startswith("<") else next(iter(repos), None)
            repo = repos.get(rn)
            if repo is None:
                continue
            hits, runs = commit_for(repo, t["name"]), runs_for(scratch, t["name"])
            last = runs[-1].split("\t")[3] if runs else "no run logged"
            tree = dirty(repo)
            if hits:
                action = f"VERIFY (commit {hits[0].split()[0]} found): run step 6, then set review"
            elif tree:
                action = "ASK the user: no commit and the tree is dirty:\n" + \
                         "\n".join("      " + l for l in tree.splitlines())
                stop.append(f"{feat}/{t['name']}: dirty tree")
            else:
                action = "REDISPATCH (no commit, tree clean)"
            report.append(f"  {t['name']} in-progress | last run: {last} | {action}")
        dec = fd / "decisions.md"
        if dec.is_file():
            lines = [l for l in dec.read_text(encoding="utf-8").splitlines() if l.startswith("- ")]
            report.append(f"  decisions.md: {len(lines)} line(s)" + "".join(f"\n    {l}" for l in lines[-10:]))
        bf = fd / "batches.md"
        if bf.is_file():
            report.append("  batches.md: " + "; ".join(l.strip() for l in bf.read_text(encoding="utf-8").splitlines()
                                                      if l.strip() and not l.startswith("#")))
    for m in msgs:
        print("  " + m)
    for r in report:
        print(r)
    if stop:
        print("RESUME STOP " + "; ".join(stop)); return 1
    print("RESUME OK")
    return 0


def pending_ticket_branches(repo, br, ts, msgs, stop):
    """D12: a `<br>--NN` branch left by an interrupted `dispatch`. Its slot dirty: ask. A dev commit for its ticket on
    it: merge it now (as dispatch would have). Otherwise: report; the ticket is redispatched."""
    names = {pl.num(t["name"]): t["name"] for t in ts}
    slots = {}
    for block in (git(repo, "worktree", "list", "--porcelain") or "").split("\n\n"):
        path = re.search(r"^worktree (.+)$", block, re.M)
        b = re.search(r"^branch refs/heads/(.+)$", block, re.M)
        if path and b:
            slots[b.group(1)] = pathlib.Path(path.group(1))
    for tb in (git(repo, "for-each-ref", "--format=%(refname:short)", f"refs/heads/{br}--*") or "").split():
        nn = tb[len(br) + 2:]
        name = names.get(nn)
        slot = slots.get(tb)
        if slot and slot != repo and dirty(slot):
            stop.append(f"{tb}: its slot {slot.name} has uncommitted changes (ask the user; never reset)"); continue
        own = git(repo, "log", "--format=%h %s", f"{br}..{tb}") or ""
        if not name or not any(l.rstrip().endswith(f"({name})") for l in own.splitlines()):
            msgs.append(f"{tb}: no dev commit for ticket {nn}; left as is (redispatch the ticket)"); continue
        if dirty(repo):
            stop.append(f"{tb}: finished but the feature tree is dirty; not merging"); continue
        if slot and slot != repo:
            git(slot, "checkout", "-q", "--detach")
        state, detail = parallel.merge_ticket(repo, br, tb, name)
        if state == "merged":
            msgs.append(f"{tb}: finished before the interruption; merged into {br} -> {detail}")
        elif state == "conflict":
            msgs.append(f"{tb}: CONFLICT merging into {br} ({', '.join(detail)}); branch kept, redispatch {nn}")
        else:
            msgs.append(f"{tb}: nothing to merge")


def cmd_dispatch(a):
    """Parallel dev workers (0.17.0): one pool slot per ticket, merged in ticket order. Last line DISPATCH OK|STOP."""
    tc, scratch, repos = ctx()
    cfg = tc["cfg"]
    fd = feature_dir(scratch, a.feature, "DISPATCH")
    if not fd:
        return 1
    max_w = int(cfg.get("max_workers") or 1)
    if len(a.tickets) < 2:
        print("DISPATCH STOP give at least two tickets (one ticket: the usual run-worker.py dev)"); return 1
    if len(a.tickets) > max_w:
        print(f"DISPATCH STOP {len(a.tickets)} tickets but .team.json max_workers is {max_w}"); return 1
    if not cfg.get("setup"):
        print("DISPATCH STOP .team.json has no `setup` (the commands that install dependencies in a fresh worktree)")
        return 1
    if tc["source"] is None:
        print("DISPATCH STOP no .team.json found"); return 1
    held, alive = teamcfg.read_lock(scratch)
    if alive:
        print(f"DISPATCH STOP a worker is running ({held.get('role')} {held.get('ticket') or '-'})"); return 1
    items, seams, repo_names, branches = [], [], set(), set()
    group = {nn.split("-")[0] for nn in a.tickets}
    for nn in a.tickets:
        t, why = pl.find_ticket(fd, nn)
        if not t:
            print(f"DISPATCH STOP {why}"); return 1
        text, _ = pl.read(t)
        st, rn, br = field(text, "Status"), one_repo(repos, text), field(text, "Branch")
        if st not in ("todo", "in-progress"):
            print(f"DISPATCH STOP {t.name} is {st} (only todo or in-progress tickets run)"); return 1
        if field(text, "Blocked by") not in (None, "None", "none", "-"):
            blockers = re.findall(r"\d+[a-z]?", field(text, "Blocked by") or "")
            open_b = [b for b in blockers if any(pl.num(x["name"]) == b and x["status"] != "done" for x in tickets(fd))]
            inside = [b for b in blockers if b in group]
            if open_b or inside:
                print(f"DISPATCH STOP {t.name} is blocked by {', '.join(open_b or inside)}"); return 1
        items.append((pl.num(t.name), t)); seams.append((pl.num(t.name), parallel.seam_paths(field(text, "Seam"))))
        repo_names.add(rn); branches.add(br)
    if len(repo_names) != 1 or None in repo_names:
        print(f"DISPATCH STOP the tickets must share one repo (got {', '.join(map(str, repo_names))})"); return 1
    if len(branches) != 1 or None in branches:
        print(f"DISPATCH STOP the tickets must share one Branch (got {', '.join(map(str, branches))})"); return 1
    rn, br = repo_names.pop(), branches.pop()
    repo = repos.get(rn)
    clash = parallel.seam_conflicts(seams)
    if clash:
        for x, y, why in clash:
            print(f"  {x} and {y} overlap: {why}")
        print("DISPATCH STOP Seams overlap (D3): run those tickets one after another"); return 1
    if git(repo, "branch", "--show-current") != br:
        print(f"DISPATCH STOP {rn} is not on {br}"); return 1
    if dirty(repo):
        print(f"DISPATCH STOP {rn} has uncommitted changes"); return 1
    print(f"  group {' '.join(nn for nn, _ in items)} on {rn} {br}, Seams separate")
    if a.dry_run:
        print("DISPATCH OK dry run (nothing started)"); return 0
    for nn, t in items:
        text, crlf = pl.read(t)
        if field(text, "Status") != "in-progress":
            pl.write(t, set_status(text, "in-progress"), crlf)
    report = parallel.run_group(repo, br, [(nn, t) for nn, t in items], tc["source"], rn, cfg["setup"],
                                scratch / "runs", extra=a.extra or [], say=print)
    paused, failed = False, []
    for nn, line, state, detail in report:
        print(line)
        if state == "merged":
            print(f"  {nn}: merged into {br} -> {detail}")
            pl.log(scratch, a.feature, f"NOTE parallel {nn} merged {detail}", "merge", ticket=None, repo=rn)
        elif state == "conflict":
            print(f"  {nn}: FAIL conflict merging into {br} ({', '.join(detail)}); branch kept, redispatch from the "
                  "new tip")
            failed.append(nn)
        elif state == "empty":
            print(f"  {nn}: PASS but no commit on its branch; nothing merged")
            failed.append(nn)
        else:
            failed.append(nn)
            paused = paused or "usage-limit" in line
            print(f"  {nn}: not merged; branch {detail} kept" if detail else f"  {nn}: not run")
    sha = commit_scratch(scratch, f"dispatch {' '.join(nn for nn, _ in items)} (parallel)")
    tail = f"; scratch committed {sha}" if sha else ""
    if paused:
        print(f"DISPATCH STOP usage limit hit: pause the queue until the limit resets{tail}"); return 1
    if failed:
        print(f"DISPATCH OK with failures: {' '.join(failed)} (fix or redispatch){tail}"); return 0
    print(f"DISPATCH OK {len(items)} merged into {br}{tail}")
    return 0


def cmd_switch(a):
    tc, scratch, repos = ctx()
    lock, alive = teamcfg.read_lock(scratch)
    if alive:
        print(f"SWITCH STOP a worker is running ({lock.get('role')} {lock.get('ticket') or '-'}); wait for its exit "
              "notice, or `scratch.py stop` first"); return 1
    stop, warn, plan = [], [], []
    for name, repo in repos.items():
        br = git(repo, "branch", "--show-current")
        tree = dirty(repo)
        if tree:
            stop.append(f"{name}: uncommitted changes (the user discards them or commits a WIP; the planner never "
                        "commits worker code):\n" + "\n".join("    " + l for l in tree.splitlines()))
            continue
        if br in MAIN or not br:
            warn.append(f"{name}: on {br or 'a detached HEAD'}, nothing pushed (main is never pushed)"); continue
        if not has_origin(repo):
            warn.append(f"{name}: no remote; {br} stays on this machine"); continue
        plan.append((name, repo, br))
    if stop:
        for s in stop:
            print("  " + s)
        print("SWITCH STOP uncommitted product changes; nothing pushed"); return 1
    pushed = []
    if is_repo_root(scratch):
        sha = commit_scratch(scratch, "switch: scratch state")
        if sha:
            print(f"  scratch: committed {sha}")
        if has_origin(scratch):
            plan.insert(0, ("scratch", scratch, git(scratch, "branch", "--show-current")))
        else:
            warn.append("scratch: no remote; tickets, decisions and runs.log stay on this machine "
                        "(create a private repo, then `scratch.py init --remote URL`)")
    else:
        warn.append("scratch: not a git repo; run `scratch.py init` first")
    for name, repo, br in plan:
        # never --force: a rejected push means the other machine pushed meanwhile; resume will report the divergence
        r = subprocess.run(["git", "-C", str(repo), "push", "-u", "origin", f"{br}:{br}"], capture_output=True,
                           text=True, env=ENV)
        if r.returncode:
            stop.append(f"{name}: push of {br} rejected: {(r.stderr or '').strip().splitlines()[-1:]}")
        else:
            pushed.append(f"{name}:{br}@{git(repo, 'rev-parse', '--short', 'HEAD')}")
    for w in warn:
        print("  WARN " + w)
    if stop:
        print("SWITCH STOP " + "; ".join(stop)); return 1
    print("SWITCH OK pushed " + (", ".join(pushed) or "nothing")
          + ". On the other machine: pull, then start the planner (orchestrate step 0 resumes)")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.add_argument("--remote"); p.add_argument("--clone")
    p = sub.add_parser("resume"); p.add_argument("feature", nargs="?")
    p = sub.add_parser("commit"); p.add_argument("message")
    p = sub.add_parser("decide"); p.add_argument("feature"); p.add_argument("text")
    p = sub.add_parser("status"); p.add_argument("feature"); p.add_argument("ticket"); p.add_argument("status")
    p.add_argument("--verify")
    p = sub.add_parser("stop"); p.add_argument("--wait", type=int, default=60)
    sub.add_parser("switch")
    p = sub.add_parser("sync-main"); p.add_argument("--apply", action="store_true")
    p = sub.add_parser("baseline"); p.add_argument("feature"); p.add_argument("--force", action="store_true")
    p.add_argument("--lines", type=int, default=60); p.add_argument("--timeout", type=int, default=900)
    p = sub.add_parser("check-run"); p.add_argument("phase", choices=["begin", "end"])
    p = sub.add_parser("dispatch"); p.add_argument("feature"); p.add_argument("tickets", nargs="+")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--extra", nargs=argparse.REMAINDER, help="passed on to every run-worker.py (e.g. --model sonnet)")
    a = ap.parse_args()
    return {"init": cmd_init, "resume": cmd_resume, "commit": cmd_commit, "decide": cmd_decide, "stop": cmd_stop,
            "switch": cmd_switch, "sync-main": cmd_sync_main, "baseline": cmd_baseline, "status": cmd_status,
            "check-run": cmd_check_run, "dispatch": cmd_dispatch}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
