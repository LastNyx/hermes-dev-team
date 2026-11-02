#!/usr/bin/env python3
"""Prepare and finish one worker run. The worker itself is a Hermes `delegate_task` child the planner starts in chat.

Usage (cwd = the project repo root):
    python <kit>/run-worker.py prepare <role> "<prompt>" [--test-cmd "<cmd>"] [--option O] [--web]
        researcher: prompt "questions: <file> out: <file> source: <folder>" (cwd = a project repo or the research
        home; may run on main, it never commits)
    python <kit>/run-worker.py finish <run-id> [--transcript <task-N.log>] [--api-calls N] < <child's final answer>

prepare: checks `.team.json` (an `options` entry for the role, no keys of the old backends), takes the worker lock,
snapshots git and the Hermes skills folder, writes the worker's brief to <scratch>/runs/<run-id>.brief.md and prints
one `DELEGATE {...}` line: the task to pass to `delegate_task` as is (goal, context, option, output_schema). Last line
`PREPARED <run-id> ...` (exit 0) or `RESULT <role> ERROR <reason>` (exit 2).

finish: reads the child's final answer on stdin (the JSON the output_schema asks for; a raw text answer whose last
line is `RESULT: PASS|FAIL ...` also works; empty = interrupted), runs the after-run guards against the prepare
snapshot, appends one line to <scratch>/runs.log, removes the lock and prints the RESULT line, always last:
`RESULT <role> PASS <sha> | ...` (exit 0) or `RESULT <role> FAIL <reason> | ...` (exit 1).

Config: the repo's .team.json, or a workspace ../.team.json whose "repos" map names this folder (see teamcfg.py).
The test command is found in this order: --test-cmd; for dev, the ticket's `Test command:` line (prompt
`ticket: <path>`); then the full suites (`"test"` in the config). Nothing blocks a child while it runs: its limits are
the brief's rules, and the guards here are what checks them afterwards (docs/backend-delegate.md).
"""
import argparse, datetime, fnmatch, hashlib, json, os, pathlib, re, subprocess, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import teamcfg  # noqa: E402

KIT = pathlib.Path(__file__).resolve().parent
ROLES = ("dev", "reviewer", "docs", "researcher")
# Files that decide what the allowed test command runs. If a worker could edit them, the test command would run
# anything (e.g. a rewritten "test" script). Named in the brief AND checked after every run (guard).
# A project adds more with .team.json "protected": ["glob", ...]; globs match the file name at any depth.
PROTECTED = ["package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
             "jest.config.*", "vitest.config.*", "tsconfig*.json", "babel.config.*", ".babelrc",
             "pyproject.toml", "setup.cfg", "pytest.ini", "tox.ini", "Makefile", ".team.json"]
MAX_IMPORT_DEPTH = 5
# What the child's final answer must be (delegate_task validates it and retries once). Kept forgiving: only the
# verdict and a summary are required.
OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["verdict", "summary"],
    "properties": {
        "verdict": {"enum": ["PASS", "FAIL"]},
        "summary": {"type": "string", "description": "PASS: what was done; FAIL: the one-line reason"},
        "sha": {"type": "string", "description": "short SHA of your commit, if you made one"},
        "not_checked": {"type": "string", "description": "what you did not run or verify; 'nothing' if so"},
        "cause": {"type": "string", "description": "bug tickets: what was actually wrong (file and mechanism)"},
    },
}


def git(*args):
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def project_memory(path, depth=0, seen=None):
    """Return a rules file (AGENTS.md, CLAUDE.md) with its `@path` import lines inlined.

    Measured: a delegate child loads no AGENTS.md or CLAUDE.md at all (probe 2026-11-02), and an import such as
    `@../other-repo/CLAUDE.md` points outside the repo. So prepare pastes them into the brief.
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
    """Rules pasted into every brief: the files named by `rules` (default AGENTS.md + CLAUDE.md), imports inlined, capped.

    Measured: a 150k-char CLAUDE.md was pasted whole into every run. Past the cap the text is cut and the worker is
    told where the full file is, so it can search it.
    """
    seen = set()                                      # shared, so a file is pasted once even if imported again
    parts = [(f, project_memory(f, seen=seen)) for f in files]
    parts = [(f, b) for f, b in parts if b.strip()]
    text = "\n\n".join(b.strip() for _, b in parts)
    full = len(text)
    if full > cap:
        where = ", ".join(f.as_posix() for f, _ in parts)
        text = text[:cap].rsplit("\n", 1)[0] + (f"\n\n[cut at {cap} of {full} chars; the full text is in {where}: "
                                               "search it for the section you need]")
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


def suites_cmd(feature):
    """The full-suite command a worker runs: kit/suites.py runs every `.team.json` suite in its own folder and judges
    failures against the feature's baseline (a `a && cd ui && b` chain stopped at baseline errors before `b`)."""
    return (f"{pathlib.Path(sys.executable).as_posix()} {(KIT / 'suites.py').as_posix()}"
            + (f" --feature {feature}" if feature and feature != "-" else ""))


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


