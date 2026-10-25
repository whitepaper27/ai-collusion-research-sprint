#!/usr/bin/env bash
# stop-verify.sh — Claude Code Stop hook: local "are you actually done?" check.
#
# When Claude tries to finish a turn and the tree has changes, run verify.sh --fast. If it
# fails, exit 2: Claude gets the failure tail and keeps working. The last retry tells it to
# finish with an explicit BLOCKED report instead of claiming success; after that the hook lets
# it stop and leaves .verify-logs/BLOCKED for you. Skips re-running when nothing changed.
#
# This is ergonomics, not enforcement: it keeps agents honest in the loop. The gate that
# decides what merges is CI (verify) + the trusted guard.

set -uo pipefail
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
cd "$ROOT" || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

SESSION="$("$ROOT/scripts/py.sh" -c 'import json,sys
try: print(json.load(sys.stdin).get("session_id","default"))
except Exception: print("default")' 2>/dev/null || echo default)"

STATE_DIR="$ROOT/.verify-logs"; mkdir -p "$STATE_DIR"
COUNT_FILE="$STATE_DIR/stop-$SESSION.count"
PASS_FILE="$STATE_DIR/last-pass.state"
MAX_RETRIES="${STOP_VERIFY_MAX:-3}"

# Fingerprint of the working tree: HEAD + tracked diff + untracked file list.
state="$(git rev-parse HEAD 2>/dev/null)-$( (git diff HEAD 2>/dev/null; git ls-files --others --exclude-standard) | git hash-object --stdin)"

# Clean tree on the base branch → nothing to verify.
if [[ -z "$(git status --porcelain)" ]] && [[ "$(git rev-parse --abbrev-ref HEAD)" =~ ^(main|master)$ ]]; then
  exit 0
fi
# Already verified this exact state.
[[ -f "$PASS_FILE" && "$(cat "$PASS_FILE")" == "$state" ]] && exit 0

count=$(cat "$COUNT_FILE" 2>/dev/null || echo 0)
if (( count >= MAX_RETRIES )); then
  # Budget spent: let the agent stop (its last message should be the BLOCKED report).
  rm -f "$COUNT_FILE"
  { date; echo "session $SESSION: verify --fast still failing after $MAX_RETRIES attempts"; } > "$STATE_DIR/BLOCKED"
  exit 0
fi

if out="$("$ROOT/scripts/verify.sh" --fast 2>&1)"; then
  echo "$state" > "$PASS_FILE"
  rm -f "$COUNT_FILE" "$STATE_DIR/BLOCKED"
  exit 0
fi

attempt=$((count + 1))
echo "$attempt" > "$COUNT_FILE"
{
  if (( attempt < MAX_RETRIES )); then
    echo "verify.sh --fast is failing, so the task isn't done yet (attempt $attempt/$MAX_RETRIES)."
    echo "Fix the cause. Don't weaken, skip or suppress checks."
  else
    echo "verify.sh --fast is still failing (final attempt $attempt/$MAX_RETRIES)."
    echo "Try once more. If it still fails, do NOT say the task is done. End your reply with:"
    echo "  BLOCKED: verification failing"
    echo "  - what fails (check name + the key error line)"
    echo "  - what you tried and your best hypothesis"
    echo "  - what you need from the human"
  fi
  echo
  echo "$out" | tail -n 80
} >&2
exit 2
