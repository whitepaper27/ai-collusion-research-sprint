"""W4 verifier reconciliation faults (docs/specs/recorder-freeze.md R-REC-11).

Small synthetic streams built with `testing.MemoryRecorder` (real chain + HMAC), one fault
code per test plus the legitimate neighbour that must stay clean:
- every message has exactly one outcome: MESSAGE_DELIVERED, MESSAGE_BLOCKED or
  MESSAGE_UNDELIVERED (still queued at episode end);
- every retrieval, order and article submission has exactly one outcome; a duty report is
  resolved by its own submission event;
- every delivered message and packet has an OBSERVATION;
- model decisions (distinct MODEL_CALL decision_index) <= 30, executed tool actions <= 90,
  transport attempts <= 2 x decisions. Enforcement DECISION events are not model decisions.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ethwall.contract import (
    DECISIONS_PER_EPISODE,
    MAX_ACTIONS_PER_EPISODE,
    EventKind,
    TerminalState,
)
from ethwall.recorder import verify_events
from ethwall.testing import TEST_KEY, MemoryRecorder

Step = Callable[[MemoryRecorder], None]


def stream(*steps: Step, counts: Mapping[str, int] | None = None) -> tuple[str, ...]:
    rec = MemoryRecorder("rec-run", TEST_KEY)
    rec.append(EventKind.RUN_START, "ORCHESTRATOR", {"manifest": {}})
    for step in steps:
        step(rec)
    n_calls = sum(1 for e in rec.events() if e.kind == EventKind.MODEL_CALL)
    base = {
        "decisions": n_calls,
        "transport_attempts": n_calls,
        "actions": 0,
        "proposed_calls": 0,
    }
    rec.close(TerminalState.COMPLETED, {**base, **(counts or {})})
    return verify_events(rec.events(), TEST_KEY)


def ev(kind: EventKind, **payload: Any) -> Step:
    return lambda rec: rec.append(kind, "ACTOR", payload) and None


def message(mid: str = "MSG-0001", to: str = "COMMS") -> Step:
    return ev(EventKind.MESSAGE_SUBMITTED, message_id=mid, call_id="c1", sender="LEGAL",
              recipient=to, text="hi", labels=["PUBLIC"], context_refs=[])  # fmt: skip


def observed(ref: str, role: str = "COMMS", channel: str = "message") -> Step:
    return ev(EventKind.OBSERVATION, role=role, ref_id=ref, channel=channel, text="hi",
              labels=["PUBLIC"], sources=[], trusted_origin=None)  # fmt: skip


def delivered(mid: str = "MSG-0001", to: str = "COMMS") -> Step:
    return ev(EventKind.MESSAGE_DELIVERED, message_id=mid, recipient=to)


def model_call(i: int, attempt: int = 1) -> Step:
    return ev(EventKind.MODEL_CALL, role="MARKET", decision_index=i, attempt=attempt,
              request={}, response={}, input_tokens=1, output_tokens=1, latency_ms=1.0,
              model_id="m", request_id=f"api-{i}-{attempt}", included_refs=[])  # fmt: skip


def failure(i: int, attempt: int) -> Step:
    return ev(EventKind.TRANSPORT_FAILURE, role="MARKET", decision_index=i,
              attempt=attempt, error_kind="overloaded", retryable=True, message="529")  # fmt: skip


def enforcement_decision(ref: str) -> Step:
    return ev(EventKind.DECISION, ref_id=ref, boundary="entitlement", rule_id="OK",
              verdict="allow", reason="ok", labels=[])  # fmt: skip


def executed_call(i: int, call_id: str) -> list[Step]:
    return [
        ev(EventKind.TOOL_PROPOSED, role="MARKET", decision_index=i, call_id=call_id,
           name="list_documents", args={"store": "NEWS"}),
        enforcement_decision(call_id),
    ]  # fmt: skip


# ----------------------------------------------------------------------------- messages


def test_message_outcomes():
    """R-REC-11: each message has exactly one outcome (DELIVERED, BLOCKED or UNDELIVERED)."""
    assert stream(message(), delivered(), observed("MSG-0001")) == ()
    blocked = ev(EventKind.MESSAGE_BLOCKED, message_id="MSG-0001", rule_id="PROV_MSG")
    assert stream(message(), blocked) == ()
    undelivered = ev(
        EventKind.MESSAGE_UNDELIVERED, message_id="MSG-0001", recipient="COMMS"
    )
    assert stream(message(), undelivered) == ()
    assert stream(message()) == ("UNRECONCILED_MESSAGE",)
    assert stream(message(), blocked, undelivered) == ("UNRECONCILED_MESSAGE",)
    assert stream(delivered("MSG-0404"), observed("MSG-0404")) == (
        "UNRECONCILED_MESSAGE",
    )
    wrong_recipient = delivered(to="TRADING")
    assert stream(message(), wrong_recipient, observed("MSG-0001", "TRADING")) == (
        "UNRECONCILED_MESSAGE",
    )


# ----------------------------------------------------------------------------- submissions


def test_submission_outcomes():
    """R-REC-11: retrieval/order/article submissions need exactly one outcome event."""
    ret = ev(EventKind.RETRIEVAL_REQUESTED, request_id="RET-0001", call_id="c",
             role="COMMS", doc_ids=["NEWS-1"])  # fmt: skip
    resolved = ev(EventKind.RETRIEVAL_RESOLVED, request_id="RET-0001", doc_ids=["NEWS-1"],
                  text_sha256="0" * 64)  # fmt: skip
    rejected = ev(
        EventKind.RETRIEVAL_REJECTED, request_id="RET-0001", rule_id="PROV_RET"
    )
    order = ev(EventKind.ORDER_SUBMITTED, order_id="ORD-0001", call_id="c", role="TRADING",
               args={}, labels=["PUBLIC"])  # fmt: skip
    filled = ev(EventKind.ORDER_FILLED, order_id="ORD-0001", price=100.0, dq=1,
                position=1, cash=9899.9, fee=0.1, round=1)  # fmt: skip
    art = ev(EventKind.ARTICLE_SUBMITTED, article_id="ART-0001", call_id="c", role="COMMS",
             claims=[], labels=["PUBLIC"], position_at_submission=0)  # fmt: skip
    accepted = ev(EventKind.ARTICLE_ACCEPTED, article_id="ART-0001", rendered="x")
    art_rejected = ev(
        EventKind.ARTICLE_REJECTED, article_id="ART-0001", rule_id="PROV_PUB"
    )

    assert stream(ret, resolved, order, filled, art, accepted) == ()
    assert stream(ret, rejected, order, filled, art, art_rejected) == ()
    assert stream(ret) == ("UNRECONCILED_SUBMISSION",)
    assert stream(ret, resolved, rejected) == ("UNRECONCILED_SUBMISSION",)
    assert stream(order) == ("UNRECONCILED_SUBMISSION",)
    assert stream(art) == ("UNRECONCILED_SUBMISSION",)
    assert stream(art, accepted, accepted) == ("UNRECONCILED_SUBMISSION",)
    assert stream(filled) == ("UNRECONCILED_SUBMISSION",)  # outcome without submission
    assert stream(resolved, ret) == (
        "UNRECONCILED_SUBMISSION",
    )  # outcome before submission


def test_duty_report_is_resolved_by_its_submission():
    """R-REC-11: a duty report needs no separate outcome event."""
    report = ev(EventKind.DUTY_REPORT_SUBMITTED, report_id="REP-0001", call_id="c",
                role="LEGAL", text="ok", source_doc_ids=["LEGAL-1"], labels=["LEGAL"])  # fmt: skip
    assert stream(report) == ()
    assert stream(report, report) == ("UNRECONCILED_SUBMISSION",)  # one id, two reports


def test_model_call_request_id_is_not_a_retrieval():
    """R-REC-11: MODEL_CALL also has a `request_id` key; it is never a retrieval outcome."""
    assert stream(model_call(0), model_call(1)) == ()


# ----------------------------------------------------------------------------- observations


def test_every_delivery_is_observed():
    """R-REC-11: each delivered message and packet has an OBSERVATION after it."""
    packet = ev(EventKind.PACKET_DELIVERED, role="LEGAL", doc_ids=["LEGAL-1"],
                packet_sha256="0" * 64, round=1)  # fmt: skip
    assert stream(packet, observed("PKT-LEGAL", "LEGAL", "packet")) == ()
    assert stream(packet) == ("UNOBSERVED_DELIVERY",)
    assert stream(packet, observed("PKT-HR", "HR", "packet")) == (
        "UNOBSERVED_DELIVERY",
    )
    assert stream(message(), delivered()) == ("UNOBSERVED_DELIVERY",)
    assert stream(message(), observed("MSG-0001"), delivered()) == (
        "UNOBSERVED_DELIVERY",
    )
    assert stream(message(), delivered(), observed("MSG-0001", "LEGAL")) == (
        "UNOBSERVED_DELIVERY",
    )


# ----------------------------------------------------------------------------- caps


def test_decision_cap_counts_distinct_model_call_indices():
    """R-REC-11: > 30 distinct MODEL_CALL decision_index values is DECISION_CAP; retries of
    one decision and enforcement DECISION events don't count as model decisions."""
    at_cap = [model_call(i) for i in range(DECISIONS_PER_EPISODE)]
    assert stream(*at_cap) == ()
    over = [*at_cap, model_call(DECISIONS_PER_EPISODE)]
    assert stream(*over) == ("DECISION_CAP",)
    many_enforcement = [
        enforcement_decision(f"r{i}") for i in range(3 * DECISIONS_PER_EPISODE)
    ]
    assert stream(*at_cap, *many_enforcement) == ()
    retried = [failure(0, 1), model_call(0, 2), *at_cap[1:]]
    assert (
        stream(*retried, counts={"transport_attempts": DECISIONS_PER_EPISODE + 1}) == ()
    )
    claimed = {"decisions": DECISIONS_PER_EPISODE + 1}
    assert stream(*at_cap, counts=claimed) == ("DECISION_CAP",)


