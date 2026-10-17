# Hermes backend (agnostic fallback)

The kit runs without Claude: when `claude` is not logged in (or `.team.json` `"backend": "hermes"`), each worker is one
headless run in a dedicated Hermes worker profile. Code: `kit/backends.py` (`Hermes`). The other backend:
[backend-claude.md](backend-claude.md).

## Setup (once per machine, by the user)

```
hermes profile create hdt-worker --no-skills --no-alias
hermes -p hdt-worker model          # provider + default model for workers, e.g. anthropic / claude-sonnet-5-5
hermes -p hdt-worker chat -q "reply OK" -Q   # the first run installs dependencies (slow once)
```

The launcher prints these on `ERROR no Hermes profile <name>`; it never creates or edits a profile or Hermes config.
Keys: `hermes_profile` (default `hdt-worker`), `hermes_models` (full provider model id per role), `hermes_provider`.
Then `python tests/backend_smoke.py hermes` proves it works.

## The run

`hermes -p <profile> chat --query-file <prompt> -Q --in <repo> -t file,terminal --max-turns N --ignore-rules
--source tool --format stream-json [-m <model>] [--provider <p>] [--reasoning <effort>]`. The researcher gets `-t file`
(no terminal); `--web` adds `web`. Sessions land in the worker profile, not the user's chat list.

Measured (Sonnet 5.5, anthropic provider, toy ticket): 28 s vs 27 s for `claude -p`, about a quarter of the cached
prompt tokens. A turn cap exits 1 while the model still writes its own RESULT line, so any non-zero exit FAILs.
`TERMINAL_CWD` overrides `--in`; the launcher unsets it. Keep the profile on a plain provider: the
claude-subscription plugin spawns one `claude` per model call and made a run about 3x slower.

## What enforces the limits

Nothing blocks a call while it runs: `-t file,terminal` restricts nothing inside the shell. Runs are tagged
`backend=hermes enforce=prompt-only`.

- **Prompt:** `kit/roles/_tool-rules-hermes.md` replaces the Claude tool rules. Measured: with it the worker refused
  all 4 forbidden chores; without it, it did all 4.
- **After-run guards (same as Claude):** main moved, branch changed, history rewritten, protected test-config file
  changed, git hooks or config changed, a push from this clone. For the researcher also: any file in the source folder
  changed (`.git` included; fingerprinted without running git), or anything but the findings file written in its
  topic folder.

Not caught: a write outside the repo, scratch and source folders; a branch switch that switches back. Review before
merge is the defence, as for the test command.
