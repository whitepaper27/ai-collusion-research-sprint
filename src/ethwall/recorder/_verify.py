"""Independent verifier: integrity faults and reconciliation faults (R-REC-04..11).

Design reuse (not code): an offline verifier that recomputes every record hash, checks each
signature, walks the chain and checks completeness against the checkpoint's declared count, as
in the Agent Worlds L3 harness `../harness/audit.py` (`_verify_bundle`) and
`../harness/evidence/log.py` (`verify_bundle`), parent repo commit d210956.

Classification (each tamper yields its own code; see tests/recorder/test_w4_recorder.py):

1. ALTERED: a line that doesn't parse into an Event, a hash that doesn't match the event body, a
   chain link (`prev_hash`) that doesn't match its predecessor, or a signature that fails while
   other events of the same stream verify under the same key (the sig bytes were changed).
2. BAD_SIG: a signature that doesn't verify under the given key. When *no* event verifies, the
   key is wrong and BAD_SIG is the only signature fault.
3. REPLAY: an event whose run_id isn't the run's (majority run_id), or whose hash or seq was
   already seen. Replayed events are set aside before the order checks below.
4. REORDERED: sequence numbers decrease somewhere in file order.
5. OMITTED: a sequence number in 0..max is missing.
6. TRUNCATED: the highest-seq event isn't a CHECKPOINT; or the checkpoint's `n_events` is not
   its own seq + 1, its run_id differs, its `head_hash` isn't the hash of the event before it, or
   that event isn't RUN_END.
7. INDEX_MISMATCH: the partition index entry (run_id, n_events, head_hash) differs from the
   checkpoint, or there is no entry for the run.

Reconciliation runs on the de-replayed, seq-ordered stream:

- UNRECONCILED_MESSAGE: a MESSAGE_SUBMITTED without exactly one later outcome
  (MESSAGE_DELIVERED, MESSAGE_BLOCKED, MESSAGE_UNDELIVERED), an outcome for an unknown message,
  a duplicate message id, or a delivered/undelivered recipient that differs from the submission.
- UNRECONCILED_SUBMISSION: a retrieval, order or article submission without exactly one later
  outcome; an outcome without its submission; a duplicate submission id (duty reports included,
  which are otherwise resolved by their own submission event).
- UNOBSERVED_DELIVERY: a MESSAGE_DELIVERED without a later OBSERVATION by the recipient with
  `ref_id == message_id`; a PACKET_DELIVERED without a later OBSERVATION by that role whose
  channel is "packet" or whose text hashes to `packet_sha256`. Tool results are not checked:
  the last slot's results are never delivered.
- DECISION_CAP: model decisions (distinct MODEL_CALL decision_index; enforcement DECISION events
  are not model decisions) or RUN_END `decisions` exceed DECISIONS_PER_EPISODE.
- ACTION_CAP: executed tool actions (TOOL_PROPOSED whose call_id the services acted on: a
  DECISION `ref_id` or a service event `call_id`) or RUN_END `actions` exceed
  MAX_ACTIONS_PER_EPISODE.
- TRANSPORT_CAP: a decision with more than 1 + MAX_TRANSPORT_RETRIES attempts (distinct
  (decision_index, attempt) over MODEL_CALL and TRANSPORT_FAILURE), or total attempts
  (observed or RUN_END) above (1 + MAX_TRANSPORT_RETRIES) x decisions.
"""

from __future__ import annotations

import hmac
import json
import os
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

from ethwall.contract import (
    DECISIONS_PER_EPISODE,
    GENESIS,
    MAX_ACTIONS_PER_EPISODE,
    MAX_TRANSPORT_RETRIES,
    Event,
    EventKind,
    event_from_dict,
    event_hash,
    sha256_hex,
    sign,
)
from ethwall.recorder._log import EVENTS_FILE, INDEX_FILE

ALTERED = "ALTERED"
BAD_SIG = "BAD_SIG"
REPLAY = "REPLAY"
REORDERED = "REORDERED"
OMITTED = "OMITTED"
TRUNCATED = "TRUNCATED"
INDEX_MISMATCH = "INDEX_MISMATCH"
UNRECONCILED_MESSAGE = "UNRECONCILED_MESSAGE"
UNRECONCILED_SUBMISSION = "UNRECONCILED_SUBMISSION"
UNOBSERVED_DELIVERY = "UNOBSERVED_DELIVERY"
DECISION_CAP = "DECISION_CAP"
ACTION_CAP = "ACTION_CAP"
TRANSPORT_CAP = "TRANSPORT_CAP"

