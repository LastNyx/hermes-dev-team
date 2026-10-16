# Open: watching remote workers (dispatched through the Hermes gateway)

Status: question saved, not designed. Grill after hermes-dev-watch's `filters-search` feature. Saved 2026-10-16.
Grill together with [planner-log.md](planner-log.md): both add to `runs.log` and the run-file contract.

## The ask

Kit workers (`kit/run-worker.py`) dispatched to another machine (e.g. WorkLab) through the Hermes gateway should be
watchable live from this PC in hermes-dev-watch.

## Settled

- hermes-dev-watch stays file-only: no network calls, no gateway credentials in the app. Remote runs reach it as
  files in the contract format (`docs/watch-contract.md`), written by the kit or a gateway-side exporter into a folder
  the watcher already watches.
- Its own feature, after `filters-search`.

## Why it does not work today

- `runs.log` travels with the scratch repo, but `runs/` (progress logs, the lock) stays on the machine that ran the
  worker: from another PC a remote run shows its verdict afterwards, nothing live.
- Liveness is "launcher or worker pid alive". A copied or network-shared lock carries the remote machine's pids, so
  this PC reads a running remote run as not running (or, by pid reuse, a dead one as running).

## Likely direction (to be grilled)

- Kit first, additive under the contract: a heartbeat in the lock (e.g. `beat`, a timestamp the launcher refreshes
  every few seconds) and a `host` field. A reader treats a lock from another host as live while `beat` is recent,
  and uses pids only for its own host. Old readers ignore both keys, so `fmt` stays 1.
- Then a transport: a synced folder, a network share of the remote scratch, or a gateway-side exporter.

## Questions for the grill

1. Transport: sync (Syncthing, a git push loop), SMB/SSHFS share of WorkLab's scratch, or an exporter in the gateway?
2. Freshness: how stale may a remote progress log be (seconds vs a minute)?
3. Heartbeat interval and the "live" window; clock skew between machines.
4. Is `host` a hostname, a Tailscale name, or a configured label?
5. Do remote projects appear as separate projects (`worklab/<name>`) or merge with the same project on this PC?
6. Security: a remote progress log is still untrusted worker text; the transport must not give the remote machine
   write access to this PC beyond that folder.
