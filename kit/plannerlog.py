#!/usr/bin/env python3
"""The planner's own trail: `runs.log` lines with role `planner`, a short progress log, and the WARN checks.

Used by scratch.py (`status`, `fix`, `rewrite`, `decide`) and tickets.py. No model is called. A planner line has the
same shape as a worker's (docs/watch-contract.md), so hermes-dev-watch shows it next to worker runs:

    <stamp> planner planner <verdict> turns=- <secs>s out=None list$=None denials=0 runs/<id>.progress.log
    feature= test=- in=None cw=None cr=None repo= backend=- ticket= action= fmt=

Verdicts: `PASS|FAIL|SKIP verify NN: <text>` for a check, `NOTE <action> ...` for everything else (status, fix,
rewrite, decide), so a NOTE never counts as a pass. `<secs>` is the time since the feature's previous planner line,
0 for the first one or after a gap of more than GAP_HOURS (a new session). Design: docs/open/planner-log.md.
"""
import datetime, pathlib, re, subprocess, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import teamcfg  # noqa: E402
import tickets as tk  # noqa: E402

ROLE = "planner"
GAP_HOURS = 2
STATUSES = tk.STATUSES
VERIFY_RE = re.compile(r"^\s*(PASS|FAIL|SKIP)\s*:\s*(\S.*)$", re.S)
# `Planner Verify: PASS ...` (scratch.py status --verify); also matches the hand-written `Planner Verify (browser): PASS`
RECORD_RE = re.compile(r"^Planner Verify\b[^:\n]*:\s*(PASS|FAIL|SKIP)\b", re.M)
FOUND_RE = re.compile(r"\bFound by the planner\b", re.I)          # also the older "(Found by the planner while ...)"
REWRITTEN_RE = re.compile(r"\bRewritten by the planner\b", re.I)
FIX_NAME_RE = re.compile(r"^\d+[a-z]?-fix-[a-z0-9][a-z0-9-]*$")


def fold(s, n=200):
    """One line: whitespace (tabs and newlines included) folded to single spaces, at most n characters."""
    return " ".join(str(s).split())[:n]


def num(name):
    """The ticket's number token as written in its file name: `05`, `13`, `02b`."""
    m = re.match(r"\d+[a-z]?", name, re.I)
    return m.group(0) if m else name


def needs_verify(text):
    v = tk.field(text, "Verify")
    return bool(v) and not v.lower().startswith("none") and not v.startswith("<")


def last_record(text):
    found = RECORD_RE.findall(text)
    return found[-1] if found else None


# ---------------------------------------------------------------- tickets

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


def fix_tickets_for(feature_dir, target):
    """[(path, status)] of fix tickets (`*-fix-*`) whose Blocked by names the target's number."""
    tid, out = tk.ticket_id(target.name), []
    for p in sorted((feature_dir / "issues").glob("*.md")):
        if p == target or "-fix-" not in p.name:
            continue
        text = p.read_text(encoding="utf-8")
        if tid in tk.blockers(tk.field(text, "Blocked by")):
            out.append((p, tk.field(text, "Status")))
    return out


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


def add_to_batch(feature_dir, orig_num, new_num):
    """Add new_num to the batches.md line that lists orig_num. Returns the batch number, or None."""
    bf = feature_dir / "batches.md"
    if not bf.is_file():
        return None
    text, crlf = read(bf)
    lines = text.split("\n")
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*(\d+)\s*:\s*)([0-9a-z][0-9a-z,\s-]*?)(\s+base:.*)?$", line, re.I)
        if not m:
            continue
        ids = [tk.ticket_id(x.strip()) for x in m.group(3).split(",") if x.strip()]
        if tk.ticket_id(orig_num) not in ids:
            continue
        if tk.ticket_id(new_num) not in ids:
            lines[i] = m.group(1) + m.group(3).rstrip().rstrip(",") + "," + new_num + (m.group(4) or "")
            write(bf, "\n".join(lines), crlf)
        return m.group(2)
    return None


# ---------------------------------------------------------------- the runs.log line

def _ids(scratch):
    p = scratch / "runs.log"
    if not p.is_file():
        return set()
    return {"-".join(l.split("\t")[:2]) for l in p.read_text(encoding="utf-8").splitlines() if "\t" in l}


