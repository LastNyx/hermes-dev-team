#!/usr/bin/env python3
"""Check a feature's tickets before dispatch, and list what can run next. No model is called.

Usage (cwd = a repo or the workspace):
    python <kit>/tickets.py <feature>              # check every ticket, print the approval table
    python <kit>/tickets.py <feature> --add-header # dry run: show which tickets lack the worker header
    python <kit>/tickets.py <feature> --add-header --apply
    python <kit>/tickets.py <feature> --next       # tickets that are todo and whose blockers are all done

<feature> is a folder name under the scratch folder (.team.json `scratch`, else .scratch) or a path.
Tickets are <feature>/issues/NN-slug.md. --add-header also moves flat <feature>/NN-*.md files into issues/.
It inserts the header from skills/orchestrate/templates/ticket-header.md.template after the title, keeps the body,
and carries over Matt Pocock's `**Blocked by:**` line; other fields stay `<...>` placeholders for the planner.
Exit 0 = every ticket is dispatchable, 1 = something to fix.
"""
import argparse, pathlib, re, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import teamcfg  # noqa: E402

TEMPLATE = pathlib.Path(__file__).resolve().parent.parent / "skills/orchestrate/templates/ticket-header.md.template"
REQUIRED = ["Lane", "Branch", "Status", "Round", "Blocked by", "Terms", "Seam", "Test command"]
STATUSES = {"todo", "in-progress", "review", "blocked", "done"}


def header_fields():
    """The header block of the template (the lines between the title and the first `## ` section)."""
    lines = TEMPLATE.read_text(encoding="utf-8").splitlines()
    block = [l for l in lines[1:lines.index(next(l for l in lines if l.startswith("## ")))] if l.strip()]
    return block


def field(text, key):
    m = re.search(rf"^{re.escape(key)}:[ \t]*(.*?)[ \t]*$", text, re.M)
    if not m:
        return None
    return m.group(1).split("  #")[0].strip().strip("`").strip()


def number(path):
    m = re.match(r"(\d+)", path.name)
    return m.group(1) if m else path.stem


def blockers(value):
    if not value or value.lower().startswith("none"):
        return []
    return re.findall(r"\d+", value)


def find_feature(arg):
    p = pathlib.Path(arg).expanduser()
    return p.resolve() if p.is_dir() else teamcfg.load()["scratch"] / arg


def add_header(feature, apply):
    issues = feature / "issues"
    flat = sorted(p for p in feature.glob("*.md") if re.match(r"\d+", p.name))
    for p in flat:
        print(f"  {'move' if apply else 'would move'} {p.name} -> issues/{p.name}")
        if apply:
            issues.mkdir(exist_ok=True)
            p.rename(issues / p.name)
    tickets = sorted(issues.glob("*.md")) if issues.is_dir() else []
    if not apply:
        tickets += [issues / p.name for p in flat]
    block = header_fields()
    for t in sorted(set(tickets)):
        src = t if t.exists() else feature / t.name
        text = src.read_text(encoding="utf-8")
        if field(text, "Status") is not None and field(text, "Seam") is not None:
            continue
        lines = text.splitlines()
        title_at = next((i for i, l in enumerate(lines) if l.startswith("# ")), -1)
        mb = re.search(r"^\*\*Blocked by:\*\*\s*(.+)$", text, re.M)
        header = [re.sub(r"^Blocked by:.*", f"Blocked by: {mb.group(1).strip()}", l) if mb else l for l in block]
        body = [l for l in lines[title_at + 1:] if not re.match(r"^\*\*(Blocked by|Status):\*\*", l)]
        new = lines[:title_at + 1] + [""] + header + [""] + body
        print(f"  {'add' if apply else 'would add'} header to {t.name}")
        if apply:
            t.write_text("\n".join(new).rstrip() + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("feature")
    ap.add_argument("--add-header", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--next", action="store_true")
    a = ap.parse_args()
    feature = find_feature(a.feature)
    if not feature.is_dir():
        print(f"ERROR no feature folder {feature}"); return 2
    if a.add_header:
        add_header(feature, a.apply)
        if not a.apply:
            return 0
    tickets = sorted((feature / "issues").glob("*.md"))
    if not tickets:
        print(f"ERROR no tickets in {feature / 'issues'} (flat NN-*.md files? run with --add-header)"); return 1
    rows, problems = [], 0
    by_num = {}
    for t in tickets:
        text = t.read_text(encoding="utf-8")
        f = {k: field(text, k) for k in REQUIRED + ["Repo", "Verify"]}
        missing = [k for k in REQUIRED if not f[k] or f[k].startswith("<")]
        if f["Status"] and f["Status"] not in STATUSES:
            missing.append(f"Status={f['Status']}")
        if f["Repo"] is not None and (not f["Repo"] or f["Repo"].startswith("<")):
            missing.append("Repo (fill it, or delete the line in a one-repo project)")
        if f["Verify"] is not None and (not f["Verify"] or f["Verify"].startswith("<")):
            missing.append("Verify (a planner check, or None)")
        if f["Seam"] and f["Seam"].lower() == "none":
            missing.append("Seam=None")
        by_num[number(t)] = f
        rows.append((t, f, missing))
        problems += bool(missing)
    if a.next:
        ready = [t for t, f, m in rows if f["Status"] == "todo" and not m
                 and all((by_num.get(b) or {}).get("Status") == "done" for b in blockers(f["Blocked by"]))]
        for t in ready:
            f = by_num[number(t)]
            print(f"{t.name}\trepo={f['Repo'] or '-'}\tbranch={f['Branch']}")
        if not ready:
            print("nothing ready (all done, in progress, blocked, or waiting on blockers)")
        return 0
    print(f"{'ticket':<34}{'repo':<18}{'status':<12}{'blocked by':<12}seam | test command | verify")
    for t, f, missing in rows:
        print(f"{t.name[:33]:<34}{(f['Repo'] or '-')[:17]:<18}{(f['Status'] or '?'):<12}"
              f"{','.join(blockers(f['Blocked by'])) or '-':<12}{f['Seam'] or '?'} | {f['Test command'] or '?'}"
              f" | {f['Verify'] or '-'}")
        if missing:
            print(f"    FIX: {', '.join(missing)}")
    unknown = sorted({b for _, f, _ in rows for b in blockers(f["Blocked by"]) if b not in by_num})
    if unknown:
        print(f"FIX: Blocked by names tickets that do not exist: {', '.join(unknown)}"); problems += 1
    print(f"{len(rows)} ticket(s), {problems} to fix")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
