#!/usr/bin/env python3
"""Run one headless worker in a project repo, on the claude or hermes backend (kit/backends.py).

Usage (cwd = the project repo root):
    python <kit>/run-worker.py <role> "<prompt>" [--test-cmd "<cmd>"] [--model M] [--max-turns N] [--backend B]
    python <kit>/run-worker.py researcher "questions: <file> out: <file> source: <folder>" [--web]
        (cwd = a project repo or the research home; may run on main, it never commits)

Config: the repo's .team.json, or a workspace ../.team.json whose "repos" map names this folder (see teamcfg.py).
The allowed test command is found in this order: --test-cmd; for dev, the ticket's `Test command:` line
(prompt `ticket: <path>`); then `"test"` in the config. Models can be set per role with `"models": {"dev": "sonnet"}`.
`ticket:` and `feature:` paths may point outside the repo (a workspace scratch folder): the launcher grants the worker
read/write on the scratch folder only and tells it where that folder is. A ticket with a `Repo:` line refuses to run
from any other folder.

Roles live next to this file: roles/<role>.md (prompt) and roles/<role>.json (claude allow-list).
While the worker runs, prints one progress line per tool call (and per permission denial) and writes the same lines
to .scratch/runs/<stamp>-<role>.progress.log. The LAST line printed is always the RESULT line; exit 0 only when the
worker's last line is `RESULT: PASS` and the guards hold. The guards run after every run on both backends; they are
the only enforcement the hermes backend has.
Every run appends a line to .scratch/runs.log and keeps the final result event (JSON) in .scratch/runs/.
"""
import argparse, datetime, fnmatch, hashlib, json, os, pathlib, re, subprocess, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import teamcfg  # noqa: E402
import backends  # noqa: E402

KIT = pathlib.Path(__file__).resolve().parent
DEFAULTS = {"dev": ("sonnet", 40), "reviewer": ("sonnet", 35), "docs": ("haiku", 25), "researcher": ("sonnet", 40)}
# Files that decide what the allowed test command runs. If a worker could edit them, the test command would run
# anything (e.g. a rewritten "test" script). Denied in the dev's allow-list AND checked after every run (guard).
# A project adds more with .team.json "protected": ["glob", ...]; globs match the file name at any depth.
PROTECTED = ["package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
             "jest.config.*", "vitest.config.*", "tsconfig*.json", "babel.config.*", ".babelrc",
             "pyproject.toml", "setup.cfg", "pytest.ini", "tox.ini", "Makefile", ".team.json"]
MAX_IMPORT_DEPTH = 5


def git(*args):
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def project_memory(path, depth=0, seen=None):
    """Return CLAUDE.md with its `@path` import lines inlined.

    Measured: with --setting-sources "" the worker does not load the project's CLAUDE.md at all, and an import such as
    `@../other-repo/CLAUDE.md` points outside the repo, where the worker may not read. So the launcher inlines it.
    """
    seen = seen if seen is not None else set()
    path = path.resolve()
    if depth > MAX_IMPORT_DEPTH or path in seen or not path.is_file():
        return ""
    seen.add(path)
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.fullmatch(r"\s*@(\S+)\s*", line)
        if m:
            target = (path.parent / pathlib.Path(m.group(1)).expanduser()).resolve()
            if target in seen:                        # already pasted (e.g. CLAUDE.md that is just `@AGENTS.md`)
                continue
            body = project_memory(target, depth + 1, seen)
            out.append(f"<!-- imported from {m.group(1)} -->\n{body}" if body else line)
        else:
            out.append(line)
    return "\n".join(out)


def protected_changes(head_before, patterns):
    """Protected files changed by commits since head_before, or left modified in the tree."""
    names = (git("diff", "--name-only", f"{head_before}..HEAD") or "").splitlines() if head_before else []
    names += [l[3:].strip().strip('"') for l in (git("status", "--porcelain") or "").splitlines() if l.strip()]
    return sorted({n for n in names if any(fnmatch.fnmatch(pathlib.PurePosixPath(n).name, p) for p in patterns)})


