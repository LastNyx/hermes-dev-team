"""The conditions `scratch.py dispatch` needs before it prepares a parallel group (no I/O).

`cfg` is `teamcfg.load()["cfg"]`, `tc` the whole `teamcfg.load()` result.
"""


def has_config(tc):
    """A `.team.json` was found."""
    return tc.get("source") is not None


def max_workers(cfg):
    """`.team.json` max_workers as an int; 0 when missing or not a number. A run needs >= 1 (the hard cap of 3 is the
    caller's)."""
    try:
        return int(cfg.get("max_workers") or 0)
    except (TypeError, ValueError):
        return 0


def has_setup(cfg):
    """`.team.json` has `setup`, the commands that install dependencies in a fresh worktree."""
    return bool(cfg.get("setup"))


def clean(dirty_text):
    """The repo has no uncommitted changes (`dirty_text` is `git status --short` output)."""
    return not dirty_text


def on_branch(current, wanted):
    """The repo is on the ticket's Branch."""
    return current == wanted
