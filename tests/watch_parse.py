#!/usr/bin/env python3
"""Parse the kit's run-file samples (tests/fixtures/watch/) with hermes-dev-watch's own reader.

    python tests/watch_parse.py <path to hermes-dev-watch>/hermes_dev_watch/core.py

CI checks out hermes-dev-watch's main (public, no token) and runs this after tests/watch_contract.py, so a kit change
the watcher cannot read fails on the kit commit that made it. It imports only core.py (stdlib, reads files; never runs
the app). Liveness is not checked: the sample lock's pids may belong to a real process on the CI machine.
Last line: `<n>/<total> checks passed`; exit 0 only when all pass. See docs/watch-contract.md.
"""
import importlib.util, pathlib, sys

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "watch"
results = []


def check(name, ok, detail=None):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name)
    if not ok and detail is not None:
        print("      " + str(detail)[-1500:])


def main():
    if len(sys.argv) != 2 or not pathlib.Path(sys.argv[1]).is_file():
        print("usage: watch_parse.py <hermes-dev-watch>/hermes_dev_watch/core.py"); return 2
    spec = importlib.util.spec_from_file_location("dev_watch_core", sys.argv[1])
    core = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(core)  # type: ignore[union-attr]

    runs = core.runs_log(FIXTURES)
    check("watcher reads all 3 runs.log lines, by run id",
          sorted(runs) == ["20261016-120000-dev", "20261016-120500-researcher", "20261016-121000-docs"], sorted(runs))
    dev = runs.get("20261016-120000-dev", {})
    check("dev run: model, verdict, turns, secs, cost, ticket, feature, repo, backend",
          (dev.get("model"), dev.get("verdict"), dev.get("turns"), dev.get("secs"), dev.get("cost"), dev.get("ticket"),
           dev.get("feature"), dev.get("repo"), dev.get("backend"))
          == ("sonnet", "PASS 1a2b3c4", "18", "22", 0.1234, "01-mul.md", "calc", "proj", "claude"), dev)
    res = runs.get("20261016-120500-researcher", {})
    check("researcher run: FAIL verdict, no cost, 1 denial, ticket `-`",
          (res.get("verdict"), res.get("cost"), res.get("denials"), res.get("ticket"))
          == ("FAIL guard:pushed:origin", None, "1", "-"), res)
    her = runs.get("20261016-121000-docs", {})
    check("hermes run: backend hermes, turns `-`", (her.get("backend"), her.get("turns")) == ("hermes", "-"), her)

    p = core.progress(FIXTURES, "20261016-120000-dev", 0)
    text = p.get("text", "") if isinstance(p, dict) else ""
    check("watcher reads the progress log by run id (say, tool, DENIED lines)",
          "say: Reading the ticket first." in text and "DENIED Bash:" in text and "Edit calc.py" in text, p)

    print(f"{sum(results)}/{len(results)} checks passed")
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
