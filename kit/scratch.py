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
    python <kit>/scratch.py stop                    # force-stop the running worker; logs it in decisions.md
    python <kit>/scratch.py switch                  # push scratch + feature branch(es) for the other machine
    python <kit>/scratch.py sync-main [--apply]     # shared repo, between batches: merge origin/main into the branch
    python <kit>/scratch.py baseline <feature>      # before the first ticket: record what already fails on main

Last line: `<COMMAND> OK ...` (exit 0) or `<COMMAND> STOP <reason>` (exit 1): a STOP needs the user.
"""
import argparse, datetime, json, os, pathlib, re, subprocess, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import teamcfg  # noqa: E402

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
    cmd = tc["cfg"].get("test")
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
    try:
        r = subprocess.run(cmd, shell=True, cwd=repo, capture_output=True, text=True, env=ENV, timeout=a.timeout)
    except subprocess.TimeoutExpired:
        print(f"BASELINE STOP `{cmd}` ran longer than {a.timeout}s; pass --timeout <seconds>"); return 1
    tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-a.lines:])
    p = fd / ("baseline.md" if len(repos) <= 1 else f"baseline-{repo.name}.md")
    p.write_text(f"# Baseline: {a.feature} ({repo.name})\n\nBefore the first ticket, at {git(repo, 'rev-parse', '--short', 'HEAD')}: "
                 f"`{cmd}` exited {r.returncode}. Failures below already existed on {mb}; they are not this feature's "
                 f"findings. A NEW failure is.\n\n```text\n{tail}\n```\n", encoding="utf-8")
    sha = commit_scratch(scratch, f"baseline: {a.feature} exit {r.returncode}")
    print(f"BASELINE OK exit {r.returncode} -> {p.relative_to(scratch).as_posix()}" + (f" committed {sha}" if sha else ""))
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


def add_decision(scratch, feature, text):
    d = scratch / feature
    if not d.is_dir():
        raise SystemExit(f"DECIDE STOP no feature folder {d.as_posix()}")
    p = d / "decisions.md"
    new = not p.is_file()
    with open(p, "a", encoding="utf-8") as f:
        if new:
            f.write(f"# Decisions: {feature}\n\nChat-made rules the files cannot show, one line each, newest last.\n\n")
        f.write(f"- {now()} {' '.join(text.split())}\n")
    return commit_scratch(scratch, f"decision: {feature}: {text[:60]}")


def cmd_decide(a):
    _, scratch, _ = ctx()
    sha = add_decision(scratch, a.feature, a.text)
    print(f"DECIDE OK {a.feature}/decisions.md" + (f" committed {sha}" if sha else " (scratch not a git repo)"))
    return 0


def cmd_stop(a):
    tc, scratch, repos = ctx()
    lock, alive = teamcfg.read_lock(scratch)
    if not lock:
        print("STOP OK no worker running"); return 0
    if not alive:
        print(f"STOP OK no worker running (stale lock from {lock.get('stamp')}, its launcher is gone)")
        teamcfg.lock_path(scratch).unlink(missing_ok=True)
        return 0
    lock["stop_requested"] = now()
    teamcfg.lock_path(scratch).write_text(json.dumps(lock), encoding="utf-8")
    target = lock.get("worker_pid") or lock.get("launcher_pid")
    killed = teamcfg.kill_tree(target)
    print(f"  killed {lock.get('role')} worker pid {target}: {killed}")
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
    if lock:
        msgs.append(f"stale worker lock from {lock.get('stamp')} ({lock.get('role')} {lock.get('ticket')}): the "
                    "launcher died; removed")
        teamcfg.lock_path(scratch).unlink(missing_ok=True)
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
    p = sub.add_parser("stop"); p.add_argument("--wait", type=int, default=60)
    sub.add_parser("switch")
    p = sub.add_parser("sync-main"); p.add_argument("--apply", action="store_true")
    p = sub.add_parser("baseline"); p.add_argument("feature"); p.add_argument("--force", action="store_true")
    p.add_argument("--lines", type=int, default=60); p.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args()
    return {"init": cmd_init, "resume": cmd_resume, "commit": cmd_commit, "decide": cmd_decide, "stop": cmd_stop,
            "switch": cmd_switch, "sync-main": cmd_sync_main, "baseline": cmd_baseline}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