def project_rules(files, cap):
    """Rules pasted into every worker prompt: the files named by `rules` (default AGENTS.md + CLAUDE.md), imports inlined, capped.

    Measured: a 150k-char CLAUDE.md was pasted whole into every run. Past the cap the text is cut and the worker is
    told where the full file is, so it can Grep it (files inside the repo only; the worker cannot read elsewhere).
    """
    seen = set()                                      # shared, so a file is pasted once even if imported again
    parts = [(f, project_memory(f, seen=seen)) for f in files]
    parts = [(f, b) for f, b in parts if b.strip()]
    text = "\n\n".join(b.strip() for _, b in parts)
    full = len(text)
    if full > cap:
        where = ", ".join(f.name if f.parent == pathlib.Path.cwd().resolve() else f.as_posix() for f, _ in parts)
        text = text[:cap].rsplit("\n", 1)[0] + (f"\n\n[cut at {cap} of {full} chars; the full text is in {where}: "
                                               "Grep it for the section you need]")
    return text, full, [f.name for f, _ in parts]


def header_line(ticket, key):
    """Value of a plain `Key: value` header line in a ticket, or None (placeholders like `<...>` count as None)."""
    if not ticket or not ticket.is_file():
        return None
    t = re.search(rf"^{key}:\s*(.+?)\s*$", ticket.read_text(encoding="utf-8"), re.M)
    v = t.group(1).split("  #")[0].strip().strip("`").strip() if t else ""
    return v if v and not v.startswith("<") else None


def prompt_path(prompt, key):
    m = re.search(rf"{key}:\s*(\S+)", prompt)
    return pathlib.Path(m.group(1)).expanduser().resolve() if m else None


def rel(p):
    """Repo-relative path for printing when the file is inside the repo, else absolute."""
    try:
        return pathlib.Path(p).resolve().relative_to(pathlib.Path.cwd().resolve()).as_posix()
    except ValueError:
        return pathlib.Path(p).as_posix()


def perm_path(p):
    """An absolute path in Claude Code permission-rule form: `//` + the POSIX path.

    Claude Code normalises Windows paths to POSIX before matching (`C:\\Users\\a` -> `/c/Users/a`), so the rule for a
    Windows folder is `//c/Users/a/**`; on Linux and macOS it is `//home/a/**`.
    """
    s = p.as_posix() if isinstance(p, pathlib.PurePath) else str(p)
    if re.match(r"^[A-Za-z]:[\\/]", s):                  # Windows drive path (backslashes if not a WindowsPath)
        s = s.replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    if m:
        s = f"/{m.group(1).lower()}/{m.group(2)}"
    return "/" + s


def scratch_rule(rule, scratch, repo):
    """Copy a role's `Tool(./.scratch/...)` rule onto a scratch folder outside the repo (`//` = absolute path)."""
    m = re.fullmatch(r"(\w+)\(\./\.scratch(/.*)?\)", rule)
    if not m or scratch == repo / ".scratch":
        return None
    return f"{m.group(1)}({perm_path(scratch)}{m.group(2) or ''})"


def anchor_rules(settings, repo):
    """Rewrite every `Tool(./x)` and `Tool(**/x)` permission rule onto the absolute repo path (`Tool(//<repo>/x)`).

    measured (Claude Code 2.1.302): both forms resolve from the worker shell's CURRENT folder, not the repo root. After
    an allowed `cd ui && npx vitest run`, `Edit(./**)` only matched `ui/**`: the ticket's `## Result`,
    `.scratch/dev-notes.md` and code outside `ui/` were denied (27 of 36 dev runs on one feature); after `cd src`,
    `Edit(**/package.json)` no longer denied the root package.json, and `Edit(./.git/**)` pointed at `src/.git`. Written
    absolute, every rule held after the `cd`.
    """
    root = perm_path(repo)
    for key in ("allow", "deny"):
        settings["permissions"][key] = [re.sub(r"^(\w+)\((\./|\*\*/)",
                                               lambda m: f"{m.group(1)}({root}/{'**/' if m.group(2) == '**/' else ''}", r)
                                        for r in settings["permissions"].get(key, [])]


RAW_GIT_READ = re.compile(r"Bash\(git (log|diff|show)\b")


def suites_cmd(feature):
    """The full-suite command a worker runs: kit/suites.py runs every `.team.json` suite in its own folder and judges
    failures against the feature's baseline (a `a && cd ui && b` chain stopped at baseline errors before `b`)."""
    return (f"{pathlib.Path(sys.executable).as_posix()} {(KIT / 'suites.py').as_posix()}"
            + (f" --feature {feature}" if feature and feature != "-" else ""))


