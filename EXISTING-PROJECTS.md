# Existing projects: turning on resume and machine switching (0.11.0+)

For a project that used the kit before 0.11.0 (it has a `.scratch/` folder that is not a git repo). Do it ONCE per
project, on one machine first, then once on each other machine. New projects do the same steps at orchestrate
step 1. Every step below was run as written by `tests/existing_project.py` (12/12, see docs/testing.md).

`KIT` = the absolute path to this repo's `kit/` folder on that machine (forward slashes on Windows).
Single repo: run the commands from the project repo root. Workspace: run them from the workspace root (the folder
holding `.team.json` with `repos`).

## Before you start (both machines)

1. `git pull` this repo on the machine, then start a NEW Hermes session (an open one keeps the old orchestrate).
2. No worker is running and the project's product tree is clean (`git status --short` prints nothing). If a Python
   project's `.gitignore` lacks `__pycache__/`, add it: an untracked cache folder counts as a dirty tree and makes
   `resume` ask and `switch` stop every time.
3. Decide which machine has the NEWEST `.scratch/` (the one you worked on last). That one is machine A. If both have
   work the other lacks, finish this guide on A, then merge B's extra files by hand (step B3).

## Machine A (the one with the newest scratch)

1. **Create a PRIVATE remote** for the scratch repo, e.g. a GitHub repo named `<project>-scratch`, empty (no README,
   no licence). Private, because it holds tickets, review notes and run logs. Agents never create it.
2. **Check this machine can reach it without a password prompt** (the kit disables prompts):
   `git ls-remote <url>` must exit 0 (empty output is fine for a new repo). If it asks for a login or fails, fix
   your SSH key or credential helper first; otherwise `resume` reports `fetch failed` and `switch` cannot push.
3. **Init:** `python KIT/scratch.py init --remote <url>`
   - Expect `committed existing scratch files: <sha>` and a last line `INIT OK scratch repo ... remote <url>`.
   - Tracked: tickets, `batches.md`, reviews, `drift.md`, `dev-notes.md`, `runs.log`. Not tracked: `runs/`.
   - `git status --short` in the product repo must still print nothing.
4. **Check:** `python KIT/scratch.py resume` ends with `RESUME OK` and lists your features. Old `runs.log` lines
   have no ticket, so `in-progress` tickets show `last run: no run logged`; that is expected.
5. **Record chat decisions now**, while you remember them (skip if none): for each rule you gave the planner that is
   not in a ticket, `python KIT/scratch.py decide <feature> "<one line>"`.
6. **Push:** `python KIT/scratch.py switch` must end with `SWITCH OK pushed scratch:main@..., <repo>:<branch>@...`.
   On `main` with no feature branch, it pushes only the scratch repo (and warns): fine.

## Machine B (every other machine)

1. Do "Before you start", including `git ls-remote <url>`.
2. Product repo: clone it if missing. No need to check out the feature branch: `resume` does it.
3. **Scratch:**
   - No `.scratch/` folder yet (or the workspace has none): `python KIT/scratch.py init --clone <url>`.
   - A `.scratch/` folder already exists (an old copy): `--clone` refuses (`INIT STOP ... already has files`) and
     changes nothing. Rename it: `mv .scratch .scratch.old`, run `init --clone <url>`, then compare:
     `diff -r .scratch.old .scratch` (ignore `runs/`). Copy anything B had that A lacked into `.scratch/`, then
     `python KIT/scratch.py commit "merge old scratch from B"`. Delete `.scratch.old` when done; until then add
     `/.scratch.old/` to `.git/info/exclude`, or the product tree is dirty.
   - Never run plain `init` (without `--clone`) on a machine whose scratch is not the newest: it starts a separate
     history that can never fast-forward; `resume` then reports `DIVERGED` on every run.
4. **Check:** `python KIT/scratch.py resume` ends with `RESUME OK`, and the product repo is now on the feature branch.

## Daily use after that

- Start the planner as usual; orchestrate step 0 runs `resume` itself.
- Leaving a machine: say **"switching"**. The planner runs `scratch.py switch`. Do not walk away before it says
  `SWITCH OK`; a `WARN` or `STOP` line means something stayed behind.
- Need a worker stopped: say "stop the worker"; it runs `scratch.py stop`, never a raw process kill.
- On `RESUME STOP ... DIVERGED`: both machines committed scratch without a switch in between. The planner shows both
  sides; you decide (usually: keep one side's commits, re-apply the other's lines by hand). It never merges.

## Troubleshooting

| You see | Meaning | Do |
|---|---|---|
| `INIT STOP origin is already <other url>` | the scratch repo has another remote | `git -C .scratch remote set-url origin <url>` yourself if intended |
| `fetch failed (offline or no access), not synced` | no network or no credentials | fix access, run `resume` again before working |
| `ASK the user: no commit and the tree is dirty` | a worker (or you) left uncommitted edits | look at them; discard, or commit a WIP yourself |
| `SWITCH STOP uncommitted product changes` | same | same, then `switch` again |
| `push of <branch> rejected` | the other machine pushed meanwhile | run `resume`; it fast-forwards or shows the divergence |
| `worker already running` / `SWITCH STOP a worker is running` | a worker is live (maybe from another session) | wait for its exit, or `scratch.py stop` |