def tree_print(folder, skip=(), follow=False, hidden=True):
    """{relative path: (size, mtime_ns, mode)} of every file under folder, .git included; folders whose name matches a
    `skip` glob are left out (and, with hidden=False, every name starting with a dot). Runs no git: the source is
    untrusted, and a repo's own git config can make a plain `git status` run a program (0.13.3)."""
    out = {}
    for dirpath, dirs, files in os.walk(folder, followlinks=follow):
        dirs[:] = [d for d in dirs if (d == ".git" and hidden) or not (any(fnmatch.fnmatch(d, s) for s in skip)
                                                                         or (not hidden and d.startswith(".")))]
        for f in files:
            if not hidden and f.startswith("."):
                continue
            p = pathlib.Path(dirpath) / f
            try:
                st = p.stat()
            except OSError:
                continue
            out[p.relative_to(folder).as_posix()] = [st.st_size, st.st_mtime_ns, st.st_mode]   # list: JSON-stable
    return out


def research_snapshot(paths, skip=()):
    q, out, src = paths
    return {"source": tree_print(src, skip), "topic": {k: v for k, v in tree_print(q.parent).items() if k != out.name}}


def changed_keys(a, b):
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def research_guards(before, paths, skip=()):
    """After a researcher run: the source folder unchanged (every file, .git included, skipped folders excepted), and
    nothing in the topic folder new or changed except the findings file. Known gaps: a write elsewhere, or inside a
    skipped folder, is not seen."""
    if before is None:
        return []
    now = research_snapshot(paths, skip)
    g = []
    if now["source"] != before["source"]:
        g.append("source-changed:" + "+".join(changed_keys(now["source"], before["source"])[:3]))
    if now["topic"] != before["topic"]:
        g.append("topic-folder-changed:" + "+".join(changed_keys(now["topic"], before["topic"])[:3]))
    return g


def skills_print():
    """Fingerprint of the Hermes skills folder (symlinked team skills followed; dot files such as the curator ledger and
    hub locks left out). A delegate child has `skill_manage`, so a change during a run means a worker edited a skill
    (D16). The planner must not edit skills while a worker runs either."""
    root = teamcfg.hermes_home() / "skills"
    return tree_print(root, follow=True, hidden=False) if root.is_dir() else {}


# Wording of a subscription or API limit in a worker's final answer. Only read on a run that did not PASS.
USAGE_LIMIT = re.compile(r"usage limit|limit reached|rate.?limit|quota exceeded|overloaded", re.I)


def claim_stamp(runs, role):
    """A run id nobody else holds: the brief is created exclusively, a taken second waits for the next."""
    while True:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            with open(runs / f"{stamp}-{role}.brief.md", "x", encoding="utf-8"):
                return stamp
        except FileExistsError:
            time.sleep(0.3)


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
            out[label] = [oct(p.stat().st_mode & 0o777), hashlib.sha256(p.read_bytes()).hexdigest()]
        except OSError:
            out[label] = None
    return out


def git_state_changes(before, after):
    norm = lambda d: {k: list(v) if isinstance(v, (list, tuple)) else v for k, v in (d or {}).items()}
    return changed_keys(norm(before), norm(after))


def snapshot(protected):
    """Everything guards_after compares, JSON-ready (prepare stores it until finish)."""
    return {"main": git("rev-parse", "main"), "branch": git("branch", "--show-current"), "head": git("rev-parse", "HEAD"),
            "protected": sorted(protected_changes(None, protected)), "remotes": remote_refs(),
            "tracking": sorted(tracking_refs()), "locals": sorted(local_branches()), "git_state": git_state()}


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
      never fetched (the kit does not fetch during a run), so in a shared repo it does not fail the worker.
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
    gone = set(snap.get("tracking") or []) - tracking_refs()
    locals_ = set(snap.get("locals") or []) | local_branches()
    pushed = [r for r, before in snap["remotes"].items() if before is not None and now.get(r) not in (None, before)
              and pushed_from_here(r, before, now.get(r), gone, locals_)]
    if pushed:
        g.append("pushed:" + "+".join(pushed))
    return g


