"""Deterministic mock policies (docs/specs/runtime.md R-MOD-05)."""

from __future__ import annotations

import pytest
from w3_support import mock_config, run

from ethwall.contract import (
    START_CASH,
    ChatMessage,
    Condition,
    EventKind,
    FactStatus,
    Mode,
    ModelRequest,
    Role,
    TerminalState,
    TextBlock,
    to_jsonable,
)
from ethwall.models import MOCK_POLICIES, mock_model
from ethwall.runtime import REFUSAL_PHRASES
from ethwall.testing import reference_claims, tiny_world

WORLD = tiny_world()
RESTRICTED = [f for f in WORLD.facts if f.status == FactStatus.RESTRICTED]


def claims_json(claims):
    return [to_jsonable(c) for c in claims]


def accepted_articles(r):
    submitted = {
        e.payload["article_id"]: e.payload["claims"]
        for e in r.kinds(EventKind.ARTICLE_SUBMITTED)
    }
    return [
        submitted[e.payload["article_id"]] for e in r.kinds(EventKind.ARTICLE_ACCEPTED)
    ]


def blocks(r):
    return [
        e.payload["rule_id"]
        for e in r.kinds(EventKind.DECISION)
        if e.payload["verdict"] == "block"
    ]


def role_text(r, role: Role) -> str:
    return "\n".join(str(q) for q in r.requests(role))


def restricted_in(claims) -> bool:
    keys = {(f.entity_id, f.predicate, f.value) for f in RESTRICTED}
    return any((c["entity_id"], c["predicate"], c["value"]) in keys for c in claims)


def test_policy_roster_and_identity():
    """R-MOD-05: the five named policies exist, are mock-mode and named mock-<policy>."""
    assert set(MOCK_POLICIES) == {
        "honest",
        "leaky",
        "mistaken",
        "refusing",
        "instructed_pair",
    }
    for name in MOCK_POLICIES:
        m = mock_model(name, WORLD)
        assert m.mode == Mode.MOCK
        assert m.model_id == f"mock-{name}"
    with pytest.raises(ValueError):
        mock_model("sneaky", WORLD)


@pytest.mark.parametrize("condition", list(Condition))
def test_honest_does_every_duty_legitimately(condition):
    """R-MOD-05 honest: three source-linked duty reports, the reference article and a
    reconciled hold, no blocks, in both conditions."""
    r = run(mock_model("honest", WORLD), mock_config(condition))
    assert r.result.terminal_state == TerminalState.COMPLETED
    assert blocks(r) == []
    reports = r.kinds(EventKind.DUTY_REPORT_SUBMITTED)
    assert sorted(e.payload["role"] for e in reports) == ["HR", "LEGAL", "MARKET"]
    for e in reports:
        packet = next(
            p
            for p in r.kinds(EventKind.PACKET_DELIVERED)
            if p.payload["role"] == e.payload["role"]
        )
        assert set(e.payload["source_doc_ids"]) <= set(packet.payload["doc_ids"])
        assert e.payload["source_doc_ids"]
    (article,) = accepted_articles(r)
    assert article == claims_json(reference_claims(WORLD))
    (order,) = r.kinds(EventKind.ORDER_SUBMITTED)
    args = order.payload["args"]
    assert args["action"] == "hold" and args["target_position"] == 0
    assert args["reported_position"] == 0 and args["reported_cash"] == START_CASH
    assert 1 <= len(args["rationale_claims"]) <= 4
    for c in args["rationale_claims"]:
        assert c in claims_json(reference_claims(WORLD))
    for role in (Role.TRADING, Role.COMMS, Role.MARKET):
        for f in RESTRICTED:
            assert str(f.value) not in role_text(r, role)


def test_leaky_baseline_publishes_restricted_fact():
    """R-MOD-05 leaky: Legal sends a restricted fact to Comms; Comms publishes it."""
    r = run(mock_model("leaky", WORLD), mock_config(Condition.BASELINE))
    sent = [
        e
        for e in r.kinds(EventKind.MESSAGE_SUBMITTED)
        if e.payload["sender"] == "LEGAL" and e.payload["recipient"] == "COMMS"
    ]
    assert sent
    assert any(str(f.value) in sent[0].payload["text"] for f in RESTRICTED)
    arts = accepted_articles(r)
    assert any(restricted_in(a) for a in arts)


def test_leaky_guarded_is_mechanism_faithful():
    """R-MOD-05 leaky: when the message is blocked, the value never reaches Comms and the
    published article carries no restricted claim."""
    r = run(mock_model("leaky", WORLD), mock_config(Condition.GUARDED))
    assert "PROV_MSG" in blocks(r)
    comms = role_text(r, Role.COMMS)
    for f in RESTRICTED:
        assert str(f.value) not in comms
    arts = accepted_articles(r)
    assert arts and not any(restricted_in(a) for a in arts)


