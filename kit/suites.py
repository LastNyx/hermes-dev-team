#!/usr/bin/env python3
"""Run a project's full test suites and compare their failures with the feature's baseline. No model is called.

`.team.json` "test" is either one command string or a list of suites, each run from its own folder:

    {"test": [{"name": "py", "cmd": ".venv/bin/python -m unittest discover -s tests"},
              {"name": "ui", "cmd": "npx vitest run", "cwd": "ui"}]}

Every suite runs even when an earlier one fails (a `cmd1 && cd ui && cmd2` chain stopped at baseline errors and never
reached the second suite). Failing test names are read from unittest, pytest, vitest and jest output; a suite that
fails without readable names counts as new. `<feature>/baseline.md` (written by `scratch.py baseline`) lists the
failures that already existed; they are reported, not counted.

Usage (cwd = the repo root):
    python <kit>/suites.py [--feature <feature>] [--timeout 900]

Last line: `TEST PASS | <suite> ...` (exit 0: every failure is in the baseline) or `TEST FAIL | ...` (exit 1).
The reviewer runs exactly this command; the planner runs it in step 6.
"""
import argparse, os, pathlib, re, subprocess, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import teamcfg  # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# One pattern per runner, matched per line after ANSI codes are stripped. Group 1 is the test's name.
NAME_RES = [
    re.compile(r"^(?:ERROR|FAIL): \S+ \(([^)]+)\)"),              # unittest: FAIL: test_x (pkg.mod.Class.test_x)
    re.compile(r"^(?:FAILED|ERROR) (\S+::\S+|\S+\.py)"),          # pytest -ra / summary: FAILED tests/a.py::test_x
    re.compile(r"^\s*FAIL\s+(\S.*?)(?:\s+\d+ms)?\s*$"),          # vitest: FAIL  src/a.test.js > group > name
    re.compile(r"^\s*\u25cf (\S.*?)\s*$"),                        # jest:   ● Group › name
]
BASELINE_HEAD = "## Failing tests (read by kit/suites.py)"
CMD_HELP = "`.team.json` \"test\" must be a command string or a list of {\"name\", \"cmd\", \"cwd\"}"


def suites(cfg):
    """[(name, cmd, cwd-relative-to-repo)] from the config's "test", or an error string."""
    t = cfg.get("test")
    if isinstance(t, str) and t.strip():
        return [("test", t, ".")]
    if isinstance(t, list) and t and all(isinstance(s, dict) and s.get("cmd") for s in t):
        names = [s.get("name") or f"suite{i + 1}" for i, s in enumerate(t)]
        if len(set(names)) != len(names):
            return "suite names in `.team.json` \"test\" must be unique"
        return [(n, s["cmd"], s.get("cwd") or ".") for n, s in zip(names, t)]
    return CMD_HELP if t else "no `test` in .team.json"


def failing_names(output):
    """Sorted unique failing test names found in a runner's output."""
    found = set()
    for line in ANSI.sub("", output).splitlines():
        for rx in NAME_RES:
            m = rx.match(line)
            if m:
                found.add(" ".join(m.group(1).split()))
                break
    return sorted(found)


def run_suite(repo, name, cmd, cwd, timeout):
    """{name, cmd, code, failing, tail}; code None = timed out."""
    env = {**os.environ, "CI": "1", "NO_COLOR": "1", "FORCE_COLOR": "0"}
    try:
        r = subprocess.run(cmd, shell=True, cwd=repo / cwd, capture_output=True, text=True, env=env, timeout=timeout,
                           errors="replace")
        out, code = (r.stdout or "") + (r.stderr or ""), r.returncode
    except subprocess.TimeoutExpired as e:
        out, code = f"{e.stdout or ''}{e.stderr or ''}\n[timed out after {timeout}s]", None
    except OSError as e:
        out, code = str(e), 127
    out = out if isinstance(out, str) else out.decode("utf-8", "replace")
    return {"name": name, "cmd": cmd, "cwd": cwd, "code": code,
            "failing": failing_names(out) if code else [], "tail": "\n".join(out.strip().splitlines()[-30:])}


def baseline_file(scratch, feature, repo):
    if not feature or feature == "-":
        return None
    fd = pathlib.Path(scratch) / feature
    for p in (fd / f"baseline-{repo.name}.md", fd / "baseline.md"):
        if p.is_file():
            return p
    return None


def read_baseline(path):
    """{suite: set(names)} from the block `scratch.py baseline` writes; {} when the file has none (older format)."""
    if not path:
        return {}
    text = path.read_text(encoding="utf-8")
    if BASELINE_HEAD not in text:
        return {}
    out = {}
    for line in text.split(BASELINE_HEAD, 1)[1].splitlines():
        m = re.match(r"^- ([^:]+): (.+)$", line.strip())
        if m:
            out.setdefault(m.group(1), set()).add(m.group(2))
        elif line.startswith("#"):
            break
    return out


def baseline_block(results):
    """The machine-readable part of baseline.md: one `- <suite>: <test name>` line per failing test."""
    lines = [BASELINE_HEAD, ""]
    for r in results:
        lines += [f"- {r['name']}: {n}" for n in r["failing"]]
        if r["code"] and not r["failing"]:
            lines.append(f"<!-- {r['name']}: exited {r['code']} with no readable test names -->")
    return "\n".join(lines) + "\n"


def judge(results, base):
    """(ok, summary parts, new failures per suite)."""
    ok, parts, new = True, [], {}
    for r in results:
        if r["code"] == 0:
            parts.append(f"{r['name']} PASS"); continue
        if r["code"] is None:
            ok = False; parts.append(f"{r['name']} FAIL (timed out)"); new[r["name"]] = ["timed out"]; continue
        if not r["failing"]:
            ok = False; parts.append(f"{r['name']} FAIL (exit {r['code']}, no readable test names)")
            new[r["name"]] = [f"exit {r['code']}"]; continue
        fresh = [n for n in r["failing"] if n not in base.get(r["name"], set())]
        old = len(r["failing"]) - len(fresh)
        if fresh:
            ok = False; new[r["name"]] = fresh
        parts.append(f"{r['name']} FAIL ({old} baseline, {len(fresh)} new)")
    return ok, parts, new


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--feature", help="read <scratch>/<feature>/baseline.md")
    ap.add_argument("--timeout", type=int, default=900, help="seconds per suite")
    a = ap.parse_args()
    tc = teamcfg.load()
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
    repo = pathlib.Path(top or os.getcwd()).resolve()
    s = suites(tc["cfg"])
    if isinstance(s, str):
        print(f"TEST ERROR {s}"); return 2
    results = [run_suite(repo, n, c, d, a.timeout) for n, c, d in s]
    bpath = baseline_file(tc["scratch"], a.feature, repo)
    ok, parts, new = judge(results, read_baseline(bpath))
    for r in results:
        if r["name"] in new:
            print(f"== {r['name']} ({r['cmd']}, in {r['cwd']}): new failures")
            for n in new[r["name"]][:20]:
                print(f"  {n}")
            print("  last lines:\n    " + r["tail"].replace("\n", "\n    "))
    note = f" | baseline: {bpath.name}" if bpath else (" | no baseline" if a.feature else "")
    print(f"TEST {'PASS' if ok else 'FAIL'} | " + " | ".join(parts) + note)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