def git_ro_cmd():
    """The exact command prefix a worker types for read-only git (kit/git-ro.py), forward slashes."""
    return f"{pathlib.Path(sys.executable).as_posix()} {(KIT / 'git-ro.py').as_posix()}"


def read_only_git(settings):
    """Swap a role's raw `Bash(git log|diff|show:*)` grants for the kit's git-ro wrapper; return the subcommands.

    measured (Claude Code 2.1.296, git 2.56): with `Bash(git log:*)` allowed, `git log --format=format:<text>
    --output=<path>` ran with 0 denials and wrote <path>, also under `.git/hooks/` although `Edit(./.git/**)` was
    denied; `git diff --output` and `git show --output` the same. A prefix rule cannot stop a trailing flag, so the
    flag check lives in git-ro.py.
    """
    perms = settings["permissions"]
    subs = sorted({m.group(1) for r in perms["allow"] for m in [RAW_GIT_READ.match(r)] if m})
    perms["allow"] = [r for r in perms["allow"] if not RAW_GIT_READ.match(r)]
    perms["allow"] += [f"Bash({git_ro_cmd()} {s}:*)" for s in subs]
    return subs


def research_paths(prompt):
    """(questions, out, source) from a researcher prompt, or an error string."""
    q, out, src = (prompt_path(prompt, k) for k in ("questions", "out", "source"))
    if not (q and out and src):
        return "prompt needs questions: <file> out: <file> source: <folder>"
    if not q.is_file():
        return f"no questions file {q.as_posix()}"
    if not src.is_dir():
        return f"no source folder {src.as_posix()}"
    if out.parent.resolve() != q.parent.resolve():
        return "out: must sit next to questions: (one topic folder)"
    return q, out, src


def research_grants(settings, paths, web):
    """Claude allow-list for the researcher: read the source and the questions, write ONLY the findings file.

    The researcher runs without Bash (0.13.1: a `git log:*` grant could write files through `--output`).
    """
    q, out, src = paths
    settings["permissions"]["allow"] += [f"Read({perm_path(src)}/**)", f"Read({perm_path(q)})",
                                         f"Read({perm_path(out)})", f"Edit({perm_path(out)})"]
    if web:
        settings["permissions"]["allow"] += ["WebFetch", "WebSearch"]


def research_prompt(prompt, web):
    """Rewrite the three paths absolute."""
    for key in ("questions", "out", "source"):
        pp = prompt_path(prompt, key)
        prompt = re.sub(rf"{key}:\s*\S+", lambda _m, k=key, v=pp: f"{k}: {v.as_posix()}", prompt, count=1)
    return prompt + ("\nweb: on" if web and "web:" not in prompt else "")


# Build output and dependency folders the researcher guard does not fingerprint (a folder name at any depth; .team.json
# "research_skip" adds globs). measured: atlas, 124,214 files and 20 s per snapshot -> 1,441 files and 0.1 s.
RESEARCH_SKIP = ["node_modules", ".venv", "venv", "dist", "build", ".next", ".turbo", "__pycache__", ".gradle",
                 "target", ".cache"]


def tree_print(folder, skip=()):
    """{relative path: (size, mtime_ns, mode)} of every file under folder, .git included; folders whose name matches a
    `skip` glob are left out. Runs no git: the source is untrusted, and a repo's own git config can make a plain
    `git status` run a program (0.13.3)."""
    out = {}
    for dirpath, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d == ".git" or not any(fnmatch.fnmatch(d, s) for s in skip)]
        for f in files:
            p = pathlib.Path(dirpath) / f
            try:
                st = p.stat()
            except OSError:
                continue
            out[p.relative_to(folder).as_posix()] = (st.st_size, st.st_mtime_ns, st.st_mode)
    return out


def research_snapshot(paths, skip=()):
    q, out, src = paths
    return {"source": tree_print(src, skip), "topic": {k: v for k, v in tree_print(q.parent).items() if k != out.name}}


def research_guards(before, paths, skip=()):
    """After a researcher run (both backends): the source folder unchanged (every file, .git included, skipped folders
    excepted), and nothing in the topic folder new or changed except the findings file. Known gaps: a write elsewhere,
    or inside a skipped folder, is not seen."""
    if before is None:
        return []
    q, out, src = paths
    now = research_snapshot(paths, skip)
    g = []
    if now["source"] != before["source"]:
        changed = sorted(k for k in set(now["source"]) | set(before["source"])
                         if now["source"].get(k) != before["source"].get(k))
        g.append("source-changed:" + "+".join(changed[:3]))
    if now["topic"] != before["topic"]:
        changed = sorted(k for k in set(now["topic"]) | set(before["topic"]) if now["topic"].get(k) != before["topic"].get(k))
        g.append("topic-folder-changed:" + "+".join(changed[:3]))
    return g


