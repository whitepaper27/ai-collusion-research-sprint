---
name: task
description: Take a GitHub issue (or a described task) from spec to an open pull request — plan, failing tests, implementation, verification, self-review, PR. Use when asked to work on, implement, fix or build an issue end to end.
argument-hint: "<issue-number or short description>"
---

# Task: $ARGUMENTS

Work this task end to end. Stop only at the checkpoints marked **STOP**.

## 1. Understand
- If `$ARGUMENTS` is an issue number: `gh issue view $ARGUMENTS --comments`.
- Pull out the acceptance criteria. If there are none, write them down yourself (checkable by a
  test or a command) and state them at the top of your reply.
- Find the code involved and an existing example of the pattern you'll need. Use an Explore
  subagent for broad searches.
- Make sure you're on an `agent/*` branch, not `main`. If you're on `main`, create
  `agent/<short-slug>` with `git switch -c`.

## 2. Decide how much planning this needs
- **Low risk** (a few files, no data model / auth / billing / public API change): plan in your
  head, put a 3–5 line summary in the PR description later.
- **Medium**: write the plan in the PR description draft before coding.
- **High risk** (data model, auth, tenancy, billing, public API, anything hard to reverse):
  write `docs/plans/<slug>.md` from `docs/plans/TEMPLATE.md`, then **STOP** and ask the human
  to approve it. Do not implement until they do.

## 3. Tests first
- Write tests that encode the acceptance criteria and **fail for the right reason**. Run them
  and confirm the failure message is the one you expect.
- Prefer new test files. Existing tests are review-tier (`.agent/protected.txt`): change one
  only when the behavior it checks legitimately changed (e.g. a renamed interface), keep or
  strengthen its assertions, expect the human to be asked, and list it in the PR description.

## 4. Implement
- Smallest change that makes the tests pass, following the existing pattern you found.
- Run the relevant tests often. Run `./scripts/verify.sh --fast` before moving on.
- Two failed attempts at the same fix → stop editing, gather evidence (logs, a narrower test),
  state your hypothesis, then try again.

## 5. Verify for real
- `./scripts/verify.sh` (full). It must print `PASS`.
- UI change: run the app and check the actual page; describe what you saw.
- Re-read the acceptance criteria and tick each one off against evidence, not intent.

## 6. Ship
Follow `.claude/skills/ship/SKILL.md`. It runs the independent reviewer and opens the PR.
Never merge.

## 7. Report
Reply with: PR link, what changed (2–4 lines), how it was verified, and anything the human
should look at closely. If the human corrected you during the task, suggest running `/learn`.
