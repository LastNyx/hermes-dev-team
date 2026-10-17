#!/usr/bin/env python3
"""Worker backends: everything that depends on which agent CLI runs a worker. Used by run-worker.py.

Each backend gives the launcher four things, and nothing else in the kit knows which one ran:
    ready()              can it run on this machine (no model call)
    command(job)         the argv of one headless run (limits applied: allow-list for claude, prompt rules for hermes)
    progress(ev, names)  progress lines for one stream-json event (watch-contract shapes: `<Tool> <arg>`, `say: `,
                         `DENIED `, `exit!=0 `)
    result(final, job)   the run's final text, subtype, turns, usage (in, cw, cr, out), list price and denials

Notes per backend: docs/backend-claude.md, docs/backend-hermes.md.
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


def hermes_home():
    if os.environ.get("HERMES_HOME"):
        return pathlib.Path(os.environ["HERMES_HOME"]).expanduser()
    la = os.environ.get("LOCALAPPDATA")
    if la and (pathlib.Path(la) / "hermes").is_dir():
        return pathlib.Path(la) / "hermes"
    return pathlib.Path.home() / ".hermes"


HERMES_SETUP = """  one-time setup on this machine (run these yourself; the kit never edits Hermes config):
    hermes profile create {p} --no-skills --no-alias
    hermes -p {p} model          # pick the provider + default model for workers (e.g. anthropic, claude-sonnet-5-5)
    hermes -p {p} chat -q "reply OK" -Q   # first run installs dependencies (slow once)"""


class Hermes:
    """One headless `hermes -p <profile> chat` run. No allow-list: limits are prompt rules, judged by the guards."""
    name = "hermes"
    tool_rules = "_tool-rules-hermes.md"
    enforce = "prompt-only"

    @staticmethod
    def ready():
        return bool(shutil.which("hermes"))

    @staticmethod
    def profile(cfg):
        return cfg.get("hermes_profile") or "hdt-worker"

    @staticmethod
    def setup_error(cfg):
        if not shutil.which("hermes"):
            return "no Claude login/API key and hermes CLI not on PATH"
        p = Hermes.profile(cfg)
        if not (hermes_home() / "profiles" / p).is_dir():
            print(HERMES_SETUP.format(p=p))
            return f"no Hermes profile {p} (setup commands above)"
        return None

    @staticmethod
    def command(job):
        cfg, profile = job["cfg"], Hermes.profile(job["cfg"])
        hmodel = (cfg.get("hermes_models") or {}).get(job["role"])
        pf = job["runs"] / f"{job['stamp']}-{job['role']}.prompt.md"
        pf.write_text(job["role_prompt"] + f"\n\n## Your task\n\nRepo root: {job['repo']} (run every command from there; "
                      f"you are on branch `{job['branch']}`).\n\n{job['prompt']}\n", encoding="utf-8")
        toolsets = ("file,terminal" if job["bash"] else "file") + (",web" if job["web"] else "")  # researcher: no terminal
        cmd = ["hermes", "-p", profile, "chat", "--query-file", str(pf), "-Q", "--in", job["repo"],
               "-t", toolsets, "--max-turns", str(job["turns"]), "--ignore-rules", "--source", "tool",
               "--format", "stream-json"]
        if hmodel:
            cmd += ["-m", hmodel]
        if cfg.get("hermes_provider"):
            cmd += ["--provider", cfg["hermes_provider"]]
        if job["effort"]:
            cmd += ["--reasoning", job["effort"]]
        job["env"].pop("TERMINAL_CWD", None)        # measured: it overrides --in and starts the worker in the home folder
        return cmd, hmodel or f"{profile}-default"

    @staticmethod
    def progress(ev, names):
        # measured (`hermes chat --format stream-json`): flat {"type": "tool_use", "name", "input"} and
        # {"type": "tool_result", "name", "is_error", "output"}; no allow-list, so no denials.
        if ev.get("type") == "tool_use" and "name" in ev:
            return [tool_line(ev.get("name"), ev.get("input") or {})]
        if ev.get("type") == "tool_result" and "name" in ev and ev.get("is_error"):
            return [f"exit!=0 {ev.get('name')}: {' '.join(str(ev.get('output') or '').split())[:100]}"]
        return []

    @staticmethod
    def result(d, job):
        t = d.get("tokens") or {}
        ok = d.get("_exit") == 0
        return {"text": d.get("text") if "text" in d else d.get("result"),
                "subtype": "success" if ok else f"exit={d.get('_exit')}", "turns": "-",
                "usage": (t.get("input"), t.get("cache_write"), t.get("cache_read"), t.get("output")), "cost": None,
                "denials": [],
                # measured: a turn cap exits 1 while the model still writes its own RESULT line
                "extra": None if ok else f"hermes exit={d.get('_exit')}"}


BACKENDS = {"claude": Claude, "hermes": Hermes}


def progress_any(ev, names):
    """Progress lines for an event of either backend (their stream shapes do not overlap)."""
    return Hermes.progress(ev, names) or Claude.progress(ev, names)


def pick(role, cfg, forced):
    """The backend class. `.team.json` "backend" is a string or a per-role map like "models"; default auto."""
    v = forced or cfg.get("backend") or "auto"
    if isinstance(v, dict):
        v = v.get(role) or v.get("default") or "auto"
    if v == "auto":
        return Claude if Claude.ready() else Hermes
    if v not in BACKENDS:
        raise SystemExit(f"RESULT {role} ERROR unknown backend {v!r} (claude, hermes, auto)")
    return BACKENDS[v]
