#!/usr/bin/env python3
"""Summarise .scratch/runs.log: one line per feature (and per role with --by-role). No model is called.

Usage (cwd = a project repo or a workspace with a .team.json):  python <kit>/costs.py [--feature NAME] [--by-role] [--log PATH] [--backfill]
Prices are list price from Claude Code's JSON; a subscription seat is billed differently.

Token columns: in = uncached input, cache_w = input written to the prompt cache, cache_r = input read from it,
out = output. Total prompt size is in + cache_w + cache_r; hit% = cache_r / that total.
--backfill fills in/cw/cr on log lines written before 0.8.0, from the result JSON kept in .scratch/runs/
(it rewrites runs.log in place, keeping a copy in .scratch/runs/, which is ignored, so the tree stays clean).
"""
import argparse, collections, datetime, json, pathlib, shutil, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import teamcfg  # noqa: E402

TOKEN_KEYS = {"in": "input_tokens", "cw": "cache_creation_input_tokens", "cr": "cache_read_input_tokens",
              "out": "output_tokens"}


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
        elif x.endswith(".json"):
            r["json"] = x
    return r


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def backfill(p):
    """Append in=/cw=/cr= to old lines that lack them, reading usage from the saved result JSON."""
    done, missing, out = 0, 0, []
    for line in p.read_text(encoding="utf-8").splitlines():
        r = parse(line)
        if r is None or "cr" in r:
            out.append(line); continue
        usage = None
        jp = pathlib.Path(r.get("json") or "")
        if jp.is_file():
            try:
                usage = json.loads(jp.read_text(encoding="utf-8")).get("usage")
            except ValueError:
                usage = None
        if not usage:
            missing += 1; out.append(line); continue
        out.append(line + "".join(f"\t{k}={usage.get(TOKEN_KEYS[k])}" for k in ("in", "cw", "cr")))
        done += 1
    if done:
        # measured: a runs.log.bak next to runs.log showed up as untracked and the dev worker FAILed "dirty tree"
        bak = p.parent / "runs" / f"runs.log.{datetime.datetime.now():%Y%m%d-%H%M%S}.bak"
        bak.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, bak)
        p.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"backfill: {done} line(s) filled, {missing} without a readable result JSON (left as is)"
          + (f"; backup {bak.as_posix()}" if done else ""))


def k(n):
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}k" if n >= 1e3 else f"{n:.0f}"


def print_row(label, role, c):
    total_in = c["in"] + c["cw"] + c["cr"]
    hit = f"{100 * c['cr'] / total_in:.0f}" if total_in else "-"
    print(f"{label[:15]:<16}{role:<10}{c['runs']:>5}{c['pass_']:>5}{c['runs'] - c['pass_']:>5}{c['turns']:>7.0f}"
          f"{c['secs'] / 60:>7.1f}{k(c['in']):>8}{k(c['cw']):>9}{k(c['cr']):>9}{k(c['out']):>8}{hit:>6}"
          f"{c['usd']:>8.3f}{c['denied']:>7.0f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", help="default: runs.log in the scratch folder (.team.json `scratch`, else .scratch)")
    ap.add_argument("--feature")
    ap.add_argument("--by-role", action="store_true")
    ap.add_argument("--backfill", action="store_true", help="add token columns to pre-0.8.0 lines, then summarise")
    a = ap.parse_args()
    p = pathlib.Path(a.log) if a.log else teamcfg.load()["scratch"] / "runs.log"
    if not p.exists():
        print(f"no log at {p}"); return 1
    if a.backfill:
        backfill(p)
    rows = [r for r in map(parse, p.read_text(encoding="utf-8").splitlines()) if r]
    if a.feature:
        rows = [r for r in rows if r["feature"] == a.feature]
    groups = collections.OrderedDict()
    for r in rows:
        role = r["role"] + ("@hermes" if r.get("backend") == "hermes" else "")   # hermes runs never mix with claude
        key = (r["feature"], role) if a.by_role else (r["feature"],)
        groups.setdefault(key, []).append(r)
    print(f"{'feature':<16}{'role':<10}{'runs':>5}{'pass':>5}{'fail':>5}{'turns':>7}{'mins':>7}"
          f"{'in':>8}{'cache_w':>9}{'cache_r':>9}{'out':>8}{'hit%':>6}{'list$':>8}{'denied':>7}")
    tot, no_tokens = collections.Counter(), 0
    for key, rs in groups.items():
        c = collections.Counter(
            runs=len(rs), pass_=sum(r["verdict"].startswith("PASS") for r in rs),
            turns=sum(num(r.get("turns")) for r in rs), secs=sum(num(r.get("secs")) for r in rs),
            usd=sum(num(r.get("list$")) for r in rs), denied=sum(num(r.get("denials")) for r in rs),
            **{t: sum(num(r.get(t)) for r in rs) for t in TOKEN_KEYS})
        no_tokens += sum("cr" not in r for r in rs)
        tot.update(c)
        print_row(key[0], key[1] if a.by_role else "all", c)
    print_row("TOTAL", "", tot)
    hermes = sum(r.get("backend") == "hermes" for r in rows)
    if hermes:
        print(f"note: {hermes} run(s) on the hermes backend (enforce=prompt-only); no $ is reported "
              "for them (list$ counts claude runs only; compare tokens). --by-role shows them as <role>@hermes.")
    if no_tokens:
        print(f"note: {no_tokens} run(s) predate input/cache logging; run with --backfill to fill them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
