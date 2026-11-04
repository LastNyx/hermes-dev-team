#!/usr/bin/env python3
"""Start a kit command detached from the planner's chat, then poll it (2.0.0). No model call here.

Hermes kills a chat's background jobs (a `/new`, a closed app, a timed-out tool call), so a worker started with
`terminal(background=true)` can die with the planner's turn. This starts it outside the chat instead: as a
`systemd-run --user --collect` unit when systemd is there, else as a detached process. Its output goes to a file in
the scratch folder, so the planner reads the RESULT line whenever it comes back.

Usage (cwd = the project repo, as for the command itself):
    python <kit>/bg.py start run-worker.py dev "ticket: .scratch/<feature>/issues/NN-slug.md"
    python <kit>/bg.py start scratch.py dispatch <feature> 01 02
    python <kit>/bg.py wait <id> [--timeout 280]    # blocks at most --timeout seconds; prints the result when done
    python <kit>/bg.py status                       # every job of this scratch folder: running or done

Only `run-worker.py` and `scratch.py dispatch` may be started. Job files: <scratch>/runs/bg-<id>.json and .out.
Last line: `BG OK ...` | `BG RUNNING ...` | `BG DONE <id> exit=<n>` then the RESULT/DISPATCH lines | `BG STOP <reason>`.
"""
import argparse, datetime, json, os, pathlib, re, shutil, subprocess, sys, time

KIT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))
import teamcfg  # noqa: E402

ALLOWED = {"run-worker.py": None, "scratch.py": ("dispatch",)}
FINAL = re.compile(r"^(RESULT |DISPATCH (OK|STOP)|  \d+: )")


def runs_dir():
    r = teamcfg.load()["scratch"] / "runs"
    r.mkdir(parents=True, exist_ok=True)
    return r


def has_systemd():
    if sys.platform != "linux" or not shutil.which("systemd-run") or os.environ.get("HDT_BG_NO_SYSTEMD"):
        return False
    r = subprocess.run(["systemctl", "--user", "is-system-running"], capture_output=True, text=True)
    return r.stdout.strip() in ("running", "degraded")


def cmd_start(a):
    script, args = a.script, a.args
    if script not in ALLOWED or (ALLOWED[script] and (not args or args[0] not in ALLOWED[script])):
        print(f"BG STOP only run-worker.py or scratch.py dispatch may be started (got {script} {' '.join(args[:1])})")
        return 1
    runs = runs_dir()
    role = args[0] if script == "run-worker.py" else "dispatch"
    jid = f"{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{re.sub(r'[^a-z0-9]', '', role.lower())}"
    while (runs / f"bg-{jid}.json").exists():
        time.sleep(1)
        jid = f"{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{re.sub(r'[^a-z0-9]', '', role.lower())}"
    out, meta = runs / f"bg-{jid}.out", runs / f"bg-{jid}.json"
    argv = [sys.executable, "-u", str(KIT / script), *args]
    cwd = os.getcwd()
    job = {"id": jid, "argv": argv, "cwd": cwd, "out": str(out), "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    out.write_text("", encoding="utf-8")
    if has_systemd():
        unit = f"hdt-{jid}"
        env = [f"--setenv={k}={os.environ[k]}" for k in ("PATH", "HOME", "HDT_CONFIG", "HDT_SLOT_OF",
                                                         "ANTHROPIC_API_KEY", "CLAUDE_CONFIG_DIR") if k in os.environ]
        # rc file: the exit code, written by the unit itself (a --collect unit is gone when it ends)
        rc = runs / f"bg-{jid}.rc"
        shell = f'{subprocess.list2cmdline(argv)} >> "{out}" 2>&1; echo $? > "{rc}"'
        r = subprocess.run(["systemd-run", "--user", "--collect", "--quiet", f"--unit={unit}",
                            f"--working-directory={cwd}", *env, "/bin/sh", "-c", shell],
                           capture_output=True, text=True)
        if r.returncode:
            print(f"BG STOP systemd-run failed: {(r.stderr or r.stdout).strip()[-300:]}"); return 1
        job.update(kind="systemd", unit=unit, rc=str(rc))
    else:
        rc = runs / f"bg-{jid}.rc"
        if os.name != "nt":                                    # same rc file as the systemd unit writes
            cmd, flags = ["/bin/sh", "-c", f'{subprocess.list2cmdline(argv)}; echo $? > "{rc}"'], {"start_new_session": True}
        else:
            cmd = argv
            flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
        with open(out, "a", encoding="utf-8") as f:
            p = subprocess.Popen(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **flags)
        job.update(kind="process", pid=p.pid, rc=str(rc))
    meta.write_text(json.dumps(job), encoding="utf-8")
    print(f"BG OK {jid} started ({job['kind']}{' ' + job.get('unit', '') if job['kind'] == 'systemd' else ''}); "
          f"output {out}; poll with `bg.py wait {jid}`")
    return 0


def load_job(jid):
    p = runs_dir() / f"bg-{jid}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def running(job):
    if job.get("rc") and pathlib.Path(job["rc"]).is_file():
        return False
    if job["kind"] == "systemd":
        r = subprocess.run(["systemctl", "--user", "is-active", job["unit"]], capture_output=True, text=True)
        return r.stdout.strip() in ("active", "activating", "reloading")
    return teamcfg.pid_alive(job.get("pid"))


def exit_code(job):
    if job.get("rc"):
        try:
            return int(pathlib.Path(job["rc"]).read_text().strip())
        except (OSError, ValueError):
            return None
    return None


def final_lines(job):
    try:
        lines = pathlib.Path(job["out"]).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    keep = [l for l in lines if FINAL.match(l) or l.startswith("  denied ")]
    return keep[-12:] or lines[-5:]


def cmd_wait(a):
    job = load_job(a.id)
    if not job:
        print(f"BG STOP no job {a.id}"); return 1
    t0 = time.time()
    while running(job) and time.time() - t0 < a.timeout:
        time.sleep(5)
    if running(job):
        mins = round((time.time() - time.mktime(time.strptime(job["started"], "%Y-%m-%d %H:%M:%S"))) / 60)
        print(f"BG RUNNING {a.id} for {mins} min; output {job['out']}"); return 0
    rc = exit_code(job)
    for l in final_lines(job):
        print(l)
    print(f"BG DONE {a.id} exit={'?' if rc is None else rc}")
    return 0


def cmd_status(_):
    jobs = sorted(runs_dir().glob("bg-*.json"))
    for p in jobs[-10:]:
        job = json.loads(p.read_text(encoding="utf-8"))
        state = "running" if running(job) else "done"
        last = (final_lines(job) or ["-"])[-1]
        print(f"  {job['id']} {state} {' '.join(pathlib.Path(x).name for x in job['argv'][2:4])} | {last[:120]}")
    print(f"BG OK {len(jobs)} job(s)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("start"); p.add_argument("script"); p.add_argument("args", nargs=argparse.REMAINDER)
    p = sub.add_parser("wait"); p.add_argument("id"); p.add_argument("--timeout", type=int, default=280)
    sub.add_parser("status")
    a = ap.parse_args()
    return {"start": cmd_start, "wait": cmd_wait, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
