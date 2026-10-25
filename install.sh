#!/usr/bin/env bash
# install.sh — copy this kit into a repo.   Usage:  ./install.sh /path/to/your/repo
# Never overwrites an existing file; it tells you what it skipped so you can merge by hand.

set -euo pipefail
KIT="$(cd "$(dirname "$0")" && pwd)"
DEST="${1:-}"
[[ -n "$DEST" && -d "$DEST/.git" ]] || { echo "usage: ./install.sh /path/to/git/repo"; exit 1; }
DEST="$(cd "$DEST" && pwd)"

FILES=(
  AGENTS.md CLAUDE.md
  .agent/protected.txt
  .claude/settings.json
  .claude/agents/reviewer.md
  .claude/skills/task/SKILL.md .claude/skills/plan/SKILL.md .claude/skills/ship/SKILL.md
  .claude/skills/fix-ci/SKILL.md .claude/skills/learn/SKILL.md
  .github/workflows/ci.yml .github/workflows/guard.yml .github/workflows/claude-review.yml
  .github/CODEOWNERS
  .github/pull_request_template.md .github/ISSUE_TEMPLATE/agent-task.md
  docs/plans/TEMPLATE.md
  scripts/verify.sh scripts/agent_guard.py scripts/stop-verify.sh scripts/py.sh scripts/format-file.sh
  scripts/wt.sh scripts/scorecard.py scripts/setup-github.sh
)

copied=0; skipped=()
for f in "${FILES[@]}"; do
  if [[ -e "$DEST/$f" ]]; then skipped+=("$f"); continue; fi
  mkdir -p "$DEST/$(dirname "$f")"
  cp "$KIT/$f" "$DEST/$f"
  copied=$((copied + 1))
done
chmod +x "$DEST"/scripts/*.sh "$DEST"/scripts/*.py 2>/dev/null || true

touch "$DEST/.gitignore"
grep -qxF ".verify-logs/" "$DEST/.gitignore" || printf '\n# agent kit\n.verify-logs/\n' >> "$DEST/.gitignore"
grep -qxF ".claude/settings.local.json" "$DEST/.gitignore" || echo ".claude/settings.local.json" >> "$DEST/.gitignore"

echo "Copied $copied files into $DEST"
if (( ${#skipped[@]} )); then
  echo "Skipped (already exist — merge by hand from $KIT):"
  printf '  %s\n' "${skipped[@]}"
fi
cat <<'NEXT'

Next:
  1. Fill in the <placeholders> in AGENTS.md (project, stack, commands, conventions).
  2. Replace @OWNER in .github/CODEOWNERS.
  3. Check ./scripts/verify.sh runs on your repo and prints PASS.
  4. Commit on a branch, open a PR, merge it once `verify` is green. (`guard` can't run on
     this PR yet: it runs from the base branch, where it doesn't exist until you merge.)
  5. Then run ./scripts/setup-github.sh (labels, TRUSTED_LABELERS, branch protection that
     requires `verify` + `guard`).
  6. Recommended: give agents their own GitHub identity, then ./scripts/setup-github.sh --reviews 1
NEXT