def main_checkout_guard(st):
    """A pool-slot run: the child starts in the planner's folder, so check the main checkout was left alone."""
    mc = st.get("main_checkout")
    if not mc:
        return []
    r = lambda *a: subprocess.run(["git", "-C", mc["path"], *a], capture_output=True, text=True).stdout.strip()
    if r("rev-parse", "HEAD") != mc["head"] or r("status", "--porcelain") != mc["status"]:
        return ["main-checkout-changed"]
    return []


def parse_answer(text):
    """The child's final answer -> dict with verdict/summary/... ; None when there is none (interrupted run).

    Accepts the output_schema JSON (also inside a code fence or after prose), or a raw answer whose last line is
    `RESULT: PASS|FAIL <text>` (what a child writes when the schema retry failed)."""
    text = (text or "").strip()
    if not text:
        return None
    for cand in (text, *re.findall(r"\{.*\}", text, re.S)):
        try:
            d = json.loads(cand)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("verdict") in ("PASS", "FAIL"):
            return d
    last = text.splitlines()[-1].strip().strip("`*").strip()
    m = re.match(r"RESULT:\s*(PASS|FAIL)\s*(.*)", last)
    if m:
        return {"verdict": m.group(1), "summary": m.group(2)}
    return {"verdict": "FAIL", "summary": "no verdict in the answer: " + " ".join(text.split())[:120]}


def verdict_of(answer, child_status, guards):
    """child_status: the manifest's task status. `running` and None say nothing (the manifest is written when the whole
    delegate call ends, and is missing on another machine or after Hermes pruned it)."""
    if answer is None:
        v = f"FAIL no answer (child {child_status or 'not found'})"
    elif child_status not in (None, "completed", "running"):
        v = f"FAIL child {child_status} | {answer['verdict']} {answer.get('summary', '')}".rstrip()
    elif answer["verdict"] == "PASS":
        v = "PASS " + (answer.get("sha") or answer.get("summary") or "")
    else:
        v = "FAIL " + " ".join(str(answer.get("summary") or "no reason").split())
    v = " ".join(v.split())[:300]
    return f"FAIL guard:{','.join(guards)} | {v}" if guards else v


def config_errors(cfg, role, option):
    """D11: the reasons a project is not set up for the delegate kit, each with the fix. Empty = fine."""
    errs = []
    old = [k for k in ("backend", "models", "hermes_profile", "hermes_models", "hermes_provider", "effort") if k in cfg]
    if old:
        errs.append(f"remove old key(s) {', '.join(old)} (workers are delegate subagents now; the model is `options`)")
    if cfg.get("max_workers") and not cfg.get("setup"):
        errs.append("`max_workers` needs a `setup` list (commands that install dependencies in a pool worktree), "
                    "or remove `max_workers`")
    opts = cfg.get("options")
    if not option and not (isinstance(opts, dict) and isinstance(opts.get(role), str) and opts[role].strip()):
        errs.append(f"add an `options` entry for {role}")
    return errs


def refuse_config(role, cfg, errs, source):
    fix = {k: v for k, v in cfg.items() if k not in ("backend", "models", "hermes_profile", "hermes_models",
                                                      "hermes_provider", "effort")}
    if fix.get("max_workers") and not fix.get("setup"):
        fix.pop("max_workers")
    fix["options"] = {**{"dev": "standard", "reviewer": "standard", "docs": "fast", "researcher": "standard"},
                      **(cfg.get("options") if isinstance(cfg.get("options"), dict) else {})}
    print(f"  {source or '.team.json'} is not set up for delegate workers:")
    for e in errs:
        print(f"  - {e}")
    print("  replacement (ask the user before writing it):")
    for line in json.dumps(fix, indent=1).splitlines():
        print("    " + line)
    print(f"RESULT {role} ERROR .team.json not set up for delegate workers; ask the user to adjust it first")
    return 2


