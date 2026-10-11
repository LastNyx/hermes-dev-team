# Changelog

Every change to a skill, role, or the launcher gets an entry here, newest first. Another machine reads this after
`git pull` to know what to re-copy into its Hermes skills folder (see [LEARN.md](LEARN.md)).

Format: version, date, then one line per change, naming the file. Mark entries that need action with **re-copy**.

## 0.2.0 - 2026-10-11

- Workers are now headless Claude Code runs (`kit/run-worker.py`, `kit/roles/`), not Hermes profiles. `profiles/` and `SETUP.md` are removed.
- **re-copy** `skills/orchestrate`: dispatches through `kit/run-worker.py`; roles are dev, reviewer, docs.
- **re-copy** `skills/claude-p-role-workers`: new. Permission rules and pitfalls for `claude -p` workers.
- `skills/grill-with-docs`, `skills/to-tickets`: unchanged from 0.1.0.
- Launcher always disables the Co-Authored-By trailer.
- `docs/design.md` rewritten: 14 decisions, measurements, known holes, rejected options.

## 0.1.0 - 2026-10-11

- First draft: four Hermes profiles (planner, dev, qa, docs). Never run end to end; superseded by 0.2.0.
