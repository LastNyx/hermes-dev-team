#!/usr/bin/env python3
"""Read-only git for workers: `git log`, `git diff`, `git show` with the options that write or reach outside refused.

    python <kit>/git-ro.py log|diff|show [args...]      # cwd = the repo; output and exit code are git's

Why (0.13.1): the role allow-lists granted `Bash(git log:*)` / `diff:*` / `show:*`, and a prefix rule cannot stop
trailing flags. Measured (Claude Code 2.1.296, git 2.56): `git log --format=format:<text> --output=<path>` was allowed
with 0 denials and wrote <path>, also inside `.git/hooks/` although `Edit(./.git/**)` was denied. The launcher now
grants this script instead of raw git log/diff/show.

Refused: `--output` (and any abbreviation git might accept), `--ext-diff`, `--no-index`, `--textconv` (from `--textc`;
plain `--text` is allowed), and for diff a path argument that exists outside the repo (git then diffs outside files
without `--no-index`). Last line on a refusal: `GIT-RO STOP <reason>` (exit 2).

0.13.3: git runs with teamcfg.GIT_SAFE and `--no-ext-diff --no-textconv`, so the repo's own config cannot make a
read run a program (diff.external, core.fsmonitor, gpg.program, textconv drivers); measured in tests/git_guard.py.
"""
import os, pathlib, subprocess, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import teamcfg  # noqa: E402

SUBCOMMANDS = ("log", "diff", "show")
# option -> shortest prefix refused (git accepts unambiguous abbreviations; `--text` is a different, harmless option)
FORBIDDEN = {"--output": "--o", "--ext-diff": "--e", "--no-index": "--no-i", "--textconv": "--textc"}


def refusal(sub, args, repo):
    if sub not in SUBCOMMANDS:
        return f"only {', '.join(SUBCOMMANDS)} (got {sub!r}); other git commands go through their own allow rules"
    for arg in args:
        if arg == "--":
            continue
        name = arg.split("=", 1)[0]
        if any(name.startswith(least) and opt.startswith(name) for opt, least in FORBIDDEN.items()):
            return f"{arg} is refused (it writes a file, runs a program or leaves the repo)"
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
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    top = subprocess.run(["git", *teamcfg.GIT_SAFE, "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                         env=env)
    if top.returncode:
        print("GIT-RO STOP cwd is not a git repo"); return 2
    repo = pathlib.Path(top.stdout.strip()).resolve()
    why = refusal(argv[0], argv[1:], repo)
    if why:
        print(f"GIT-RO STOP {why}"); return 2
    return subprocess.run(["git", *teamcfg.GIT_SAFE, argv[0], "--no-ext-diff", "--no-textconv", *argv[1:]],
                          env=env).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
