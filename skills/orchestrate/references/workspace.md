# Multi-repo workspace

Load when the config has `"repos"`: several git repos side by side in a folder that is not a repo, team files kept
out of the repos.

- The workspace holds one `.team.json` with shared keys, `"scratch": ".scratch"`, optional `"glossary"`, and
  `"repos": {"<folder>": {"test": "..."}}` for per-repo keys. The launcher, `costs.py` and `tickets.py` find it from
  inside a repo.
- One feature folder in the workspace scratch; one feature branch per repo the feature touches (same name).
- Every ticket has a `Repo:` line (the launcher refuses another cwd); `Blocked by` may cross repos.
- A review batch holds tickets of one repo only.
- Dispatch with cwd = the ticket's repo, and the ticket path as the absolute path or `../.scratch/...` (the launcher
  grants the worker that folder and nothing else outside the repo).
- Glossary and ADRs: one workspace-level glossary when the terms are shared (set `"glossary"`; reviewer and docs read
  it), else one per repo. Never commit a workspace file into a repo.
- Step 10: name every repo's branch and the merge order; the user merges the upstream repo first (e.g. API before UI).