def short(n):
    """Token count for the RESULT line: 950, 12k, 1.4M; '?' when the result had no usage."""
    if n is None:
        return "?"
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{round(n / 1e3)}k" if n >= 1e3 else str(n)


# Wording of a subscription or API limit in a worker's final message (Claude Code: "Claude AI usage limit reached",
# "5-hour limit reached"; API: "rate limit"). Only read on a run that did not PASS.
USAGE_LIMIT = re.compile(r"usage limit|limit reached|rate.?limit|quota exceeded|overloaded", re.I)


def claim_stamp(runs, role):
    """A run stamp nobody else holds: the progress log is created exclusively, a taken second waits for the next."""
    while True:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            with open(runs / f"{stamp}-{role}.progress.log", "x", encoding="utf-8"):
                return stamp
        except FileExistsError:
            time.sleep(0.3)


def stream_worker(cmd, progress_path, env=None, lock=None):
    """Run a backend's command with stream-json, print + log one line per tool call and per denial; return the final
    `result` event (the backend's `result()` reads it).

    Progress lines go to stdout (flushed, so a background heartbeat sees them) and to progress_path; the RESULT line
    printed by main() stays the last line.
    lock: dict written to the worker lock file once the worker's pid is known (scratch.py stop kills that pid).
    """
    t0, final, names = time.time(), None, {}
    # own session on POSIX, so `scratch.py stop` can kill the worker's group without touching the planner's
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", env=env, start_new_session=(os.name != "nt"))
    if lock is not None:
        write_lock(lock, worker_pid=proc.pid)
    with open(progress_path, "w", encoding="utf-8") as log:
        def emit(msg):
            s = f"  [{round(time.time() - t0):>4}s] {msg}"
            print(s, flush=True); log.write(s + "\n"); log.flush()
        for raw in proc.stdout:
            try:
                ev = json.loads(raw)
                if not isinstance(ev, dict):
                    continue
                if ev.get("type") == "result":
                    final = ev
                    continue
                for line in backends.progress_any(ev, names):
                    emit(line)
            except Exception:                          # progress is best-effort; never let it kill the run
                continue
    err = proc.stderr.read()
    proc.wait()
    if final is not None:
        final.setdefault("_exit", proc.returncode)
    return final or {"subtype": "unparsed", "result": err.strip()[-300:], "_exit": proc.returncode}


def write_lock(lock, **upd):
    """Write the worker lock (see teamcfg.py). Keeps a `stop_requested` that scratch.py stop wrote meanwhile."""
    lock.update(upd)
    p = pathlib.Path(lock["path"])
    old, _ = teamcfg.read_lock_file(p)
    if old and old.get("stop_requested") and old.get("launcher_pid") == lock["launcher_pid"]:
        lock["stop_requested"] = old["stop_requested"]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({k: v for k, v in lock.items() if k not in ("path", "_path")}), encoding="utf-8")


def stop_requested(lock):
    d, _ = teamcfg.read_lock_file(lock["path"])
    return bool(d and d.get("stop_requested"))


def remote_refs():
    """`git ls-remote` per remote; None for a remote that could not be read (offline, no auth): not checked."""
    out = {}
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    for r in (git("remote") or "").split():
        try:
            p = subprocess.run(["git", "ls-remote", r], capture_output=True, text=True, timeout=30, env=env)
            out[r] = p.stdout if p.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            out[r] = None
    return out


def tracking_refs():
    return set((git("for-each-ref", "--format=%(refname)", "refs/remotes") or "").split())


def local_branches():
    return set((git("for-each-ref", "--format=%(refname:short)", "refs/heads") or "").split())


