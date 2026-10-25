---
name: ship
description: Verify the current branch, get an independent review of the diff from the reviewer subagent, fix what it finds, then push and open a pull request. Use when work on a branch is finished and ready for a PR.
---

# Ship the current branch

1. **Preconditions.** `git branch --show-current` must be `agent/*` (never `main`).
   `git status` should be clean except for the work you're shipping. Commit with a clear
   message: `<type>: <what>` (feat, fix, refactor, test, docs, chore).
2. **Full verify.** `./scripts/verify.sh` must print `PASS`. If not, fix and repeat. Never
   weaken a check to get there.
3. **Independent review.** Run the `reviewer` subagent on `git diff origin/main...HEAD`, giving
   it the issue/plan acceptance criteria. It has fresh context on purpose; don't pre-argue your
   choices to it.
   - Fix every `blocker` and `major` finding, then re-run verify.
   - For `minor` findings, fix them or list them in the PR description with a reason.
   - If the reviewer and you disagree on something important, say so in the PR description.
4. **Write the PR description** from `.github/pull_request_template.md`: what and why, how it was
   verified (real evidence), what the reviewer should look at, new dependencies, protected files.
5. **Push and open the PR.**
   ```bash
   git push -u origin HEAD
   gh pr create --title "<type>: <what>" --body-file <description-file> --label agent
   ```
   Write the description to a temp file outside the repo first so formatting survives.
   If the `agent` label doesn't exist yet, drop `--label agent` (the `agent/` branch prefix is
   enough for the scorecard).
6. **Never merge.** Report the PR URL and stop.
