# Switching machines and force-stop

Load when the user says "switching", asks to stop a running worker, or step 0 reports a lock, `orphaned worker`, or
`DIVERGED`.

- **"switching"** (the user is moving to another machine): `python KIT/scratch.py switch`. This is the ONLY push you
  ever make: the scratch repo plus the checked-out feature branch of each repo, plain `git push` (never `--force`,
  never main). It refuses while a worker runs (wait for the exit notice, or force-stop first), and refuses when a
  product tree has uncommitted changes: show the user `git status --short` and let them discard the changes or commit
  a WIP themselves; never commit worker code yourself. `WARN ... no remote` means that part stays on this machine:
  tell the user. A rejected push means the other machine pushed meanwhile; run step 0 and show the divergence.
- **Carrying work between machines** needs a PRIVATE scratch remote that the user creates and gives you:
  `scratch.py init --remote <url>`; on the other machine `scratch.py init --clone <url>`. Never create a remote
  yourself.
- **Force-stop** (the user asks to stop a running worker): `python KIT/scratch.py stop`. It kills the worker's process
  tree, waits for the launcher's `RESULT ... FAIL force-stopped` line, leaves the ticket `in-progress`, and commits a
  `force-stopped` line to `decisions.md`. Do not change the ticket's Status: the next step 0 decides (redispatch on a
  clean tree, ask on a dirty one). Never `process kill` the background terminal instead: that skips the log line.
- **Worker lock:** the launcher keeps `<scratch>/runs/worker.lock` while the launcher or its worker runs and refuses a
  second dispatch (`ERROR worker already running`), also from another planner session. Step 0 removes a lock whose
  launcher and worker are both gone. `RESUME STOP orphaned worker` (launcher gone, worker alive; no exit notice will
  come): ask the user, then `scratch.py stop`.
- A killed worker leaves `<scratch>/runs/worker.lock`: `scratch.py stop`, never a blind `pkill`.
- **Dirty-tree causes** (step 0 says ASK, switching stops):
  - An untracked `__pycache__/` (or any build output). Ask the user to add it to `.gitignore`.
  - A tracked `.scratch/` (inside the product repo, not its own git repo) makes every `status`/`decide` dirty the
    tree, and the dev refuses a dirty tree: commit `.scratch` before each dispatch, or move it out with
    `scratch.py init`.
- **`DIVERGED`:** both machines committed. Show both sides; never merge, reset or rebase to get past it.
