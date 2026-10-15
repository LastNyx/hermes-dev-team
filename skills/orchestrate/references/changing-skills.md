# Changing the team skills

Load before patching `orchestrate` or `claude-p-role-workers` (or any file under them).

- The team skills are linked into the team repo, so any `skill_manage` edit is an uncommitted change there. Before
  patching one, show the user the proposed change in one or two lines and wait for a yes. Keep it general: no project
  paths or names.
- At the end of a session that changed one, tell the user to run `python KIT/review-skills.py`, then commit.
- If this machine cannot push to the team repo: `python KIT/review-skills.py --patch > <file>`, give the user that file
  plus a one-line prompt for their other machine (`git pull`, then `git apply <file>`, show the diff, commit after a
  yes), and restore the local tree with `git restore`.
- Matt Pocock's skills are not part of the repo: never patch them, so they can still be updated from upstream.
- A lesson that helps every project goes into the team repo's `kit/roles/*.md` only after the user has read it.