FAULT_ORDER: tuple[str, ...] = (
    ALTERED,
    BAD_SIG,
    REPLAY,
    REORDERED,
    OMITTED,
    TRUNCATED,
    INDEX_MISMATCH,
    UNRECONCILED_MESSAGE,
    UNRECONCILED_SUBMISSION,
    UNOBSERVED_DELIVERY,
    DECISION_CAP,
    ACTION_CAP,
    TRANSPORT_CAP,
)

_TRIPLE = ("run_id", "n_events", "head_hash")

# submission kind -> (id key, outcome kinds)
_MESSAGE_OUTCOMES = frozenset(
    {
        EventKind.MESSAGE_DELIVERED,
        EventKind.MESSAGE_BLOCKED,
        EventKind.MESSAGE_UNDELIVERED,
    }
)
_SUBMISSIONS: Mapping[EventKind, tuple[str, frozenset[EventKind]]] = {
    EventKind.RETRIEVAL_REQUESTED: (
        "request_id",
        frozenset({EventKind.RETRIEVAL_RESOLVED, EventKind.RETRIEVAL_REJECTED}),
    ),
    EventKind.ORDER_SUBMITTED: (
        "order_id",
        frozenset({EventKind.ORDER_FILLED, EventKind.ORDER_REJECTED}),
    ),
    EventKind.ARTICLE_SUBMITTED: (
        "article_id",
        frozenset({EventKind.ARTICLE_ACCEPTED, EventKind.ARTICLE_REJECTED}),
    ),
}
# Service events that prove a proposed call was executed.
_CALL_EVENTS = frozenset(
    {
        EventKind.TOOL_DENIED,
        EventKind.SOURCE_READ,
        EventKind.MESSAGE_SUBMITTED,
        EventKind.RETRIEVAL_REQUESTED,
        EventKind.ORDER_SUBMITTED,
        EventKind.ARTICLE_SUBMITTED,
        EventKind.DUTY_REPORT_SUBMITTED,
    }
)


def _ordered(faults: Iterable[str]) -> tuple[str, ...]:
    found = set(faults)
    return tuple(f for f in FAULT_ORDER if f in found)


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ----------------------------------------------------------------------------- integrity


def _sig_valid(key: bytes, e: Event) -> bool:
    try:
        return hmac.compare_digest(
            sign(key, e.hash).encode("ascii"), str(e.sig).encode("utf-8")
        )
    except (AttributeError, TypeError, UnicodeError):
        return False


def _integrity(
    events: Sequence[Event], key: bytes
) -> tuple[set[str], list[Event], Event | None]:
    """Return (faults, kept events in seq order, checkpoint or None)."""
    faults: set[str] = set()
    if not events:
        return {TRUNCATED}, [], None

    sig_ok = [_sig_valid(key, e) for e in events]
    if not all(sig_ok):
        faults.add(BAD_SIG)
        if any(sig_ok):
            faults.add(ALTERED)
    if any(event_hash(e.body()) != e.hash for e in events):
        faults.add(ALTERED)

    run_id = Counter(e.run_id for e in events).most_common(1)[0][0]
    kept: list[Event] = []
    seen_hash: set[str] = set()
    seen_seq: set[int] = set()
    for e in events:
        if e.run_id != run_id or e.hash in seen_hash or e.seq in seen_seq:
            faults.add(REPLAY)
            continue
        seen_hash.add(e.hash)
        seen_seq.add(e.seq)
        kept.append(e)

    if any(b.seq < a.seq for a, b in pairwise(kept)):
        faults.add(REORDERED)
    kept.sort(key=lambda e: e.seq)
    if kept[0].seq < 0 or set(range(kept[-1].seq + 1)) - seen_seq:
        faults.add(OMITTED)
    by_seq = {e.seq: e for e in kept}
    for e in kept:
        prev = by_seq.get(e.seq - 1)
        expected = GENESIS if e.seq == 0 else (prev.hash if prev else None)
        if expected is not None and e.prev_hash != expected:
            faults.add(ALTERED)

    last = kept[-1]
    checkpoint = last if last.kind == EventKind.CHECKPOINT else None
    if checkpoint is None:
        faults.add(TRUNCATED)
    else:
        p = checkpoint.payload
        head = by_seq.get(checkpoint.seq - 1)
        if _int(p.get("n_events")) != checkpoint.seq + 1 or p.get("run_id") != run_id:
            faults.add(TRUNCATED)
        if head is not None and (
            head.hash != p.get("head_hash") or head.kind != EventKind.RUN_END
        ):
            faults.add(TRUNCATED)
    return faults, kept, checkpoint


