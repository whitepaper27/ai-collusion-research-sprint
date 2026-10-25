#!/usr/bin/env bash
# setup-github.sh — one-time GitHub setup. Run it yourself (a human, repo admin), AFTER the PR
# that installs the kit is merged (guard.yml must exist on the default branch first).
#
#   ./scripts/setup-github.sh                  agents use your account: checks only, no approvals
#   ./scripts/setup-github.sh --reviews 1      agents use a bot account (recommended): you must
#                                              approve every PR, code owners approve gate files
#
# Sets up:
#   - labels: agent, agent-ready, human-reviewed, allow-protected
#   - repo variable TRUSTED_LABELERS = you (only these logins' approval labels count)
#   - a ruleset on the default branch: PRs only, required checks `verify` + `guard`,
#     no force pushes, no deletion, nobody on the bypass list
#
# This, not any prompt or hook, is what stops unverified code from reaching main.

set -euo pipefail
REVIEWS=0
[[ "${1:-}" == "--reviews" ]] && REVIEWS="${2:-1}"

command -v gh >/dev/null || { echo "Install the GitHub CLI first: https://cli.github.com"; exit 1; }
REPO="$(gh repo view --json nameWithOwner,defaultBranchRef -q .nameWithOwner)"
DEFAULT="$(gh repo view --json defaultBranchRef -q .defaultBranchRef.name)"
ME="$(gh api user -q .login)"

if ! gh api "repos/$REPO/contents/.github/workflows/guard.yml?ref=$DEFAULT" >/dev/null 2>&1; then
  echo "guard.yml isn't on $DEFAULT yet. Merge the kit's install PR first, then re-run this."
  exit 1
fi
echo "Configuring $REPO (required approvals: $REVIEWS, trusted labeler: $ME)"

gh label create agent           --color 5319E7 --description "PR opened by a coding agent" --force
gh label create agent-ready     --color 0E8A16 --description "Issue is specced and ready for an agent" --force
gh label create human-reviewed  --color FBCA04 --description "HUMAN ONLY: reviewed changes to tests / check config / agent instructions" --force
gh label create allow-protected --color B60205 --description "HUMAN ONLY: approve changes to the gate, CI or applied migrations" --force

current="$(gh variable get TRUSTED_LABELERS 2>/dev/null || true)"
if [[ -z "$current" ]]; then
  gh variable set TRUSTED_LABELERS --body "$ME"
  echo "Set TRUSTED_LABELERS=$ME (add teammates comma-separated; never add the agents' bot account)"
else
  echo "TRUSTED_LABELERS already set: $current"
fi

CODE_OWNER=false; (( REVIEWS > 0 )) && CODE_OWNER=true

payload="$(cat <<JSON
{
  "name": "main: agents ship through PRs",
  "target": "branch",
  "enforcement": "active",
  "bypass_actors": [],
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "pull_request", "parameters": {
        "required_approving_review_count": $REVIEWS,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": $CODE_OWNER,
        "require_last_push_approval": $CODE_OWNER,
        "required_review_thread_resolution": false } },
    { "type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": false,
        "required_status_checks": [ { "context": "verify" }, { "context": "guard" } ] } }
  ]
}
JSON
)"

existing="$(gh api "repos/$REPO/rulesets" -q '.[] | select(.name=="main: agents ship through PRs") | .id' 2>/dev/null || true)"
if [[ -n "$existing" ]]; then
  echo "$payload" | gh api -X PUT "repos/$REPO/rulesets/$existing" --input - >/dev/null
  echo "Updated ruleset $existing"
else
  echo "$payload" | gh api -X POST "repos/$REPO/rulesets" --input - >/dev/null
  echo "Created ruleset"
fi

cat <<EOF
Done. Open a test PR and confirm both 'verify' and 'guard' run and are required.

Strongly recommended: run agents as a separate GitHub identity (a bot/machine account or a
GitHub App) with Write access only — not Admin, not Maintain. Then:
  - re-run this script with --reviews 1 so every agent PR needs your approval
  - the agent can't apply approval labels that count (it isn't in TRUSTED_LABELERS)
  - it can't edit rulesets or merge past required checks
While agents use your own account, the checks still hold, but approvals and labels can't
distinguish you from your agent.
EOF