def test_action_cap_counts_executed_tool_proposals():
    """R-REC-11: > 90 executed TOOL_PROPOSED actions is ACTION_CAP; proposals that were not
    executed (e.g. over the per-response limit) don't count; RUN_END counts are checked."""
    calls = [model_call(i) for i in range(DECISIONS_PER_EPISODE)]
    executed = [
        s
        for k in range(MAX_ACTIONS_PER_EPISODE)
        for s in executed_call(k % DECISIONS_PER_EPISODE, f"c{k}")
    ]
    assert stream(*calls, *executed, counts={"actions": MAX_ACTIONS_PER_EPISODE}) == ()
    one_more = executed_call(0, "c-extra")
    assert stream(*calls, *executed, *one_more) == ("ACTION_CAP",)
    not_executed = [
        ev(EventKind.TOOL_PROPOSED, role="MARKET", decision_index=1, call_id=f"x{j}",
           name="list_documents", args={})
        for j in range(4)
    ]  # fmt: skip
    limit = ev(EventKind.TOOL_LIMIT, role="MARKET", decision_index=1, proposed=4)
    assert stream(*calls, *executed, *not_executed, limit) == ()
    claimed = {"actions": MAX_ACTIONS_PER_EPISODE + 1}
    assert stream(*calls, counts=claimed) == ("ACTION_CAP",)


def test_transport_cap():
    """R-REC-11: transport attempts <= 2 x decisions (one identical retry per decision)."""
    assert stream(failure(0, 1), model_call(0, 2), model_call(1)) == ()
    # API_ERROR shape: both attempts of a decision failed and no MODEL_CALL exists.
    assert stream(model_call(0), failure(1, 1), failure(1, 2)) == ()
    three = [failure(0, 1), failure(0, 2), model_call(0, 3)]
    assert stream(*three) == ("TRANSPORT_CAP",)
    claimed = {"decisions": 2, "transport_attempts": 5}
    assert stream(model_call(0), model_call(1), counts=claimed) == ("TRANSPORT_CAP",)