def _index_faults(
    checkpoint: Event | None, entries: Sequence[Mapping[str, Any]]
) -> set[str]:
    """INDEX_MISMATCH when no entry exists or any entry differs from the checkpoint triple."""
    if not entries:
        return {INDEX_MISMATCH}
    if checkpoint is None:
        return set()  # already TRUNCATED; the index is what proves the missing tail
    want = tuple(checkpoint.payload.get(k) for k in _TRIPLE)
    if any(tuple(e.get(k) for k in _TRIPLE) != want for e in entries):
        return {INDEX_MISMATCH}
    return set()


# ----------------------------------------------------------------------------- reconciliation


def _reconcile(events: Sequence[Event]) -> set[str]:
    faults: set[str] = set()
    of = [(e, e.payload) for e in events]

    # Messages: exactly one outcome each, after the submission, same recipient.
    msgs: dict[Any, Event] = {}
    for e, p in of:
        if e.kind == EventKind.MESSAGE_SUBMITTED:
            if p.get("message_id") in msgs:
                faults.add(UNRECONCILED_MESSAGE)
            msgs[p.get("message_id")] = e
    msg_outcomes: Counter[Any] = Counter()
    for e, p in of:
        if e.kind not in _MESSAGE_OUTCOMES:
            continue
        mid = p.get("message_id")
        sub = msgs.get(mid)
        if sub is None or e.seq < sub.seq:
            faults.add(UNRECONCILED_MESSAGE)
            continue
        if "recipient" in p and p["recipient"] != sub.payload.get("recipient"):
            faults.add(UNRECONCILED_MESSAGE)
        msg_outcomes[mid] += 1
    if any(msg_outcomes[mid] != 1 for mid in msgs):
        faults.add(UNRECONCILED_MESSAGE)

    # Retrieval, order, article: exactly one outcome each; duty reports: unique ids.
    for kind, (key, outcome_kinds) in _SUBMISSIONS.items():
        subs: dict[Any, int] = {}
        for e, p in of:
            if e.kind == kind:
                if p.get(key) in subs:
                    faults.add(UNRECONCILED_SUBMISSION)
                subs[p.get(key)] = e.seq
        counts: Counter[Any] = Counter()
        for e, p in of:
            if e.kind in outcome_kinds:
                sid = p.get(key)
                if sid not in subs or e.seq < subs[sid]:
                    faults.add(UNRECONCILED_SUBMISSION)
                    continue
                counts[sid] += 1
        if any(counts[sid] != 1 for sid in subs):
            faults.add(UNRECONCILED_SUBMISSION)
    reports = Counter(
        p.get("report_id") for e, p in of if e.kind == EventKind.DUTY_REPORT_SUBMITTED
    )
    if any(n > 1 for n in reports.values()):
        faults.add(UNRECONCILED_SUBMISSION)

    # Every delivered message and packet is observed by its recipient afterwards.
    observations = [(e.seq, p) for e, p in of if e.kind == EventKind.OBSERVATION]
    for e, p in of:
        if e.kind == EventKind.MESSAGE_DELIVERED:
            ok = any(
                s > e.seq
                and o.get("ref_id") == p.get("message_id")
                and o.get("role") == p.get("recipient")
                for s, o in observations
            )
        elif e.kind == EventKind.PACKET_DELIVERED:
            ok = any(
                s > e.seq
                and o.get("role") == p.get("role")
                and (
                    o.get("channel") == "packet"
                    or sha256_hex(str(o.get("text", ""))) == p.get("packet_sha256")
                )
                for s, o in observations
            )
        else:
            continue
        if not ok:
            faults.add(UNOBSERVED_DELIVERY)

    # Caps.
    run_end = next((p for e, p in reversed(of) if e.kind == EventKind.RUN_END), {})
    model_decisions = {
        p.get("decision_index") for e, p in of if e.kind == EventKind.MODEL_CALL
    }
    claimed_decisions = _int(run_end.get("decisions"))
    if max(len(model_decisions), claimed_decisions) > DECISIONS_PER_EPISODE:
        faults.add(DECISION_CAP)

    acted_on = {p.get("ref_id") for e, p in of if e.kind == EventKind.DECISION}
    acted_on |= {p.get("call_id") for e, p in of if e.kind in _CALL_EVENTS}
    executed = {
        p.get("call_id")
        for e, p in of
        if e.kind == EventKind.TOOL_PROPOSED and p.get("call_id") in acted_on
    }
    if max(len(executed), _int(run_end.get("actions"))) > MAX_ACTIONS_PER_EPISODE:
        faults.add(ACTION_CAP)

    per_decision = 1 + MAX_TRANSPORT_RETRIES
    attempts = {
        (p.get("decision_index"), p.get("attempt"))
        for e, p in of
        if e.kind in (EventKind.MODEL_CALL, EventKind.TRANSPORT_FAILURE)
    }
    tried = Counter(d for d, _ in attempts)
    basis = max(len(tried), claimed_decisions)
    total = max(len(attempts), _int(run_end.get("transport_attempts")))
    if any(n > per_decision for n in tried.values()) or total > per_decision * basis:
        faults.add(TRANSPORT_CAP)
    return faults


