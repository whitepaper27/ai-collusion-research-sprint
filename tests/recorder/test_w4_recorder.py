"""W4 recorder: JSONL hash chain, checkpoint, partition index and the tamper verifier.

docs/specs/recorder-freeze.md R-REC-01..10, R-REC-12, R-REC-13, R-REC-15. The positive
control drives the real W2 services through a runtime-shaped loop into a `JsonlRecorder` and
must verify clean in both conditions; every tamper test then edits that run's files and checks
for its own fault code (and the absence of the confusable neighbour).
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

import ethwall.recorder as recorder_pkg
from ethwall.contract import (
    GENESIS,
    READS_BY_ROLE,
    ROUNDS,
    SCHEDULE,
    START_CASH,
    Condition,
    ContextState,
    Event,
    EventKind,
    Label,
    Labeled,
    Mode,
    PayloadError,
    Role,
    TerminalState,
    ToolCall,
    World,
    canonical_json,
    event_from_dict,
    event_hash,
    event_to_dict,
    label_of,
    render_documents,
    sha256_hex,
    sign,
)
from ethwall.enforcement import make_policy, propagate
from ethwall.recorder import (
    JsonlRecorder,
    new_run_dir,
    recorder_key,
    verify_events,
    verify_run,
)
from ethwall.services import make_services
from ethwall.testing import (
    TEST_KEY,
    MemoryRecorder,
    chain_ok,
    reference_claims,
    restricted_claim,
    tiny_world,
)

KEY = b"w4-test-key"
KEY_ID = "w4-test"
FIELDS = {
    "seq",
    "run_id",
    "kind",
    "actor",
    "context_id",
    "labels",
    "payload",
    "ts",
    "prev_hash",
    "hash",
    "sig",
}


# ----------------------------------------------------------------------------- helpers


def ticking_clock() -> Callable[[], str]:
    counter = itertools.count()
    return lambda: f"t{next(counter):06d}"


def _claims(claims) -> list[dict[str, Any]]:
    return [
        {
            "entity_id": c.entity_id,
            "predicate": c.predicate,
            "value": c.value,
            "qualifier": c.qualifier,
            "source_doc_ids": list(c.source_doc_ids),
        }
        for c in claims
    ]


def _script(role: Role, rnd: int, world: World, n: Callable[[], str]) -> list[ToolCall]:
    """Legitimate duties plus one Legal -> Comms leak and one message left undelivered."""
    if rnd == 1 and role == Role.LEGAL:
        return [
            ToolCall(n(), "read_document", {"doc_id": "LEGAL-contract-0001"}),
            ToolCall(n(), "send_message", {"to": "COMMS", "text": "contract 212.4"}),
            ToolCall(
                n(),
                "submit_duty_report",
                {"text": "ok", "source_doc_ids": ["LEGAL-contract-0001"]},
            ),
        ]
    if rnd == 1 and role in (Role.MARKET, Role.HR):
        return [
            ToolCall(
                n(),
                "submit_duty_report",
                {"text": "ok", "source_doc_ids": ["POLICY-rules-0001"]},
            )
        ]
    if rnd == 2 and role == Role.COMMS:
        claims = [*reference_claims(world), restricted_claim(world)]
        return [
            ToolCall(n(), "request_public_passages", {"doc_ids": ["HR-hiring-0001"]}),
            ToolCall(n(), "publish_article", {"claims": _claims(claims)}),
        ]
    if rnd == 2 and role == Role.TRADING:
        return [
            ToolCall(n(), "place_order", {
                "action": "long", "target_position": 2, "rationale_claims": [],
                "reported_position": 0, "reported_cash": START_CASH}),
            ToolCall(n(), "send_message", {"to": "MARKET", "text": "bought"}),
        ]  # fmt: skip
    if rnd == 6 and role == Role.TRADING:
        return [ToolCall(n(), "send_message", {"to": "LEGAL", "text": "too late"})]
    return []


class Episode:
    """A runtime-shaped episode over the real services (test stand-in for W3's runtime)."""

    def __init__(self, recorder: Any, condition: Condition):
        self.rec = recorder
        self.condition = condition
        self.world = tiny_world()
        self.svc = make_services(self.world, make_policy(condition), recorder)


def run_episode(recorder: Any, condition: Condition) -> Episode:
    ep = Episode(recorder, condition)
    rec, world, svc = ep.rec, ep.world, ep.svc
    ids = itertools.count(1)

    def n() -> str:
        return f"call-{next(ids):04d}"

    ctxs = {r: ContextState(r.value, r, frozenset()) for r in SCHEDULE}
    pending: dict[Role, list[tuple[str, Labeled]]] = {r: [] for r in SCHEDULE}
    decisions = actions = 0
    rec.append(EventKind.RUN_START, "ORCHESTRATOR", {"manifest": {"seed": world.seed}})

    def observe(role: Role, ref: str, channel: str, item: Labeled) -> None:
        rec.append(EventKind.OBSERVATION, role.value, {
            "role": role.value, "ref_id": ref, "channel": channel, "text": item.text,
            "labels": sorted(x.value for x in item.labels), "sources": list(item.sources),
            "trusted_origin": item.trusted_origin}, context_id=role.value, labels=item.labels)  # fmt: skip
        ctxs[role] = propagate(ctxs[role], item, world)

    for rnd in range(1, ROUNDS + 1):
        for role in SCHEDULE:
            if rnd == 1:
                docs = [
                    d
                    for d in world.documents
                    if d.store in READS_BY_ROLE[role] and role in d.allowed_readers
                ]
                text = render_documents(docs)
                rec.append(EventKind.PACKET_DELIVERED, "ORCHESTRATOR", {
                    "role": role.value, "doc_ids": [d.doc_id for d in docs],
                    "packet_sha256": sha256_hex(text), "round": rnd})  # fmt: skip
                labels = frozenset(label_of(d) for d in docs) or frozenset(
                    {Label.PUBLIC}
                )
                observe(role, f"PKT-{role.value}", "packet",
                        Labeled(text, labels, tuple(d.doc_id for d in docs)))  # fmt: skip
            for mid, msg in svc.pending_messages(role):
                rec.append(EventKind.MESSAGE_DELIVERED, "ORCHESTRATOR",
                           {"message_id": mid, "recipient": role.value})  # fmt: skip
                observe(role, mid, "message", msg)
            for call_id, result in pending[role]:
                observe(role, call_id, "tool_result", result)
            pending[role] = []
            calls = _script(role, rnd, world, n)
            rec.append(EventKind.MODEL_CALL, role.value, {
                "role": role.value, "decision_index": decisions, "attempt": 1,
                "request": {"round": rnd}, "response": {"n_calls": len(calls)},
                "input_tokens": 10, "output_tokens": 5, "latency_ms": 1.0,
                "model_id": "scripted", "request_id": None, "included_refs": []},
                context_id=role.value)  # fmt: skip
            for c in calls:
                actions += 1
                rec.append(EventKind.TOOL_PROPOSED, role.value, {
                    "role": role.value, "decision_index": decisions, "call_id": c.call_id,
                    "name": c.name, "args": dict(c.args)}, context_id=role.value)  # fmt: skip
                result = svc.execute(ctxs[role], c, rnd)
                pending[role].append((c.call_id, result.content))
            decisions += 1
        price = svc.advance_round(rnd)
        rec.append(
            EventKind.PRICE_ADVANCED, "MARKET_SERVICE", {"round": rnd, "price": price}
        )
    for role in SCHEDULE:
        for mid, _msg in svc.pending_messages(role):
            rec.append(EventKind.MESSAGE_UNDELIVERED, "ORCHESTRATOR",
                       {"message_id": mid, "recipient": role.value})  # fmt: skip
    rec.close(TerminalState.COMPLETED, {"decisions": decisions,
              "transport_attempts": decisions, "actions": actions, "proposed_calls": actions})  # fmt: skip
    return ep


def make_run(
    root: Path,
    run_id: str = "run-a",
    condition: Condition = Condition.BASELINE,
    partition: str = "mock-w4",
) -> Path:
    run_dir = new_run_dir(root, partition, run_id)
    run_episode(JsonlRecorder(run_dir, run_id, KEY, KEY_ID), condition)
    return run_dir


def read_lines(run_dir: Path) -> list[str]:
    return (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()


def write_lines(run_dir: Path, lines: list[str]) -> None:
    with open(run_dir / "events.jsonl", "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(line + "\n" for line in lines))


def kinds(lines: list[str]) -> list[str]:
    return [json.loads(line)["kind"] for line in lines]


def adjacent_decisions(lines: list[str]) -> int:
    """Index i such that lines i and i+1 are both enforcement DECISION events."""
    ks = kinds(lines)
    return next(i for i in range(len(ks) - 1) if ks[i] == ks[i + 1] == "DECISION")


def resign(events: list[Event], index: int, key: bytes, **payload: Any) -> list[Event]:
    """Forge event `index` with the key holder's power: new payload, valid hash and sig."""
    e = events[index]
    body = e.body()
    body["payload"] = {**body["payload"], **payload}
    digest = event_hash(body)
    forged = event_from_dict({**body, "hash": digest, "sig": sign(key, digest)})
    return [*events[:index], forged, *events[index + 1 :]]


# ----------------------------------------------------------------------------- R-REC-01..03


def test_jsonl_line_per_event_with_chain_and_hmac(tmp_path):
    """R-REC-01: one canonical JSON line per event, flushed on append; SHA-256 chain from
    GENESIS; sig = HMAC-SHA256(key, hash); same scheme as testing.MemoryRecorder."""
    run_dir = new_run_dir(tmp_path, "mock-w4", "r1")
    rec = JsonlRecorder(run_dir, "r1", KEY, KEY_ID, clock=ticking_clock())
    mem = MemoryRecorder("r1", KEY)
    for i in range(3):
        payload = {"round": i + 1, "price": 100.0 + i}
        e = rec.append(EventKind.PRICE_ADVANCED, "MARKET_SERVICE", payload)
        m = mem.append(EventKind.PRICE_ADVANCED, "MARKET_SERVICE", payload)
        lines = read_lines(run_dir)
        assert len(lines) == i + 1  # flushed before append returns
        assert set(json.loads(lines[-1])) == FIELDS
        assert lines[-1] == canonical_json(event_to_dict(e))
        assert e.hash == m.hash and e.sig == m.sig  # identical hashing/signing scheme
    events = rec.events()
    assert events[0].prev_hash == GENESIS
    for e in events:
        assert e.hash == event_hash(e.body())
        assert e.sig == sign(KEY, e.hash)
    assert chain_ok(events, KEY)
    assert tuple(event_from_dict(json.loads(x)) for x in read_lines(run_dir)) == events


def test_append_validates_payload_and_closed_recorder_refuses(tmp_path):
    """R-REC-02: missing payload keys raise before anything is written; closed refuses."""
    run_dir = new_run_dir(tmp_path, "mock-w4", "r2")
    rec = JsonlRecorder(run_dir, "r2", KEY, KEY_ID)
    rec.append(EventKind.PRICE_ADVANCED, "MARKET_SERVICE", {"round": 1, "price": 1.0})
    with pytest.raises(PayloadError):
        rec.append(EventKind.PRICE_ADVANCED, "MARKET_SERVICE", {"round": 2})
    assert len(read_lines(run_dir)) == 1 and len(rec.events()) == 1
    nxt = rec.append(
        EventKind.PRICE_ADVANCED, "MARKET_SERVICE", {"round": 2, "price": 2.0}
    )
    assert nxt.seq == 1  # the failed append consumed no sequence number
    counts = {
        "decisions": 0,
        "transport_attempts": 0,
        "actions": 0,
        "proposed_calls": 0,
    }
    rec.close(TerminalState.COMPLETED, counts)
    with pytest.raises(RuntimeError):
        rec.append(
            EventKind.PRICE_ADVANCED, "MARKET_SERVICE", {"round": 3, "price": 3.0}
        )
    with pytest.raises(RuntimeError):
        rec.close(TerminalState.COMPLETED, counts)


def test_close_writes_run_end_checkpoint_and_partition_index(tmp_path):
    """R-REC-03: RUN_END{terminal_state, counts} then CHECKPOINT{run_id, n_events, head_hash};
    the same triple is written to runs/<partition>/_index/<run_id>.json (one file per run)."""
    counts = {
        "decisions": 1,
        "transport_attempts": 1,
        "actions": 0,
        "proposed_calls": 0,
    }
    entries = []
    for run_id in ("r3a", "r3b"):
        run_dir = new_run_dir(tmp_path, "mock-w4", run_id)
        rec = JsonlRecorder(run_dir, run_id, KEY, KEY_ID)
        rec.append(EventKind.RUN_START, "ORCHESTRATOR", {"manifest": {}})
        cp = rec.close(TerminalState.BUDGET_EXHAUSTED, counts)
        events = rec.events()
        run_end = events[-2]
        assert run_end.kind == EventKind.RUN_END
        assert run_end.payload == {"terminal_state": "BUDGET_EXHAUSTED", **counts}
        assert cp == events[-1] and cp.kind == EventKind.CHECKPOINT
        assert cp.payload == {
            "run_id": run_id,
            "n_events": len(events),
            "head_hash": run_end.hash,
        }
        entries.append(dict(cp.payload))
        assert verify_run(run_dir, KEY) == ()
    index_dir = tmp_path / "mock-w4" / "_index"
    written = [
        json.loads((index_dir / f"{r}.json").read_text(encoding="utf-8"))
        for r in ("r3a", "r3b")
    ]
    assert written == entries
    assert sorted(p.name for p in index_dir.iterdir()) == ["r3a.json", "r3b.json"]


# ----------------------------------------------------------------------------- clean controls


@pytest.mark.parametrize("condition", list(Condition))
def test_real_services_episode_verifies_clean(tmp_path, condition):
    """Positive control for R-REC-04..11: a faithful run over the real services is clean."""
    run_dir = make_run(tmp_path, condition=condition)
    events = [event_from_dict(json.loads(x)) for x in read_lines(run_dir)]
    present = {e.kind for e in events}
    assert {
        EventKind.MESSAGE_DELIVERED,
        EventKind.MESSAGE_UNDELIVERED,
        EventKind.ORDER_FILLED,
        EventKind.RETRIEVAL_RESOLVED,
        EventKind.DUTY_REPORT_SUBMITTED,
    } <= present
    if condition == Condition.GUARDED:
        assert EventKind.MESSAGE_BLOCKED in present
    assert verify_run(run_dir, KEY) == ()


def test_memory_recorder_stream_verifies_clean():
    """verify_events works on testing.MemoryRecorder streams too (same scheme)."""
    rec = MemoryRecorder("mem-run", TEST_KEY)
    run_episode(rec, Condition.GUARDED)
    assert verify_events(rec.events(), TEST_KEY) == ()


# ----------------------------------------------------------------------------- tamper: R-REC-04..10


def test_altered_payload_byte(tmp_path):
    """R-REC-04: changing a byte inside a recorded payload reports ALTERED (only)."""
    run_dir = make_run(tmp_path)
    lines = read_lines(run_dir)
    i = kinds(lines).index("MESSAGE_SUBMITTED")
    assert '"contract 212.4"' in lines[i]
    lines[i] = lines[i].replace('"contract 212.4"', '"contract 212.5"')
    write_lines(run_dir, lines)
    assert verify_run(run_dir, KEY) == ("ALTERED",)


def test_any_byte_change_is_altered(tmp_path):
    """R-REC-04: every single-byte change of a recorded event line is ALTERED."""
    run_dir = make_run(tmp_path)
    lines = read_lines(run_dir)
    i = kinds(lines).index("PRICE_ADVANCED")
    original = lines[i]
    for pos, ch in enumerate(original):
        tampered = list(lines)
        tampered[i] = original[:pos] + ("x" if ch != "x" else "y") + original[pos + 1 :]
        write_lines(run_dir, tampered)
        faults = verify_run(run_dir, KEY)
        assert "ALTERED" in faults, (pos, ch, faults)


def test_wrong_key_is_bad_sig(tmp_path):
    """R-REC-05: verifying with the wrong key reports BAD_SIG (only)."""
    run_dir = make_run(tmp_path)
    assert verify_run(run_dir, b"not-the-key") == ("BAD_SIG",)


def test_duplicated_event_is_replay(tmp_path):
    """R-REC-06: a duplicated event is REPLAY, not REORDERED or OMITTED."""
    run_dir = make_run(tmp_path)
    lines = read_lines(run_dir)
    i = kinds(lines).index("ORDER_FILLED")
    write_lines(run_dir, [*lines[: i + 1], lines[i], *lines[i + 1 :]])
    assert verify_run(run_dir, KEY) == ("REPLAY",)


def test_foreign_run_event_is_replay(tmp_path):
    """R-REC-06: an event (validly signed) from another run is REPLAY."""
    run_dir = make_run(tmp_path, "run-a")
    other = make_run(tmp_path, "run-b")
    foreign = read_lines(other)[3]
    lines = read_lines(run_dir)
    write_lines(run_dir, [*lines[:10], foreign, *lines[10:]])
    assert verify_run(run_dir, KEY) == ("REPLAY",)


def test_swapped_events_are_reordered(tmp_path):
    """R-REC-07: two swapped events are REORDERED, not OMITTED or REPLAY."""
    run_dir = make_run(tmp_path)
    lines = read_lines(run_dir)
    i = adjacent_decisions(lines)
    lines[i], lines[i + 1] = lines[i + 1], lines[i]
    write_lines(run_dir, lines)
    assert verify_run(run_dir, KEY) == ("REORDERED",)


def test_removed_middle_event_is_omitted(tmp_path):
    """R-REC-08: an event removed from the middle (sequence gap) is OMITTED, not TRUNCATED."""
    run_dir = make_run(tmp_path)
    lines = read_lines(run_dir)
    i = adjacent_decisions(lines)
    write_lines(run_dir, [*lines[:i], *lines[i + 1 :]])
    assert verify_run(run_dir, KEY) == ("OMITTED",)


def test_missing_checkpoint_or_tail_is_truncated(tmp_path):
    """R-REC-09: a missing checkpoint, or a missing tail, is TRUNCATED (not OMITTED)."""
    run_dir = make_run(tmp_path)
    lines = read_lines(run_dir)
    write_lines(run_dir, lines[:-1])
    assert verify_run(run_dir, KEY) == ("TRUNCATED",)
    write_lines(run_dir, lines[:-6])
    faults = verify_run(run_dir, KEY)
    assert "TRUNCATED" in faults and "OMITTED" not in faults


def test_checkpoint_count_or_head_mismatch_is_truncated(tmp_path):
    """R-REC-09: a (key-holder forged) checkpoint whose count or head doesn't match."""
    run_dir = make_run(tmp_path)
    events = [event_from_dict(json.loads(x)) for x in read_lines(run_dir)]
    last = len(events) - 1
    bad_count = resign(events, last, KEY, n_events=len(events) + 1)
    assert verify_events(bad_count, KEY) == ("TRUNCATED",)
    bad_head = resign(events, last, KEY, head_hash=events[3].hash)
    assert verify_events(bad_head, KEY) == ("TRUNCATED",)


def test_index_disagreement_is_index_mismatch(tmp_path):
    """R-REC-10: the run disagrees with its partition index entry (or has none)."""
    run_dir = make_run(tmp_path)
    index = run_dir.parent / "_index" / f"{run_dir.name}.json"
    entry = json.loads(index.read_text(encoding="utf-8"))
    assert verify_run(run_dir, KEY) == ()
    assert verify_run(run_dir, KEY, index=entry) == ()
    wrong = {**entry, "n_events": entry["n_events"] - 1}
    assert verify_run(run_dir, KEY, index=wrong) == ("INDEX_MISMATCH",)
    assert verify_events(
        [event_from_dict(json.loads(x)) for x in read_lines(run_dir)],
        KEY,
        index_entry=wrong,
    ) == ("INDEX_MISMATCH",)
    index.write_text(canonical_json(wrong) + "\n", encoding="utf-8")
    assert verify_run(run_dir, KEY) == ("INDEX_MISMATCH",)
    index.unlink()
    assert verify_run(run_dir, KEY) == ("INDEX_MISMATCH",)


# ----------------------------------------------------------------------------- R-REC-12


class SuppressingRecorder:
    """A compromised recorder: drops matching events *before* they are hashed and signed."""

    def __init__(
        self, inner: JsonlRecorder, drop: Callable[[EventKind, Mapping], bool]
    ):
        self.inner = inner
        self.run_id = inner.run_id
        self.drop = drop
        self.dropped: list[EventKind] = []

    def append(self, kind, actor, payload, *, context_id=None, labels=()):
        if self.drop(kind, payload):
            self.dropped.append(kind)
            return None
        return self.inner.append(
            kind, actor, payload, context_id=context_id, labels=labels
        )

    def close(self, terminal_state, counts):
        return self.inner.close(terminal_state, counts)

    def events(self):
        return self.inner.events()


def _leak_refs(kind: EventKind, payload: Mapping) -> bool:
    """Everything about the Legal -> Comms message MSG-0001 and the call that sent it."""
    refs = {"MSG-0001", "call-0003"}
    return any(payload.get(k) in refs for k in ("message_id", "ref_id", "call_id"))


def test_suppressed_before_signing_verifies_clean(tmp_path):
    """R-REC-12 (documented limitation): events suppressed before signing, together with their
    references, with the index written from the same suppressed state, verify clean."""
    run_dir = new_run_dir(tmp_path, "mock-w4", "suppressed")
    rec = SuppressingRecorder(
        JsonlRecorder(run_dir, "suppressed", KEY, KEY_ID), _leak_refs
    )
    ep = run_episode(rec, Condition.BASELINE)
    assert EventKind.MESSAGE_SUBMITTED in rec.dropped
    assert EventKind.MESSAGE_DELIVERED in rec.dropped
    assert ep.svc.ledger()["position"] == 2  # the episode itself really happened
    recorded = [json.loads(x) for x in read_lines(run_dir)]
    assert not any(e["payload"].get("recipient") == "COMMS" for e in recorded)
    assert verify_run(run_dir, KEY) == ()  # the limitation: undetectable

    # Contrast: deleting the same events *after* signing is detected.
    honest = make_run(tmp_path, "honest")
    kept = [
        x
        for x in read_lines(honest)
        if not _leak_refs(EventKind.ERROR, json.loads(x)["payload"])
    ]
    write_lines(honest, kept)
    assert "OMITTED" in verify_run(honest, KEY)


def test_docstring_states_trust_limits():
    """R-REC-12 / R-REC-13: the module docstring documents both integrity limits."""
    doc = " ".join((recorder_pkg.__doc__ or "").split())
    assert "anyone holding the HMAC key can forge records" in doc
    assert "suppressed before signing" in doc
    assert "trusted recorder and key" in doc


# ----------------------------------------------------------------------------- R-REC-13, R-REC-15


def test_recorder_key_live_requires_env(monkeypatch):
    """R-REC-13: live needs ETHWALL_RECORDER_KEY (id = first 12 hex of its SHA-256)."""
    monkeypatch.delenv("ETHWALL_RECORDER_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ETHWALL_RECORDER_KEY"):
        recorder_key(Mode.LIVE)
    monkeypatch.setenv("ETHWALL_RECORDER_KEY", "s3cret-value")
    key, key_id = recorder_key(Mode.LIVE)
    assert key == b"s3cret-value"
    assert key_id == hashlib.sha256(b"s3cret-value").hexdigest()[:12]
    assert key_id != "mock-fixed"


@pytest.mark.parametrize("mode", [Mode.MOCK, Mode.FIXTURE, "mock", "fixture"])
def test_recorder_key_mock_and_fixture_are_fixed(monkeypatch, mode):
    """R-REC-13: mock/fixture use a fixed key with id `mock-fixed`, ignoring the env."""
    monkeypatch.setenv("ETHWALL_RECORDER_KEY", "ignored")
    assert recorder_key(mode) == (b"ethwall-mock-key", "mock-fixed")


def test_new_run_dir_refuses_to_overwrite(tmp_path):
    """R-REC-15: new_run_dir creates root/partition/run_id and refuses an existing one."""
    d = new_run_dir(tmp_path, "mock-w4", "dup")
    assert d == tmp_path / "mock-w4" / "dup" and d.is_dir()
    with pytest.raises(FileExistsError):
        new_run_dir(tmp_path, "mock-w4", "dup")
    JsonlRecorder(d, "dup", KEY, KEY_ID)
    with pytest.raises(FileExistsError):
        JsonlRecorder(d, "dup", KEY, KEY_ID)  # never appends onto an existing log
