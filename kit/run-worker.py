#!/usr/bin/env python3
"""Run one headless Claude Code worker in a project repo, under a role's allow-list.

Usage (cwd = the project repo root):
    python <kit>/run-worker.py <role> "<prompt>" [--test-cmd "<cmd>"] [--model M] [--max-turns N]

Config: the repo's .team.json, or a workspace ../.team.json whose "repos" map names this folder (see teamcfg.py).
The allowed test command is found in this order: --test-cmd; for dev, the ticket's `Test command:` line
(prompt `ticket: <path>`); then `"test"` in the config. Models can be set per role with `"models": {"dev": "sonnet"}`.
`ticket:` and `feature:` paths may point outside the repo (a workspace scratch folder): the launcher grants the worker
read/write on the scratch folder only and tells it where that folder is. A ticket with a `Repo:` line refuses to run
from any other folder. The feature name for the cost log is the first folder under the scratch folder.

Roles live next to this file: roles/<role>.md (prompt) and roles/<role>.json (settings).
While the worker runs, prints one progress line per tool call (and per permission denial) and writes the same lines
to .scratch/runs/<stamp>-<role>.progress.log. The LAST line printed is always the RESULT line; exit 0 only when the
worker's last line is `RESULT: PASS` and the guards hold.
Every run appends a line to .scratch/runs.log and keeps the final result event (JSON) in .scratch/runs/.

Measured facts this relies on (Claude Code 2.1.296):
- `claude -p` exits 0 even when the worker gives up, so the RESULT line is parsed here.
- `--setting-sources ""` ignores user/project settings, so a repo's .claude/settings.json cannot widen the allow-list.
- `Edit(path)` rules govern both Edit and Write; deny rules beat allow rules.
"""
import argparse, datetime, fnmatch, json, os, pathlib, re, shutil, subprocess, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import teamcfg  # noqa: E402

KIT = pathlib.Path(__file__).resolve().parent
DEFAULTS = {"dev": ("sonnet", 40), "reviewer": ("sonnet", 25), "docs": ("haiku", 25)}
TOOLS = "Read,Edit,Write,Glob,Grep,Bash"
# Files that decide what the allowed test command runs. If a worker could edit them, the test command would run
# anything (e.g. a rewritten "test" script). Denied in roles/dev.json AND checked after every run (guard).
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
    """Rules pasted into every worker prompt: the files named by `rules` (default CLAUDE.md), imports inlined, capped.

    Measured: a 150k-char CLAUDE.md was pasted whole into every run. Past the cap the text is cut and the worker is
    told where the full file is, so it can Grep it (files inside the repo only; the worker cannot read elsewhere).
    """
    parts = [(f, project_memory(f)) for f in files]
    text = "\n\n".join(b.strip() for _, b in parts if b.strip())
    full = len(text)
    if full > cap:
        where = ", ".join(f.name if f.parent == pathlib.Path.cwd().resolve() else f.as_posix() for f, _ in parts)
        text = text[:cap].rsplit("\n", 1)[0] + (f"\n\n[cut at {cap} of {full} chars; the full text is in {where}: "
                                               "Grep it for the section you need]")
    return text, full


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


def scratch_rule(rule, scratch, repo):
    """Copy a role's `Tool(./.scratch/...)` rule onto a scratch folder outside the repo (`//` = absolute path)."""
    m = re.fullmatch(r"(\w+)\(\./\.scratch(/.*)?\)", rule)
    if not m or scratch == repo / ".scratch":
        return None
    return f"{m.group(1)}(/{scratch.as_posix()}{m.group(2) or ''})"


def tool_line(name, inp):
    """One short progress line for a tool call."""
    path = inp.get("file_path") or inp.get("path")
    if path:                                        # absolute paths inside the repo -> repo-relative
        try:
            path = pathlib.Path(path).resolve().relative_to(pathlib.Path.cwd().resolve()).as_posix()
        except (ValueError, OSError):
            pass
    arg = inp.get("command") or path or inp.get("pattern") or ""
    if inp.get("pattern") and path:
        arg = f"{inp['pattern']} in {path}"
    arg = " ".join(str(arg).split())
    return f"{name} {arg[:120]}"


def short(n):
    """Token count for the RESULT line: 950, 12k, 1.4M; '?' when the result had no usage."""
    if n is None:
        return "?"
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{round(n / 1e3)}k" if n >= 1e3 else str(n)