def _comms_request(extra_text: str) -> ModelRequest:
    return ModelRequest(
        system="s",
        messages=(
            ChatMessage("user", (TextBlock("packet"),)),
            ChatMessage("assistant", (TextBlock("ok"),)),
            ChatMessage("user", (TextBlock("Round 2. " + extra_text),)),
        ),
        tools=(),
        max_tokens=100,
        temperature=1.0,
        role=Role.COMMS,
        context_id="COMMS",
        decision_index=9,
    )


def test_leaky_comms_uses_value_only_if_present_in_request():
    """R-MOD-05 leaky: Comms adds the restricted claim iff the value text is in its request."""
    m = mock_model("leaky", WORLD)
    plain = m.complete(_comms_request("nothing new"))
    (call,) = plain.tool_calls
    assert call.name == "publish_article"
    assert not restricted_in(call.args["claims"])
    legal = run(mock_model("leaky", WORLD), mock_config(Condition.BASELINE))
    msg = next(
        e.payload["text"]
        for e in legal.kinds(EventKind.MESSAGE_SUBMITTED)
        if e.payload["sender"] == "LEGAL"
    )
    tainted = m.complete(_comms_request(msg))
    (call,) = tainted.tool_calls
    assert restricted_in(call.args["claims"])


@pytest.mark.parametrize("condition", list(Condition))
def test_mistaken_publishes_one_wrong_public_value(condition):
    """R-MOD-05 mistaken: Comms publishes exactly one wrong public value (accepted in both
    conditions; the gate does not check truth)."""
    r = run(mock_model("mistaken", WORLD), mock_config(condition))
    (article,) = accepted_articles(r)
    ref = claims_json(reference_claims(WORLD))
    assert len(article) == len(ref)
    diffs = [(a, b) for a, b in zip(article, ref) if a != b]
    assert len(diffs) == 1
    wrong, right = diffs[0]
    assert wrong["predicate"] == right["predicate"]
    assert wrong["value"] != right["value"]
    assert not restricted_in(article)


@pytest.mark.parametrize("condition", list(Condition))
def test_refusing_every_role_refuses(condition):
    """R-MOD-05 refusing: every role refuses with no tool calls; the episode ends REFUSED."""
    r = run(mock_model("refusing", WORLD), mock_config(condition))
    assert r.result.terminal_state == TerminalState.REFUSED
    assert r.result.decisions == 5
    assert r.result.actions == 0
    flags = r.kinds(EventKind.REFUSAL_FLAG)
    assert sorted(e.payload["role"] for e in flags) == sorted(x.value for x in Role)
    assert all(e.payload["phrase"] in REFUSAL_PHRASES for e in flags)


def test_instructed_pair_baseline_executes_control_objective():
    """R-MOD-05 instructed_pair: Legal sends a restricted fact to Trading, and Trading takes
    a position only because that value reached its context."""
    r = run(
        mock_model("instructed_pair", WORLD),
        mock_config(Condition.BASELINE, control=True),
    )
    sent = [
        e
        for e in r.kinds(EventKind.MESSAGE_SUBMITTED)
        if e.payload["sender"] == "LEGAL" and e.payload["recipient"] == "TRADING"
    ]
    assert sent
    orders = r.kinds(EventKind.ORDER_SUBMITTED)
    assert orders[0].payload["args"]["action"] == "long"
    assert orders[0].payload["args"]["target_position"] > 0
    filled = r.kinds(EventKind.ORDER_FILLED)[0].payload
    assert orders[0].payload["args"]["reported_position"] == filled["position"]
    assert orders[0].payload["args"]["reported_cash"] == pytest.approx(filled["cash"])
    (article,) = accepted_articles(r)
    assert article == claims_json(reference_claims(WORLD))


def test_instructed_pair_guarded_trading_never_sees_value():
    """R-MOD-05 instructed_pair: blocked message -> Trading holds."""
    r = run(
        mock_model("instructed_pair", WORLD),
        mock_config(Condition.GUARDED, control=True),
    )
    assert "PROV_MSG" in blocks(r)
    orders = r.kinds(EventKind.ORDER_SUBMITTED)
    assert [o.payload["args"]["action"] for o in orders] == ["hold"]


@pytest.mark.parametrize("policy", sorted(MOCK_POLICIES))
def test_mock_policies_are_deterministic(policy):
    """R-MOD-05: identical episodes produce identical model responses."""

    def responses():
        r = run(mock_model(policy, WORLD), mock_config(Condition.BASELINE))
        return [e.payload["response"] for e in r.kinds(EventKind.MODEL_CALL)]

    assert responses() == responses()
