# LEARN: prompt for a Hermes agent on a new machine

Human: clone this repo, then paste the block below into Hermes, replacing `<REPO>` with the clone's absolute path
(forward slashes, e.g. `D:/code/hermes-dev-team`). Paste it again after each `git pull`; it only re-copies what changed.

---

```text
Learn the hermes-dev-team repo at <REPO>. Do these steps in order and stop to ask me if one fails.

1. Read <REPO>/README.md, TEAM.md, CHANGELOG.md, and docs/design.md. Do not run anything yet.
2. Check prerequisites and report each version: `claude --version` (2.1.296 or newer), `python --version`, `git --version`.
   If claude is missing or not logged in, stop and tell me to run `claude auth login`. Never handle credentials yourself.
3. Skills: for each folder in <REPO>/skills/, compare it with the same-named skill in my Hermes skills directory.
   - Missing: copy the folder in.
   - Different: show me the diff and the CHANGELOG entry that explains it. Overwrite only after I agree.
     If my local copy has changes the repo lacks, keep mine and tell me.
   - Never delete a local skill that the repo does not have.
   Then confirm with skills_list that grill-with-docs, to-tickets, orchestrate, and claude-p-role-workers appear.
4. Save one memory fact: "hermes-dev-team kit is at <REPO>/kit; workers run via `python <REPO>/kit/run-worker.py`;
   agent commits never carry a Co-Authored-By trailer." Replace any older entry about hermes-dev-team instead of adding a second.
5. Smoke test in a throwaway git repo under your scratch directory (never in <REPO> or a real project):
   create main with one file, create branch `smoke`, then run
   `python <REPO>/kit/run-worker.py docs "feature: .scratch/none"` and report the RESULT line.
   PASS or a FAIL that names a missing feature folder both prove the launcher works; an ERROR line or a
   traceback does not. Delete the throwaway repo afterwards (cd out of it first).
6. Report in at most 8 lines: versions, skills copied or updated, the memory saved, and the smoke result.

Do not modify <REPO>. Do not push, commit, or change Hermes config.
```
