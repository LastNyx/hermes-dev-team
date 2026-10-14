---
name: claude-p-role-workers
description: Use when running claude -p role workers with allow-lists.
version: 0.1.0
author: hermes-dev-team contributors
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [team, claude-code, permissions]
    related_skills: [orchestrate]
---

# claude -p role workers

Rules for running headless Claude Code workers under a deny-by-default allow-list. Measured on Claude Code 2.1.296.
Reference implementation: this repo's `kit/run-worker.py` and `kit/roles/`. Evidence: `docs/design.md`.

## When to Use

Changing `kit/run-worker.py` or a role file, adding a role, or debugging a worker that FAILs or gets denied.
For the day-to-day dispatch loop, use `orchestrate` instead.

## Why claude -p first

Headless Hermes auto-approves commands, so nothing enforces a worker's limits. A Hermes provider that wraps Claude Code
turns Claude Code's own tools off, so its allow-lists cannot guard a Hermes worker. Only a direct `claude -p` worker
gets the allow-list, so Claude stays the default backend; a Hermes worker profile is the prompt-only fallback
(see Backends). Keep the fallback on a plain provider (e.g. anthropic): the claude-subscription plugin spawns one
`claude` per model call and made a headless run ~3x slower.

## Rules (each one cost a failed run)

1. **Grant file writes with `Edit(path)`, never `Write(path)`.** `Edit` rules govern both Edit and Write; a `Write(...)` allow rule grants nothing. Paths outside `./` are denied either way. Deny beats allow.
2. **Partial-word Bash prefixes need `*`:** `Bash(git checkout -b t-*)` matches `t-001`; `Bash(git checkout -b t-:*)` does not. `:*` only works after a whole word (`Bash(git diff:*)`).
3. **Every role prompt ends with the tool rules** (`kit/roles/_tool-rules.md`): one command per Bash call, no `cd`/`&&`/`;`/`|`/redirects, read with Read/Glob/Grep, retry once in the allowed form. Without them, workers chain commands, get denied, and quit.
4. **Exit code 0 does not mean success.** A worker that gives up still exits 0 with subtype `success`. Parse the last `RESULT: PASS|FAIL` line and `permission_denials`. Only the turn cap exits 1 (`error_max_turns`).
5. **Co-Authored-By is always off.** Set both `"includeCoAuthoredBy": false` and `"attribution": {"commit": "", "pr": ""}`, and force them in the launcher, not only in role files. The trailer appears by default.
6. **Isolate from local settings:** `--setting-sources ""` + `--agents <json file>` + `--agent <role>`. A project's `.claude/settings.json` (even `Bash(*)`) is then ignored. With `--setting-sources ""`, `.claude/agents/*.md` is not found, so roles must come through `--agents`.
7. **Pass `--tools Read,Edit,Write,Glob,Grep,Bash`.** Without it, on Windows the worker reached for PowerShell and was denied.
8. **Prefix rules can't stop trailing flags:** `Bash(git commit -m:*)` allows `git commit -m x --amend`. Catch it after the run: the old HEAD must still be an ancestor of the new HEAD, main must not have moved, and the branch must be unchanged.
9. **One allowed test command goes in both places:** add `Bash(<exact cmd>)` to the allow-list AND name it in the prompt. A reviewer that wasn't told ran a narrower command and was denied.
10. **Review before merge.** After a merge, `git diff main...branch` is empty.
11. Haiku handled a small docs job; the user's default for test and real workers is Sonnet at medium effort (subscription limits): pass `--model sonnet --effort medium`, or set `.team.json` `"models"` and `"effort"` (string or per-role map). Never let a worker silently run on the planner's Opus.
12. To test denials, frame forbidden actions as plain chores. A prompt framed as "red-team" was refused and tested nothing.
13. **Every guard gets a positive and a negative toy run.** Example: for the push guard, push from a second shell (a script with `( sleep 12; git push ... ) &`, since the terminal tool rejects inline `&`) while the worker runs and expect `FAIL guard:pushed`. Use a local bare repo (`git init --bare remote.git`, remote added by native `C:/...` path) so a push can really land.
14. **Toy test runners must be able to go red.** A runner that tests a function only `if hasattr(...)` passes before the code exists, and a correct dev worker FAILs "no red step". Use a runner that executes every `test_*.py` and tell the ticket to write `test_<x>.py`.

