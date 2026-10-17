#!/usr/bin/env python3
"""The planner's trail: one `runs.log` line with role `planner` per `scratch.py status` and `decide`. No model call.

A planner line has the same shape as a worker's (docs/watch-contract.md), so hermes-dev-watch lists it next to the
worker runs:

    <stamp> planner planner <verdict> turns=- <secs>s out=None list$=None denials=0 - feature= test=- in=None
    cw=None cr=None repo= backend=- ticket= action=<status|decide> fmt=

Verdicts: `PASS|FAIL|SKIP verify NN: <text>` for a status change with `--verify`, `NOTE status NN <status>` or
`NOTE decide: <text>` otherwise, so a NOTE never counts as a pass. `<secs>` is the time since the feature's previous
planner line, 0 for the first one or after a gap of more than GAP_HOURS (a new session). There is no progress log.
"""
import datetime, pathlib, re, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import teamcfg  # noqa: E402
import tickets as tk  # noqa: E402

ROLE = "planner"
GAP_HOURS = 2
STATUSES = tk.STATUSES
VERIFY_RE = re.compile(r"^\s*(PASS|FAIL|SKIP)\s*:\s*(\S.*)$", re.S)


def fold(s, n=200):
    """One line: whitespace (tabs and newlines included) folded to single spaces, at most n characters."""
    return " ".join(str(s).split())[:n]


def num(name):
    """The ticket's number token as written in its file name: `05`, `13`, `02b`."""
    m = re.match(r"\d+[a-z]?", name, re.I)
    return m.group(0) if m else name


def find_ticket(feature_dir, nn):
    """(path, None) or (None, reason). `nn` is a file name, a stem, or its leading number; a plain number never
    matches the fix tickets that share it (`05` is 05-x.md, not 05-fix-y.md)."""
    issues = feature_dir / "issues"
    nn = nn.removesuffix(".md")
    files = sorted(issues.glob("*.md")) if issues.is_dir() else []
    exact = [p for p in files if p.stem == nn]
    if exact:
        return exact[0], None
    hits = [p for p in files if p.stem.startswith(nn + "-")]
    if "-fix" not in nn:
        hits = [p for p in hits if "-fix-" not in p.name] or hits
    if len(hits) == 1:
        return hits[0], None
    if not hits:
        return None, f"no ticket {nn} in {issues.as_posix()}"
    return None, f"{nn} is ambiguous: {', '.join(p.name for p in hits)}; give the file name"


def read(p):
    raw = p.read_bytes()
    return raw.decode("utf-8").replace("\r\n", "\n"), b"\r\n" in raw


def write(p, text, crlf):
    p.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


def append(text, block):
    """Append a block at the end of the ticket (the `## Result` section, added when missing)."""
    if not re.search(r"^## Result\s*$", text, re.M):
        text = text.rstrip("\n") + "\n\n## Result\n"
    return text.rstrip("\n") + "\n\n" + block.rstrip("\n") + "\n"


def _ids(scratch):
    p = scratch / "runs.log"
    if not p.is_file():
        return set()
    return {"-".join(l.split("\t")[:2]) for l in p.read_text(encoding="utf-8").splitlines() if "\t" in l}


def new_stamp(scratch, now=None):
    """`YYYYMMDD-HHMMSS`, moved on by a second while `<stamp>-planner` is taken (two actions in one second)."""
    t, taken = (now or datetime.datetime.now()).replace(microsecond=0), _ids(scratch)
    while True:
        s = t.strftime("%Y%m%d-%H%M%S")
        if f"{s}-{ROLE}" not in taken:
            return s
        t += datetime.timedelta(seconds=1)


def secs_since(scratch, feature, stamp):
    p, last = scratch / "runs.log", None
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            f = line.split("\t")
            if len(f) >= 6 and f[1] == ROLE and f"feature={feature}" in f[6:]:
                last = f[0]
    if not last:
        return 0
    try:
        d = (datetime.datetime.strptime(stamp, "%Y%m%d-%H%M%S")
             - datetime.datetime.strptime(last, "%Y%m%d-%H%M%S")).total_seconds()
    except ValueError:
        return 0
    return int(d) if 0 <= d <= GAP_HOURS * 3600 else 0


def log(scratch, feature, verdict, action, ticket=None, repo=None, now=None):
    """Append one planner line to runs.log. Returns the run id."""
    stamp = new_stamp(scratch, now)
    secs = secs_since(scratch, feature, stamp)
    line = "\t".join([stamp, ROLE, ROLE, fold(verdict), "turns=-", f"{secs}s", "out=None", "list$=None", "denials=0",
                      "-", f"feature={feature}", "test=-", "in=None", "cw=None", "cr=None",
                      f"repo={repo or '-'}", "backend=-", f"ticket={ticket or '-'}", f"action={action}",
                      f"fmt={teamcfg.RUN_FORMAT}"])
    # ^ docs/watch-contract.md: the first six fields never move; keys are read by name; fmt stays last
    with open(scratch / "runs.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return f"{stamp}-{ROLE}"
