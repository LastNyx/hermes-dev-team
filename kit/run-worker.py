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
    """Run claude or hermes with stream-json, print + log one line per tool call and per denial; return the final result event.

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
                # Hermes (`hermes chat --format stream-json`, measured): flat events {"type": "tool_use", "name",
                # "input"} and {"type": "tool_result", "name", "is_error", "output"}; no allow-list, so no denials.
                if ev.get("type") == "tool_use" and "name" in ev:
                    emit(tool_line(ev.get("name"), ev.get("input") or {}))
                    continue
                if ev.get("type") == "tool_result" and "name" in ev:
                    if ev.get("is_error"):
                        emit(f"exit!=0 {ev.get('name')}: {' '.join(str(ev.get('output') or '').split())[:100]}")
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
    if final is not None:
        final.setdefault("_exit", proc.returncode)
    return final or {"subtype": "unparsed", "result": err.strip()[-300:], "_exit": proc.returncode}


def claude_ready():
    """True when Claude Code can run a worker: `claude` on PATH and an API key or a logged-in subscription.

    measured: `claude auth status` prints JSON with "loggedIn" and needs no model call.
    """
    if not shutil.which("claude"):
        return False
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    try:
        r = subprocess.run(["claude", "auth", "status"], capture_output=True, text=True, timeout=30)
        return bool(json.loads(r.stdout or "{}").get("loggedIn"))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def pick_backend(role, cfg, forced):
    """claude | hermes. `.team.json` "backend" is a string or a per-role map like "models"; default auto."""
    v = forced or cfg.get("backend") or "auto"
    if isinstance(v, dict):
        v = v.get(role) or v.get("default") or "auto"
    if v == "auto":
        return "claude" if claude_ready() else "hermes"
    if v not in ("claude", "hermes"):
        raise SystemExit(f"RESULT {role} ERROR unknown backend {v!r} (claude, hermes, auto)")
    return v


def hermes_home():
    if os.environ.get("HERMES_HOME"):
        return pathlib.Path(os.environ["HERMES_HOME"]).expanduser()
    la = os.environ.get("LOCALAPPDATA")
    if la and (pathlib.Path(la) / "hermes").is_dir():
        return pathlib.Path(la) / "hermes"
    return pathlib.Path.home() / ".hermes"


def hermes_profile_dir(name):
    return hermes_home() / "profiles" / name


HERMES_SETUP = """  one-time setup on this machine (run these yourself; the kit never edits Hermes config):
    hermes profile create {p} --no-skills --no-alias
    hermes -p {p} model          # pick the provider + default model for workers (e.g. anthropic, claude-sonnet-5-5)
    hermes -p {p} chat -q "reply OK" -Q   # first run installs dependencies (slow once)"""


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


def snapshot(protected):
    return {"main": git("rev-parse", "main"), "branch": git("branch", "--show-current"), "head": git("rev-parse", "HEAD"),
            "protected": sorted(protected_changes(None, protected)), "remotes": remote_refs()}


