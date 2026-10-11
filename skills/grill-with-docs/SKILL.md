---
name: grill-with-docs
description: Grill a plan in rounds and write CONTEXT.md and ADRs.
version: 0.1.0
author: hermes-dev-team contributors
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [team, planner, grilling]
---

# grill-with-docs

Interview the user until every branch of the design is decided, and write the shared language down as you go.
Learned from mattpocock/skills (grilling and domain-modeling), shortened.

## When to Use

The user wants to think a plan through before any code. Do NOT use when the design is settled, or for a one-line fix (quick lane).

## Procedure

1. Ground with a few reads: `CONTEXT.md`, `docs/adr/`, the manifest, `git log`. Look up facts yourself; ask only for decisions. Done when you can quote paths for the constraints that shape the questions.
2. Map the design tree. The frontier is every decision whose prerequisites are settled. Ask the whole frontier in one round (max 5 questions): `clarify` for picks (2-4 options, recommended first, the consequence inside each label), plain text for open questions. State low-stakes defaults instead of asking.
3. After each round, state what the answers change or invalidate, recompute the frontier, ask the next round.
4. Whenever a term is used loosely or two words mean one thing, stop and settle the word. Write it to `CONTEXT.md` immediately (format in this skill's `templates/CONTEXT.md.template`, via `skill_view` with file_path): one line per term, plus words to avoid.
5. Write an ADR in `docs/adr/NNNN-slug.md` only for a decision that is hard to reverse, surprising, and the result of a real trade-off. Three sentences: context, decision, why.
6. Push back when the evidence says so. Name the mechanism and the alternative once; if the user reaffirms, record their choice and stop.
7. The grill ends when the frontier is empty. Do not start building. Wait for build, implement, fix, go or commit.

## Pitfalls

- Answering open questions is not a build signal.
- Do not ask what you can read.
- Keep CONTEXT.md a glossary, not a design doc: terms and one-line meanings only.

## Verification

Every branch has a decision or a named open question; `CONTEXT.md` holds each term settled; no code was written.
