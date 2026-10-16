# Browser-based projects

Load only for a project with a browser UI. Workers have no browser tools, but a headless Playwright run works as the
test command.

- Set `.team.json` `test` to it (e.g. `npx playwright test`) and let the Playwright config's `webServer` start the app:
  the worker may run only that one command, so it cannot start a server itself.
- Add `playwright.config.*` and the server script to `protected` (they are not in the launcher's list).
- Put the page or flow in each ticket's `Seam` so the dev writes a browser test.
- Tests check only what they assert: visual or UX checks stay with you (planner) in your own browser, with findings
  written to the review file or a fix ticket, not only chat.

## No browser test in the project (page-only tickets)

- Put the testable logic (filtering, searching, counting) in the backend layer with unit tests, and let the page only
  render: a page-only ticket then has `Seam: <page file> (no automated UI test; the planner checks it in a browser)`,
  the normal test command (it must stay green), and a `Verify:` line naming the browser check. Dev workers commit such
  tickets fine and say "no red step" in Result.
- To verify a page that normally runs inside a native shell (pywebview, Electron) without that shell: a throwaway
  stdlib HTTP server on `127.0.0.1` in the scratch dir that serves the real page with a small JS shim replacing the
  bridge, and answers each bridge call with the project's REAL backend functions (not canned data). Drive it with the
  project's fake data generator plus the project's own real files. Kill the server and delete the script and sandbox
  afterwards.
- Check interactions, not only each criterion: open an item, then change the filter/search so it is excluded; let a
  live item end while hidden; look at every other screen that reads the changed data (settings, counts, toasts).
  Criteria-only checks miss these and the reviewer then spends rounds finding them one at a time.
- Use real files written on this OS too (e.g. Windows CRLF logs), not only generated ones: a generator writing `\n`
  hides line-ending bugs the real writer has.
- When review rounds keep finding narrower bugs in page-side state (a cached copy that goes stale), stop patching the
  page: rewrite the fix ticket so the backend returns what the page needs (tested), and the page drops its copy.