def guards_after(snap, protected):
    g = []
    if git("rev-parse", "main") != snap["main"]: g.append("main-moved")
    if git("branch", "--show-current") != snap["branch"]: g.append("branch-changed")
    if snap["head"] and git("merge-base", "--is-ancestor", snap["head"], "HEAD") is None:
        g.append("history-rewritten")                          # amend/reset dropped the old tip
    touched = [n for n in protected_changes(snap["head"], protected) if n not in snap["protected"]]
    if touched:
        g.append("test-config-changed:" + "+".join(touched[:3]))
    now = remote_refs()                                        # measured: a hermes worker pushed when not told not to
    pushed = [r for r, before in snap["remotes"].items() if before is not None and now.get(r) not in (None, before)]
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
            f"\tbackend={backend}{enforce}")
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
                    help="Claude Code effort; default .team.json effort (string or per-role map), else Claude's default")
    ap.add_argument("--backend", choices=["claude", "hermes", "auto"], help="default: .team.json backend, else auto")
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
    backend = pick_backend(a.role, cfg, a.backend)
    if backend == "claude" and not shutil.which("claude"):
        print(f"RESULT {a.role} ERROR claude CLI not on PATH"); return 2
    hprofile = cfg.get("hermes_profile") or "hdt-worker"
    if backend == "hermes":
        if not shutil.which("hermes"):
            print(f"RESULT {a.role} ERROR no Claude login/API key and hermes CLI not on PATH"); return 2
        if not hermes_profile_dir(hprofile).is_dir():
            print(HERMES_SETUP.format(p=hprofile))
            print(f"RESULT {a.role} ERROR no Hermes profile {hprofile} (setup commands above)"); return 2
    ticket = prompt_path(a.prompt, "ticket")
    want_repo = header_line(ticket, "Repo")
    if want_repo and want_repo != repo.name:
        print(f"RESULT {a.role} ERROR ticket is for repo {want_repo}, cwd is {repo.name}"); return 2
    model, turns = DEFAULTS.get(a.role, ("sonnet", 25))
    model = a.model or (cfg.get("models") or {}).get(a.role) or model
    turns = a.max_turns or turns
    effort = cfg.get("effort")
    effort = a.effort or ((effort or {}).get(a.role) if isinstance(effort, dict) else effort)
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
    if outside or backend == "hermes":                         # hermes: absolute paths, its cwd is not the repo
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
        prompt += (f"\nThe scratch folder is {scratch.as_posix()}: wherever your instructions say `.scratch/`, use that"
                   " folder (absolute paths).")
    glossary = cfg.get("glossary")
    if glossary and a.role != "dev":
        gp = (tc["base"] / glossary).expanduser().resolve()
        settings["permissions"]["allow"].append(f"Read({perm_path(gp)})")
        settings["permissions"]["deny"].append(f"Edit({perm_path(gp)})")
        prompt += f"\nThe glossary is {gp.as_posix()} (read it there; never edit it)."
    if test_cmd:
        settings["permissions"]["allow"].append(f"Bash({test_cmd})")
        # measured: without this line a reviewer ran the ticket's narrower command and was denied
        prompt += f"\nThe only test command you may run is exactly: {test_cmd}"

    runs = scratch / "runs"; runs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path, agents_path = runs / f"{stamp}-{a.role}.json", runs / f"{stamp}-{a.role}.agents.json"
    rules, rules_full, rules_used = project_rules(tc["rules"], tc["rules_max_chars"])
    rules_block = ("\n\n## Project rules (imports inlined; they bind you unless a tool rule below forbids the "
                   "action)\n\n" + rules.strip() + "\n") if rules.strip() else ""
    tool_rules = "_tool-rules-hermes.md" if backend == "hermes" else "_tool-rules.md"
    role_prompt = (prompt_file.read_text(encoding="utf-8").rstrip() + rules_block + "\n\n"
                   + (KIT / "roles" / tool_rules).read_text(encoding="utf-8"))

    branch_before = git("branch", "--show-current")
    if branch_before in ("main", "master"):
        print(f"RESULT {a.role} ERROR refusing to run on {branch_before}; check out the feature branch"); return 2
    snap = snapshot(protected)
    st = {"role": a.role, "model": model, "test_cmd": test_cmd, "feature": feature, "repo_name": repo.name,
          "repo": repo.as_posix(), "scratch": scratch.as_posix(), "stamp": stamp, "protected": protected,
          "snapshot": snap, "out_path": out_path.as_posix()}
    cut = f" rules={'+'.join(rules_used) or 'none'}"
    if rules_full > tc["rules_max_chars"]:
        cut += f"({round(rules_full / 1000)}k>cap)"

    env = dict(os.environ)
    if tc["path"]:
        env["PATH"] = os.pathsep.join(tc["path"] + [env.get("PATH", "")])
    progress_path = runs / f"{stamp}-{a.role}.progress.log"

    if backend == "hermes":
        return run_hermes(st, role_prompt, prompt, runs, cut, cfg, hprofile, effort, turns, env, progress_path,
                          snap, protected, out_path)

    agents_path.write_text(json.dumps({a.role: {"description": f"{a.role} worker", "prompt": role_prompt}}),
                           encoding="utf-8")
    cmd = ["claude", "-p", prompt, "--agents", str(agents_path), "--agent", a.role, "--tools", TOOLS,
           "--settings", json.dumps(settings), "--setting-sources", "", "--model", model,
           "--max-turns", str(turns), "--output-format", "stream-json", "--verbose", "--no-session-persistence"]
    if effort:
        cmd += ["--effort", effort]
    print(f"START {a.role} claude:{model}{'/' + effort if effort else ''} max-turns={turns}{cut} | "
          f"progress: {rel(progress_path)}", flush=True)
    t0 = time.time()
    d = stream_worker(cmd, progress_path, env)
    secs = round(time.time() - t0)
    out_path.write_text(json.dumps(d), encoding="utf-8")
    verdict = verdict_of(d.get("result"), d.get("subtype"), guards_after(snap, protected))
    u = d.get("usage") or {}
    # in = uncached input only; cw/cr = cache write/read. Total prompt size is in+cw+cr (cr is usually the bulk).
    usage = tuple(u.get(k) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
                                     "output_tokens"))
    return report(st, verdict, d.get("num_turns"), secs, usage, d.get("total_cost_usd"),
                  d.get("permission_denials") or [], out_path, "claude")


