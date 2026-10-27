"""Parallel dev workers (0.17.0, docs/open/parallel-workers.md D1-D13). Used by `scratch.py dispatch` and `resume`.

A group of dev tickets of one batch runs at once, one per pool slot: a sibling worktree `<repo>.wN` on a local ticket
branch `<feature branch>--<NN>` started from the feature branch tip. When every worker is done, each PASS branch is
merged into the feature branch in ticket order (`--no-ff`); a conflict aborts that merge and the ticket is reported
`FAIL conflict` (its branch kept, the planner redispatches it from the new tip). A merged ticket branch is deleted with
`git branch -d`. Nothing is pushed. No model is called here: the workers are ordinary `run-worker.py dev` runs.
"""
import fnmatch, hashlib, os, pathlib, re, subprocess, sys, threading

KIT = pathlib.Path(__file__).resolve().parent
ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
LOCKFILES = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "uv.lock", "poetry.lock",
             "requirements.txt", "Pipfile.lock", "go.sum", "Cargo.lock", "composer.lock", "Gemfile.lock")


def git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=ENV,
                       encoding="utf-8", errors="replace")
    return (r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip())


def out(cwd, *args):
    rc, so, _ = git(cwd, *args)
    return so if rc == 0 else None


# ------------------------------------------------------------------ seams (D3)

PATH_RE = re.compile(r"`([^`]+)`")


def seam_paths(seam):
    """Repo paths named in a ticket's Seam line: every backticked token that looks like a path (has a `/` or a dot
    extension). Trailing `:line`, `()` and ` (comment)` are dropped."""
    paths = []
    toks = PATH_RE.findall(seam or "") or re.split(r"[\s,;]+", seam or "")   # field() strips a lone token's backticks
    for tok in toks:
        tok = tok.strip().strip("`").split(" ")[0].split(":")[0].rstrip("()").strip("`")
        if tok and ("/" in tok or re.search(r"\.[A-Za-z0-9]+$", tok)) and not tok.startswith(("http", "-")):
            paths.append(tok.removeprefix("./"))
    return paths


def overlaps(a, b):
    """True when two seam paths can name the same file: equal, one a folder of the other, or a glob that matches."""
    if a == b or fnmatch.fnmatch(a, b) or fnmatch.fnmatch(b, a):
        return True
    a2, b2 = a.rstrip("/") + "/", b.rstrip("/") + "/"
    return b.startswith(a2) or a.startswith(b2)


def seam_conflicts(tickets):
    """[(nnA, nnB, path)] for every pair of tickets whose Seam paths overlap; a ticket with no path is a conflict
    with every other (nothing proves it is separate)."""
    found = []
    for i, (na, pa) in enumerate(tickets):
        for nb, pb in tickets[i + 1:]:
            if not pa or not pb:
                found.append((na, nb, "no file path in Seam"))
                continue
            hit = next(((x, y) for x in pa for y in pb if overlaps(x, y)), None)
            if hit:
                found.append((na, nb, hit[0] if hit[0] == hit[1] else f"{hit[0]} ~ {hit[1]}"))
    return found


# ------------------------------------------------------------------ pool (D5, D6)

def slot_path(repo, n):
    repo = pathlib.Path(repo)
    return repo.parent / f"{repo.name}.w{n}"


def lock_digest(tree):
    """Fingerprint of the dependency lock files in a tree (D6: a change means install again)."""
    parts = []
    for name in LOCKFILES:
        for p in sorted(pathlib.Path(tree).rglob(name)):
            if "node_modules" in p.parts or ".venv" in p.parts or ".git" in p.parts:
                continue
            parts.append(f"{p.relative_to(tree).as_posix()}:{hashlib.sha1(p.read_bytes()).hexdigest()}")
    return hashlib.sha1("\n".join(parts).encode()).hexdigest()[:12]


def run_setup(slot, setup):
    """Run the `.team.json` `setup` commands in a slot. Returns None or an error line."""
    for step in setup:
        cmd = step["cmd"] if isinstance(step, dict) else step
        cwd = slot / (step.get("cwd", "") if isinstance(step, dict) else "")
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, env=ENV)
        if r.returncode:
            return f"setup `{cmd}` failed in {cwd}: {(r.stderr or r.stdout).strip()[-300:]}"
    return None


