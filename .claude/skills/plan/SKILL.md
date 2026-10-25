---
name: plan
description: Write an implementation plan to docs/plans/ for a non-trivial or risky change and stop for human approval. Use for data model, auth, billing, public API or multi-module changes, or when asked to plan or design before coding.
argument-hint: "<issue-number or description>"
---

# Plan: $ARGUMENTS

Produce a plan a human can approve in two minutes and an agent can implement in one shot.
Do not write implementation code in this skill.

1. **Gather context.** If `$ARGUMENTS` is an issue number, `gh issue view $ARGUMENTS --comments`.
   Read the code involved. Find the existing pattern to follow and name it.
2. **Check the hard-to-reverse decisions:** data model, migrations, auth and permissions,
   tenancy, billing, public API shape, new dependencies. These are where planning pays off;
   be specific about each one that applies.
3. **Write `docs/plans/<kebab-slug>.md`** from `docs/plans/TEMPLATE.md`. Rules:
   - Acceptance criteria must each be checkable by a test or a command.
   - "Out of scope" is mandatory. It's how small PRs stay small.
   - If the change is bigger than ~400 lines, split it into numbered plans or phases, each
     independently shippable.
   - List real alternatives only where you actually considered one, and why you didn't pick it.
4. **Self-check before stopping:** could another agent implement this without asking a question?
   If not, the plan is missing something. Fix it.
5. **STOP.** Show the human the plan path, the acceptance criteria, and the one or two decisions
   you most want them to check. Wait for approval before any implementation.
