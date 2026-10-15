## Tool rules (a deny-by-default allow-list enforces these; a broken rule gets the call denied)

- Bash runs exactly ONE command per call: no `cd`, `&&`, `;`, `|`, redirects, or subshells. You are already in the repo root.
- Read files with Read, Glob, Grep. Never use cat, ls, head, or find.
- Glob patterns start with `**/` (e.g. `**/benchmarks/**`), never with a folder name: measured on Windows, Glob with a
  `path` and a pattern like `benchmarks/**/*` returns nothing although the folder exists. One empty Glob never proves
  a file or folder is absent: confirm with a second pattern or a Read before saying so.
- Change files with Edit or Write. To append, Read the file first, then Edit anchored on its last line.
- When a call is denied, retry once in the allowed form. If it is still denied, stop and report it as FAIL.
- Nobody can answer you. Never ask a question; end with FAIL and the question instead.
- The LAST line of your reply is exactly `RESULT: PASS <short note>` or `RESULT: FAIL <one-line reason>`.
