#!/usr/bin/env bash
# paid-pipeline.sh — the owner-approved paid pipeline (sol.md v5.3 §9–§11, GOAL.md budget).
# The OWNER starts this from the repo root so that .env loads in the owner's own shell:
#
#   ./scripts/paid-pipeline.sh all              # every stage in order
#   ./scripts/paid-pipeline.sh <stage> [...]    # resume from one stage
#
# Stages (each refuses to overwrite existing outputs, so re-running resumes safely):
#   doctor           settings present? (prints set/missing, never values)
#   auditor-dev      auditor on the 6 development fixtures x 4 views (<=24 calls)
#   auditor-freeze   record the auditor prompt hash, model id and settings (frozen, committed)
#   auditor-heldout  auditor on the 12 held-out fixtures x 4 views (48 calls). Predictions are
#                    SEALED: per-item results stay unseen until Sahil labels (ethwall label).
#   smoke            live smoke (seeds 100-102 x 2) + instructed controls (seeds 150-151 x 2)
#   project          cost projection from smoke -> main size (20 or the 6-seed fallback),
#                    decided by rule BEFORE any main outcome, written and committed
#   freeze           commit evidence so far, create + commit the protocol freeze
#   main             main runs on the chosen seeds, both conditions, 4 parallel workers
#                    (the spend ledger lock serializes spend across processes)
#   audit-main       export the main trajectories (leak triage) + auditor on all 4 views
#   report           tables/figures for smoke, control, main; audit metrics (provisional vs
#                    construction labels for fixtures; human labels once they exist)
#
# Every paid command carries --confirm-spend: running this script IS the owner's go. Caps
# (episode, batch, $50 total) are enforced by the ledger before every call; a SPEND_LIMIT
# stops a batch. Nothing here edits or deletes recorded evidence.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [ ! -f .env ]; then echo "paid-pipeline: .env not found in $(pwd)" >&2; exit 2; fi
set -a
# shellcheck disable=SC1091
. ./.env
set +a

E="uv run --frozen ethwall"
FREEZE_ID="${FREEZE_ID:-main-v1}"
step() { printf '\n== %s (%s)\n' "$*" "$(date '+%H:%M:%S')"; }
commit() { git add -A "$@" && { git diff --cached --quiet || git commit -qm "$MSG"; }; }

stage_doctor() {
  step "doctor"
  $E doctor
}

stage_auditor_dev() {
  step "auditor on development fixtures (<=24 calls)"
  [ -d review/fixture-dev ] || $E export-review --fixtures dev --out review/fixture-dev
  [ -f audit/fixture-dev.jsonl ] && { echo "audit/fixture-dev.jsonl exists; skipping"; return; }
  $E audit run --mode live --confirm-spend --exports review/fixture-dev \
    --out audit/fixture-dev.jsonl
  $E audit report --predictions audit/fixture-dev.jsonl --references fixtures:dev \
    --exports review/fixture-dev --source fixture --out analysis/audit-fixture-dev
}

stage_auditor_freeze() {
  step "freeze the auditor (prompt hash, model id, settings)"
  local f=protocol/frozen/auditor-v1/auditor.json
  [ -f "$f" ] && { echo "$f exists; skipping"; return; }
  mkdir -p "$(dirname "$f")"
  uv run --frozen python - "$f" <<'PY'
import json, os, sys
from datetime import datetime, timezone
from ethwall.audit import AuditorSettings, load_auditor_prompt
_, sha = load_auditor_prompt()
rec = {
    "frozen_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "auditor_prompt_sha256": sha,
    "auditor_model_id": os.environ["AUDITOR_MODEL_ID"].strip(),
    "settings": AuditorSettings().to_dict(),
    "frozen_after": "6 development fixtures x 4 views (audit/fixture-dev.jsonl)",
}
with open(sys.argv[1], "x", encoding="utf-8") as fh:
    json.dump(rec, fh, indent=1, sort_keys=True)
print(json.dumps(rec, indent=1))
PY
  MSG="Freeze auditor after development fixtures (sol.md §10)" \
    commit "$f" review/fixture-dev audit/fixture-dev.jsonl analysis/audit-fixture-dev
}

stage_auditor_heldout() {
  step "auditor on held-out fixtures (48 calls; predictions sealed until human labels)"
  [ -f protocol/frozen/auditor-v1/auditor.json ] || { echo "freeze the auditor first" >&2; exit 2; }
  [ -d review/fixture-heldout ] || $E export-review --fixtures heldout --out review/fixture-heldout
  [ -f audit/fixture-heldout.jsonl ] && { echo "audit/fixture-heldout.jsonl exists; skipping"; return; }
  $E audit run --mode live --confirm-spend --exports review/fixture-heldout \
    --out audit/fixture-heldout.jsonl
  echo "sealed: audit/fixture-heldout.jsonl (do not open before labelling)"
}

stage_smoke() {
  step "live smoke (seeds 100-102, both conditions)"
  [ -d runs/smoke ] && { echo "runs/smoke exists; skipping smoke"; } || \
    $E run --mode live --confirm-spend --partition smoke --seeds 100-102
  step "live instructed controls (seeds 150-151, both conditions)"
  [ -d runs/control ] && { echo "runs/control exists; skipping controls"; } || \
    $E run --mode live --confirm-spend --partition control --seeds 150-151 --control
  $E verify --partition smoke
  $E verify --partition control
}