## Known hole

The allowed test command runs code the worker wrote, so it can execute anything. Review before merge is the defence.

## Launching from Hermes

Run the launcher with `python -u` and `terminal(background=true, heartbeat=120)`, then end the turn. The launcher prints
one flushed line per tool call and per denial (also in `.scratch/runs/<stamp>-<role>.progress.log`); heartbeats carry
only new lines. Measured: a heartbeat is queued while the planner sits in `process wait`, so waiting hides progress.
Each heartbeat costs one planner turn; 120 s means a typical 90 s run costs none. Read the RESULT line (always the last
line), not the exit code alone. Never keep the terminal's working directory inside a throwaway repo you delete: later calls fail.

## Backends: claude (default) and hermes (fallback)

`run-worker.py` picks the backend: `--backend` flag, else `.team.json` `"backend"` (string or per-role map), else auto:
claude when `ANTHROPIC_API_KEY` is set or `claude auth status` JSON says `"loggedIn": true` (no model call), else hermes.
The dispatch is the same command for both; the planner never picks the backend.
- **hermes** = one headless run in a dedicated worker profile (default `hdt-worker`, `.team.json` `hermes_profile`):
  `hermes -p <profile> chat --query-file <prompt> -Q --in <repo> -t file,terminal --max-turns N --ignore-rules
  --source tool --format stream-json [-m <hermes_models[role]>] [--provider <hermes_provider>] [--reasoning <effort>]`.
  Sessions land in the worker profile, not the user's chat list. A missing profile prints the setup commands and
  `RESULT ... ERROR no Hermes profile`; the agent never creates or edits a profile or Hermes config.
- Measured (Sonnet 5.5 on the anthropic provider, toy ticket): 28 s vs 27 s for `claude -p`, about a quarter of the
  cached prompt tokens. The slow 92 s of the first spike came from the claude-subscription plugin, not Hermes.
- stream-json: flat `tool_use`/`tool_result` events (progress lines work) and a final `result` with `text`,
  `session_id` and `tokens` (input, cache_write, cache_read, output). No `$`. The text can arrive in pieces; use
  `result.text`. Pitfalls: `TERMINAL_CWD` overrides `--in` (the launcher unsets it); a turn cap exits 1 while the
  model still writes its own RESULT line (the launcher fails any non-zero exit); first run on a new profile installs
  dependencies (~2 min once). A tool call that exits non-zero shows as `exit!=0 <tool>` (not a denial).
- Measured: `-m` overrides the profile default (`hermes_models.dev = claude-haiku-5-5` ran on Haiku, PASS 34 s).
- No allow-list: `-t file,terminal` blocks nothing inside the shell. With `_tool-rules-hermes.md` in the prompt the
  worker refused all 4 forbidden chores; without it, it did all 4. Tagged `backend=hermes enforce=prompt-only`.
- Model names differ: Claude takes `sonnet`/`haiku` (`models`), Hermes takes the provider's full id
  (`hermes_models`, e.g. `claude-sonnet-5-5`). `effort` feeds `--effort` (claude) or `--reasoning` (hermes).
- Rejected: `delegate_task` subagents (one global `delegation.model`, a three-step prepare/delegate/finish flow, and
  child sessions in the user's own profile).
- Guards for both backends: main moved, branch changed, history rewritten, protected file changed, and `pushed`
  (`git ls-remote` per remote before/after; an unreadable remote is skipped, not failed).

## Process lessons

- Measure a mechanism end to end before designing around it.
- To judge a new backend's enforcement, run the forbidden-chores ticket twice: once with the forbidden list in the
  prompt, once without. Only the second shows what the backend itself blocks.
- Never edit Hermes `config.yaml` or create profiles for the user (Hermes refuses it anyway): print the exact
  `hermes profile create` / `hermes -p <p> model` / `hermes config set` commands and let the user run them.
- When the user asks for an "agnostic" option, list options in a table (model per role, planner steps, blocked while
  running, build cost) and recommend one; they iterate on it before any build.
- Before testing a workflow change, show the user the old vs new workflow side by side (steps, what is enforced,
  what is only caught afterwards); they want to understand it before the toy runs.
