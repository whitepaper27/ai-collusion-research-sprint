"""Append-only, hash-chained, HMAC-signed run recorder and its verifier
(docs/specs/recorder-freeze.md R-REC-01..15; sol.md §10-11; ARCHITECTURE.md §7).

Exports: `JsonlRecorder(run_dir, run_id, key, key_id)` (implements contract.Recorder),
`verify_run(run_dir, key, index=None)`, `verify_events(events, key, index_entry=None)`,
`build_manifest(config, *, world, prompts_sha256, run_id, freeze_id=None, repo_root=None)`,
`new_run_dir(root, partition, run_id)` and `recorder_key(mode)`. The verifier's fault codes and
how each is classified are documented in `ethwall.recorder._verify`.

Trust limits (read before citing a clean verification):

- Integrity is relative to a trusted recorder and key. The signature is a symmetric HMAC, so
  anyone holding the HMAC key can forge records: rewrite any event, re-sign the chain and write
  a matching checkpoint and index entry. In mock/fixture mode the key is public (`mock-fixed`),
  so a clean verification there only rules out accidental damage.
- Events suppressed before signing are undetectable. If the recorder (or the code calling it)
  never appends an event, and also never appends the events that reference it, and the
  partition index is written from that same suppressed state, the run verifies clean. The
  verifier detects only changes made after signing. This limitation is pinned by
  `test_suppressed_before_signing_verifies_clean` (R-REC-12).
- The partition index lives next to the runs, so it only detects a missing tail when it was
  written by the trusted recorder and not rewritten afterwards.

Design reuse (not code): hash chain + checkpoint + independent verifier from the Agent Worlds
L3 harness (`../harness/evidence/log.py`, `../harness/audit.py`, parent repo commit d210956).
"""

from ethwall.recorder._log import (
    EVENTS_FILE,
    INDEX_DIR,
    INDEX_FILE,
    JsonlRecorder,
    new_run_dir,
    recorder_key,
)
from ethwall.recorder._manifest import (
    build_manifest,
    git_state,
    hash_dirs,
    prompts_dir_sha256,
    source_hashes,
)
from ethwall.recorder._verify import FAULT_ORDER, verify_events, verify_run

__all__ = [
    "EVENTS_FILE",
    "FAULT_ORDER",
    "INDEX_DIR",
    "INDEX_FILE",
    "JsonlRecorder",
    "build_manifest",
    "git_state",
    "hash_dirs",
    "new_run_dir",
    "prompts_dir_sha256",
    "recorder_key",
    "source_hashes",
    "verify_events",
    "verify_run",
]
