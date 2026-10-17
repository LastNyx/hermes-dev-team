# LEARN: prompt for a Hermes agent on a new machine

Human: clone this repo, then paste the block below into Hermes, replacing `<REPO>` with the clone's absolute path
(forward slashes, e.g. `D:/code/hermes-dev-team`). After the first run the skills are LINKED to the repo, so a
`git pull` updates them directly; paste again only when CHANGELOG marks an **action**, or to check the setup.

---

```text
Learn the hermes-dev-team repo at <REPO>. Do these steps in order and stop to ask me if one fails.

1. Read <REPO>/README.md, TEAM.md, CHANGELOG.md, and docs/design.md. Do not run anything yet.
2. Check prerequisites and report each version: `claude --version` (2.1.296 or newer), `python --version`, `git --version`.
   If claude is missing or not logged in, ask me: run `claude auth login` (recommended), or use the Hermes fallback
   backend. For the fallback, check `hermes profile list` for `hdt-worker`; if missing, give me the three setup
   commands from the README's Backends section and wait. Never handle credentials or edit Hermes config yourself.
3. Skills: run `python <REPO>/kit/link-skills.py` (dry run) and show me its output.
   - Lines "would ... link" are safe. Run it again with `--apply`.
   - A "SKIP ... local copy differs" line means my local skill grew. Show me the diff between my copy and
     <REPO>/skills/<name>. Ask whether to merge my changes into the repo copy first. Never discard my changes.
     After merging, run link-skills again with `--apply`.
   - Never delete a local skill that the repo does not have.
   - A "remove dangling link" line is expected after a pull that dropped a skill: the link points into <REPO>
     at a folder that no longer exists. --apply removes the link only.
   - A "MISSING <name>" line means one of Matt Pocock's planning skills is not installed. Ask me once for all of
     them, then run the printed `hermes skills install skills-sh/mattpocock/skills/<name> --yes` for each.
     Never copy them into <REPO>, never edit them.
   Then confirm with skills_list that orchestrate, grill-with-docs, grilling,
   domain-modeling, and to-tickets appear, and that grill-with-docs and to-tickets are NOT links into <REPO>.
4. Save one memory fact: "hermes-dev-team kit is at <REPO>/kit; workers run via `python <REPO>/kit/run-worker.py`;
   team skills are linked into the repo, so skill edits show in `git -C <REPO> status`; agent commits never carry a
   Co-Authored-By trailer." Replace any older entry about hermes-dev-team instead of adding a second.
5. Smoke test in a throwaway git repo under your scratch directory (never in <REPO> or a real project):
   create main with one file, create branch `smoke`, then run
   `python <REPO>/kit/run-worker.py docs "feature: .scratch/none"` and report the RESULT line.
   PASS or a FAIL that names a missing feature folder both prove the launcher works; an ERROR line or a
   traceback does not. Delete the throwaway repo afterwards (cd out of it first).
6. Report in at most 8 lines: versions, skills linked, the memory saved, and the smoke result.
7. Research home: tell me the researcher keeps clones and findings in `HDT_RESEARCH_HOME` (else `~/research`; keep
   it out of OneDrive, AppData and paths with spaces). Ask whether to run `python <REPO>/kit/research.py init`;
   never set the environment variable yourself.
8. If I have projects that used the kit before 0.11.0, tell me to follow <REPO>/EXISTING-PROJECTS.md for each one
   (it needs a private remote I create and a choice of which machine is newest). Do not run it yourself unasked.

Do not commit or push. Do not change Hermes config. The only repo changes allowed are skill merges I approved in step 3.
```
