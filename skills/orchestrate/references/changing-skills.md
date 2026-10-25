# Changing the team skills

Load before patching `orchestrate` (or any file under it), a role file, or the launcher. Backend details live in the
team repo, not in a skill: `docs/backend-claude.md` (allow-list rules, each measured) and `docs/backend-hermes.md`
(prompt-only limits, which guards apply). After a launcher or backend change, run `tests/backend_smoke.py`.
New machine, or after a kit change: `python <team repo>/tests/backend_smoke.py` (calls a model) proves each backend.
Workers may run the test command, which executes code the dev wrote. That hole remains; review before merge.

- The team skills are linked into the team repo, so any `skill_manage` edit is an uncommitted change there. Before
  patching one, show the user the proposed change in one or two lines and wait for a yes. Keep it general: no project
  paths or names.
- At the end of a session that changed one, tell the user to run `python KIT/review-skills.py`, then commit.
- If this machine cannot push to the team repo: `python KIT/review-skills.py --patch > <file>`, give the user that file
  plus a one-line prompt for their other machine (`git pull`, then `git apply <file>`, show the diff, commit after a
  yes), and restore the local tree with `git restore`.
- Matt Pocock's skills are not part of the repo: never patch them, so they can still be updated from upstream.
- A lesson that helps every project goes into the team repo's `kit/roles/*.md` only after the user has read it.

## Adding a role or a role rule

- Ship the role prompt, its JSON allow-list, the launcher wiring (and what the Hermes backend gets instead: tool
  rules, a guard), a row in TEAM.md, a line in `docs/design.md`, a skill reference with its trigger row, and a test
  file with at least one real run; one grilled design is one release.
- Agnostic by default: a new mechanism must work on both backends, or say in `docs/design.md` why it cannot. Prefer
  an after-run guard (works everywhere) over a Claude-only permission rule.
- Don't grow back: when the planner skips a step, fix the instruction or accept the slip. Add code (a refusal, a WARN)
  only when a slip has cost real work more than once.
- Each new role rule gets a toy repo where the wrong path is tempting, run for real; if the worker ignores a rule
  written as a principle, rewrite it as a numbered procedure step and rerun.
- When a `## Result` or review gains a new line (e.g. `Not checked:`), also add the planner step that shows it to the
  user, or nobody reads it.

## Keeping SKILL.md small

The planner loads all of SKILL.md every session, so its size is paid on every project. Check `wc -w` before adding.
- A rule that applies only to one project shape or event (multi-repo, shared repo, switching, Hermes backend, browser
  UI, a single step's detail) goes into the matching `references/<topic>.md`, never into the main procedure. Extend
  an existing reference before creating one; add a new one only with a row in SKILL.md's trigger table.
- SKILL.md keeps the core loop (steps 0-11) and pitfalls that apply to every project; each step names the reference
  it needs (e.g. step 2 -> `tickets.md`).
- Moving text into a reference is a move, not a rewrite: keep each rule's wording, so the CHANGELOG can say "moved,
  not changed". Mark the CHANGELOG entry **re-copy** and tell the user to start a new planner session.
- After a split, confirm a reference loads: `skill_view(name="orchestrate", file_path="references/<file>")`.
- Then test the skill TEXT with a real planner, not just the scripts: build a throwaway workspace that hits the
  triggers you moved (e.g. `shared: true`, a red test on main, a ticket that deletes a file), and run
  `hermes chat --query-file <prompt> -Q --format stream-json -s orchestrate -t skills,terminal,file --max-turns 40
  --source tool --in <repo>` with a prompt that bounds the steps, says "where the skill says ask or wait, do not
  act; write the question instead", forbids dispatch, and asks for the final approval message. Parse the JSONL for
  `skill_view` file_paths and the commands run, check git state (main unmoved, nothing committed into the repo), then
  delete the workspace. It takes several minutes, so run it as a background terminal with notify.
- To rewrite SKILL.md wholesale, write the new text to the scratch dir and copy it over the linked file; `write_file`
  refuses to overwrite a file it last saw only partially.