def brief_text(role, prompt, repo, branch, scratch, rules, test_cmd, extra, web):
    rules_block = ("\n\n## Project rules (imports inlined; they bind you unless a tool rule below forbids the "
                   "action)\n\n" + rules.strip() + "\n") if rules.strip() else ""
    tools = (KIT / "roles" / "_tool-rules.md").read_text(encoding="utf-8")
    if web:
        tools += "- `web: on`: you may also use web_search and web_extract for this run.\n"
    task = [f"Repo root: {repo} (absolute). Your terminal may start elsewhere: your FIRST terminal command is "
            f"`cd {repo}`, and every file path you pass to a file tool is absolute.",
            f"Branch: `{branch}`." if branch else "", f"Scratch folder: {scratch} (wherever your instructions say "
            "`.scratch/`, use that folder).", prompt]
    if test_cmd:
        task.append(f"The only test command you may run is exactly: {test_cmd}")
    task += extra
    return ((KIT / "roles" / f"{role}.md").read_text(encoding="utf-8").rstrip() + rules_block + "\n\n" + tools.rstrip()
            + "\n\n## Your task\n\n" + "\n".join(t for t in task if t) + "\n")


def cmd_prepare(a):
    role = a.role
    if role not in ROLES:
        print(f"RESULT {role} ERROR unknown role ({', '.join(ROLES)})"); return 2
    if git("rev-parse", "--show-toplevel") is None:
        print(f"RESULT {role} ERROR cwd is not a git repo"); return 2
    if a.web and role != "researcher":
        print(f"RESULT {role} ERROR --web is for the researcher only"); return 2
    tc = teamcfg.load()
    cfg, scratch, repo = tc["cfg"], tc["scratch"], pathlib.Path.cwd().resolve()
    errs = config_errors(cfg, role, a.option)
    if errs:
        return refuse_config(role, cfg, errs, tc["source"] and pathlib.Path(tc["source"]).as_posix())
    option = a.option or cfg["options"][role].strip()
    repo_name = os.environ.get("HDT_SLOT_OF") or repo.name     # a pool slot reports as the repo it belongs to
    ticket = prompt_path(a.prompt, "ticket")
    want_repo = header_line(ticket, "Repo")
    if want_repo and want_repo != repo_name:
        print(f"RESULT {role} ERROR ticket is for repo {want_repo}, cwd is {repo_name}"); return 2
    target = ticket or prompt_path(a.prompt, "feature") or prompt_path(a.prompt, "out")
    feature = target.relative_to(scratch).parts[0] if target and scratch in target.parents else "-"
    test_cmd = a.test_cmd or (header_line(ticket, "Test command") if role == "dev" else None) or \
        (suites_cmd(feature) if role not in ("docs", "researcher") and cfg.get("test") else None)
    branch = git("branch", "--show-current")
    if branch in ("main", "master") and role != "researcher":       # the researcher never commits
        print(f"RESULT {role} ERROR refusing to run on {branch}; check out the feature branch"); return 2

    prompt, extra = a.prompt, []
    for key in ("ticket", "feature"):                          # absolute: the child does not start in the repo
        pp = prompt_path(prompt, key)
        if pp:
            prompt = re.sub(rf"{key}:\s*\S+", lambda _m, k=key, v=pp: f"{k}: {v.as_posix()}", prompt, count=1)
    protected = PROTECTED + list(cfg.get("protected") or [])
    if role == "dev":
        extra.append("Protected files (never edit): " + ", ".join(protected))
    docs_edit = list(cfg.get("docs_edit") or []) if role == "docs" else []
    if docs_edit:
        extra.append("You may also edit these files (the project's rules say what goes in them): " + ", ".join(docs_edit))
    rpaths = None
    if role == "researcher":
        rpaths = research_paths(a.prompt)
        if isinstance(rpaths, str):
            print(f"RESULT researcher ERROR {rpaths}"); return 2
        prompt = research_prompt(a.prompt, a.web)
    glossary = cfg.get("glossary")
    if not glossary and cfg.get("shared") and (scratch / "GLOSSARY.md").is_file():
        glossary = str(scratch / "GLOSSARY.md")             # shared repo: the glossary stays out of the repo
    if glossary and role != "dev":
        extra.append(f"The glossary is {(tc['base'] / glossary).expanduser().resolve().as_posix()} (read it there; "
                     "never edit it).")
    if tc["path"]:
        extra.append("Before your first command run `export PATH=" + os.pathsep.join(tc["path"]) + os.pathsep + "$PATH`.")

    # One worker at a time, across planner sessions too. A pool slot (scratch.py dispatch) refuses only a serial run
    # or its own slot.
    held = teamcfg.blocking_lock(scratch, a.slot)
    if held:
        lk, state = held
        why = {"running": "still running: wait for its result",
               "prepared": "prepared and not finished: start its child or `scratch.py stop`",
               "done": "ended but not finished: `run-worker.py finish` it (or `scratch.py resume`)"}.get(
            state, "interrupted (its child is gone): `scratch.py resume` finishes it")
        print(f"RESULT {role} ERROR worker {lk.get('run_id')} ({lk.get('ticket') or '-'}) {why}"); return 2

    runs = scratch / "runs"; runs.mkdir(parents=True, exist_ok=True)
    stamp = claim_stamp(runs, role)
    run_id = f"{stamp}-{role}"
    rules, rules_full, rules_used = project_rules(tc["rules"], tc["rules_max_chars"])
    brief = runs / f"{run_id}.brief.md"
    brief.write_text(brief_text(role, prompt, repo.as_posix(), branch, scratch.as_posix(), rules, test_cmd, extra,
                                a.web), encoding="utf-8")
    rskip = RESEARCH_SKIP + list(cfg.get("research_skip") or [])
    st = {"run_id": run_id, "role": role, "option": option, "test_cmd": test_cmd, "feature": feature,
          "repo": repo.as_posix(), "repo_name": repo_name, "scratch": scratch.as_posix(), "stamp": stamp,
          "ticket": ticket.name if ticket else None, "slot": a.slot, "protected": protected,
          "prepared": time.time(), "snap": snapshot(protected), "skills": skills_print(),
          "research": ({"paths": [p.as_posix() for p in rpaths], "skip": rskip,
                        "snap": research_snapshot(rpaths, rskip)} if rpaths else None)}
    mc = os.environ.get("HDT_MAIN_CHECKOUT")
    if mc:
        g = lambda *x: subprocess.run(["git", "-C", mc, *x], capture_output=True, text=True).stdout.strip()
        st["main_checkout"] = {"path": mc, "head": g("rev-parse", "HEAD"), "status": g("status", "--porcelain")}
    (runs / f"{run_id}.state.json").write_text(json.dumps(st), encoding="utf-8")
    teamcfg.write_lock(scratch, a.slot, {"run_id": run_id, "role": role, "ticket": st["ticket"], "feature": feature,
                                         "repo": repo_name, "stamp": stamp, "prepared": st["prepared"],
                                         "fmt": teamcfg.RUN_FORMAT})
    task = {"goal": f"{teamcfg.GOAL_TAG} {run_id}: {role} worker" + (f" for {st['ticket']}" if st["ticket"] else "")
                    + f". Follow your brief file {brief.as_posix()} exactly.",
            "context": (f"Your complete instructions are in {brief.as_posix()}. Read the whole file with read_file "
                        "before doing anything else and follow it; this message adds nothing to it."),
            "option": option, "output_schema": OUTPUT_SCHEMA}
    print("DELEGATE " + json.dumps(task))
    cut = f" rules={'+'.join(rules_used) or 'none'}" + (f"({round(rules_full / 1000)}k>cap)"
                                                        if rules_full > tc["rules_max_chars"] else "")
    print(f"PREPARED {run_id} option={option}{cut} test={test_cmd or '-'} | brief: {rel(brief)}")
    return 0