def git_state():
    """Fingerprint of what makes git run code: every hook file (name, mode, content) and the repo's config files.

    Covers the hooks folder git uses (`git rev-parse --git-path hooks`, which honours core.hooksPath), the common
    `.git/config` and a worktree's `config.worktree`. A change during a run means a worker wrote there (0.13.1).
    """
    out = {}
    hooks = git("rev-parse", "--git-path", "hooks")
    common = git("rev-parse", "--git-common-dir")
    gdir = git("rev-parse", "--git-dir")
    files = []
    if hooks and pathlib.Path(hooks).is_dir():
        files += [("hooks/" + p.relative_to(hooks).as_posix(), p) for p in sorted(pathlib.Path(hooks).rglob("*"))
                  if p.is_file()]
    for label, base in (("config", common), ("config.worktree", gdir)):
        if base and (pathlib.Path(base) / label).is_file():
            files.append((label, pathlib.Path(base) / label))
    for label, p in files:
        try:
            out[label] = (oct(p.stat().st_mode & 0o777), hashlib.sha256(p.read_bytes()).hexdigest())
        except OSError:
            out[label] = None
    return out


def git_state_changes(before, after):
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


def snapshot(protected):
    return {"main": git("rev-parse", "main"), "branch": git("branch", "--show-current"), "head": git("rev-parse", "HEAD"),
            "protected": sorted(protected_changes(None, protected)), "remotes": remote_refs(),
            "tracking": tracking_refs(), "locals": local_branches(), "git_state": git_state()}


def parse_ls_remote(out):
    refs = {}
    for line in (out or "").splitlines():
        if "\t" in line:
            sha, ref = line.split("\t", 1)
            refs[ref] = sha
    return refs


def pushed_from_here(remote, before, after, gone_tracking=frozenset(), locals_=frozenset()):
    """True when the remote's refs changed in a way only this clone can have caused.

    - A ref added or moved to a commit that exists in this clone. A teammate's push points at commits this clone has
      never fetched (the launcher does not fetch during a run), so in a shared repo it does not fail the worker.
    - A branch deleted on the remote whose remote-tracking ref also vanished from this clone during the run (`git push
      <remote> --delete <b>` removes both), or whose name is a branch of this clone (a deletion by URL leaves the
      tracking ref; the feature branch and main are always local). A teammate deleting their own merged branch passes.
    `before`/`after` are `git ls-remote` outputs. Known gap: a teammate moving a ref to a commit this clone already has
    (e.g. fast-forwarding main to a commit fetched earlier) reads as pushed from here: a false FAIL, never a miss.
    """
    b, a = parse_ls_remote(before), parse_ls_remote(after)
    for ref, sha in a.items():
        if b.get(ref) != sha and subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"],
                                                 capture_output=True).returncode == 0:
            return True
    for ref in b.keys() - a.keys():
        if ref.startswith("refs/heads/"):
            name = ref[len("refs/heads/"):]
            if name in locals_ or f"refs/remotes/{remote}/{name}" in gone_tracking:
                return True
    return False


def guards_after(snap, protected):
    g = []
    if git("rev-parse", "main") != snap["main"]: g.append("main-moved")
    if git("branch", "--show-current") != snap["branch"]: g.append("branch-changed")
    if snap["head"] and git("merge-base", "--is-ancestor", snap["head"], "HEAD") is None:
        g.append("history-rewritten")                          # amend/reset dropped the old tip
    touched = [n for n in protected_changes(snap["head"], protected) if n not in snap["protected"]]
    if touched:
        g.append("test-config-changed:" + "+".join(touched[:3]))
    hooked = git_state_changes(snap.get("git_state", {}), git_state()) if "git_state" in snap else []
    if hooked:                                                 # a hook or git config written during the run
        g.append("git-hooks-or-config-changed:" + "+".join(hooked[:3]))
    now = remote_refs()                                        # measured: a hermes worker pushed when not told not to
    gone = snap.get("tracking", set()) - tracking_refs()
    locals_ = snap.get("locals", set()) | local_branches()
    pushed = [r for r, before in snap["remotes"].items() if before is not None and now.get(r) not in (None, before)
              and pushed_from_here(r, before, now.get(r), gone, locals_)]
    if pushed:
        g.append("pushed:" + "+".join(pushed))
    return g


def verdict_of(final_text, subtype, guards):
    lines = (final_text or "").strip().splitlines()
    last = lines[-1].strip().strip("`*").strip() if lines else ""
    if subtype == "error_max_turns": v = "FAIL turn-cap"
    elif last.startswith("RESULT: PASS") and not guards: v = "PASS" + last[len("RESULT: PASS"):]
    elif last.startswith("RESULT:"): v = last[len("RESULT:"):].strip()
    else: v = f"FAIL no RESULT line ({subtype})"
    return f"FAIL guard:{','.join(guards)} | {v}" if guards else v