stage_project() {
  step "cost projection -> main size (rule decided before any main outcome)"
  local f=protocol/decisions/main-size.json
  [ -f "$f" ] && { cat "$f"; return; }
  mkdir -p "$(dirname "$f")"
  uv run --frozen python - "$f" <<'PY'
import json, sys
from pathlib import Path
from statistics import mean
eps = []
for part in ("smoke", "control"):
    p = Path("spend") / part / "_spend.json"
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        eps += [e["spent_micro"] / 1e6 for e in data.get("episodes", {}).values() if e.get("calls")]
if not eps:
    sys.exit("no smoke/control spend recorded")
per_ep = mean(eps)
worst = max(eps)
need20 = per_ep * 20
# Rule (sol.md §9, GOAL.md): 20 main episodes if the projection fits the $25 main batch with a
# 20% margin; else the 6-seed fallback (12 episodes) if that fits; else fixture-only.
if need20 * 1.2 <= 25:
    seeds, size = "200-209", 20
elif per_ep * 12 * 1.2 <= 25:
    seeds, size = "200-205", 12
else:
    seeds, size = "", 0
rec = {"smoke_control_episodes": len(eps), "mean_usd_per_episode": round(per_ep, 4),
       "max_usd_per_episode": round(worst, 4), "projected_usd_20": round(need20, 2),
       "main_seeds": seeds, "main_episodes": size,
       "rule": "20 if 1.2x projection <= $25; else 12 (seeds 200-205) if it fits; else none"}
with open(sys.argv[1], "x", encoding="utf-8") as fh:
    json.dump(rec, fh, indent=1, sort_keys=True)
print(json.dumps(rec, indent=1))
PY
}

stage_freeze() {
  step "commit evidence so far + protocol freeze ($FREEZE_ID)"
  MSG="Record smoke/control runs, audit dev results and main-size decision (before freeze)" \
    commit runs protocol review audit analysis .gitignore scripts
  if [ ! -d "protocol/frozen/$FREEZE_ID" ]; then
    $E freeze create --id "$FREEZE_ID"
    MSG="Protocol freeze $FREEZE_ID (sol.md §10)" commit "protocol/frozen/$FREEZE_ID"
  fi
  $E freeze validate --id "$FREEZE_ID"
}

stage_main() {
  local seeds
  seeds="$(uv run --frozen python -c "import json;print(json.load(open('protocol/decisions/main-size.json'))['main_seeds'])")"
  [ -n "$seeds" ] || { echo "projection says main does not fit: fixture-only" >&2; return; }
  step "main runs, seeds $seeds, both conditions, 4 parallel workers"
  local lo hi mid
  lo="${seeds%-*}"; hi="${seeds#*-}"; mid=$(( (lo + hi) / 2 ))
  local pids=() rc=0 logs
  logs="$(mktemp -d)"  # outside the repo: the freeze gate needs a clean tree
  for cond in baseline guarded; do
    for range in "$lo-$mid" "$((mid + 1))-$hi"; do
      $E run --mode live --confirm-spend --partition main --freeze "$FREEZE_ID" \
        --condition "$cond" --seeds "$range" > "$logs/main-$cond-$range.log" 2>&1 &
      pids+=($!)
    done
  done
  for p in "${pids[@]}"; do wait "$p" || rc=1; done
  cat "$logs"/main-*.log
  rm -rf "$logs"
  $E verify --partition main || rc=1
  return "$rc"
}

stage_audit_main() {
  step "export main trajectories (leak triage) + auditor on 4 views"
  if [ ! -d review/main ]; then
    if ! $E export-review --partition main --out review/main; then
      echo "export-review stopped: review the REVIEW/LEAK lines above, then re-run this stage" \
           "with ACK_REVIEW=1 if every review hit is benign (blocking leaks are never ackable)." >&2
      [ "${ACK_REVIEW:-0}" = 1 ] || exit 1
      rm -rf review/main.staging review/main.private
      $E export-review --partition main --out review/main --ack-review
    fi
  fi
  [ -f audit/main.jsonl ] && { echo "audit/main.jsonl exists; skipping"; return; }
  $E audit run --mode live --confirm-spend --exports review/main --out audit/main.jsonl
  echo "sealed: audit/main.jsonl (do not open before labelling)"
}

stage_report() {
  step "analysis"
  for p in smoke control main; do
    [ -d "runs/$p" ] && $E analyze --partition "$p" --out "analysis/$p"
  done
  if [ -f audit/fixture-heldout.jsonl ]; then
    $E audit report --predictions audit/fixture-heldout.jsonl --references fixtures:heldout \
      --exports review/fixture-heldout --source fixture \
      --out analysis/audit-fixture-heldout-provisional
    echo "PROVISIONAL (construction labels); final metrics use Sahil's blind labels"
  fi
  MSG="Record paid-run evidence and analysis" commit runs review audit analysis protocol
}

STAGES=(doctor auditor-dev auditor-freeze auditor-heldout smoke project freeze main audit-main report)
run_stage() { "stage_${1//-/_}"; }
if [ "${1:-}" = all ]; then
  for s in "${STAGES[@]}"; do run_stage "$s"; done
elif [ $# -gt 0 ]; then
  for s in "$@"; do run_stage "$s"; done
else
  sed -n '2,32p' "$0"; exit 2
fi
printf '\npaid-pipeline: done (%s)\n' "$(date '+%H:%M:%S')"
