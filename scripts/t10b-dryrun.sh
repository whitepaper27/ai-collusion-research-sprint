#!/usr/bin/env bash
# t10b-dryrun.sh — TASKS.md T10b, the owner's gate before any paid-run planning. All mock; no
# network calls, no spend. Runs in a throwaway clone of the committed tree so nothing here
# creates real evidence or a real freeze.
#
#   1. mock smoke:    seeds 100-102 x {baseline, guarded}   (honest and leaky policies)
#   2. mock controls: seeds 150-151 x {baseline, guarded}   (instructed_pair, --control)
#   3. verify every run, analyze each partition (tables, figures 2-3, fact sheet)
#   4. audit pipeline on held-out fixtures (export-review -> mock auditor -> Figure 1)
#   5. freeze dry run: create, validate (valid), tamper a prompt (invalid), and confirm a live
#      main run refuses (exit 2) without --confirm-spend
#
# Exits non-zero on the first failure.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
step() { printf '\n== %s\n' "$*"; }

step "clone committed tree"
git clone -q "$ROOT" "$WORK/repo"
cd "$WORK/repo"
git config user.email t10b@local
git config user.name t10b
uv sync -q --frozen
E="uv run --frozen ethwall"

step "1. mock smoke (seeds 100-102, both conditions)"
$E run --seeds 100-102 --mode mock --policy honest --partition mock-smoke-honest
$E run --seeds 100-102 --mode mock --policy leaky --partition mock-smoke-leaky

step "2. mock instructed controls (seeds 150-151)"
$E run --seeds 150-151 --mode mock --policy instructed_pair --control --partition mock-control

step "3. verify + analyze"
for p in mock-smoke-honest mock-smoke-leaky mock-control; do
  $E verify --partition "$p"
  $E analyze --partition "$p" --out "analysis/$p"
done
test -f analysis/mock-smoke-leaky/figures/figure2_live.png

step "4. audit pipeline on held-out fixtures"
$E export-review --fixtures heldout --out review/fixture-heldout
$E audit run --exports review/fixture-heldout --out audit/heldout.jsonl
$E audit report --predictions audit/heldout.jsonl --references fixtures:heldout \
  --exports review/fixture-heldout --out analysis/audit-heldout
test -f analysis/audit-heldout/figures/figure1_audit.png

step "5. freeze dry run"
rm -rf runs analysis review audit spend   # keep the tree clean for the freeze
$E freeze create --id t10b-dryrun
git add protocol/frozen && git commit -qm "t10b dry-run freeze (throwaway clone)"
$E freeze validate --id t10b-dryrun
# Commit the tamper so the tree is clean: validation must fail on the prompts hash itself,
# not merely on a dirty tree.
echo "# tamper" >> protocol/draft/prompts/pressure.md
git commit -qam "t10b: tamper one prompt (throwaway clone)"
set +e
out="$($E freeze validate --id t10b-dryrun 2>&1)"
rc=$?
set -e
echo "$out"
if [ "$rc" = 0 ] || ! grep -q "hashes.prompts" <<<"$out" || grep -q "git_dirty" <<<"$out"; then
  echo "t10b: tampered prompt was not caught by the prompts hash" >&2; exit 1
fi
git revert --no-edit HEAD >/dev/null
$E freeze validate --id t10b-dryrun
# Live refusal, with provider keys stripped so no future reordering could ever spend.
set +e
env -u ANTHROPIC_API_KEY -u ETHWALL_RECORDER_KEY \
  $E run --seeds 200 --mode live --partition main --freeze t10b-dryrun
rc=$?
set -e
if [ "$rc" != 2 ]; then
  echo "t10b: live main run without --confirm-spend returned $rc, expected 2" >&2; exit 1
fi

printf '\nt10b: PASS (mock smoke, controls, verify, analyze, audit, freeze dry run)\n'
