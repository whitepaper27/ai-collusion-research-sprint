#!/usr/bin/env bash
# format-file.sh — Claude Code PostToolUse hook: format each file right after the agent edits it,
# so diffs stay clean and the format step in verify.sh never fails on agent output.
# Never blocks: formatting problems are verify.sh's job.

ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
file="$("$ROOT/scripts/py.sh" -c 'import json,sys
try:
  ti=json.load(sys.stdin).get("tool_input") or {}
  print(ti.get("file_path") or "")
except Exception: print("")' 2>/dev/null)"

[[ -n "$file" && -f "$file" ]] || exit 0

case "$file" in
  *.ts|*.tsx|*.js|*.jsx|*.mjs|*.cjs|*.json|*.css|*.scss|*.md|*.mdx|*.yml|*.yaml|*.html|*.vue|*.svelte)
    if [[ -x "$ROOT/node_modules/.bin/biome" ]]; then
      "$ROOT/node_modules/.bin/biome" format --write "$file" >/dev/null 2>&1
    elif [[ -x "$ROOT/node_modules/.bin/prettier" ]]; then
      "$ROOT/node_modules/.bin/prettier" --write --log-level=silent "$file" >/dev/null 2>&1
    fi ;;
  *.py)
    if command -v uv >/dev/null 2>&1; then uv run ruff format -q "$file" >/dev/null 2>&1
    elif command -v ruff >/dev/null 2>&1; then ruff format -q "$file" >/dev/null 2>&1
    fi ;;
esac
exit 0