def prepare_slot(repo, n, branch, base, setup, state_dir):
    """Create or reuse slot N, put it on `branch` at `base`, install when needed. Returns (path, error|None, note)."""
    slot = slot_path(repo, n)
    note = []
    if not slot.exists():
        rc, _, err = git(repo, "worktree", "add", "-q", "--detach", str(slot), base)
        if rc:
            return slot, f"worktree add failed: {err}", ""
        note.append("created")
    elif out(slot, "rev-parse", "--show-toplevel") is None:
        return slot, f"{slot} exists but is not a git worktree; move it away", ""
    if out(slot, "status", "--porcelain"):
        return slot, f"slot {n} has uncommitted changes (a failed earlier run?); look, then clean it by hand", ""
    rc, _, err = git(slot, "checkout", "-q", "-B", branch, base)
    if rc:
        return slot, f"checkout {branch} failed: {err}", ""
    stamp = pathlib.Path(state_dir) / f"slot-{n}.deps"
    digest = lock_digest(slot)
    if not stamp.is_file() or stamp.read_text().strip() != digest:
        e = run_setup(slot, setup)
        if e:
            return slot, e, ""
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(digest)
        note.append("installed")
    return slot, None, ",".join(note)


# ------------------------------------------------------------------ run + merge (D4, D7, D8, D10)

def run_worker(slot, n, ticket_path, cfg_file, repo_name, extra, results, i):
    env = {**os.environ, "HDT_CONFIG": str(cfg_file), "HDT_SLOT_OF": repo_name, "PYTHONUNBUFFERED": "1"}
    cmd = [sys.executable, "-u", str(KIT / "run-worker.py"), "dev", f"ticket: {pathlib.Path(ticket_path).as_posix()}",
           "--slot", str(n), *extra]
    p = subprocess.run(cmd, cwd=slot, capture_output=True, text=True, env=env, encoding="utf-8", errors="replace")
    lines = [l for l in (p.stdout or "").splitlines() if l.startswith("RESULT ")]
    results[i] = lines[-1] if lines else f"RESULT dev FAIL launcher exited {p.returncode}: " + \
        " ".join(((p.stderr or p.stdout or "").strip().splitlines() or ["no output"])[-1:])


def verdict_word(result_line):
    m = re.match(r"RESULT \w+ (PASS|FAIL|ERROR)", result_line or "")
    return m.group(1) if m else "FAIL"


def merge_ticket(repo, feature_branch, ticket_branch, ticket_name):
    """Merge one ticket branch into the checked-out feature branch. ('merged', sha) | ('conflict', files) |
    ('empty', None) when the branch has no commit of its own."""
    if (out(repo, "rev-list", "--count", f"{feature_branch}..{ticket_branch}") or "0") == "0":
        return "empty", None
    rc, _, _ = git(repo, "merge", "--no-ff", "--no-edit", "-m",
                   f"Merge {ticket_branch} into {feature_branch} (parallel, {ticket_name})", ticket_branch)
    if rc:
        files = out(repo, "diff", "--name-only", "--diff-filter=U") or ""
        git(repo, "merge", "--abort")
        return "conflict", files.split()
    git(repo, "branch", "-d", ticket_branch)                  # -d refuses an unmerged branch: nothing can be lost
    return "merged", out(repo, "rev-parse", "--short", "HEAD")


def ticket_branch(feature_branch, nn):
    return f"{feature_branch}--{nn}"


def run_group(repo, feature_branch, items, cfg_file, repo_name, setup, state_dir, extra=(), say=print):
    """items: [(nn, ticket_path)]. Returns [(nn, result_line, merge_state, detail)]."""
    base = out(repo, "rev-parse", "HEAD")
    if not base:
        return [(nn, "RESULT dev ERROR cannot read the feature branch tip", "skipped", None) for nn, _ in items]
    slots, errors = [], []
    for k, (nn, tp) in enumerate(items, start=1):
        slot, err, note = prepare_slot(repo, k, ticket_branch(feature_branch, nn), base, setup, state_dir)
        if err:
            errors.append((nn, err))
        slots.append(slot)
        say(f"  slot {k}: {slot.name} {ticket_branch(feature_branch, nn)} @ {base[:8]}" + (f" ({note})" if note else "")
            + (f" ERROR {err}" if err else ""))
    if errors:
        return [(nn, f"RESULT dev ERROR slot not ready: {e}", "skipped", None) for nn, e in errors]
    results = [None] * len(items)
    threads = [threading.Thread(target=run_worker, args=(slots[i], i + 1, tp, cfg_file, repo_name, list(extra),
                                                         results, i)) for i, (nn, tp) in enumerate(items)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    report = []
    for i, (nn, tp) in enumerate(items):
        line, tb = results[i], ticket_branch(feature_branch, nn)
        git(slots[i], "checkout", "-q", "--detach")                # free the ticket branch for the merge / -d
        if verdict_word(line) != "PASS":
            report.append((nn, line, "kept", tb)); continue      # D8: no merge; branch kept to look at
        state, detail = merge_ticket(repo, feature_branch, tb, pathlib.Path(tp).name)
        report.append((nn, line, state, detail))
    return report
