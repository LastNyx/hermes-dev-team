#!/usr/bin/env python3
"""Read-only git for workers: `git log`, `git diff`, `git show` with the options that write or reach outside refused.

    python <kit>/git-ro.py log|diff|show [args...]      # cwd = the repo; output and exit code are git's

Why (0.13.1): the role allow-lists granted `Bash(git log:*)` / `diff:*` / `show:*`, and a prefix rule cannot stop
trailing flags. Measured (Claude Code 2.1.296, git 2.56): `git log --format=format:<text> --output=<path>` was allowed
with 0 denials and wrote <path>, also inside `.git/hooks/` although `Edit(./.git/**)` was denied. The launcher now
grants this script instead of raw git log/diff/show.

Refused: `--output` (and any abbreviation git might accept), `--ext-diff`, `--no-index`, and for diff a path argument
that exists outside the repo (git then diffs outside files without `--no-index`). Last line on a refusal:
`GIT-RO STOP <reason>` (exit 2).
"""
import os, pathlib, subprocess, sys

SUBCOMMANDS = ("log", "diff", "show")
FORBIDDEN = ("--output", "--ext-diff", "--no-index")


def refusal(sub, args, repo):
    if sub not in SUBCOMMANDS:
        return f"only {', '.join(SUBCOMMANDS)} (got {sub!r}); other git commands go through their own allow rules"
    for arg in args:
        if arg == "--":
            continue
        name = arg.split("=", 1)[0]
        if name.startswith("--") and len(name) > 2 and any(f.startswith(name) for f in FORBIDDEN):
            return f"{arg} is refused (it writes a file or leaves the repo)"
    if sub == "diff":
        for arg in args:
            if arg.startswith("-"):
                continue
            p = pathlib.Path(arg)
            if p.exists() and repo not in (p.resolve(), *p.resolve().parents):
                return f"path {arg} is outside the repo"
    return None


def main(argv):
    if not argv:
        print(f"GIT-RO STOP usage: git-ro.py {'|'.join(SUBCOMMANDS)} [args...]"); return 2
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if top.returncode:
        print("GIT-RO STOP cwd is not a git repo"); return 2
    repo = pathlib.Path(top.stdout.strip()).resolve()
    why = refusal(argv[0], argv[1:], repo)
    if why:
        print(f"GIT-RO STOP {why}"); return 2
    return subprocess.run(["git", "--no-pager", *argv], env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
