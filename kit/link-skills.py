#!/usr/bin/env python3
"""Link this repo's skills into Hermes' skills directory, so skills that grow while you work land in the repo.

Usage:
    python kit/link-skills.py [--skills-dir DIR] [--apply]

Without --apply it only reports what it would do. For each folder in <repo>/skills/:
- missing locally            -> create a link (Windows: directory junction, no admin; elsewhere: symlink)
- already linked to the repo -> ok, nothing to do
- a plain copy, identical    -> replace it with a link
- a plain copy, different    -> SKIP and report: the local copy grew, so merge it into the repo first, then re-run
- a link pointing elsewhere  -> SKIP and report
Also: removes dangling links that point into this repo's skills/ (a skill the repo dropped), and reports any of
Matt Pocock's planning skills (UPSTREAM) that are not installed, with the install command. Exit 1 if anything
was skipped or missing.

Measured with Hermes skill_manage (2026-10-11): patch, write_file and full rewrite all write through a junction
and keep the link; delete refuses on a junction (remove the link with `rmdir` on Windows, `rm` elsewhere).
"""
import argparse, filecmp, os, pathlib, subprocess, sys

REPO_SKILLS = pathlib.Path(__file__).resolve().parent.parent / "skills"
# Matt Pocock's planning skills (github.com/mattpocock/skills), used as-is by the planner. Not vendored here, so
# they keep updating from upstream. grill-with-docs calls grilling + domain-modeling, so all three are needed.
UPSTREAM = ["grill-with-docs", "grilling", "domain-modeling", "to-tickets"]


def default_skills_dir():
    home = os.environ.get("HERMES_HOME")
    if home:
        return pathlib.Path(home) / "skills"
    if os.name == "nt":
        return pathlib.Path(os.environ["LOCALAPPDATA"]) / "hermes" / "skills"
    return pathlib.Path.home() / ".hermes" / "skills"


def link_target(p):
    try:
        t = os.readlink(p)
        if t.startswith("\\\\?\\"):          # Windows junctions report \\?\C:\...
            t = t[4:]
        return pathlib.Path(t).resolve()
    except OSError:
        return None


def same_tree(a, b):
    c = filecmp.dircmp(a, b)
    if c.left_only or c.right_only or c.funny_files:
        return False
    for f in c.common_files:
        # compare ignoring CRLF/LF, since Hermes on Windows writes CRLF
        if a.joinpath(f).read_bytes().replace(b"\r\n", b"\n") != b.joinpath(f).read_bytes().replace(b"\r\n", b"\n"):
            return False
    return all(same_tree(a / d, b / d) for d in c.common_dirs)


def make_link(link, target):
    if os.name == "nt":
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
        if r.returncode != 0:
            raise OSError(r.stderr.strip() or r.stdout.strip())
    else:
        os.symlink(target, link, target_is_directory=True)


def remove_copy(p):
    import shutil
    shutil.rmtree(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skills-dir", type=pathlib.Path, default=None)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    dest = (a.skills_dir or default_skills_dir()).resolve()
    if not dest.is_dir():
        print(f"ERROR skills dir not found: {dest}"); return 2
    print(f"repo skills: {REPO_SKILLS}\nhermes skills: {dest}\nmode: {'APPLY' if a.apply else 'dry run'}")
    skipped = 0
    # Hermes also keeps skills in category folders (skills/<category>/<name>/SKILL.md); find those copies too.
    nested = {}
    for skill_md in dest.glob("*/*/SKILL.md"):
        nested.setdefault(skill_md.parent.name, []).append(skill_md.parent)
    for src in sorted(p for p in REPO_SKILLS.iterdir() if (p / "SKILL.md").is_file()):  # git leaves empty dirs
        local = dest / src.name
        if not os.path.lexists(local) and src.name in nested:
            where = ", ".join(str(p.relative_to(dest)) for p in nested[src.name])
            print(f"  SKIP    {src.name}: already installed in a category folder ({where}). "
                  f"Diff it with the repo copy, merge if it grew, delete that copy, re-run.")
            skipped += 1; continue
        tgt = link_target(local) if local.exists() or os.path.lexists(local) else None
        if not os.path.lexists(local):
            action = "link (missing locally)"
        elif tgt is not None:
            if tgt == src.resolve():
                print(f"  ok      {src.name}: already linked"); continue
            print(f"  SKIP    {src.name}: linked elsewhere ({tgt})"); skipped += 1; continue
        elif same_tree(src, local):
            action = "replace identical copy with link"
        else:
            print(f"  SKIP    {src.name}: local copy differs from the repo (it grew). Diff it, merge into the repo, re-run.")
            skipped += 1; continue
        print(f"  {'do' if a.apply else 'would'}    {src.name}: {action}")
        if a.apply:
            if os.path.lexists(local):
                remove_copy(local)
            make_link(local, src.resolve())
    # Links left behind by skills the repo no longer has (e.g. grill-with-docs and to-tickets, removed in 0.5.0).
    # They point into this repo but the target is gone, so Hermes sees a broken skill. Only such links are removed.
    repo_root = REPO_SKILLS.resolve()
    for entry in sorted(dest.iterdir()):
        tgt = link_target(entry)
        if tgt is None or tgt.exists() or repo_root not in tgt.parents:
            continue
        print(f"  {'do' if a.apply else 'would'}    {entry.name}: remove dangling link (skill removed from the repo)")
        if a.apply:
            os.rmdir(entry) if os.name == "nt" else os.unlink(entry)   # removes the link only, never a target
    # Planning skills come from Matt Pocock's repo, not this one. Report what is missing; install nothing here.
    missing = [s for s in UPSTREAM if not (dest / s / "SKILL.md").exists() and s not in nested]
    for s in missing:
        print(f"  MISSING {s}: install with  hermes skills install skills-sh/mattpocock/skills/{s} --yes")
    if skipped:
        print(f"{skipped} skipped; nothing was overwritten.")
    return 1 if (skipped or missing) else 0


if __name__ == "__main__":
    sys.exit(main())
