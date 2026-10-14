"""Find a project's team config, scratch folder and worker rules. Shared by run-worker.py, costs.py and tickets.py.

Lookup, from the repo root (or any folder):
1. <dir>/.team.json: the repo's own config (committed). Scratch defaults to <dir>/.scratch.
2. else <dir>/../.team.json with a "repos" map that names this folder: a WORKSPACE config, for several repos side by
   side whose team files must stay out of the repos. Shared keys apply to every repo; "repos": {"<folder>": {...}}
   overrides them per repo. Scratch defaults to <workspace>/.scratch.
Relative paths in a config resolve from the folder the config file sits in.

Keys: test, models, protected (as before), scratch (folder for tickets, reviews, runs), rules (file or list of files
pasted into every worker prompt; default: the repo's AGENTS.md and CLAUDE.md, whichever exist, in that order), rules_max_chars (default 40000), path (folders put in
front of PATH for the worker, e.g. ["~/.local/bin"]).
"""
import json, os, pathlib

RULES_MAX_CHARS = 40000


def _read(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise SystemExit(f"RESULT ERROR {p} is not valid JSON: {e}")


def _list(v):
    return [v] if isinstance(v, str) else list(v or [])


def load(folder=None):
    d = pathlib.Path(folder or os.getcwd()).resolve()
    own, ws = d / ".team.json", d.parent / ".team.json"
    cfg, base, source = {}, d, None
    if own.is_file():
        cfg, source = _read(own), own
        if d.name in (cfg.get("repos") or {}):        # run from inside a workspace root that is also a repo: unusual
            cfg = {**{k: v for k, v in cfg.items() if k != "repos"}, **cfg["repos"][d.name]}
    elif ws.is_file():
        w = _read(ws)
        if d.name in (w.get("repos") or {}):
            cfg = {**{k: v for k, v in w.items() if k != "repos"}, **(w["repos"][d.name] or {})}
            base, source = ws.parent, ws
    scratch = (base / cfg.get("scratch", ".scratch")).expanduser().resolve()
    rules = [(base / r).expanduser().resolve() for r in _list(cfg.get("rules"))] or \
        [d / n for n in ("AGENTS.md", "CLAUDE.md") if (d / n).is_file()]
    path = [str(pathlib.Path(p).expanduser()) for p in _list(cfg.get("path"))]
    return {"cfg": cfg, "source": source, "base": base, "scratch": scratch, "rules": rules, "path": path,
            "rules_max_chars": int(cfg.get("rules_max_chars", RULES_MAX_CHARS))}
