# Changelog

Every change to a skill, role, or the launcher gets an entry here, newest first. Linked skills update on `git pull`;
mark an entry **action** when another machine must do something (re-run `link-skills.py`, paste LEARN.md, etc.).

Format: version, date, then one line per change, naming the file.

## 0.7.0 - 2026-10-12

- `skills/orchestrate` steps 2, 7, 8: review per **batch** instead of once per feature. Step 2 proposes batches (one
  milestone each, at most 4 tickets) for the user to approve and records them in `.scratch/<feature>/batches.md`; step 8
  reviews a batch as soon as its tickets are all `review`, with `batch: <b> tickets: NN,NN base: <sha> round: <n>`.
  Docs still runs once at the end. **action:** start a new planner session (an open session keeps the old step 8).
- `kit/roles/reviewer.md`: with `batch/tickets/base` it reads only those tickets and reviews `git diff <base>...HEAD`;
  writes `review-<batch>-<round>.md`. Without them it behaves as before.
- Measured on a toy repo (Sonnet): batch 1 (clean) PASS, 9 turns, 13 s; batch 2 (planted bug) FAIL with the right
  finding and one fix ticket, 10 turns, 15 s; each read only its own tickets. $0.29 list for both.

## 0.6.0 - 2026-10-12

- `kit/run-worker.py`: live progress. The worker runs with `--output-format stream-json --verbose`; the launcher prints
  `START <role> <model>` first, then one line per tool call (`[  12s] Edit calc.py`) and per denial
  (`[   3s] DENIED Bash: This command requires approval`), flushed, and writes the same lines to
  `.scratch/runs/<stamp>-<role>.progress.log` (tail it). The RESULT line is still the LAST line, so parsing is
  unchanged; `.scratch/runs/<stamp>-<role>.json` now holds the final `result` event (same fields as before), so
  `costs.py` is unchanged. Progress parsing is best-effort and cannot fail a run.
- `skills/orchestrate` step 4, `skills/claude-p-role-workers`: dispatch with `python -u` and
  `terminal(background=true, heartbeat=120)`, end the turn, never `process wait` (it holds heartbeats back); answer a
  heartbeat in one line with no tool calls. Linked skills update on `git pull`; nothing to re-run.

## 0.5.1 - 2026-10-12

- `skills/orchestrate`: step 2 stops if a ticket lacks Matt's `**Blocked by:**` or `**Status:**` line (upstream
  format changed) instead of guessing.
- `docs/design.md` decision 16: protecting test config stops passing-by-config; it does not close the
  code-execution hole. Lists files to add to `protected` (conftest.py, jest setupFiles, scripts a test script calls).
- Matt Pocock skill versions known to work with step 2 (sha256 of SKILL.md, first 12): grill-with-docs 7de372c13488,
  grilling befdff1e27a1, domain-modeling 7b925d7b1e34, to-tickets 56ec23c5b278. If to-tickets changes, re-check step 2.

## 0.5.0 - 2026-10-12

- **action (every machine):** `git pull`, then `python kit/link-skills.py` and `--apply`. It removes the now-dangling
  `grill-with-docs` and `to-tickets` links and prints a `MISSING` line per Matt Pocock skill you lack; install each with
  `hermes skills install skills-sh/mattpocock/skills/<name> --yes` (grill-with-docs, grilling, domain-modeling,
  to-tickets). Or paste LEARN.md again; step 3 covers it.
- `skills/grill-with-docs`, `skills/to-tickets`: removed. The planner uses Matt Pocock's originals, which keep the
  scenario and code cross-check steps the rewrites dropped, and keep updating upstream.
- `skills/orchestrate` 0.3.0: new step 2 adds the worker header (`templates/ticket-header.md.template`: Lane, Branch,
  Status, Round, Blocked by, Terms, Seam, Test command) to tickets from Matt's `to-tickets`; glossary is `CONTEXT.md`
  if present, else `GLOSSARY.md`; never patch Matt's skills.
- `kit/run-worker.py`: pastes the project's `CLAUDE.md` with `@` imports inlined into every worker prompt (measured:
  `--setting-sources ""` hides it). Denies dev edits to package/lock/test-config files and `.team.json`
  (`Edit(**/<name>)`), and FAILs a run with `guard:test-config-changed` if one changed anyway. `.team.json` may add
  `"protected": [globs]`.
- `kit/link-skills.py`: removes dangling links into the repo, skips folders without SKILL.md, reports missing
  upstream (Matt) skills with the install command.
- `kit/roles/dev.md`, `reviewer.md`, `docs.md`, `dev.json`: glossary may be `CONTEXT.md` or `GLOSSARY.md`; dev FAILs
  with `needs <file> change` instead of touching protected files.

## 0.4.0 - 2026-10-11

- `.team.json` per project: `{"test": "<full test command>", "models": {"<role>": "<model>"}}`. The launcher reads it,
  and for dev takes the test command from the ticket's `Test command:` line, so `--test-cmd` is now optional.
  The dev role cannot edit `.team.json` (deny rule, measured). **action:** add `.team.json` to each project.
- `kit/costs.py`: per-feature (and `--by-role`) table of runs, pass/fail, turns, minutes, tokens, list price, denials.
  `runs.log` lines now carry `feature=` and `test=`.
- `kit/review-skills.py`: lists grown team skills, shows the diff (new files too), warns on machine paths, drafts
  CHANGELOG lines. Read-only; never stages anything.
- `skills/orchestrate`: uses `.team.json` instead of retyping test commands; shows the cost table at the end of a
  feature; asks before patching a team skill and points to `review-skills.py`.

## 0.3.0 - 2026-10-11

- `kit/link-skills.py`: links `skills/*` into Hermes' skills directory, so skills that grow while you work land in
  this repo as reviewable diffs. Refuses to overwrite a local copy that differs, and flags copies installed in a
  category folder (`skills/<category>/<name>`) instead of creating a duplicate. **action:** run it once with `--apply`.
- `.gitattributes`: LF line endings, so diffs of skills edited on Windows show content only.
- LEARN.md uses link-skills instead of copying; README gains "When a skill grows".

## 0.2.0 - 2026-10-11

- Workers are now headless Claude Code runs (`kit/run-worker.py`, `kit/roles/`), not Hermes profiles. `profiles/` and `SETUP.md` are removed.
- **re-copy** `skills/orchestrate`: dispatches through `kit/run-worker.py`; roles are dev, reviewer, docs.
- **re-copy** `skills/claude-p-role-workers`: new. Permission rules and pitfalls for `claude -p` workers.
- `skills/grill-with-docs`, `skills/to-tickets`: unchanged from 0.1.0.
- Launcher always disables the Co-Authored-By trailer.
- `docs/design.md` rewritten: 14 decisions, measurements, known holes, rejected options.

## 0.1.0 - 2026-10-11

- First draft: four Hermes profiles (planner, dev, qa, docs). Never run end to end; superseded by 0.2.0.
