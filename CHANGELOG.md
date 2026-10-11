# Changelog

Every change to a skill, role, or the launcher gets an entry here, newest first. Linked skills update on `git pull`;
mark an entry **action** when another machine must do something (re-run `link-skills.py`, paste LEARN.md, etc.).

Format: version, date, then one line per change, naming the file.

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
