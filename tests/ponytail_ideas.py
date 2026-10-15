#!/usr/bin/env python3
"""E2E for the dev's "reuse first" rule, the reviewer reading beyond the diff, and the `Not checked:` lines.

Toy repo: `util.py` has `fmt_money(cents)`; `report.py` reads `order["qty"]` and has NO test. On the feature branch a
hand-made commit (ticket 01) renames the field `qty` -> `quantity` in `orders.py` only, so `report.py` is now broken
while every test still passes. Then two real `claude -p` runs (Sonnet, about $0.30 list):
  - dev, ticket 02: `order_line(order)` must format a price; it should reuse `fmt_money`, and end its Result with
    `Not checked:`.
  - reviewer, tickets 01,02: must FAIL citing `report.py` (a file outside the diff), and end with `Not checked:`.
Everything happens in a temp folder, deleted at the end (--keep keeps it).

    python tests/ponytail_ideas.py [--keep]

Last line: `<n>/<total> checks passed`; exit 0 only when all pass. See docs/testing.md.
"""
import json, os, pathlib, re, shutil, subprocess, sys, tempfile, time

KIT = (pathlib.Path(__file__).resolve().parent.parent / "kit").as_posix()
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hdt-ponytail-"))
ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
results = []


def sh(cwd, *args):
    r = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, env=ENV, encoding="utf-8", errors="replace")
    if r.returncode:
        raise SystemExit(f"FAILED {args}: {r.stdout}{r.stderr}")
    return r.stdout.strip()


def kit(cwd, *args):
    r = subprocess.run([sys.executable, "-u", f"{KIT}/run-worker.py", *args], cwd=cwd, capture_output=True, text=True,
                       env=ENV, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout + r.stderr).strip()


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name)
    if not ok and detail:
        print("      " + str(detail).replace("\n", "\n      ")[-1500:])


def last(out):
    return next((l for l in reversed(out.splitlines()) if l.startswith("RESULT")), out[-300:])


TICKET = """# {n}: {title}

Lane: full
Branch: feat
Status: {status}
Round: 0
Blocked by: {blocked}
Terms: order, order line
Seam: {seam}
Test command: python -m unittest -q test_orders
Verify: None

## What to build

{what}

## Acceptance criteria

- [ ] {crit}

## Result
"""


def setup():
    a = ROOT / "shop"; a.mkdir()
    w = lambda n, s: (a / n).write_text(s, encoding="utf-8")
    w("util.py", "def fmt_money(cents):\n    \"\"\"1250 -> '$12.50'\"\"\"\n    return f\"${cents // 100}.{cents % 100:02d}\"\n")
    w("orders.py", "def make_order(item, qty, price_cents):\n"
                   "    return {\"item\": item, \"qty\": qty, \"price_cents\": price_cents}\n")
    w("report.py", "def summary(orders):\n    return sum(o[\"qty\"] for o in orders)\n")
    w("test_orders.py", "import unittest\nfrom orders import make_order\n\n\nclass T(unittest.TestCase):\n"
                        "    def test_make(self):\n        self.assertEqual(make_order('pen', 2, 150)['qty'], 2)\n")
    w(".team.json", json.dumps({"test": "python -m unittest -q", "models": {"dev": "sonnet", "reviewer": "sonnet"}}))
    w(".gitignore", "__pycache__/\n.scratch/\n")
    sh(a, "git", "init", "-q", "-b", "main"); sh(a, "git", "config", "user.email", "me@x"); sh(a, "git", "config", "user.name", "me")
    sh(a, "git", "add", "-A"); sh(a, "git", "commit", "-q", "-m", "init")
    sh(a, "git", "checkout", "-q", "-b", "feat")
    # ticket 01, done by hand: rename the field in orders.py and its test only; report.py (untested) is left broken
    w("orders.py", "def make_order(item, quantity, price_cents):\n"
                   "    return {\"item\": item, \"quantity\": quantity, \"price_cents\": price_cents}\n")
    w("test_orders.py", "import unittest\nfrom orders import make_order\n\n\nclass T(unittest.TestCase):\n"
                        "    def test_make(self):\n        self.assertEqual(make_order('pen', 2, 150)['quantity'], 2)\n")
    sh(a, "git", "commit", "-q", "-am", "rename order qty to quantity (01-rename.md)")
    iss = a / ".scratch/f1/issues"; iss.mkdir(parents=True)
    (iss / "01-rename.md").write_text(TICKET.format(
        n="01", title="rename order quantity", status="review", blocked="None", seam="orders.make_order",
        what="An order's count field is called `quantity` instead of `qty` everywhere orders are used.",
        crit="make_order(...) returns the count under `quantity`; nothing reads `qty` any more"), encoding="utf-8")
    (iss / "02-line.md").write_text(TICKET.format(
        n="02", title="order line", status="in-progress", blocked="01", seam="orders.order_line",
        what="Add `order_line(order)` to orders.py: one display line for an order, item, quantity and the total price "
             "(quantity * price_cents) formatted as money, e.g. `pen x2 $3.00`.",
        crit="order_line(make_order('pen', 2, 150)) == 'pen x2 $3.00'"), encoding="utf-8")
    return a


