#!/usr/bin/env bash
# py.sh — run the first Python >= 3.11 that actually works:  scripts/py.sh script.py [args]
#
# Hooks, verify.sh and the kit tests call this instead of `python3` directly. On Windows,
# `python3` is often the Microsoft Store stub: it's on PATH but fails (exit 126), and Claude
# Code treats a failing hook as non-blocking, so the guard would silently stop guarding.
# 3.11+ because agent_guard.py needs tomllib to check pyproject test config (on 3.10 it
# silently skips that check). Falls back to a uv-managed interpreter.
# If no interpreter works, exit 2 so hooks fail closed with a visible reason.

for p in python3 python py; do
  if "$p" -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
    exec "$p" "$@"
  fi
done
if p="$(uv python find '>=3.11' 2>/dev/null)" && [[ -n "$p" ]]; then
  exec "$p" "$@"
fi
echo "py.sh: no working Python >= 3.11 (tried python3, python, py, uv). Try: uv python install 3.12" >&2
exit 2