def new_stamp(scratch, now=None):
    """`YYYYMMDD-HHMMSS`, moved on by a second while `<stamp>-planner` is taken (two actions in one second)."""
    t, runs, taken = (now or datetime.datetime.now()).replace(microsecond=0), scratch / "runs", _ids(scratch)
    while True:
        s = t.strftime("%Y%m%d-%H%M%S")
        if f"{s}-{ROLE}" not in taken and not (runs / f"{s}-{ROLE}.progress.log").exists():
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


def log(scratch, feature, verdict, action, ticket=None, repo=None, steps=(), changes=(), now=None):
    """Append one planner line to runs.log and write its progress log. Returns the run id."""
    stamp = new_stamp(scratch, now)
    secs = secs_since(scratch, feature, stamp)
    runs = scratch / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    prog = runs / f"{stamp}-{ROLE}.progress.log"
    lines = [f"  [{0:>4}s] say: {fold(s, 160)}" for s in steps] + [f"  [{0:>4}s] {fold(c, 160)}" for c in changes]
    with open(prog, "w", encoding="utf-8") as f:          # text mode, like the workers' logs (\r\n on Windows)
        f.write("".join(l + "\n" for l in lines))
    line = "\t".join([stamp, ROLE, ROLE, fold(verdict), "turns=-", f"{secs}s", "out=None", "list$=None", "denials=0",
                      f"runs/{prog.name}", f"feature={feature}", "test=-", "in=None", "cw=None", "cr=None",
                      f"repo={repo or '-'}", "backend=-", f"ticket={ticket or '-'}", f"action={action}",
                      f"fmt={teamcfg.RUN_FORMAT}"])
    # ^ docs/watch-contract.md: the first six fields never move; keys are read by name; fmt stays last
    with open(scratch / "runs.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return f"{stamp}-{ROLE}"


# ---------------------------------------------------------------- WARN checks (resume, tickets.py)

def body(text):
    """The part of a ticket a rewrite changes: above `## Result`, without the Status line, checkboxes unticked."""
    text = re.split(r"^## Result", text.replace("\r\n", "\n"), flags=re.M)[0]
    keep = [l.rstrip().replace("- [x]", "- [ ]").replace("- [X]", "- [ ]") for l in text.split("\n")
            if not l.startswith("Status:")]
    return "\n".join(keep).strip()


def _git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return r.stdout if r.returncode == 0 else None


def rewritten_after_dispatch(t, text):
    """True when the ticket's body differs from the commit that first set it in-progress (scratch repo history)."""
    first = (_git(t.parent, "log", "--reverse", "--format=%H", "-S", "Status: in-progress", "--", t.name) or "").split()
    if not first:
        return False
    old = _git(t.parent, "show", f"{first[0]}:./{t.name}")
    return old is not None and body(old) != body(text)


def named_by_review(reviews, t):
    """True when a review file names this fix ticket: its file stem, or its number on a line that says `fix`."""
    if t.stem in reviews:
        return True
    n = re.escape(num(t.name).lstrip("0") or "0")
    return any(re.search(rf"\b0*{n}\b", l) for l in reviews.splitlines() if re.search(r"\bfix", l, re.I))


def warnings(feature_dir):
    """WARN lines (never refusals) for planner steps that left no record. A feature whose tickets are all done is
    finished and gets none (features from before these records would only show noise)."""
    out = []
    reviews = "\n".join(p.read_text(encoding="utf-8") for p in sorted(feature_dir.glob("review-*.md")))
    ts = sorted((feature_dir / "issues").glob("*.md")) if (feature_dir / "issues").is_dir() else []
    texts = {t: t.read_text(encoding="utf-8") for t in ts}
    if texts and all(tk.field(x, "Status") == "done" for x in texts.values()):
        return []
    for t, text in texts.items():
        st = tk.field(text, "Status")
        if st in ("review", "done") and needs_verify(text) and not last_record(text):
            out.append(f"{t.name}: {st} with a Verify line but no `Planner Verify:` record "
                       "(scratch.py status --verify)")
        if "-fix-" in t.name and not FOUND_RE.search(text) and not named_by_review(reviews, t):
            out.append(f"{t.name}: a fix ticket with neither `Found by the planner:` nor a review file naming it "
                       "(planner fix tickets: scratch.py fix)")
        if st in ("in-progress", "review", "done") and not REWRITTEN_RE.search(text) \
                and rewritten_after_dispatch(t, text):
            out.append(f"{t.name}: changed after it was dispatched, with no `Rewritten by the planner:` line "
                       "(scratch.py rewrite)")
    return out
