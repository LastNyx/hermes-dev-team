#!/usr/bin/env python3
"""Summarise .scratch/runs.log: one line per feature (and per role with --by-role). No model is called.

Usage (cwd = the project repo):  python <kit>/costs.py [--feature NAME] [--by-role] [--log PATH]
Prices are list price from Claude Code's JSON; a subscription seat is billed differently.
"""
import argparse, collections, pathlib, sys


def parse(line):
    f = line.rstrip("\n").split("\t")
    if len(f) < 9:
        return None
    r = {"stamp": f[0], "role": f[1], "model": f[2], "verdict": f[3], "feature": "-"}
    for x in f[4:]:
        if "=" in x:
            k, v = x.split("=", 1); r[k] = v
        elif x.endswith("s") and x[:-1].isdigit():
            r["secs"] = x[:-1]
    return r


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=".scratch/runs.log")
    ap.add_argument("--feature")
    ap.add_argument("--by-role", action="store_true")
    a = ap.parse_args()
    p = pathlib.Path(a.log)
    if not p.exists():
        print(f"no log at {p}"); return 1
    rows = [r for r in map(parse, p.read_text(encoding="utf-8").splitlines()) if r]
    if a.feature:
        rows = [r for r in rows if r["feature"] == a.feature]
    groups = collections.OrderedDict()
    for r in rows:
        key = (r["feature"], r["role"]) if a.by_role else (r["feature"],)
        groups.setdefault(key, []).append(r)
    print(f"{'feature':<16}{'role':<10}{'runs':>5}{'pass':>5}{'fail':>5}{'turns':>7}{'mins':>7}{'out_tok':>9}{'list$':>8}{'denied':>7}")
    tot = collections.Counter()
    for key, rs in groups.items():
        c = collections.Counter(
            runs=len(rs), pass_=sum(r["verdict"].startswith("PASS") for r in rs),
            turns=sum(num(r.get("turns")) for r in rs), secs=sum(num(r.get("secs")) for r in rs),
            out=sum(num(r.get("out")) for r in rs), usd=sum(num(r.get("list$")) for r in rs),
            denied=sum(num(r.get("denials")) for r in rs))
        tot.update(c)
        role = key[1] if a.by_role else "all"
        print(f"{key[0][:15]:<16}{role:<10}{c['runs']:>5}{c['pass_']:>5}{c['runs'] - c['pass_']:>5}{c['turns']:>7.0f}"
              f"{c['secs'] / 60:>7.1f}{c['out']:>9.0f}{c['usd']:>8.3f}{c['denied']:>7.0f}")
    print(f"{'TOTAL':<26}{tot['runs']:>5}{tot['pass_']:>5}{tot['runs'] - tot['pass_']:>5}{tot['turns']:>7.0f}"
          f"{tot['secs'] / 60:>7.1f}{tot['out']:>9.0f}{tot['usd']:>8.3f}{tot['denied']:>7.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
