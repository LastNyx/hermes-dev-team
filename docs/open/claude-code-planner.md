# Design: Claude Code as planner, in every repo (open)

Status: grill in progress, started 2026-10-22 in a Claude Code session in comfyui-prompting-skill. Nothing built.
Continue the grill in a Claude Code session opened in this repo.

## Goal

Claude Code (Opus) takes over as planner from Hermes and can run the `orchestrate` loop in any repo, not just one.
Workers stay as they are (`kit/run-worker.py`, sonnet/haiku, allow-lists, guards).

## Decided

1. **Skill source: link + adapter.** Junction `~/.claude/skills/orchestrate` -> `<kit>/skills/orchestrate` (the same
   files Hermes uses, so a fix lands here for both planners). Add one reference `references/claude-code.md` mapping the
   Hermes calls: `terminal(background=true, heartbeat=120)` -> Bash/PowerShell `run_in_background`; `skill_view(file_path=X)`
   -> Read `references/X`; `clarify` -> AskUserQuestion; Hermes memory fact -> Claude Code memory; `python` -> see open 2.
   SKILL.md's trigger table gets one row: `claude-code.md` | the planner is Claude Code.
2. **Planning skills: install separately** from https://github.com/mattpocock/skills.git, not by linking Hermes' copies.

## Open (next session)

1. Where the mattpocock clone lives (proposed `C:/home/project/mattpocock-skills`, you pull), and whether to link only
   grill-with-docs, grilling, domain-modeling, to-tickets into `~/.claude/skills` or all of them; check whether the
   repo ships a Claude Code plugin manifest first.
2. `python` is not on PATH on this machine (only uv's `C:\Users\User\AppData\Roaming\uv\python\cpython-3.14-windows-x86_64-none\python.exe`),
   and project test commands call bare `python`. Options: the user adds it to user PATH / `uv python install 3.14 --default`
   (proposed); the adapter prepends it per dispatch; install python.org Python.
3. Two planners: one per feature (proposed: step 0 writes `decide: planner = claude-code`, the other refuses until
   handed over) / Claude Code only / either anytime.
4. Rules for every repo (no Co-Authored-By trailer, never push, Opus plans and Sonnet works): `~/.claude/CLAUDE.md`
   (proposed, Claude Code memory is per project folder) / only in the skill / per-project memory.
5. Do Hermes' uncommitted edits to `skills/orchestrate` (SKILL.md, browser-projects.md, research.md, tickets.md,
   seen 2026-10-22) get reviewed (`kit/review-skills.py`) and committed before the adapter lands on top of them?
6. Release: one grilled design = one version (0.15.0?), CHANGELOG line, test with a headless Claude Code planner on a
   throwaway repo (the design.md "test planner-facing skill text" lesson, with `claude -p` instead of `hermes chat`).

## Handover state

comfyui-prompting-skill, feature `comfy`, batch 1 (01, 02, base `9d01b01`): both tickets `review`, planner Verify PASS,
shim browser run and reviewer round 1 not done yet.
