# Spec: Recorder + freeze + Docker clean room

Module: src/ethwall/recorder/ (R-REC), src/ethwall/freeze/ (R-FRZ), Dockerfile (R-DKR)
sol.md: §9 (budget), §10 (protocol), §11 (recorder, sandbox)
Workstream: W4 · Wave 2 · Owns also `Dockerfile`, `.dockerignore`, `scripts/docker-verify.sh`

Design reuse (not code): hash chain + checkpoint + independent verifier from
`../harness/evidence/log.py` and `../harness/audit.py` (parent repo commit `d210956`).

## Recorder

- **R-REC-01** `JsonlRecorder(run_dir, run_id, key, key_id)` appends one JSON line per event and
  flushes each append. Event fields: `seq, run_id, kind, actor, context_id, labels, payload, ts,
  prev_hash, hash, sig`; `hash` = SHA-256 of the canonical event without `hash`/`sig`; chain
  starts at `GENESIS` ("0"×64); `sig` = HMAC-SHA256(key, hash).
- **R-REC-02** Every append validates the payload keys required for its kind; a closed recorder
  refuses appends.
- **R-REC-03** `close(status, ...)` writes `RUN_END` then `CHECKPOINT{run_id, n_events,
  head_hash}` and appends the same triple to `runs/<partition>/_index.jsonl`.
- **R-REC-04** `verify` reports `ALTERED` when any byte of a recorded event changes.
- **R-REC-05** `BAD_SIG` when verified with the wrong key.
- **R-REC-06** `REPLAY` for a duplicated event or an event with a foreign `run_id`.
- **R-REC-07** `REORDERED` when two events swap.
- **R-REC-08** `OMITTED` when an event is removed from the middle (sequence gap).
- **R-REC-09** `TRUNCATED` when the tail or the checkpoint is missing or the checkpoint
  count/head doesn't match.
- **R-REC-10** `INDEX_MISMATCH` when the run disagrees with its partition index entry.
- **R-REC-11** Reconciliation faults: every submission (message, retrieval, order, publication,
  duty report) has exactly one outcome; every delivered item has an observation; decisions ≤ 30,
  tool actions ≤ 90, transport attempts ≤ 2 × decisions.
- **R-REC-12** Documented limitation, pinned by a test: events suppressed before signing, with
  the index written from the same suppressed state, verify clean. The module docstring says so.
- **R-REC-13** Live mode requires `ETHWALL_RECORDER_KEY` (key id = first 12 hex of its
  SHA-256); mock/fixture use a fixed key with id `mock-fixed`. Docs state that anyone holding the
  HMAC key can forge records.
- **R-REC-14** `build_manifest(...)` fills git commit and dirty flag, model id, condition, seed,
  partition, mode, caps, and SHA-256 of prompts, world generator, enforcement policies, tool
  schemas and analysis config; plus freeze id when given.
- **R-REC-15** `new_run_dir(root, partition, run_id)` refuses to overwrite an existing directory.

## Freeze

- **R-FRZ-01** `create_freeze(freeze_id)` writes `protocol/frozen/<id>/freeze.json` with the git
  commit and the hashes listed in R-REC-14 plus seeds, caps and model ids.
- **R-FRZ-02** `validate_freeze(freeze_id)` recomputes every hash; any mismatch or a dirty tree
  fails with the differing item named. Main-partition runs require a valid freeze.
- **R-FRZ-03** Changing one prompt byte after the freeze makes validation fail (test).

## Docker clean room

- **R-DKR-01** `Dockerfile` installs the locked environment with `uv sync --frozen`;
  `scripts/docker-verify.sh` runs the test suite and a mock episode in both conditions inside
  the container with `--network none` and exits non-zero on any failure. Covered by an `e2e`
  test that runs the script.
