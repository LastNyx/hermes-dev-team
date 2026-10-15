# Browser-based projects

Load only for a project with a browser UI. Workers have no browser tools, but a headless Playwright run works as the
test command.

- Set `.team.json` `test` to it (e.g. `npx playwright test`) and let the Playwright config's `webServer` start the app:
  the worker may run only that one command, so it cannot start a server itself.
- Add `playwright.config.*` and the server script to `protected` (they are not in the launcher's list).
- Put the page or flow in each ticket's `Seam` so the dev writes a browser test.
- Tests check only what they assert: visual or UX checks stay with you (planner) in your own browser, with findings
  written to the review file or a fix ticket, not only chat.
