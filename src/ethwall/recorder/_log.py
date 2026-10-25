"""Append-only JSONL recorder (R-REC-01..03, R-REC-13, R-REC-15).

Design reuse (not code): the append-only, hash-chained log closed by a checkpoint that declares
its record count comes from the Agent Worlds L3 harness, `../harness/evidence/log.py`
(`EvidenceLog.append/close`, parent repo commit d210956). Differences: HMAC-SHA256 instead of
Ed25519, ethwall's contract event shape, and a partition index outside the run folder.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ethwall.contract import (
    GENESIS,
    Event,
    EventKind,
    Label,
    Mode,
    TerminalState,
    canonical_json,
    event_hash,
    event_to_dict,
    sha256_hex,
    sign,
    to_jsonable,
    validate_payload,
)

EVENTS_FILE = "events.jsonl"
INDEX_DIR = "_index"  # one file per run: runs/<partition>/_index/<run_id>.json
INDEX_FILE = INDEX_DIR  # kept for import compatibility
KEY_ENV = "ETHWALL_RECORDER_KEY"
MOCK_KEY = b"ethwall-mock-key"
MOCK_KEY_ID = "mock-fixed"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _append_line(path: Path, line: str) -> None:
    """Append one line and make it durable before returning (flush + fsync)."""
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


class JsonlRecorder:
    """contract.Recorder writing `run_dir/events.jsonl`, one canonical JSON line per event.

    Hashing and signing match `ethwall.testing.MemoryRecorder` exactly: `hash` is
    `event_hash(event.body())` (SHA-256 of the canonical event without hash/sig), the chain starts
    at `GENESIS`, and `sig = sign(key, hash)` (HMAC-SHA256). `close()` appends the checkpoint
    triple to `<run_dir.parent>/_index/<run_id>.json` (the partition index). One new file per
    run, never an append, so committed evidence under `runs/` is only ever added (runs/** is
    immutable in .agent/protected.txt).
    """

    def __init__(
        self,
        run_dir: str | os.PathLike[str],
        run_id: str,
        key: bytes,
        key_id: str,
        *,
        clock: Callable[[], str] | None = None,
    ):
        if not key:
            raise ValueError("recorder key must be non-empty")
        self.run_dir = Path(run_dir)
        self.run_id = run_id
        self.key_id = key_id
        self._key = key
        self._clock = clock or utc_now
        self._events: list[Event] = []
        self._closed = False
        self.path = self.run_dir / EVENTS_FILE
        self.index_path = self.run_dir.parent / INDEX_DIR / f"{run_id}.json"
        # 'x': never append onto an existing log (R-REC-15).
        with open(self.path, "x", encoding="utf-8", newline="\n"):
            pass

    def append(
        self,
        kind: EventKind,
        actor: str,
        payload: Mapping[str, Any],
        *,
        context_id: str | None = None,
        labels: frozenset[Label] | tuple[str, ...] = (),
    ) -> Event:
        if self._closed:
            raise RuntimeError("recorder is closed")
        kind = EventKind(kind)
        validate_payload(kind, payload)  # before any state changes (R-REC-02)
        prev = self._events[-1].hash if self._events else GENESIS
        lab = tuple(sorted(x.value if isinstance(x, Label) else str(x) for x in labels))
        data = to_jsonable(dict(payload))
        body = {
            "seq": len(self._events),
            "run_id": self.run_id,
            "kind": kind.value,
            "actor": actor,
            "context_id": context_id,
            "labels": list(lab),
            "payload": data,
            "ts": self._clock(),
            "prev_hash": prev,
        }
        digest = event_hash(body)
        event = Event(
            seq=body["seq"],
            run_id=self.run_id,
            kind=kind,
            actor=actor,
            context_id=context_id,
            labels=lab,
            payload=data,
            ts=body["ts"],
            prev_hash=prev,
            hash=digest,
            sig=sign(self._key, digest),
        )
        _append_line(self.path, canonical_json(event_to_dict(event)))
        self._events.append(event)
        return event

    def close(self, terminal_state: TerminalState, counts: Mapping[str, int]) -> Event:
        if self._closed:
            raise RuntimeError("recorder is closed")
        state = TerminalState(terminal_state)
        self.append(
            EventKind.RUN_END, "RECORDER", {"terminal_state": state.value, **counts}
        )
        triple = {
            "run_id": self.run_id,
            "n_events": len(self._events) + 1,  # includes the checkpoint itself
            "head_hash": self._events[-1].hash,
        }
        checkpoint = self.append(EventKind.CHECKPOINT, "RECORDER", triple)
        self._closed = True
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.index_path, "x", encoding="utf-8") as f:  # never overwrite
            f.write(canonical_json(triple) + "\n")
        return checkpoint

    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)


def new_run_dir(root: str | os.PathLike[str], partition: str, run_id: str) -> Path:
    """Create `root/partition/run_id`; refuse an existing directory (R-REC-15)."""
    for part in (partition, run_id):
        if not part or part in (".", "..") or any(c in part for c in "/\\"):
            raise ValueError(f"invalid path component {part!r}")
    d = Path(root) / partition / run_id
    d.parent.mkdir(parents=True, exist_ok=True)
    d.mkdir()  # FileExistsError if it already exists
    return d


def recorder_key(mode: Mode | str) -> tuple[bytes, str]:
    """(key, key_id) for a run (R-REC-13).

    live: `ETHWALL_RECORDER_KEY` is required; key_id = first 12 hex of SHA-256(key).
    mock/fixture: the fixed public key `ethwall-mock-key` with id `mock-fixed` (no integrity
    claim beyond accidental-damage detection: the key is in the source).
    """
    m = Mode(mode)
    if m != Mode.LIVE:
        return MOCK_KEY, MOCK_KEY_ID
    value = os.environ.get(KEY_ENV, "")
    if not value:
        raise RuntimeError(f"live mode requires {KEY_ENV} to be set")
    key = value.encode("utf-8")
    return key, sha256_hex(key)[:12]
