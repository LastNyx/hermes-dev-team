#!/usr/bin/env python3
"""The research home: a standing git repo where the researcher works on code that belongs to no project.

Home = env HDT_RESEARCH_HOME, else ~/research (C:/Users/<you>/research on Windows, /home/<you>/research on Linux).
Keep it out of OneDrive-synced folders, AppData, and paths with spaces.

    python <kit>/research.py init                 # create the home (git repo, .team.json, .gitignore src/); idempotent
    python <kit>/research.py clone <url> [--name N]  # shallow clone into <home>/src/<name>; reuses an existing one
    python <kit>/research.py topic <topic>        # create <home>/.scratch/<topic>/ and print questions/findings paths
    python <kit>/research.py done <name>          # delete <home>/src/<name> (the user said done)
    python <kit>/research.py list                 # clones and topics

Clones are untrusted code: nothing here runs them. They are git-ignored, so they never travel; the findings live in
the home's scratch folder (`scratch.py init --remote <url>` from the home carries them between machines).
Last line: `RESEARCH OK ...` (exit 0) or `RESEARCH STOP <reason>` (exit 1).
"""
import argparse, json, os, pathlib, re, shutil, stat, subprocess, sys

ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


def home():
    return pathlib.Path(os.environ.get("HDT_RESEARCH_HOME") or pathlib.Path.home() / "research").expanduser().resolve()


def git(cwd, *a):
    r = subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, env=ENV)
    return r.returncode, (r.stdout + r.stderr).strip()


def cmd_init(_):
    h = home()
    if " " in str(h):
        print(f"RESEARCH STOP {h.as_posix()} has a space; set HDT_RESEARCH_HOME to a path without spaces"); return 1
    h.mkdir(parents=True, exist_ok=True)
    made = []
    if not (h / ".git").is_dir():
        git(h, "init", "-q", "-b", "main"); made.append("git init")
    for name, body in ((".team.json", json.dumps({"scratch": ".scratch"}, indent=1) + "\n"),
                       (".gitignore", "src/\n.scratch/\n__pycache__/\n")):
        if not (h / name).is_file():
            (h / name).write_text(body, encoding="utf-8"); made.append(name)
    (h / "src").mkdir(exist_ok=True)
    (h / ".scratch").mkdir(exist_ok=True)
    if git(h, "rev-parse", "-q", "--verify", "HEAD")[0]:
        git(h, "add", ".team.json", ".gitignore")
        rc, out = git(h, "commit", "-q", "-m", "research home")
        if rc:
            print(f"RESEARCH STOP first commit failed (set git user.name/user.email?): {out[-200:]}"); return 1
        made.append("first commit")
    print(f"RESEARCH OK home {h.as_posix()}" + (f" ({', '.join(made)})" if made else " (already set up)"))
    return 0


def cmd_clone(a):
    h = home()
    if not (h / ".git").is_dir():
        print("RESEARCH STOP no research home; run `research.py init` first"); return 1
    name = a.name or re.sub(r"\.git$", "", a.url.rstrip("/").split("/")[-1])
    if not re.fullmatch(r"[\w.-]+", name):
        print(f"RESEARCH STOP bad name {name!r}; pass --name"); return 1
    dest = h / "src" / name
    if dest.is_dir():
        rc, head = git(dest, "log", "-1", "--format=%h %ad", "--date=short")
        print(f"RESEARCH OK reused {dest.as_posix()} at {head} (`done {name}` deletes it)"); return 0
    rc, out = git(h / "src", "clone", "-q", "--depth", str(a.depth), a.url, name)
    if rc:
        print(f"RESEARCH STOP clone failed: {out[-300:]}"); return 1
    _, head = git(dest, "log", "-1", "--format=%h %ad", "--date=short")
    print(f"RESEARCH OK cloned {dest.as_posix()} at {head} (depth {a.depth}; never run its code)")
    return 0


def cmd_topic(a):
    h = home()
    if not re.fullmatch(r"[\w.-]+", a.topic):
        print(f"RESEARCH STOP bad topic {a.topic!r} (letters, digits, - . _)"); return 1
    d = h / ".scratch" / a.topic
    d.mkdir(parents=True, exist_ok=True)
    q = d / "questions.md"
    if not q.is_file():
        q.write_text(f"# Research: {a.topic}\n\nSource: <src/<name>, or a repo path>\n\n1. <question>\n", encoding="utf-8")
    print(f"  questions: {q.as_posix()}\n  findings:  {(d / 'findings.md').as_posix()}")
    print(f"RESEARCH OK topic {a.topic}")
    return 0


def cmd_done(a):
    dest = home() / "src" / a.name
    if not dest.is_dir():
        print(f"RESEARCH OK nothing to delete ({dest.as_posix()} does not exist)"); return 0
    shutil.rmtree(dest, onerror=lambda f, p, e: (os.chmod(p, stat.S_IWRITE), f(p)))   # .git packs are read-only
    print(f"RESEARCH OK deleted {dest.as_posix()}")
    return 0


def cmd_list(_):
    h = home()
    src = sorted(p.name for p in (h / "src").iterdir() if p.is_dir()) if (h / "src").is_dir() else []
    topics = sorted(p.name for p in (h / ".scratch").iterdir()
                    if p.is_dir() and (p / "questions.md").is_file()) if (h / ".scratch").is_dir() else []
    print(f"  home: {h.as_posix()}\n  clones: {', '.join(src) or '-'}\n  topics: {', '.join(topics) or '-'}")
    print("RESEARCH OK")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    p = sub.add_parser("clone"); p.add_argument("url"); p.add_argument("--name"); p.add_argument("--depth", type=int, default=50)
    p = sub.add_parser("topic"); p.add_argument("topic")
    p = sub.add_parser("done"); p.add_argument("name")
    sub.add_parser("list")
    a = ap.parse_args()
    return {"init": cmd_init, "clone": cmd_clone, "topic": cmd_topic, "done": cmd_done, "list": cmd_list}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
