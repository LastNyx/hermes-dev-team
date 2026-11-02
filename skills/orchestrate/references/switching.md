# Switching machines and force-stop

Load when the user says "switching", asks to stop a running worker, the chat was reset or closed mid-run, or step 0
reports `INTERRUPTED`, `worker prepared` or `DIVERGED`.

- **"switching"** (the user is moving to another machine): `python KIT/scratch.py switch`. This is the ONLY push you
  ever make: the scratch repo plus the checked-out feature branch of each repo, plain `git push` (never `--force`,
  never main). It refuses while a run is open (wait for its result, or force-stop first), and refuses when a
  product tree has uncommitted changes: show the user `git status --short` and let them discard the changes or commit
  a WIP themselves; never commit worker code yourself. `WARN ... no remote` means that part stays on this machine:
  tell the user. A rejected push means the other machine pushed meanwhile; run step 0 and show the divergence.
- **Carrying work between machines** needs a PRIVATE scratch remote that the user creates and gives you:
  `scratch.py init --remote <url>`; on the other machine `scratch.py init --clone <url>`. Never create a remote
  yourself. A child runs only on the machine whose chat started it.
- **Force-stop** (the user asks to stop a running worker): first stop the child, `delegate_task` with
  `action: "stop"` and its subagent id (`action: "list"` shows it). Then `python KIT/scratch.py stop`: it finishes
  the run as `FAIL force-stopped` with the guards, leaves the ticket `in-progress`, and commits a `force-stopped`
  line to `decisions.md`. It refuses while the child still runs; `--force` only when the child is surely gone (the
  user closed the chat). Do not change the ticket's Status: the next step 0 decides (redispatch on a clean tree, ask
  on a dirty one).
- **Worker lock:** `prepare` writes `<scratch>/runs/worker.lock` and `finish` removes it; a second `prepare` is
  refused, also from another planner session. A lock whose child is gone (`/new`, app closed, 2 h without transcript
  activity, or prepared 10 min ago and never delegated) is finished by step 0 as `INTERRUPTED`; nothing to do by hand.
- **Dirty-tree causes** (step 0 says ASK, switching stops):
  - What an interrupted child left half-done: show the user `git status --short` and `git diff --stat`; they commit
    or discard.
  - An untracked `__pycache__/` (or any build output). Ask the user to add it to `.gitignore`.
  - A tracked `.scratch/` (inside the product repo, not its own git repo) makes every `status`/`decide` dirty the
    tree, and the dev refuses a dirty tree: commit `.scratch` before each dispatch, or move it out with
    `scratch.py init`.
- **`DIVERGED`:** both machines committed. Show both sides; never merge, reset or rebase to get past it.
