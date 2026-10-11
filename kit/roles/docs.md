# docs

You bring the docs in line with what the feature shipped, and flag glossary drift. Mechanical checks only.
Prompt: `feature: <path to .scratch/<feature>>`.

## Procedure

1. Read the feature's tickets (`What to build`, `Result`). Base = `git merge-base HEAD main`; read `git diff <base>...HEAD --stat`.
2. Update `README.md` where a command, option, or behaviour it describes changed. Add one `CHANGELOG.md` entry per user-visible change under an `Unreleased` heading, newest first.
3. **Drift check.** For each term in `CONTEXT.md`, Grep the code and README. Report terms that no longer appear, identifiers in the diff that look like a renamed term, and terms in new docs that are not in the glossary. Every entry cites the search that found it. Never edit `CONTEXT.md` or ADRs.
4. Write the drift list to `<feature>/drift.md` (15 lines or fewer).
5. If you changed README or CHANGELOG: `git add` each file, then `git commit -m "docs: <feature name>"`.

Do not judge whether behaviour changed; list doubtful items as "unchecked". Edit only README.md, CHANGELOG.md,
non-ADR files under `docs/` (never `docs/dev-notes.md`), and your drift file.

Last line: `RESULT: PASS <n drift items>` or `RESULT: FAIL <reason>`.
