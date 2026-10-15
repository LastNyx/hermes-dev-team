# Shared repo

Load when the config has `"shared": true`: other people, with their own workflows, commit to the repo. The kit is the
user's personal workflow, not the team's. Change nothing the team sees except the feature's code commits.

- **Config** lives outside the repo: a workspace `.team.json` in the parent folder (`"repos": {"<repo>": {...}}`), even
  for one repo.
- **Glossary, ADRs, dev notes** stay in the scratch folder; `<scratch>/GLOSSARY.md` is read by reviewer and docs. Never
  commit them into the repo. This replaces step 1's `docs: context` commit and step 11's `docs/dev-notes.md` commit.
- **Baseline**, before the first ticket: `python KIT/scratch.py baseline <feature>` (from the repo) runs the full test
  command and records what already fails on main; reviewer and dev treat those failures as pre-existing. Keep each
  ticket's `Test command` narrow (one test file, typecheck) so unrelated red suites do not block it.
- **Sync main, between batches only:** `python KIT/scratch.py sync-main` (dry run), then `--apply` after the user's yes.
  It merges `origin/main` into the feature branch (never rebase), refuses while any ticket of the branch is
  `in-progress` or `review`, and on a conflict aborts and leaves the branch unchanged (the user resolves it). The merge
  commit it prints is the next batch's `base:`; write it into `batches.md`. Never sync mid-batch: the reviewer's diff
  would include teammates' code.
- **One feature branch per person.** `resume` prints `WARN ... commit by someone else on the feature branch`: show it.
  It compares author emails, so the user's own commits from a machine with a different `user.email` also trigger it.
- **Merge (step 10)** may be a pull request into their protected main: the user opens it, or says "push the branch";
  then push the feature branch only, never main. Their review takes over from there.
- `guard:pushed` ignores a teammate's push of commits this clone has not fetched and a teammate deleting their own
  branch. A teammate moving a ref to a commit this clone already has reads as a push from here (false FAIL): check
  the remote with the user before treating it as the worker's.