def run_hermes(st, role_prompt, prompt, runs, cut, cfg, profile, effort, turns, env, progress_path, snap,
               protected, out_path):
    """Fallback backend: one headless `hermes -p <profile> chat` run, judged exactly like a claude run.

    measured (Sonnet 5.5, anthropic provider, toy ticket): 28 s, 8 API calls, sessions stay in the worker profile.
    No allow-list: with the forbidden list in the prompt it refused all 4 forbidden chores; without it, it did all 4.
    """
    hmodel = (cfg.get("hermes_models") or {}).get(st["role"])
    pf = runs / f"{st['stamp']}-{st['role']}.prompt.md"
    pf.write_text(role_prompt + f"\n\n## Your task\n\nRepo root: {st['repo']} (run every command from there; you are "
                  f"on branch `{snap['branch']}`).\n\n{prompt}\n", encoding="utf-8")
    cmd = ["hermes", "-p", profile, "chat", "--query-file", str(pf), "-Q", "--in", st["repo"],
           "-t", "file,terminal", "--max-turns", str(turns), "--ignore-rules", "--source", "tool",
           "--format", "stream-json"]
    if hmodel:
        cmd += ["-m", hmodel]
    if cfg.get("hermes_provider"):
        cmd += ["--provider", cfg["hermes_provider"]]
    if effort:
        cmd += ["--reasoning", effort]
    env.pop("TERMINAL_CWD", None)                # measured: it overrides --in and starts the worker in the home folder
    print(f"START {st['role']} hermes:{hmodel or profile + '-default'}{'/' + effort if effort else ''} "
          f"max-turns={turns}{cut} enforce=prompt-only | progress: {rel(progress_path)}", flush=True)
    t0 = time.time()
    d = stream_worker(cmd, progress_path, env)
    secs = round(time.time() - t0)
    out_path.write_text(json.dumps(d), encoding="utf-8")
    st["model"] = hmodel or f"{profile}-default"
    subtype = "success" if d.get("_exit") == 0 else f"exit={d.get('_exit')}"
    verdict = verdict_of(d.get("text") if "text" in d else d.get("result"), subtype, guards_after(snap, protected))
    if verdict.startswith("PASS") and d.get("_exit") != 0:     # measured: turn cap = exit 1, model writes its own line
        verdict = f"FAIL hermes exit={d.get('_exit')} | {verdict}"
    t = d.get("tokens") or {}
    usage = (t.get("input"), t.get("cache_write"), t.get("cache_read"), t.get("output"))
    return report(st, verdict, "-", secs, usage, None, [], out_path, "hermes")


if __name__ == "__main__":
    sys.exit(main())