def report(st, verdict, turns, secs, usage, cost, denials, out_path, backend):
    """One runs.log line + the RESULT line (always the last line printed). usage = (in, cw, cr, out)."""
    tin, tcw, tcr, tout = usage
    enforce = "\tenforce=prompt-only" if backend == "hermes" else ""
    line = (f"{st['stamp']}\t{st['role']}\t{st['model']}\t{verdict}\tturns={turns}\t{secs}s\t"
            f"out={tout}\tlist$={cost}\tdenials={len(denials)}\t{rel(out_path)}\t"
            f"feature={st['feature']}\ttest={st['test_cmd'] or '-'}\tin={tin}\tcw={tcw}\tcr={tcr}\trepo={st['repo_name']}"
            f"\tbackend={backend}{enforce}\tticket={st.get('ticket') or '-'}\tfmt={teamcfg.RUN_FORMAT}")
    # ^ read by hermes-dev-watch (docs/watch-contract.md): append new fields at the end, never reorder the first six
    with open(pathlib.Path(st["scratch"]) / "runs.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")
    tag = " backend=hermes enforce=prompt-only" if backend == "hermes" else ""
    print(f"RESULT {st['role']} {verdict} |{tag} turns={turns} {secs}s denials={len(denials)} "
          f"tok=in {short(tin)} / cache {short((tcw or 0) + (tcr or 0))} / out {short(tout)} "
          f"test={st['test_cmd'] or '-'} | {rel(out_path)}")
    for x in denials[:5]:
        i = x.get("tool_input") or {}
        print(f"  denied {x.get('tool_name')}: {i.get('command') or i.get('file_path')}")
    return 0 if verdict.startswith("PASS") else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("role", nargs="?")
    ap.add_argument("prompt", nargs="?")
    ap.add_argument("--test-cmd", help="exact command added to the Bash allow-list")
    ap.add_argument("--model")
    ap.add_argument("--max-turns", type=int)
    ap.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"],
                    help="effort / reasoning; default .team.json effort (string or per-role map), else the backend's")
    ap.add_argument("--backend", choices=["claude", "hermes", "auto"], help="default: .team.json backend, else auto")
    ap.add_argument("--web", action="store_true", help="researcher only: allow web fetch/search for this run")
    ap.add_argument("--slot", type=int, help="pool slot N (set by scratch.py dispatch): lock runs/slot-N.lock")
    a = ap.parse_args()
    if not a.role or a.prompt is None:
        ap.error("role and prompt are required")

    prompt_file, settings_file = KIT / "roles" / f"{a.role}.md", KIT / "roles" / f"{a.role}.json"
    if not prompt_file.exists() or not settings_file.exists():
        print(f"RESULT {a.role} ERROR unknown role (no {prompt_file.name}/{settings_file.name})"); return 2
    if git("rev-parse", "--show-toplevel") is None:
        print(f"RESULT {a.role} ERROR cwd is not a git repo"); return 2

    tc = teamcfg.load()
    cfg, scratch, repo = tc["cfg"], tc["scratch"], pathlib.Path.cwd().resolve()
    repo_name = os.environ.get("HDT_SLOT_OF") or repo.name       # a pool slot reports as the repo it belongs to
    be = backends.pick(a.role, cfg, a.backend)
    err = be.setup_error(cfg)
    if err:
        print(f"RESULT {a.role} ERROR {err}"); return 2
    if a.web and a.role != "researcher":
        print(f"RESULT {a.role} ERROR --web is for the researcher only"); return 2
    ticket = prompt_path(a.prompt, "ticket")
    want_repo = header_line(ticket, "Repo")
    if want_repo and want_repo != repo_name:
        print(f"RESULT {a.role} ERROR ticket is for repo {want_repo}, cwd is {repo_name}"); return 2
    model, turns = DEFAULTS.get(a.role, ("sonnet", 25))
    model = a.model or (cfg.get("models") or {}).get(a.role) or model
    turns = a.max_turns or turns
    effort = cfg.get("effort")
    effort = a.effort or ((effort or {}).get(a.role) if isinstance(effort, dict) else effort)
    target = ticket or prompt_path(a.prompt, "feature") or prompt_path(a.prompt, "out")
    feature = target.relative_to(scratch).parts[0] if target and scratch in target.parents else "-"
    test_cmd = a.test_cmd or (header_line(ticket, "Test command") if a.role == "dev" else None) or \
        (suites_cmd(feature) if a.role not in ("docs", "researcher") and cfg.get("test") else None)

    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    settings["includeCoAuthoredBy"] = False                      # always, whatever the role file says
    settings.setdefault("attribution", {}).update({"commit": "", "pr": ""})
    protected = PROTECTED + list(cfg.get("protected") or [])
    # measured: Edit(**/<name>) denies the file at the root and in subfolders, for both Edit and Write
    settings["permissions"].setdefault("deny", []).extend(f"Edit(**/{p})" for p in protected)
    prompt = a.prompt
    docs_edit = list(cfg.get("docs_edit") or []) if a.role == "docs" else []
    if docs_edit:                                              # repo-relative files the project's AGENTS.md names
        settings["permissions"]["allow"] += [f"Edit(./{p.removeprefix('./')})" for p in docs_edit]
        prompt += "\nYou may also edit these files (the project's rules say what goes in them): " + ", ".join(docs_edit)
    outside = scratch != repo / ".scratch"                   # always true in a pool slot: scratch is the main checkout's
    if outside or be is backends.Hermes:                       # hermes: absolute paths, its cwd is not the repo
        for key in ("ticket", "feature"):
            pp = prompt_path(prompt, key)
            if pp:
                prompt = re.sub(rf"{key}:\s*\S+", lambda _m, k=key, v=pp: f"{k}: {v.as_posix()}", prompt, count=1)
    if outside:
        # measured: Read(//abs/**) + Edit(//abs/**) let a worker read and edit a folder outside its cwd, no --add-dir
        # needed; a sibling folder stayed denied. Dev gets the same reach it has on an in-repo .scratch.
        perms = settings["permissions"]
        for key in ("allow", "deny"):
            perms[key] += [r for r in (scratch_rule(x, scratch, repo) for x in perms[key]) if r]
        if a.role == "dev":
            perms["allow"] += [f"Read({perm_path(scratch)}/**)", f"Edit({perm_path(scratch)}/**)"]
            perms["deny"].append(f"Edit({perm_path(scratch)}/runs/**)")
            perms["deny"].append(f"Edit({perm_path(scratch)}/.git/**)")   # the scratch repo's own git
        prompt += (f"\nThe scratch folder is {scratch.as_posix()}: wherever your instructions say `.scratch/`, use that"
                   " folder (absolute paths).")
    git_subs = read_only_git(settings)
    if git_subs:
        prompt += (f"\nRead-only git ({', '.join(git_subs)}) only through the kit wrapper, exactly: `{git_ro_cmd()} "
                   f"<{'|'.join(git_subs)}> <args>` (e.g. `{git_ro_cmd()} {git_subs[0]} --stat`). Plain `git "
                   f"{git_subs[0]}` is denied. Options that write a file (`--output`) are refused.")
    rpaths = None
    if a.role == "researcher":
        rpaths = research_paths(a.prompt)
        if isinstance(rpaths, str):
            print(f"RESULT researcher ERROR {rpaths}"); return 2
        research_grants(settings, rpaths, a.web)
        prompt = research_prompt(a.prompt, a.web)
    glossary = cfg.get("glossary")
    if not glossary and cfg.get("shared") and (scratch / "GLOSSARY.md").is_file():
        glossary = str(scratch / "GLOSSARY.md")             # shared repo: the glossary stays out of the repo
    if glossary and a.role != "dev":
        gp = (tc["base"] / glossary).expanduser().resolve()
        settings["permissions"]["allow"].append(f"Read({perm_path(gp)})")
        settings["permissions"]["deny"].append(f"Edit({perm_path(gp)})")
        prompt += f"\nThe glossary is {gp.as_posix()} (read it there; never edit it)."
    if test_cmd:
        settings["permissions"]["allow"].append(f"Bash({test_cmd})")
        # measured: without this line a reviewer ran the ticket's narrower command and was denied
        prompt += f"\nThe only test command you may run is exactly: {test_cmd}"
    anchor_rules(settings, repo)                               # last: every `./` rule above becomes absolute

    runs = scratch / "runs"; runs.mkdir(parents=True, exist_ok=True)
    stamp = claim_stamp(runs, a.role)                          # unique even when two slots start in the same second
    out_path = runs / f"{stamp}-{a.role}.json"
    rules, rules_full, rules_used = project_rules(tc["rules"], tc["rules_max_chars"])
    rules_block = ("\n\n## Project rules (imports inlined; they bind you unless a tool rule below forbids the "
                   "action)\n\n" + rules.strip() + "\n") if rules.strip() else ""
    role_prompt = (prompt_file.read_text(encoding="utf-8").rstrip() + rules_block + "\n\n"
                   + (KIT / "roles" / be.tool_rules).read_text(encoding="utf-8"))

    branch_before = git("branch", "--show-current")
    if branch_before in ("main", "master") and a.role != "researcher":   # the researcher never commits
        print(f"RESULT {a.role} ERROR refusing to run on {branch_before}; check out the feature branch"); return 2
    snap = snapshot(protected)
    st = {"role": a.role, "model": model, "test_cmd": test_cmd, "feature": feature, "repo_name": repo_name,
          "scratch": scratch.as_posix(), "stamp": stamp, "ticket": ticket.name if ticket else None}
    cut = f" rules={'+'.join(rules_used) or 'none'}"
    if rules_full > tc["rules_max_chars"]:
        cut += f"({round(rules_full / 1000)}k>cap)"

    env = dict(os.environ)
    if tc["path"]:
        env["PATH"] = os.pathsep.join(tc["path"] + [env.get("PATH", "")])
    progress_path = runs / f"{stamp}-{a.role}.progress.log"
    job = {"role": a.role, "prompt": prompt, "role_prompt": role_prompt, "settings": settings, "model": model,
           "turns": turns, "effort": effort, "runs": runs, "stamp": stamp, "repo": repo.as_posix(),
           "branch": branch_before, "cfg": cfg, "env": env, "bash": a.role != "researcher", "web": a.web}

    # One worker at a time, across planner sessions too: a live lock refuses; a stale one (launcher gone) is replaced.
    # A pool slot (scratch.py dispatch) refuses only a serial run or its own slot (0.17.0).
    if a.slot:
        held, alive = next(((h, al) for h, al in (teamcfg.read_lock_file(teamcfg.lock_path(scratch)),
                                                  teamcfg.read_lock_file(teamcfg.lock_path(scratch, a.slot))) if al),
                           (None, False))
    else:
        held, alive = teamcfg.read_lock(scratch)
    if alive:
        print(f"RESULT {a.role} ERROR worker already running: {held.get('role')} {held.get('ticket') or '-'} "
              f"since {held.get('stamp')} (wait for its exit, or `scratch.py stop`)"); return 2
    lock = {"path": str(teamcfg.lock_path(scratch, a.slot)), "launcher_pid": os.getpid(), "role": a.role,
            "ticket": st["ticket"], "feature": feature, "repo": repo_name, "stamp": stamp, "fmt": teamcfg.RUN_FORMAT}
    write_lock(lock)
    try:
        rskip = RESEARCH_SKIP + list(cfg.get("research_skip") or [])
        rsnap = research_snapshot(rpaths, rskip) if rpaths else None
        cmd, st["model"] = be.command(job)
        print(f"START {a.role} {be.name}:{st['model']}{'/' + effort if effort else ''} max-turns={turns}{cut}"
              f"{' enforce=' + be.enforce if be.enforce else ''} | progress: {rel(progress_path)}", flush=True)
        t0 = time.time()
        d = stream_worker(cmd, progress_path, env, lock)
        secs = round(time.time() - t0)
        out_path.write_text(json.dumps(d), encoding="utf-8")
        r = be.result(d, job)
        guards = guards_after(snap, protected) + (research_guards(rsnap, rpaths, rskip) if rpaths else [])
        verdict = verdict_of(r["text"], r["subtype"], guards)
        if r["extra"] and verdict.startswith("PASS"):
            verdict = f"FAIL {r['extra']} | {verdict}"
        if not verdict.startswith("PASS") and USAGE_LIMIT.search(r["text"] or ""):
            verdict = f"FAIL usage-limit | {verdict}"          # 0.17.0: the planner pauses the queue, no redispatch
        if stop_requested(lock):
            verdict = f"FAIL force-stopped | {verdict}"
        return report(st, verdict, r["turns"], secs, r["usage"], r["cost"], r["denials"], out_path, be.name)
    finally:
        try:
            pathlib.Path(lock["path"]).unlink()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
