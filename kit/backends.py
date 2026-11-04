#!/usr/bin/env python3
"""The worker runner: everything that depends on the agent CLI that runs a worker. Used by run-worker.py.

Since 2.0.0 the only runner is `claude -p` (headless hermes and delegate children were removed). It gives the launcher:
    ready()              can it run on this machine (no model call)
    command(job)         the argv of one headless run, under the role's allow-list
    progress(ev, names)  progress lines for one stream-json event (`<Tool> <arg>`, `say: `, `DENIED `)
    result(final, job)   the run's final text, subtype, turns, usage (in, cw, cr, out), list price and denials

Notes: docs/backend-claude.md.
"""
import json, os, pathlib, shutil, subprocess

KIT = pathlib.Path(__file__).resolve().parent
TOOLS = "Read,Edit,Write,Glob,Grep,Bash"


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


class Claude:
    """`claude -p` under a deny-by-default allow-list (`--settings`, `--setting-sources ""`, `--agents`)."""
    name = "claude"
    tool_rules = "_tool-rules.md"
    enforce = ""                                    # the allow-list enforces; nothing to tag

    @staticmethod
    def ready():
        """`claude` on PATH and an API key or a logged-in seat. measured: `claude auth status` needs no model call."""
        if not shutil.which("claude"):
            return False
        if os.environ.get("ANTHROPIC_API_KEY"):
            return True
        try:
            r = subprocess.run(["claude", "auth", "status"], capture_output=True, text=True, timeout=30)
            return bool(json.loads(r.stdout or "{}").get("loggedIn"))
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False

    @staticmethod
    def setup_error(cfg):
        return None if shutil.which("claude") else "claude CLI not on PATH"

    @staticmethod
    def command(job):
        tools = TOOLS if job["bash"] else TOOLS.replace(",Bash", "")
        if job["web"]:
            tools += ",WebFetch,WebSearch"
        agents = job["runs"] / f"{job['stamp']}-{job['role']}.agents.json"
        agents.write_text(json.dumps({job["role"]: {"description": f"{job['role']} worker", "prompt": job["role_prompt"]}}),
                          encoding="utf-8")
        cmd = ["claude", "-p", job["prompt"], "--agents", str(agents), "--agent", job["role"], "--tools", tools,
               "--settings", json.dumps(job["settings"]), "--setting-sources", "", "--model", job["model"],
               "--max-turns", str(job["turns"]), "--output-format", "stream-json", "--verbose", "--no-session-persistence"]
        if job["effort"]:
            cmd += ["--effort", job["effort"]]
        # measured (2.1.302): a `cd` inside an allowed command persisted into later calls (a second `cd ui && ...`
        # failed in `ui/`); with this set, `pwd` after `cd src && ls` printed the repo root again.
        job["env"]["CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR"] = "1"
        return cmd, job["model"]

    @staticmethod
    def progress(ev, names):
        out = []
        # measured (2.1.296): a denied call comes back as a tool_result "This command requires approval" with
        # tool_result_meta[].permission_decision.decision == "reject"; the wording mentions neither "denied" nor
        # "permission", so match the structured field first.
        rejected = {m.get("id") for m in ev.get("tool_result_meta") or [] if isinstance(m, dict)
                    and (m.get("permission_decision") or {}).get("decision") == "reject"}
        msg = ev.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        for c in content if isinstance(content, list) else []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "text" and ev.get("type") == "assistant" and (c.get("text") or "").strip():
                out.append("say: " + " ".join(c["text"].split())[:160])     # what the worker says it is doing
            elif c.get("type") == "tool_use":
                names[c.get("id")] = c.get("name")
                out.append(tool_line(c.get("name"), c.get("input") or {}))
            elif c.get("type") == "tool_result" and c.get("is_error"):
                body = c.get("content")
                body = body if isinstance(body, str) else json.dumps(body)
                low = body.lower()
                if c.get("tool_use_id") in rejected or "requires approval" in low or "permission" in low:
                    out.append(f"DENIED {names.get(c.get('tool_use_id'), '?')}: {' '.join(body.split())[:100]}")
        return out

    @staticmethod
    def result(d, job):
        u = d.get("usage") or {}
        # in = uncached input only; cw/cr = cache write/read. Total prompt size is in+cw+cr (cr is usually the bulk).
        usage = tuple(u.get(k) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
                                         "output_tokens"))
        return {"text": d.get("result"), "subtype": d.get("subtype"), "turns": d.get("num_turns"), "usage": usage,
                "cost": d.get("total_cost_usd"), "denials": d.get("permission_denials") or [], "extra": None}


BACKENDS = {"claude": Claude}


def progress_any(ev, names):
    """Progress lines for one stream-json event."""
    return Claude.progress(ev, names)


def pick(role, cfg, forced=None):
    """The one backend (2.0.0: `claude -p` only; the headless hermes backend and delegate children are gone)."""
    return Claude
