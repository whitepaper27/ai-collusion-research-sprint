#!/usr/bin/env bash
# verify.sh — the single gate. Agents run it before saying "done"; CI runs the same thing.
#
#   ./scripts/verify.sh          full: format, lint, types, tests, build
#   ./scripts/verify.sh --fast   skip build and e2e (for the inner loop)
#
# Prints one line per step. On failure, prints only the tail of that step's log so an agent's
# context isn't flooded. Full logs: .verify-logs/<step>.log
#
# Auto-detects a JS/TS project (package.json) and/or a Python project (pyproject.toml).
# For JS/TS it runs these package.json scripts if they exist, in order:
#   format:check, lint, typecheck, test, build, test:e2e
# Edit STEPS below if your repo differs — but this file is protected: agents can't change it.

set -uo pipefail

FAST=0
[[ "${1:-}" == "--fast" ]] && FAST=1

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT" || exit 1
LOG_DIR="$ROOT/.verify-logs"
mkdir -p "$LOG_DIR"
TAIL_LINES="${VERIFY_TAIL_LINES:-60}"

if [[ -t 1 ]]; then OK=$'\033[32m✓\033[0m'; NO=$'\033[31m✗\033[0m'; else OK='✓'; NO='✗'; fi

FAILED=()
PASSED=0
START=$(date +%s)

run_step() {
  local name="$1"; shift
  local log="$LOG_DIR/$name.log"
  local t0; t0=$(date +%s)
  if "$@" >"$log" 2>&1; then
    printf '  %s %-14s %ss\n' "$OK" "$name" "$(( $(date +%s) - t0 ))"
    PASSED=$((PASSED + 1))
  else
    printf '  %s %-14s %ss  (full log: .verify-logs/%s.log)\n' "$NO" "$name" "$(( $(date +%s) - t0 ))" "$name"
    echo "  ---- last $TAIL_LINES lines ----"
    tail -n "$TAIL_LINES" "$log" | sed 's/^/  │ /'
    echo "  -------------------------------"
    FAILED+=("$name")
  fi
}

# ---------- JS / TS ----------
if [[ -f package.json ]]; then
  if   [[ -f pnpm-lock.yaml ]]; then PM=pnpm
  elif [[ -f yarn.lock ]];      then PM=yarn
  elif [[ -f bun.lockb || -f bun.lock ]]; then PM=bun
  else PM=npm; fi

  has_script() {
    node -e "const s=require('./package.json').scripts||{};process.exit(s[process.argv[1]]?0:1)" "$1" 2>/dev/null
  }

  echo "verify ($PM)$([[ $FAST == 1 ]] && echo ' --fast')"
  STEPS=(format:check lint typecheck test)
  [[ $FAST == 0 ]] && STEPS+=(build test:e2e)
  for s in "${STEPS[@]}"; do
    if has_script "$s"; then run_step "${s//:/-}" "$PM" run "$s"; fi
  done
fi

# ---------- Python ----------
if [[ -f pyproject.toml ]]; then
  echo "verify (python)$([[ $FAST == 1 ]] && echo ' --fast')"
  PY_RUN=""
  command -v uv >/dev/null 2>&1 && PY_RUN="uv run"
  if $PY_RUN ruff --version >/dev/null 2>&1; then
    run_step format  $PY_RUN ruff format --check .
    run_step lint    $PY_RUN ruff check .
  fi
  if   $PY_RUN pyright --version >/dev/null 2>&1; then run_step typecheck $PY_RUN pyright
  elif $PY_RUN mypy --version   >/dev/null 2>&1; then run_step typecheck $PY_RUN mypy .
  fi
  if $PY_RUN pytest --version >/dev/null 2>&1; then
    if [[ $FAST == 1 ]]; then run_step test $PY_RUN pytest -q -x -m "not e2e"
    else                      run_step test $PY_RUN pytest -q; fi
  fi
fi

# ---------- Guard (local feedback) ----------
# Flags diffs that weaken checks or touch protected paths. Locally this is early warning; the
# authoritative run is CI's guard.yml, which uses the base branch's copy of the rules and script.
if [[ -f scripts/agent_guard.py && "${VERIFY_SKIP_GUARD:-0}" != "1" ]]; then
  run_step guard scripts/py.sh scripts/agent_guard.py --diff "${VERIFY_BASE:-origin/main}"
fi

ELAPSED=$(( $(date +%s) - START ))
if (( ${#FAILED[@]} )); then
  echo "FAIL: ${FAILED[*]}  (${PASSED} passed, ${ELAPSED}s)"
  exit 1
fi
if (( PASSED == 0 )); then
  echo "FAIL: no checks ran. Add scripts to package.json or tools to pyproject.toml."
  exit 1
fi
echo "PASS: ${PASSED} checks (${ELAPSED}s)"