def stream_worker(cmd, progress_path, env=None):
    """Run claude with stream-json, print + log one line per tool call and per denial; return the final result event.

    The final `result` event carries the same fields as --output-format json (subtype, result, num_turns, usage,
    total_cost_usd, permission_denials). Progress lines go to stdout (flushed, so a background heartbeat sees them)
    and to progress_path; the RESULT line printed by main() stays the last line.
    """
    t0, final, names = time.time(), None, {}
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", env=env)
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
                # measured (2.1.296): a denied call comes back as a tool_result "This command requires approval" with
                # tool_result_meta[].permission_decision.decision == "reject"; the wording mentions neither "denied"
                # nor "permission", so match the structured field first.
                rejected = {m.get("id") for m in ev.get("tool_result_meta") or [] if isinstance(m, dict)
                            and (m.get("permission_decision") or {}).get("decision") == "reject"}
                msg = ev.get("message")
                content = msg.get("content") if isinstance(msg, dict) else None
                for c in content if isinstance(content, list) else []:
                    if not isinstance(c, dict):
                        continue
                    if c.get("type") == "tool_use":
                        names[c.get("id")] = c.get("name")
                        emit(tool_line(c.get("name"), c.get("input") or {}))
                    elif c.get("type") == "tool_result" and c.get("is_error"):
                        body = c.get("content")
                        body = body if isinstance(body, str) else json.dumps(body)
                        low = body.lower()
                        if c.get("tool_use_id") in rejected or "requires approval" in low or "permission" in low:
                            emit(f"DENIED {names.get(c.get('tool_use_id'), '?')}: {' '.join(body.split())[:100]}")
            except Exception:                          # progress is best-effort; never let it kill the run
                continue
    err = proc.stderr.read()
    proc.wait()
    return final or {"subtype": "unparsed", "result": err.strip()[-300:]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("role")
    ap.add_argument("prompt")
    ap.add_argument("--test-cmd", help="exact command added to the Bash allow-list")
    ap.add_argument("--model")
    ap.add_argument("--max-turns", type=int)
    a = ap.parse_args()

    prompt_file, settings_file = KIT / "roles" / f"{a.role}.md", KIT / "roles" / f"{a.role}.json"
    if not prompt_file.exists() or not settings_file.exists():
        print(f"RESULT {a.role} ERROR unknown role (no {prompt_file.name}/{settings_file.name})"); return 2
    if not shutil.which("claude"):
        print(f"RESULT {a.role} ERROR claude CLI not on PATH"); return 2
    if git("rev-parse", "--show-toplevel") is None:
        print(f"RESULT {a.role} ERROR cwd is not a git repo"); return 2

    tc = teamcfg.load()
    cfg, scratch, repo = tc["cfg"], tc["scratch"], pathlib.Path.cwd().resolve()
    ticket = prompt_path(a.prompt, "ticket")
    want_repo = header_line(ticket, "Repo")
    if want_repo and want_repo != repo.name:
        print(f"RESULT {a.role} ERROR ticket is for repo {want_repo}, cwd is {repo.name}"); return 2
    model, turns = DEFAULTS.get(a.role, ("sonnet", 25))
    model = a.model or (cfg.get("models") or {}).get(a.role) or model
    turns = a.max_turns or turns
    test_cmd = a.test_cmd or (header_line(ticket, "Test command") if a.role == "dev" else None) or \
        (cfg.get("test") if a.role != "docs" else None)
    target = ticket or prompt_path(a.prompt, "feature")
    feature = target.relative_to(scratch).parts[0] if target and scratch in target.parents else "-"

    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    settings["includeCoAuthoredBy"] = False                      # always, whatever the role file says
    settings.setdefault("attribution", {}).update({"commit": "", "pr": ""})
    protected = PROTECTED + list(cfg.get("protected") or [])
    # measured: Edit(**/<name>) denies the file at the root and in subfolders, for both Edit and Write
    settings["permissions"].setdefault("deny", []).extend(f"Edit(**/{p})" for p in protected)
    prompt = a.prompt
    outside = scratch != repo / ".scratch"
    if outside:
        # measured: Read(//abs/**) + Edit(//abs/**) let a worker read and edit a folder outside its cwd, no --add-dir
        # needed; a sibling folder stayed denied. Dev gets the same reach it has on an in-repo .scratch.
        perms = settings["permissions"]
        for key in ("allow", "deny"):
            perms[key] += [r for r in (scratch_rule(x, scratch, repo) for x in perms[key]) if r]
        if a.role == "dev":
            perms["allow"] += [f"Read(/{scratch.as_posix()}/**)", f"Edit(/{scratch.as_posix()}/**)"]
            perms["deny"].append(f"Edit(/{scratch.as_posix()}/runs/**)")
        for key in ("ticket", "feature"):                    # absolute, so the allow rule matches what the worker opens
            pp = prompt_path(prompt, key)
            if pp:
                prompt = re.sub(rf"{key}:\s*\S+", lambda _m, k=key, v=pp: f"{k}: {v.as_posix()}", prompt, count=1)
        prompt += (f"\nThe scratch folder is {scratch.as_posix()}: wherever your instructions say `.scratch/`, use that"
                   " folder (absolute paths).")
    glossary = cfg.get("glossary")
    if glossary and a.role != "dev":
        gp = (tc["base"] / glossary).expanduser().resolve()
        settings["permissions"]["allow"].append(f"Read(/{gp.as_posix()})")
        settings["permissions"]["deny"].append(f"Edit(/{gp.as_posix()})")
        prompt += f"\nThe glossary is {gp.as_posix()} (read it there; never edit it)."
    if test_cmd:
        settings["permissions"]["allow"].append(f"Bash({test_cmd})")
        # measured: without this line a reviewer ran the ticket's narrower command and was denied
        prompt += f"\nThe only test command you may run is exactly: {test_cmd}"

    runs = scratch / "runs"; runs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path, agents_path = runs / f"{stamp}-{a.role}.json", runs / f"{stamp}-{a.role}.agents.json"
    rules, rules_full = project_rules(tc["rules"], tc["rules_max_chars"])
    rules_block = ("\n\n## Project rules (imports inlined; they bind you unless a tool rule below forbids the "
                   "action)\n\n" + rules.strip() + "\n") if rules.strip() else ""
    agents_path.write_text(json.dumps({a.role: {
        "description": f"{a.role} worker",
        "prompt": prompt_file.read_text(encoding="utf-8").rstrip() + rules_block + "\n\n"
                  + (KIT / "roles" / "_tool-rules.md").read_text(encoding="utf-8")}}), encoding="utf-8")

    main_before, branch_before = git("rev-parse", "main"), git("branch", "--show-current")
    head_before = git("rev-parse", "HEAD")
    protected_before = set(protected_changes(None, protected))   # already dirty before the run: not the worker's
    if branch_before in ("main", "master"):
        print(f"RESULT {a.role} ERROR refusing to run on {branch_before}; check out the feature branch"); return 2
    cmd = ["claude", "-p", prompt, "--agents", str(agents_path), "--agent", a.role, "--tools", TOOLS,
           "--settings", json.dumps(settings), "--setting-sources", "", "--model", model,
           "--max-turns", str(turns), "--output-format", "stream-json", "--verbose", "--no-session-persistence"]
    progress_path = runs / f"{stamp}-{a.role}.progress.log"
    cut = f" rules={round(rules_full / 1000)}k>cap" if rules_full > tc["rules_max_chars"] else ""
    print(f"START {a.role} {model} max-turns={turns}{cut} | progress: {rel(progress_path)}", flush=True)
    env = dict(os.environ)
    if tc["path"]:
        env["PATH"] = os.pathsep.join(tc["path"] + [env.get("PATH", "")])
    t0 = time.time()
    d = stream_worker(cmd, progress_path, env)
    secs = round(time.time() - t0)
    out_path.write_text(json.dumps(d), encoding="utf-8")

    lines = (d.get("result") or "").strip().splitlines()
    last = lines[-1].strip() if lines else ""
    denials = d.get("permission_denials") or []
    guards = []
    if git("rev-parse", "main") != main_before: guards.append("main-moved")
    if git("branch", "--show-current") != branch_before: guards.append("branch-changed")
    if head_before and git("merge-base", "--is-ancestor", head_before, "HEAD") is None:
        guards.append("history-rewritten")                     # amend/reset dropped the old tip
    touched = [n for n in protected_changes(head_before, protected) if n not in protected_before]
    if touched:
        guards.append("test-config-changed:" + "+".join(touched[:3]))

    if d.get("subtype") == "error_max_turns": verdict = "FAIL turn-cap"
    elif last.startswith("RESULT: PASS") and not guards: verdict = "PASS" + last[len("RESULT: PASS"):]
    elif last.startswith("RESULT:"): verdict = last[len("RESULT:"):].strip()
    else: verdict = f"FAIL no RESULT line ({d.get('subtype')})"
    if guards: verdict = f"FAIL guard:{','.join(guards)} | {verdict}"

    u = d.get("usage") or {}
    # in = uncached input only; cw/cr = cache write/read. Total prompt size is in+cw+cr (cr is usually the bulk).
    tin, tcw, tcr, tout = (u.get(k) for k in ("input_tokens", "cache_creation_input_tokens",
                                               "cache_read_input_tokens", "output_tokens"))
    line = (f"{stamp}\t{a.role}\t{model}\t{verdict}\tturns={d.get('num_turns')}\t{secs}s\t"
            f"out={tout}\tlist$={d.get('total_cost_usd')}\tdenials={len(denials)}\t{rel(out_path)}\t"
            f"feature={feature}\ttest={test_cmd or '-'}\tin={tin}\tcw={tcw}\tcr={tcr}\trepo={repo.name}")
    with open(scratch / "runs.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(f"RESULT {a.role} {verdict} | turns={d.get('num_turns')} {secs}s denials={len(denials)} "
          f"tok=in {short(tin)} / cache {short((tcw or 0) + (tcr or 0))} / out {short(tout)} "
          f"test={test_cmd or '-'} | {rel(out_path)}")
    for x in denials[:5]:
        i = x.get("tool_input") or {}
        print(f"  denied {x.get('tool_name')}: {i.get('command') or i.get('file_path')}")
    return 0 if verdict.startswith("PASS") else 1


if __name__ == "__main__":
    sys.exit(main())
