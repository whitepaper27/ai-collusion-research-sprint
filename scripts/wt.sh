#!/usr/bin/env bash
# wt.sh — one git worktree per agent, so 3–10 agents can work in parallel without colliding.
#
#   ./scripts/wt.sh new <name> [base]   create ../<repo>.worktrees/<name> on branch agent/<name>
#   ./scripts/wt.sh ls                  list worktrees with branch, PR state, dirty/clean
#   ./scripts/wt.sh path <name>         print the path (cd "$(./scripts/wt.sh path foo)")
#   ./scripts/wt.sh rm <name>           remove a worktree (refuses if it has uncommitted work)
#   ./scripts/wt.sh clean               remove every worktree whose branch is merged into base
#
# Copies gitignored env files (.env, .env.local, ...) into new worktrees and installs deps,
# so each agent starts from a working checkout.

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
REPO="$(basename "$ROOT")"
WT_DIR="${WT_DIR:-$(dirname "$ROOT")/$REPO.worktrees}"
BASE_DEFAULT="${WT_BASE:-main}"

die() { echo "wt: $*" >&2; exit 1; }

install_deps() {
  local dir="$1"
  ( cd "$dir"
    if   [[ -f pnpm-lock.yaml ]]; then pnpm install --frozen-lockfile --silent
    elif [[ -f yarn.lock ]];      then yarn install --frozen-lockfile --silent
    elif [[ -f bun.lockb || -f bun.lock ]]; then bun install --frozen-lockfile
    elif [[ -f package-lock.json ]]; then npm ci --silent
    fi
    if [[ -f uv.lock ]]; then uv sync --quiet; fi
  ) || echo "wt: dependency install failed — run it manually in $dir" >&2
}

cmd_new() {
  local name="${1:-}"; local base="${2:-$BASE_DEFAULT}"
  [[ -n "$name" ]] || die "usage: wt new <name> [base]"
  [[ "$name" =~ ^[a-zA-Z0-9._-]+$ ]] || die "name may only contain letters, digits, . _ -"
  local dir="$WT_DIR/$name" branch="agent/$name"
  [[ -e "$dir" ]] && die "$dir already exists"
  mkdir -p "$WT_DIR"
  git -C "$ROOT" fetch --quiet origin "$base" 2>/dev/null || true
  local start="origin/$base"
  git -C "$ROOT" rev-parse --verify --quiet "$start" >/dev/null || start="$base"
  git -C "$ROOT" worktree add --quiet -b "$branch" "$dir" "$start"
  # copy env files — only ones git ignores, so a secret can never end up committed from here
  for f in "$ROOT"/.env "$ROOT"/.env.*; do
    [[ -f "$f" ]] || continue
    if git -C "$ROOT" check-ignore -q "$f"; then
      cp "$f" "$dir/"
    elif [[ "$(basename "$f")" != ".env.example" ]]; then
      echo "  skipped $(basename "$f"): not gitignored (add it to .gitignore first)" >&2
    fi
  done
  install_deps "$dir"
  echo "$dir"
  echo "  branch: $branch (from $start)"
  echo "  start an agent:  cd \"$dir\" && claude"
}

cmd_ls() {
  git -C "$ROOT" worktree list --porcelain | awk '
    /^worktree /{path=substr($0,10)} /^branch /{br=substr($0,8); sub("refs/heads/","",br)}
    /^$/{if(path!="") print path"\t"br; path=""; br=""} END{if(path!="") print path"\t"br}' |
  while IFS=$'\t' read -r path br; do
    [[ "$path" == "$ROOT" ]] && continue
    local dirty="clean"; [[ -n "$(git -C "$path" status --porcelain 2>/dev/null)" ]] && dirty="DIRTY"
    local pr="-"
    if command -v gh >/dev/null 2>&1 && [[ -n "$br" ]]; then
      pr="$(gh pr view "$br" --json number,state -q '"#\(.number) \(.state)"' 2>/dev/null || echo '-')"
    fi
    printf '%-28s %-28s %-6s %s\n' "$(basename "$path")" "$br" "$dirty" "$pr"
  done
}

cmd_path() { [[ -n "${1:-}" ]] || die "usage: wt path <name>"; echo "$WT_DIR/$1"; }

cmd_rm() {
  local name="${1:-}"; [[ -n "$name" ]] || die "usage: wt rm <name>"
  local dir="$WT_DIR/$name"
  [[ -d "$dir" ]] || die "no worktree at $dir"
  [[ -z "$(git -C "$dir" status --porcelain)" ]] || die "$name has uncommitted changes; commit or discard first"
  git -C "$ROOT" worktree remove "$dir"
  git -C "$ROOT" branch -d "agent/$name" 2>/dev/null \
    && echo "removed $name and merged branch agent/$name" \
    || echo "removed $name (branch agent/$name kept: not merged)"
}

cmd_clean() {
  git -C "$ROOT" fetch --quiet --prune origin 2>/dev/null || true
  local base="origin/$BASE_DEFAULT"
  git -C "$ROOT" rev-parse --verify --quiet "$base" >/dev/null || base="$BASE_DEFAULT"
  local merged; merged="$(git -C "$ROOT" branch --merged "$base" --format='%(refname:short)' | grep '^agent/' || true)"
  # squash-merged PRs don't show as merged in git; ask GitHub when available
  if command -v gh >/dev/null 2>&1; then
    merged+=$'\n'"$(gh pr list --state merged --limit 200 --json headRefName -q '.[].headRefName' 2>/dev/null | grep '^agent/' || true)"
  fi
  local n=0
  for br in $(echo "$merged" | sort -u); do
    local name="${br#agent/}" dir="$WT_DIR/${br#agent/}"
    # A branch with no commits yet is trivially "merged" — it's a fresh worktree, keep it.
    local entries; entries="$(git -C "$ROOT" reflog show --format=%H "$br" -- 2>/dev/null | wc -l)"
    (( entries <= 1 )) && continue
    if [[ -d "$dir" && -z "$(git -C "$dir" status --porcelain)" ]]; then
      git -C "$ROOT" worktree remove "$dir" && git -C "$ROOT" branch -D "$br" >/dev/null 2>&1 || true
      echo "cleaned $name"; n=$((n + 1))
    fi
  done
  echo "$n worktree(s) cleaned"
}

case "${1:-}" in
  new)   shift; cmd_new "$@" ;;
  ls)    cmd_ls ;;
  path)  shift; cmd_path "$@" ;;
  rm)    shift; cmd_rm "$@" ;;
  clean) cmd_clean ;;
  *) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