# ----------------------------------------------------------------------------- public API


def verify_events(
    events: Sequence[Event],
    key: bytes,
    index_entry: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """Fault codes for one run's events, in FAULT_ORDER; empty tuple = clean.

    `index_entry` is the run's partition-index triple {run_id, n_events, head_hash}; when given
    it must match the checkpoint (else INDEX_MISMATCH). None skips the index check.
    """
    return _verify(events, key, None if index_entry is None else [index_entry])


def _verify(
    events: Sequence[Event],
    key: bytes,
    index_entries: Sequence[Mapping[str, Any]] | None,
    parse_failed: bool = False,
) -> tuple[str, ...]:
    faults, kept, checkpoint = _integrity(events, key)
    if parse_failed:
        faults.add(ALTERED)
    if index_entries is not None:
        faults |= _index_faults(checkpoint, index_entries)
    faults |= _reconcile(kept)
    return _ordered(faults)


def _read_events(path: Path) -> tuple[list[Event], bool]:
    events: list[Event] = []
    failed = False
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [], True
    for line in text.splitlines():
        if not line.strip():
            failed = True
            continue
        try:
            d = json.loads(line)
            e = event_from_dict(d)
            strings = (e.run_id, e.actor, e.ts, e.prev_hash, e.hash, e.sig)
            if not all(isinstance(x, str) for x in strings) or not isinstance(
                e.payload, Mapping
            ):
                raise ValueError("bad field types")
            e.body()
        except (ValueError, KeyError, TypeError, AttributeError):
            failed = True
            continue
        events.append(e)
    return events, failed


def _read_index(path: Path, run_id: str) -> list[Mapping[str, Any]]:
    entries: list[Mapping[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return entries
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("run_id") == run_id:
            entries.append(d)
    return entries


def verify_run(
    run_dir: str | os.PathLike[str],
    key: bytes,
    index: str | os.PathLike[str] | Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """Verify `run_dir/events.jsonl` and its partition index; empty tuple = clean.

    `index`: None reads `<run_dir.parent>/_index.jsonl`; a path reads that index file; a mapping
    is used directly as the run's index entry. Never raises on malformed input: unparseable
    lines are ALTERED, a missing log is TRUNCATED.
    """
    run_dir = Path(run_dir)
    events, failed = _read_events(run_dir / EVENTS_FILE)
    if events:
        run_id = Counter(e.run_id for e in events).most_common(1)[0][0]
    else:
        run_id = run_dir.name
    if isinstance(index, Mapping):
        entries: list[Mapping[str, Any]] = [index]
    else:
        index_path = Path(index) if index is not None else run_dir.parent / INDEX_FILE
        entries = _read_index(index_path, run_id)
    return _verify(events, key, entries, parse_failed=failed)
