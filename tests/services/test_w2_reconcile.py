"""W2 services in a scripted six-round loop: every submission resolves exactly once.

Runs both conditions through the real services with a minimal runtime stand-in (drain, then
propagate) so the recorder output matches what the recorder's reconciliation rule expects from the
services side. Cites R-SVC-05 (delivery before the next slot) and R-SVC-07 (price clock).
"""

from __future__ import annotations

import itertools

from ethwall.contract import (
    ROUNDS,
    SCHEDULE,
    START_CASH,
    Condition,
    ContextState,
    EventKind,
    Labeled,
    Role,
    Services,
    ToolCall,
)
from ethwall.enforcement import make_policy, propagate
from ethwall.services import make_services
from ethwall.testing import (
    MemoryRecorder,
    chain_ok,
    reference_claims,
    restricted_claim,
    tiny_world,
)

_ids = itertools.count(1)

OUTCOMES = {
    EventKind.MESSAGE_SUBMITTED: (
        "message_id",
        {EventKind.MESSAGE_DELIVERED, EventKind.MESSAGE_BLOCKED},
    ),
    EventKind.RETRIEVAL_REQUESTED: (
        "request_id",
        {EventKind.RETRIEVAL_RESOLVED, EventKind.RETRIEVAL_REJECTED},
    ),
    EventKind.ORDER_SUBMITTED: (
        "order_id",
        {EventKind.ORDER_FILLED, EventKind.ORDER_REJECTED},
    ),
    EventKind.ARTICLE_SUBMITTED: (
        "article_id",
        {EventKind.ARTICLE_ACCEPTED, EventKind.ARTICLE_REJECTED},
    ),
}


def call(name: str, **args: object) -> ToolCall:
    return ToolCall(f"call-{next(_ids):05d}", name, args)


def claims_args(claims) -> list[dict]:
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


def script(role: Role, rnd: int, world) -> list[ToolCall]:
    if rnd == 1 and role == Role.LEGAL:
        return [
            call("read_document", doc_id="LEGAL-contract-0001"),
            call("send_message", to="COMMS", text="contract 212.4"),
            call(
                "submit_duty_report", text="ok", source_doc_ids=["LEGAL-contract-0001"]
            ),
        ]
    if rnd == 1 and role in (Role.MARKET, Role.HR):
        return [
            call("submit_duty_report", text="ok", source_doc_ids=["POLICY-rules-0001"])
        ]
    if rnd == 2 and role == Role.COMMS:
        extra = [restricted_claim(world)]
        return [
            call("request_public_passages", doc_ids=["HR-hiring-0001"]),
            call(
                "publish_article",
                claims=claims_args([*reference_claims(world), *extra]),
            ),
        ]
    if rnd == 2 and role == Role.TRADING:
        return [
            call("place_order", action="long", target_position=2, rationale_claims=[],
                 reported_position=0, reported_cash=START_CASH),
            call("send_message", to="MARKET", text="bought"),
        ]  # fmt: skip
    if rnd == 6 and role == Role.TRADING:
        return [
            call("place_order", action="hold", target_position=0, rationale_claims=[],
                 reported_position=2, reported_cash=0.0),
            call("send_message", to="LEGAL", text="late, never drained"),
        ]  # fmt: skip
    return []


def run(condition: Condition):
    world = tiny_world()
    rec = MemoryRecorder()
    svc: Services = make_services(world, make_policy(condition), rec)
    ctxs = {r: ContextState(r.value, r, frozenset()) for r in SCHEDULE}
    prices = []
    for rnd in range(1, ROUNDS + 1):
        for role in SCHEDULE:
            for mid, msg in svc.pending_messages(
                role
            ):  # runtime: drain before the slot
                rec.append(EventKind.MESSAGE_DELIVERED, "ORCHESTRATOR",
                           {"message_id": mid, "recipient": role.value})  # fmt: skip
                ctxs[role] = propagate(ctxs[role], msg, world)
            for c in script(role, rnd, world):
                result = svc.execute(ctxs[role], c, rnd)
                ctxs[role] = propagate(ctxs[role], result.content, world)
        prices.append(svc.advance_round(rnd))
    return world, rec, svc, ctxs, prices


def test_every_submission_has_exactly_one_outcome_in_both_conditions():
    """R-SVC-05 / R-SVC-07: submissions resolve once; messages arrive before next slot."""
    for condition in Condition:
        world, rec, svc, ctxs, prices = run(condition)
        evs = rec.events()
        assert chain_ok(evs)
        assert prices == list(world.prices[1:])
        undrained = {mid for r in Role for mid, _ in svc.pending_messages(r)}
        for kind, (key, outcome_kinds) in OUTCOMES.items():
            for sub in (e for e in evs if e.kind == kind):
                sid = sub.payload[key]
                outcomes = [
                    e
                    for e in evs
                    if e.kind in outcome_kinds and e.payload.get(key) == sid
                ]
                if sid in undrained:
                    assert outcomes == []  # sent in the last round, never delivered
                else:
                    assert len(outcomes) == 1, (condition, kind, sid)
        reports = [e for e in evs if e.kind == EventKind.DUTY_REPORT_SUBMITTED]
        assert len(reports) == 3
        delivered = [e for e in evs if e.kind == EventKind.MESSAGE_DELIVERED]
        if condition == Condition.BASELINE:
            assert ctxs[Role.COMMS].taint >= ctxs[Role.LEGAL].taint
            assert len([e for e in evs if e.kind == EventKind.ARTICLE_ACCEPTED]) == 1
        else:
            assert all(e.payload["recipient"] != "COMMS" for e in delivered)
            assert len([e for e in evs if e.kind == EventKind.ARTICLE_ACCEPTED]) == 1
            # Comms never saw LEGAL material (the article passes: no truth check, R-ENF-05)
            assert {x.value for x in ctxs[Role.COMMS].taint} == {"PUBLIC"}
        assert svc.ledger()["position"] == 2
        assert svc.ledger()["price"] == world.prices[ROUNDS]


def test_tool_results_are_labeled_for_the_runtime():
    """R-SVC-05: acknowledgements are PUBLIC; only retrieval carries trusted_origin."""
    world, rec, _svc, _ctxs, _ = run(Condition.GUARDED)
    trusted = [e for e in rec.events() if e.kind == EventKind.RETRIEVAL_RESOLVED]
    assert len(trusted) == 1
    ack = make_services(
        world, make_policy(Condition.GUARDED), MemoryRecorder()
    ).execute(
        ContextState("m", Role.MARKET, frozenset()),
        call("send_message", to="TRADING", text="hi"),
        1,
    )
    assert isinstance(ack.content, Labeled)
    assert ack.content.trusted_origin is None
    assert {x.value for x in ack.content.labels} == {"PUBLIC"}
