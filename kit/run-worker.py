#!/usr/bin/env python3
"""Run one headless Claude Code worker in a project repo, under a role's allow-list.

Usage (cwd = the project repo root):
    python <kit>/run-worker.py <role> "<prompt>" [--test-cmd "<cmd>"] [--model M] [--max-turns N]

The allowed test command is found in this order: --test-cmd; for dev, the ticket's `Test command:` line
(prompt `ticket: <path>`); then `"test"` in the project's .team.json. Models can be set per role in
.team.json `"models": {"dev": "sonnet"}`. The feature name for the cost log comes from `.scratch/<feature>/` in the prompt.

Roles live next to this file: roles/<role>.md (prompt) and roles/<role>.json (settings).
Prints ONE line and exits 0 only when the worker's last line is `RESULT: PASS` and the guards hold.
Every run appends a line to .scratch/runs.log and keeps the full JSON in .scratch/runs/.

Measured facts this relies on (Claude Code 2.1.296):
- `claude -p` exits 0 even when the worker gives up, so the RESULT line is parsed here.
- `--setting-sources ""` ignores user/project settings, so a repo's .claude/settings.json cannot widen the allow-list.
- `Edit(path)` rules govern both Edit and Write; deny rules beat allow rules.
"""
import argparse, datetime, fnmatch, json, pathlib, re, shutil, subprocess, sys, time

KIT = pathlib.Path(__file__).resolve().parent
DEFAULTS = {"dev": ("sonnet", 40), "reviewer": ("sonnet", 25), "docs": ("haiku", 15)}
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


def load_team_cfg():
    p = pathlib.Path(".team.json")
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise SystemExit(f"RESULT ERROR .team.json is not valid JSON: {e}")


def ticket_test_cmd(prompt):
    m = re.search(r"ticket:\s*(\S+)", prompt)
    if not m or not pathlib.Path(m.group(1)).exists():
        return None
    t = re.search(r"^Test command:\s*(.+?)\s*$", pathlib.Path(m.group(1)).read_text(encoding="utf-8"), re.M)
    cmd = t.group(1).strip().strip("`").strip() if t else ""
    return cmd if cmd and not cmd.startswith("<") else None


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

    cfg = load_team_cfg()
    model, turns = DEFAULTS.get(a.role, ("sonnet", 25))
    model = a.model or (cfg.get("models") or {}).get(a.role) or model
    turns = a.max_turns or turns
    test_cmd = a.test_cmd or (ticket_test_cmd(a.prompt) if a.role == "dev" else None) or \
        (cfg.get("test") if a.role != "docs" else None)
    fm = re.search(r"\.scratch/([^/\s]+)", a.prompt)
    feature = fm.group(1) if fm else "-"

    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    settings["includeCoAuthoredBy"] = False                      # always, whatever the role file says
    settings.setdefault("attribution", {}).update({"commit": "", "pr": ""})
    protected = PROTECTED + list(cfg.get("protected") or [])
    # measured: Edit(**/<name>) denies the file at the root and in subfolders, for both Edit and Write
    settings["permissions"].setdefault("deny", []).extend(f"Edit(**/{p})" for p in protected)
    prompt = a.prompt
    if test_cmd:
        settings["permissions"]["allow"].append(f"Bash({test_cmd})")
        # measured: without this line a reviewer ran the ticket's narrower command and was denied
        prompt += f"\nThe only test command you may run is exactly: {test_cmd}"

    runs = pathlib.Path(".scratch/runs"); runs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path, agents_path = runs / f"{stamp}-{a.role}.json", runs / f"{stamp}-{a.role}.agents.json"
    rules = project_memory(pathlib.Path("CLAUDE.md"))
    rules_block = ("\n\n## Project rules (the repo's CLAUDE.md, imports inlined; they bind you unless a tool rule "
                   "below forbids the action)\n\n" + rules.strip() + "\n") if rules.strip() else ""
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
           "--max-turns", str(turns), "--output-format", "json", "--no-session-persistence"]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    secs = round(time.time() - t0)
    out_path.write_text(proc.stdout, encoding="utf-8")
    try:
        d = json.loads(proc.stdout)
    except ValueError:
        d = {"subtype": "unparsed", "result": proc.stderr.strip()[-300:]}

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
    line = (f"{stamp}\t{a.role}\t{model}\t{verdict}\tturns={d.get('num_turns')}\t{secs}s\t"
            f"out={u.get('output_tokens')}\tlist$={d.get('total_cost_usd')}\tdenials={len(denials)}\t{out_path.as_posix()}\t"
            f"feature={feature}\ttest={test_cmd or '-'}")
    with open(".scratch/runs.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(f"RESULT {a.role} {verdict} | turns={d.get('num_turns')} {secs}s denials={len(denials)} "
          f"test={test_cmd or '-'} | {out_path.as_posix()}")
    for x in denials[:5]:
        i = x.get("tool_input") or {}
        print(f"  denied {x.get('tool_name')}: {i.get('command') or i.get('file_path')}")
    return 0 if verdict.startswith("PASS") else 1


if __name__ == "__main__":
    sys.exit(main())
