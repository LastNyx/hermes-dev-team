#!/usr/bin/env python3
"""Show what grew in the team skills since the last commit, and draft the CHANGELOG lines. No model is called.

Usage:  python <kit>/review-skills.py [--stat] [--patch]
Skills are linked into Hermes (link-skills.py), so Hermes' edits appear as uncommitted changes under skills/.
Exit 0 = nothing changed, 1 = changes to review.

Then: keep what helps every project, `git restore skills/<name>` what does not, paste the draft into
CHANGELOG.md under a new version, commit.

--patch prints ONE `git apply`-able patch of every uncommitted change in the repo (skills, kit, docs, new files
included) and nothing else, for a machine that cannot push: save it to a file, apply it on a machine that can with
`git apply <file>`. The index is restored afterwards.
"""
import argparse, pathlib, re, subprocess, sys

REPO = pathlib.Path(__file__).resolve().parent.parent
LOCAL_HINTS = re.compile(r"[A-Za-z]:[\\/]|/Users/|/home/|AppData|\\Users\\", re.I)


def git(*args):
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stat", action="store_true", help="file list only, no diff body")
    ap.add_argument("--patch", action="store_true", help="print one git-apply patch of every uncommitted change")
    a = ap.parse_args()
    if a.patch:
        new = [l[3:].strip().strip('"') for l in git("status", "--porcelain").splitlines() if l.startswith("??")]
        if new:
            git("add", "--intent-to-add", "--", *new)          # so untracked files show in the diff
        try:
            out = git("diff", "--binary", "HEAD")
        finally:
            if new:
                git("reset", "--quiet", "--", *new)            # back to untracked; the files are untouched
        if not out.strip():
            print("# nothing to hand over: no uncommitted changes", file=sys.stderr); return 0
        sys.stdout.write(f"# base: {git('rev-parse', '--short', 'HEAD').strip()}\n" + out)
        return 1
    status = [l for l in git("status", "--porcelain", "--", "skills").splitlines() if l.strip()]
    if not status:
        print("skills: no changes since the last commit."); return 0
    changed = {}
    for l in status:
        path = l[3:].strip().strip('"')
        parts = pathlib.PurePosixPath(path).parts
        if len(parts) >= 2:
            changed.setdefault(parts[1], []).append((l[:2].strip() or "M", "/".join(parts[2:]) or "(folder)"))
    print(f"{len(changed)} skill(s) changed:\n")
    for name, files in changed.items():
        print(f"== {name}: " + ", ".join(f"{s} {f}" for s, f in files))
    print()
    if not a.stat:
        diff = git("diff", "--", "skills")                 # tracked files; read-only, the index is never touched
        for name, files in changed.items():
            for s, f in files:
                if s == "??":                              # untracked: show the new file(s) in full
                    p = REPO / "skills" / name / (f if f != "(folder)" else "")
                    for q in ([p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file())):
                        body = q.read_text(encoding="utf-8", errors="replace")
                        diff += f"\n+++ new file: {q.relative_to(REPO).as_posix()}\n" + \
                                "".join("+" + l + "\n" for l in body.splitlines())
        print(diff)
        added = [l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
        flagged = [l for l in added if LOCAL_HINTS.search(l)]
        if flagged:
            print("WARNING: added lines that look machine-specific (paths). Remove before committing:")
            for l in flagged[:10]:
                print("   ", l.strip()[:160])
            print()
    print("Draft CHANGELOG lines (edit the wording, keep the file names):")
    for name, files in changed.items():
        what = "new skill" if any(s in ("??", "A") and f == "SKILL.md" for s, f in files) else "updated"
        print(f"- `skills/{name}`: {what} ({', '.join(f for _, f in files)}): <what it learned>")
    return 1


if __name__ == "__main__":
    sys.exit(main())
