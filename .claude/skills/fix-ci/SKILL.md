---
name: fix-ci
description: Diagnose and fix failing CI checks on a pull request by reproducing them locally first. Use when a PR's checks are red or the user says CI is failing.
argument-hint: "[pr-number]"
---

# Fix CI

PR: $ARGUMENTS (if empty, use the current branch's PR)

1. **Find what failed.** `gh pr checks $ARGUMENTS` (omit the number to use the current branch).
   For each failing check: `gh run view <run-id> --log-failed`, and read only the failing step.
2. **Reproduce locally before changing anything.** Run the same command CI ran (usually
   `./scripts/verify.sh`). If it passes locally but fails in CI, look for environment
   differences: lockfile, Node/Python version, missing env var, test order, timing, timezone.
3. **Classify it**, and say which in your reply:
   - **Real bug** in the change → fix the code.
   - **Flaky test** → don't retry until green. Find the nondeterminism (time, order, network,
     shared state). Fixing an existing test is review-tier: make the fix, explain it in the PR,
     and leave the `human-reviewed` label to the human.
   - **`guard` failed** → read its report. Suppressed checks or locked/immutable files: undo that
     part. Review-tier changes the PR genuinely needs: keep them, justify them in the PR
     description, and tell the human it's waiting on their review label. Agents never apply
     `human-reviewed` or `allow-protected`.
   - **Infrastructure** (runner, outage, quota) → report it and stop; don't change code.
4. **Fix, verify, push.** `./scripts/verify.sh` → commit → `git push`. Then report what was
   wrong in one or two lines.
