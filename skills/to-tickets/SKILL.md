---
name: to-tickets
description: Break a plan into vertical-slice tickets with seams.
version: 0.1.0
author: hermes-dev-team contributors
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [team, planner, tickets]
---

# to-tickets

Turn the settled plan into tracer-bullet tickets in `.scratch/<feature>/issues/`. Learned from mattpocock/skills (to-tickets), trimmed.

## When to Use

After a grill, to make a FULL-lane feature. For the QUICK lane write one ticket from the template directly, with a Seam line, and skip the rest.

## Procedure

1. Explore the code for the area. Use `CONTEXT.md` vocabulary and respect ADRs. Done when you know the modules touched.
2. Cut vertical slices: each is a thin but complete path (schema, logic, interface, test), demoable alone, and sized for one fresh worker run (one commit). Prefactoring tickets come first. A mechanical change across many call sites is a wide refactor: expand, migrate in batches, contract.
3. For each ticket set `Blocked by`. A ticket with none can start now.
4. Propose the test seam for each ticket: the highest existing public interface; fewer seams are better. Note what the seam catches and misses.
5. Quiz the user with one `clarify` round: granularity (too coarse or fine), blocking edges, seams, merges or splits. Iterate until approved. Done when the user has approved the list.
6. Write one file per ticket, numbered in dependency order, from this skill's `templates/ticket.md.template` (`skill_view` with file_path): `Lane: full`, `Status: todo`, `Round: 0`, the feature branch, a `Terms:` line with the words the dev needs, the `Seam`, and a `Test command` that runs only that ticket's test. A worker has no web and cannot ask: put every fact it needs in the ticket.

## Pitfalls

- No file paths or code snippets in tickets unless a prototype produced a snippet that encodes a decision.
- A ticket with two commits worth of work is two tickets.
- Do not publish before approval.

## Verification

Every ticket has all fields, a runnable `Test command`, and a `Seam`; `Blocked by` forms no cycle.