def run():
    a = setup()
    base = sh(a, "git", "merge-base", "HEAD", "main")

    t0 = time.time()
    rc, out = kit(a, "dev", "ticket: .scratch/f1/issues/02-line.md")
    check(f"dev 02 PASS ({round(time.time() - t0)} s)", rc == 0 and "RESULT dev PASS" in out, last(out))
    added = [l for l in sh(a, "git", "show", "HEAD", "--", "orders.py").splitlines() if l.startswith("+")]
    dev_reused = any("fmt_money" in l for l in added)
    check("dev reused util.fmt_money (no own money formatting)",
          any("fmt_money" in l for l in added) and not any("// 100" in l or "/ 100" in l or "100:" in l for l in added),
          "\n".join(added))
    res = (a / ".scratch/f1/issues/02-line.md").read_text(encoding="utf-8").split("## Result", 1)[1]
    check("dev Result ends with a `Not checked:` line", "Not checked:" in res, res)

    t0 = time.time()
    rc, out = kit(a, "reviewer", f"feature: .scratch/f1 batch: 1 tickets: 01,02 base: {base} round: 1")
    check("reviewer started with max-turns=35", "max-turns=35" in out, out[:400])
    check(f"reviewer FAIL ({round(time.time() - t0)} s): report.py breaks outside the diff",
          rc != 0 and "RESULT reviewer FAIL" in out, last(out))
    rv = a / ".scratch/f1/review-1-1.md"
    text = rv.read_text(encoding="utf-8") if rv.is_file() else ""
    spec = re.split(r"^#+\s*Standards", re.split(r"^#+\s*Spec", text, flags=re.M | re.I)[-1], flags=re.M | re.I)[0]
    check("review cites report.py under Spec", "report.py" in spec, text)
    check("review ends with a `Not checked:` line", "Not checked:" in text, text[-500:])
    check("reviewer flags the missed fmt_money reuse, if the dev missed it",
          dev_reused or "fmt_money" in text.split("Standards", 1)[-1], text)
    fixes = list((a / ".scratch/f1/issues").glob("*-fix-*.md"))
    check("reviewer wrote a fix ticket naming report", any("report" in f.read_text(encoding="utf-8").lower() for f in fixes),
          [f.name for f in fixes])


def main():
    try:
        run()
    finally:
        if "--keep" not in sys.argv:
            shutil.rmtree(ROOT, onerror=lambda f, p, e: (os.chmod(p, 0o700), f(p)))
        else:
            print(f"kept {ROOT}")
    print(f"{sum(results)}/{len(results)} checks passed")
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