def find_state(run_id):
    """The run's state file: in this folder's scratch, else (finish run from a pool slot worktree) in the main
    checkout's, found through git's common dir."""
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir")
    folders = [os.environ.get("HDT_MAIN_CHECKOUT"), os.getcwd(), str(pathlib.Path(common).parent) if common else None]
    for folder in filter(None, folders):
        try:
            p = teamcfg.load(folder)["scratch"] / "runs" / f"{run_id}.state.json"
        except SystemExit:
            continue
        if p.is_file():
            return p
    return None


def finish(run_id, answer_text, transcript=None, api_calls=None, forced=None, state_path=None):
    """Guards + runs.log + RESULT line for one prepared run. Returns (exit code, RESULT line). Used by the CLI,
    scratch.py resume (interrupted runs) and scratch.py stop."""
    sp = pathlib.Path(state_path) if state_path else find_state(run_id)
    if not sp:
        line = f"RESULT ? ERROR no prepared run {run_id} (already finished, or prepared in another scratch folder)"
        print(line); return 2, line
    st = json.loads(sp.read_text(encoding="utf-8"))
    if st.get("finished"):
        line = f"RESULT {st['role']} ERROR {run_id} was already finished: {st['finished']}"
        print(line); return 2, line
    cwd = os.getcwd()
    os.chdir(st["repo"])
    try:
        child = teamcfg.child_of(run_id, transcript)
        status = (child or {}).get("status")
        model = (child or {}).get("model") or st["option"]
        secs = round(time.time() - st["prepared"])
        if child and child.get("started_ts") and child.get("ended_ts"):
            secs = round(child["ended_ts"] - child["started_ts"])
        guards = guards_after(st["snap"], st["protected"]) + main_checkout_guard(st)
        if st.get("research"):
            r = st["research"]
            guards += research_guards(r["snap"], [pathlib.Path(p) for p in r["paths"]], r["skip"])
        sk = changed_keys(skills_print(), st["skills"])
        if sk:
            guards.append("skills-changed:" + "+".join(sk[:3]))
        answer = parse_answer(answer_text)
        verdict = verdict_of(answer, status, guards)
        if forced:                                             # resume (interrupted) or stop (force-stopped)
            verdict = f"FAIL {forced} | {verdict}"
        if not verdict.startswith("PASS") and USAGE_LIMIT.search(answer_text or ""):
            verdict = f"FAIL usage-limit | {verdict}"          # the planner pauses the queue, no redispatch
        runs = pathlib.Path(st["scratch"]) / "runs"
        out_path = runs / f"{run_id}.json"
        out_path.write_text(json.dumps({"answer": answer, "raw": answer_text, "child": child, "guards": guards}),
                            encoding="utf-8")
        tp = (child or {}).get("log") or "-"
        line = (f"{st['stamp']}\t{st['role']}\t{model}\t{verdict}\tturns={api_calls if api_calls is not None else '-'}"
                f"\t{secs}s\tout=None\tlist$=None\tdenials=0\t{rel(out_path)}\tfeature={st['feature']}"
                f"\ttest={st['test_cmd'] or '-'}\tin=None\tcw=None\tcr=None\trepo={st['repo_name']}\tbackend=delegate"
                f"\tenforce=prompt-only\tticket={st.get('ticket') or '-'}\tfmt={teamcfg.RUN_FORMAT}\ttranscript={tp}")
        with open(pathlib.Path(st["scratch"]) / "runs.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
        st["finished"] = verdict
        sp.write_text(json.dumps(st), encoding="utf-8")
        teamcfg.remove_lock(st["scratch"], st.get("slot"), run_id)
        extra = []
        if answer and answer.get("not_checked"):
            extra.append(f"  Not checked: {' '.join(str(answer['not_checked']).split())}")
        if answer and answer.get("cause"):
            extra.append(f"  Cause: {' '.join(str(answer['cause']).split())}")
        for x in extra:
            print(x)
        result = (f"RESULT {st['role']} {verdict} | option={st['option']} model={model} enforce=prompt-only "
                  f"api_calls={api_calls if api_calls is not None else '-'} {secs}s test={st['test_cmd'] or '-'} "
                  f"| {rel(out_path)}")
        print(result)
        return (0 if verdict.startswith("PASS") else 1), result
    finally:
        os.chdir(cwd)


def cmd_finish(a):
    text = pathlib.Path(a.answer_file).read_text(encoding="utf-8") if a.answer_file else \
        ("" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read())
    return finish(a.run_id, text, a.transcript, a.api_calls)[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("role"); p.add_argument("prompt")
    p.add_argument("--test-cmd", help="the exact test command the worker may run")
    p.add_argument("--option", help="delegate model option for this run (default: .team.json options[role])")
    p.add_argument("--web", action="store_true", help="researcher only: allow web search/extract for this run")
    p.add_argument("--slot", type=int, help="pool slot N (set by scratch.py dispatch): lock runs/slot-N.lock")
    p = sub.add_parser("finish")
    p.add_argument("run_id")
    p.add_argument("--transcript", help="the child's live transcript (…/delegation/live/<id>/task-N.log)")
    p.add_argument("--api-calls", type=int)
    p.add_argument("--answer-file", help="read the child's final answer from this file instead of stdin")
    a = ap.parse_args()
    return cmd_prepare(a) if a.cmd == "prepare" else cmd_finish(a)


if __name__ == "__main__":
    sys.exit(main())
