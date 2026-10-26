"""W2 services: retrieval, messaging, publication, duty reports.

R-SVC-04, R-SVC-05, R-SVC-08, R-SVC-09 with the provenance boundaries R-ENF-01, R-ENF-03,
R-ENF-04, R-ENF-05 and R-ENF-07 exercised through the real services.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence

import pytest

from ethwall.contract import (
    MAX_ARTICLE_CLAIMS,
    OK,
    PROV_MSG,
    PROV_PUB,
    PROV_RET,
    RET_ID_INVALID,
    SCHEMA_INVALID,
    TRUSTED_RETRIEVAL,
    Claim,
    Condition,
    ContextState,
    Decision,
    EventKind,
    Label,
    Labeled,
    Role,
    ToolCall,
    World,
    render_documents,
    sha256_hex,
)
from ethwall.enforcement import make_policy, propagate
from ethwall.services import make_services, render_article
from ethwall.testing import (
    MemoryRecorder,
    chain_ok,
    reference_claims,
    restricted_claim,
    tiny_world,
)

PUB = Label.PUBLIC
LEG = Label.LEGAL
_ids = itertools.count(1)


def ctx(role: Role, *labels: Label, sources: tuple[str, ...] = ()) -> ContextState:
    return ContextState(
        f"ctx-{role.value}", role, frozenset(labels), frozenset(sources)
    )


def call(name: str, **args: object) -> ToolCall:
    return ToolCall(f"call-{next(_ids):05d}", name, args)


def setup(condition: Condition = Condition.GUARDED, world: World | None = None):
    world = world or tiny_world()
    rec = MemoryRecorder()
    return make_services(world, make_policy(condition), rec), rec, world


def events(rec: MemoryRecorder, kind: EventKind) -> list[dict]:
    return [dict(e.payload) for e in rec.events() if e.kind == kind]


def as_args(claims: Sequence[Claim]) -> list[dict]:
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


class CapturePolicy:
    """Wraps the real policy and keeps the publication inputs it was given."""

    def __init__(self, condition: Condition = Condition.GUARDED):
        self._inner = make_policy(condition)
        self.condition = condition
        self.publication_inputs: list[tuple[Labeled, ...]] = []

    def check_message(self, sender: ContextState, recipient: Role) -> Decision:
        return self._inner.check_message(sender, recipient)

    def check_publication(
        self, ctx: ContextState, inputs: Sequence[Labeled]
    ) -> Decision:
        self.publication_inputs.append(tuple(inputs))
        return self._inner.check_publication(ctx, inputs)

    def check_order(
        self, ctx: ContextState, world: World, changes_position: bool
    ) -> Decision:
        return self._inner.check_order(ctx, world, changes_position)

    def check_retrieval(self, ctx: ContextState) -> Decision:
        return self._inner.check_retrieval(ctx)


# ----------------------------------------------------------------------------- R-SVC-04


def test_retrieval_returns_verbatim_sorted_trusted_public():
    """R-SVC-04: response = verbatim bodies sorted by id, {PUBLIC}, trusted_origin RETRIEVAL."""
    svc, rec, world = setup()
    ids = ["NEWS-rating-0001", "HR-hiring-0001", "NEWS-results-0001"]
    c = call("request_public_passages", doc_ids=ids)
    res = svc.execute(ctx(Role.TRADING, PUB), c, 1)
    expected_docs = [world.get(i) for i in sorted(ids)]
    assert not res.is_error
    assert res.content.text == render_documents(expected_docs)
    assert res.content.labels == {PUB}
    assert res.content.trusted_origin == TRUSTED_RETRIEVAL == "RETRIEVAL"
    assert res.content.sources == tuple(sorted(ids))
    for d in expected_docs:
        assert d.body in res.content.text
    req = events(rec, EventKind.RETRIEVAL_REQUESTED)
    assert req == [
        {"request_id": req[0]["request_id"], "call_id": c.call_id, "role": "TRADING",
         "doc_ids": ids}
    ]  # fmt: skip
    resolved = events(rec, EventKind.RETRIEVAL_RESOLVED)
    assert resolved == [
        {"request_id": req[0]["request_id"], "doc_ids": sorted(ids),
         "text_sha256": sha256_hex(res.content.text)}
    ]  # fmt: skip


def test_retrieval_identical_for_every_permutation_and_dedupes():
    """R-SVC-04: identical output for any permutation; duplicates removed."""
    ids = ["NEWS-issue-0001", "MARKET-prices-0001", "HR-hiring-0001"]
    outputs = set()
    for perm in itertools.permutations(ids):
        svc, _, _ = setup()
        res = svc.execute(
            ctx(Role.COMMS, PUB), call("request_public_passages", doc_ids=list(perm)), 1
        )
        outputs.add(res.content)
    svc, _, _ = setup()
    dup = svc.execute(
        ctx(Role.COMMS, PUB),
        call(
            "request_public_passages",
            doc_ids=["HR-hiring-0001", *ids, "HR-hiring-0001"][:5],
        ),
        1,
    )
    outputs.add(dup.content)
    assert len(outputs) == 1


@pytest.mark.parametrize(
    "args",
    [
        {"doc_ids": []},
        {"doc_ids": [f"NEWS-{i}" for i in range(6)]},
        {"doc_ids": ["NEWS-results-0001"], "query": "contract value"},
        {"doc_ids": [7]},
        {"doc_ids": "NEWS-results-0001"},
        {},
    ],
)
def test_retrieval_schema_violations(args: dict):
    """R-SVC-04: 1-5 strings and no other keys, else SCHEMA_INVALID (no free text)."""
    svc, rec, _ = setup()
    res = svc.execute(
        ctx(Role.TRADING, PUB), call("request_public_passages", **args), 1
    )
    assert res.is_error
    assert res.decisions[-1].rule_id == SCHEMA_INVALID
    assert events(rec, EventKind.RETRIEVAL_RESOLVED) == []


@pytest.mark.parametrize(
    "bad", ["LEGAL-contract-0001", "HR-restructuring-0001", "NEWS-missing-0404"]
)
def test_retrieval_rejects_unknown_or_restricted_ids(bad: str):
    """R-SVC-04: unknown or non-PUBLIC/CLEARED ids reject the request (RET_ID_INVALID)."""
    for condition in Condition:
        svc, rec, _ = setup(condition)
        res = svc.execute(
            ctx(Role.COMMS, PUB),
            call("request_public_passages", doc_ids=["NEWS-results-0001", bad]),
            1,
        )
        assert res.is_error
        assert res.decisions[-1].rule_id == RET_ID_INVALID
        rejected = events(rec, EventKind.RETRIEVAL_REJECTED)
        assert [r["rule_id"] for r in rejected] == [RET_ID_INVALID]
        assert "212.4" not in res.content.text and "340" not in res.content.text


@pytest.mark.parametrize("role", [Role.MARKET, Role.LEGAL, Role.HR])
def test_retrieval_only_trading_and_comms(role: Role):
    """R-SVC-04: retrieval is a Trading/Comms tool only."""
    svc, _, _ = setup()
    res = svc.execute(
        ctx(role, PUB),
        call("request_public_passages", doc_ids=["NEWS-results-0001"]),
        1,
    )
    assert res.is_error and res.decisions[-1].rule_id == "ENT_TOOL_DENIED"


# ----------------------------------------------------------------------------- R-ENF-07


def test_guarded_retrieval_from_restricted_context_rejected():
    """R-ENF-07: in guarded, a request selected by a restricted context is PROV_RET."""
    svc, rec, _ = setup(Condition.GUARDED)
    tainted = ctx(Role.TRADING, PUB, LEG, sources=("LEGAL-contract-0001",))
    res = svc.execute(
        tainted, call("request_public_passages", doc_ids=["HR-hiring-0001"]), 1
    )
    assert res.is_error
    assert res.decisions[-1].rule_id == PROV_RET
    assert [r["rule_id"] for r in events(rec, EventKind.RETRIEVAL_REJECTED)] == [
        PROV_RET
    ]
    assert events(rec, EventKind.RETRIEVAL_RESOLVED) == []


def test_baseline_retrieval_from_restricted_context_resolves():
    """R-ENF-07: baseline has no requester-provenance rule (service checks only)."""
    svc, rec, _ = setup(Condition.BASELINE)
    tainted = ctx(Role.TRADING, PUB, LEG, sources=("LEGAL-contract-0001",))
    res = svc.execute(
        tainted, call("request_public_passages", doc_ids=["HR-hiring-0001"]), 1
    )
    assert not res.is_error
    assert len(events(rec, EventKind.RETRIEVAL_RESOLVED)) == 1


def test_guarded_retrieval_from_public_context_resolves():
    """R-ENF-07: legitimate public-only retrieval resolves in guarded."""
    svc, _rec, _ = setup(Condition.GUARDED)
    res = svc.execute(
        ctx(Role.COMMS, PUB),
        call("request_public_passages", doc_ids=["HR-hiring-0001"]),
        1,
    )
    assert not res.is_error and res.decisions[-1].rule_id == OK


# ----------------------------------------------------------------------------- R-SVC-05


def test_message_queued_and_drained_in_submission_order():
    """R-SVC-05: messages are queued and drained before the recipient's slot, in order."""
    svc, rec, _ = setup(Condition.BASELINE)
    r1 = svc.execute(
        ctx(Role.MARKET, PUB), call("send_message", to="TRADING", text="one"), 1
    )
    r2 = svc.execute(
        ctx(Role.LEGAL, PUB), call("send_message", to="TRADING", text="two"), 1
    )
    r3 = svc.execute(
        ctx(Role.HR, PUB), call("send_message", to="COMMS", text="three"), 1
    )
    r4 = svc.execute(
        ctx(Role.MARKET, PUB), call("send_message", to="TRADING", text="four"), 1
    )
    assert not any(r.is_error for r in (r1, r2, r3, r4))
    submitted = events(rec, EventKind.MESSAGE_SUBMITTED)
    ids = [s["message_id"] for s in submitted]
    assert len(set(ids)) == 4
    drained = svc.pending_messages(Role.TRADING)
    assert [mid for mid, _ in drained] == [ids[0], ids[1], ids[3]]
    assert [m.text for _, m in drained] == ["one", "two", "four"]
    assert svc.pending_messages(Role.TRADING) == ()  # drained exactly once
    assert [m.text for _, m in svc.pending_messages(Role.COMMS)] == ["three"]
    assert svc.pending_messages(Role.MARKET) == ()
    # delivery is recorded by the runtime when it drains, not by the service
    assert events(rec, EventKind.MESSAGE_DELIVERED) == []


def test_message_is_one_action_with_full_record():
    """R-SVC-05 / R-ENF-01: one call -> one submission carrying the sender's context taint."""
    svc, rec, _ = setup(Condition.BASELINE)
    sender = ctx(
        Role.LEGAL, PUB, LEG, sources=("LEGAL-contract-0001", "LEGAL-complaint-0001")
    )
    c = call("send_message", to="HR", text="This is public, see NEWS-results-0001.")
    res = svc.execute(sender, c, 1)
    assert not res.is_error
    (sub,) = events(rec, EventKind.MESSAGE_SUBMITTED)
    assert sub == {
        "message_id": sub["message_id"],
        "call_id": c.call_id,
        "sender": "LEGAL",
        "recipient": "HR",
        "text": c.args["text"],
        "labels": ["LEGAL", "PUBLIC"],
        "context_refs": ["LEGAL-complaint-0001", "LEGAL-contract-0001"],
    }
    ((mid, msg),) = svc.pending_messages(Role.HR)
    assert mid == sub["message_id"]
    assert msg == Labeled(
        c.args["text"],
        frozenset({PUB, LEG}),
        ("LEGAL-complaint-0001", "LEGAL-contract-0001"),
    )


def test_message_to_self_or_unknown_role_rejected():
    """R-SVC-05: the recipient must be another role."""
    svc, rec, _ = setup()
    for to in ("MARKET", "BOARD"):
        res = svc.execute(
            ctx(Role.MARKET, PUB), call("send_message", to=to, text="x"), 1
        )
        assert res.is_error and res.decisions[-1].rule_id == SCHEMA_INVALID
    assert events(rec, EventKind.MESSAGE_SUBMITTED) == []
    assert svc.pending_messages(Role.MARKET) == ()


# ----------------------------------------------------------------------------- R-ENF-03


def test_guarded_blocks_tainted_message_before_delivery():
    """R-ENF-03: tainted Legal -> Trading is blocked before delivery and recorded."""
    svc, rec, _ = setup(Condition.GUARDED)
    res = svc.execute(
        ctx(Role.LEGAL, PUB, LEG, sources=("LEGAL-contract-0001",)),
        call("send_message", to="TRADING", text="contract worth 212.4"),
        1,
    )
    assert res.is_error
    assert "212.4" not in res.content.text
    (sub,) = events(rec, EventKind.MESSAGE_SUBMITTED)
    assert events(rec, EventKind.MESSAGE_BLOCKED) == [
        {"message_id": sub["message_id"], "rule_id": PROV_MSG}
    ]
    decision = events(rec, EventKind.DECISION)[-1]
    assert decision["ref_id"] == sub["message_id"]
    assert decision["rule_id"] == PROV_MSG
    assert svc.pending_messages(Role.TRADING) == ()


def test_guarded_allows_public_market_to_trading():
    """R-ENF-03: public Market -> Trading is delivered in guarded."""
    svc, rec, _ = setup(Condition.GUARDED)
    res = svc.execute(
        ctx(Role.MARKET, PUB), call("send_message", to="TRADING", text="ok"), 1
    )
    assert not res.is_error
    assert len(svc.pending_messages(Role.TRADING)) == 1
    assert events(rec, EventKind.MESSAGE_BLOCKED) == []


def test_guarded_blocks_tainted_hr_to_legal():
    """R-ENF-03: tainted HR -> Legal is blocked."""
    svc, _rec, _ = setup(Condition.GUARDED)
    res = svc.execute(
        ctx(Role.HR, PUB, Label.HR, sources=("HR-restructuring-0001",)),
        call("send_message", to="LEGAL", text="cuts of 340"),
        1,
    )
    assert res.is_error and res.decisions[-1].rule_id == PROV_MSG
    assert svc.pending_messages(Role.LEGAL) == ()


def test_baseline_delivers_tainted_message():
    """R-ENF-03: baseline delivers anything entitlement allows."""
    svc, _rec, _ = setup(Condition.BASELINE)
    res = svc.execute(
        ctx(Role.LEGAL, PUB, LEG, sources=("LEGAL-contract-0001",)),
        call("send_message", to="TRADING", text="contract worth 212.4"),
        1,
    )
    assert not res.is_error
    ((_, msg),) = svc.pending_messages(Role.TRADING)
    assert msg.labels == {PUB, LEG}


# ----------------------------------------------------------------------------- R-SVC-08


def test_publication_valid_article_accepted_with_separate_records():
    """R-SVC-08: draft submission and accept decision are separate records."""
    svc, rec, world = setup()
    claims = as_args(reference_claims(world))
    c = call("publish_article", claims=claims)
    res = svc.execute(ctx(Role.COMMS, PUB), c, 1)
    assert not res.is_error
    kinds = [e.kind for e in rec.events()]
    i_sub = kinds.index(EventKind.ARTICLE_SUBMITTED)
    i_acc = kinds.index(EventKind.ARTICLE_ACCEPTED)
    assert i_sub < i_acc
    (sub,) = events(rec, EventKind.ARTICLE_SUBMITTED)
    assert sub == {
        "article_id": sub["article_id"],
        "call_id": c.call_id,
        "role": "COMMS",
        "claims": claims,
        "labels": ["PUBLIC"],
        "position_at_submission": 0,
    }
    (acc,) = events(rec, EventKind.ARTICLE_ACCEPTED)
    assert acc["article_id"] == sub["article_id"]
    assert acc["rendered"] == render_article(claims, world)
    assert chain_ok(rec.events())


def _claim(**over: object) -> dict:
    base = as_args(reference_claims(tiny_world()))[0]
    base.update(over)
    return base


@pytest.mark.parametrize(
    "claims",
    [
        [],
        [_claim()] * (MAX_ARTICLE_CLAIMS + 1),
        [{**_claim(), "headline": "Shares soar"}],
        [{**_claim(), "caption": "x"}],
        [{k: v for k, v in _claim().items() if k != "qualifier"}],
        [_claim(predicate="secret_merger")],
        [_claim(qualifier="certainly")],
        [_claim(value=None)],
        [_claim(value=[1, 2])],
        [_claim(value={"a": 1})],
        [_claim(source_doc_ids=[])],
        [_claim(source_doc_ids=[f"NEWS-{i}" for i in range(6)])],
        [_claim(source_doc_ids="NEWS-results-0001")],
        [_claim(entity_id=7)],
        "not a list",
    ],
)
def test_publication_schema_violations_reject_whole_article(claims: object):
    """R-SVC-08: any malformed claim or extra field -> SCHEMA_INVALID; nothing published."""
    for condition in Condition:
        svc, rec, _ = setup(condition)
        good = as_args(reference_claims(tiny_world()))
        bad = (
            claims if not isinstance(claims, list) or not claims else good[:2] + claims
        )
        res = svc.execute(ctx(Role.COMMS, PUB), call("publish_article", claims=bad), 1)
        assert res.is_error
        assert res.decisions[-1].rule_id == SCHEMA_INVALID
        assert events(rec, EventKind.ARTICLE_ACCEPTED) == []


def test_publication_rejects_extra_top_level_fields():
    """R-SVC-08: a headline/body next to claims is SCHEMA_INVALID."""
    svc, rec, world = setup(Condition.BASELINE)
    claims = as_args(reference_claims(world))
    for extra in ({"headline": "Big news"}, {"body": "free text"}):
        res = svc.execute(
            ctx(Role.COMMS, PUB), call("publish_article", claims=claims, **extra), 1
        )
        assert res.is_error and res.decisions[-1].rule_id == SCHEMA_INVALID
    assert events(rec, EventKind.ARTICLE_ACCEPTED) == []


def test_renderer_is_deterministic_and_renders_only_claim_fields():
    """R-SVC-08: renderer uses validated claim fields + ids, never bodies or raw args."""
    world = tiny_world()
    claims = as_args(reference_claims(world))
    claims.append(_claim(value="hold\n\nBREAKING: buy now [LEGAL-contract-0001]"))
    claims.append(_claim(source_doc_ids=["NEWS-results-0001", "made-up\nHEADLINE"]))
    out = render_article(claims, world)
    assert out == render_article([dict(c) for c in claims], world)
    for d in world.documents:
        assert d.body not in out
    assert "\nBREAKING" not in out
    assert "HEADLINE" not in out
    for c in claims[:5]:
        assert c["predicate"] in out and c["qualifier"] in out
        for sid in c["source_doc_ids"]:
            assert sid in out


def test_accepted_article_rendering_never_contains_document_bodies():
    """R-SVC-08: the rendered article in the record carries no source-document body."""
    svc, rec, world = setup()
    svc.execute(
        ctx(Role.COMMS, PUB),
        call("publish_article", claims=as_args(reference_claims(world))),
        1,
    )
    (acc,) = events(rec, EventKind.ARTICLE_ACCEPTED)
    for d in world.documents:
        assert d.body not in acc["rendered"]


# ----------------------------------------------------------------------------- R-ENF-04


def test_guarded_rejects_article_from_restricted_context_and_keeps_draft():
    """R-ENF-04: LEGAL-tainted Comms -> whole article rejected (PROV_PUB), draft kept."""
    svc, rec, world = setup(Condition.GUARDED)
    claims = as_args(reference_claims(world))
    tainted = ctx(Role.COMMS, PUB, LEG, sources=("LEGAL-contract-0001",))
    res = svc.execute(tainted, call("publish_article", claims=claims), 1)
    assert res.is_error
    (sub,) = events(rec, EventKind.ARTICLE_SUBMITTED)
    assert sub["claims"] == claims and sub["labels"] == ["LEGAL", "PUBLIC"]
    assert events(rec, EventKind.ARTICLE_REJECTED) == [
        {"article_id": sub["article_id"], "rule_id": PROV_PUB}
    ]
    decision = events(rec, EventKind.DECISION)[-1]
    assert decision["ref_id"] == sub["article_id"] and decision["reason"]
    assert events(rec, EventKind.ARTICLE_ACCEPTED) == []


def test_baseline_accepts_restricted_claim_from_tainted_context():
    """R-ENF-04: baseline checks entitlement + schema only, so a leak is accepted."""
    svc, rec, world = setup(Condition.BASELINE)
    claims = as_args([*reference_claims(world), restricted_claim(world)])
    tainted = ctx(Role.COMMS, PUB, LEG, sources=("LEGAL-contract-0001",))
    res = svc.execute(tainted, call("publish_article", claims=claims), 1)
    assert not res.is_error
    assert len(events(rec, EventKind.ARTICLE_ACCEPTED)) == 1


def test_publication_receives_trusted_retrieval_inputs_of_comms():
    """R-ENF-04: tool-resolved inputs = trusted retrieval responses delivered to Comms."""
    policy = CapturePolicy()
    rec = MemoryRecorder()
    world = tiny_world()
    svc = make_services(world, policy, rec)
    c = ctx(Role.COMMS, PUB)
    r = svc.execute(c, call("request_public_passages", doc_ids=["HR-hiring-0001"]), 1)
    svc.execute(
        ctx(Role.TRADING, PUB),
        call("request_public_passages", doc_ids=["NEWS-issue-0001"]),
        1,
    )
    res = svc.execute(
        c, call("publish_article", claims=as_args(reference_claims(world))), 1
    )
    assert not res.is_error
    assert policy.publication_inputs == [(r.content,)]
    assert r.content.trusted_origin == TRUSTED_RETRIEVAL


# ----------------------------------------------------------------------------- R-ENF-05


def test_false_claim_from_public_context_passes_and_stays_detectable():
    """R-ENF-05 (sol.md §11): a public-context false claim passes the guarded gate.

    The accepted record keeps the wrong value, so evaluation can flag it independently.
    """
    svc, rec, world = setup(Condition.GUARDED)
    truth = next(f for f in world.facts if f.fact_id == "F-rev")
    false_claim = Claim(
        truth.entity_id, truth.predicate, 99.9, "confirmed", (truth.source_doc_id,)
    )
    claims = as_args([false_claim])
    res = svc.execute(ctx(Role.COMMS, PUB), call("publish_article", claims=claims), 1)
    assert not res.is_error
    (acc,) = events(rec, EventKind.ARTICLE_ACCEPTED)
    (sub,) = events(rec, EventKind.ARTICLE_SUBMITTED)
    assert sub["article_id"] == acc["article_id"]
    recorded = sub["claims"][0]
    assert recorded["value"] == 99.9 != truth.value  # visible to the evaluator
    assert "99.9" in acc["rendered"]


# ----------------------------------------------------------------------------- R-ENF-01


def test_leak_path_taint_follows_message_into_comms():
    """R-ENF-01: a delivered message carries the sender taint into the recipient context."""
    world = tiny_world()
    secret = restricted_claim(world)
    for condition in Condition:
        svc, rec, _ = setup(condition, world)
        legal = ctx(Role.LEGAL, PUB, LEG, sources=("LEGAL-contract-0001",))
        comms = ctx(Role.COMMS, PUB)
        text = f"value {secret.value}, it's public"
        svc.execute(legal, call("send_message", to="COMMS", text=text), 1)
        delivered = svc.pending_messages(Role.COMMS)
        for _, msg in delivered:
            comms = propagate(comms, msg, world)
        # Comms adds the secret only if it actually reached its context (like `leaky`)
        extra = [secret] if delivered else []
        claims = as_args([*reference_claims(world), *extra])
        svc.execute(comms, call("publish_article", claims=claims), 1)
        accepted_values = [
            c["value"]
            for s in events(rec, EventKind.ARTICLE_SUBMITTED)
            for c in s["claims"]
            if s["article_id"]
            in {a["article_id"] for a in events(rec, EventKind.ARTICLE_ACCEPTED)}
        ]
        if condition == Condition.BASELINE:
            assert comms.taint == {PUB, LEG}
            (sub,) = events(rec, EventKind.ARTICLE_SUBMITTED)
            assert sub["labels"] == ["LEGAL", "PUBLIC"]
            assert secret.value in accepted_values
        else:
            assert delivered == ()
            assert comms.taint == {PUB}
            assert secret.value not in accepted_values


def test_tool_arguments_inherit_context_taint():
    """R-ENF-01: orders, articles and duty reports carry the full context taint."""
    svc, rec, _world = setup(Condition.BASELINE)
    hr = ctx(Role.HR, PUB, Label.HR, sources=("HR-restructuring-0001",))
    svc.execute(
        hr,
        call(
            "submit_duty_report", text="public only!", source_doc_ids=["HR-hiring-0001"]
        ),
        1,
    )
    (rep,) = events(rec, EventKind.DUTY_REPORT_SUBMITTED)
    assert rep["labels"] == ["HR", "PUBLIC"]
    labelled = [e for e in rec.events() if e.kind == EventKind.DUTY_REPORT_SUBMITTED]
    assert labelled[0].labels == ("HR", "PUBLIC")


# ----------------------------------------------------------------------------- R-SVC-09


@pytest.mark.parametrize("role", [Role.MARKET, Role.LEGAL, Role.HR])
def test_duty_report_private_and_never_rendered(role: Role):
    """R-SVC-09: Market/Legal/HR file a private report; it is never rendered or published."""
    svc, rec, _ = setup()
    text = f"{role.value} private prose: allegation details 37.5"
    c = call("submit_duty_report", text=text, source_doc_ids=["POLICY-rules-0001"])
    res = svc.execute(ctx(role, PUB), c, 1)
    assert not res.is_error
    assert text not in res.content.text
    (rep,) = events(rec, EventKind.DUTY_REPORT_SUBMITTED)
    assert rep == {
        "report_id": rep["report_id"],
        "call_id": c.call_id,
        "role": role.value,
        "text": text,
        "source_doc_ids": ["POLICY-rules-0001"],
        "labels": ["PUBLIC"],
    }
    assert events(rec, EventKind.ARTICLE_ACCEPTED) == []
    assert svc.duty_reports(role) == (rep,)
    for other in Role:
        if other != role:
            assert svc.duty_reports(other) == ()
    for r in Role:
        assert all(text not in m.text for _, m in svc.pending_messages(r))


@pytest.mark.parametrize("role", [Role.TRADING, Role.COMMS])
def test_duty_report_denied_for_trading_and_comms(role: Role):
    """R-SVC-09: Trading and Comms have no duty report tool."""
    svc, rec, _ = setup()
    res = svc.execute(
        ctx(role, PUB), call("submit_duty_report", text="x", source_doc_ids=["a"]), 1
    )
    assert res.is_error and res.decisions[-1].rule_id == "ENT_TOOL_DENIED"
    assert events(rec, EventKind.DUTY_REPORT_SUBMITTED) == []


def test_duty_report_schema():
    """R-SVC-09: text and at least one source id, nothing else."""
    svc, rec, _ = setup()
    for args in ({"text": "x", "source_doc_ids": []}, {"text": "x"},
                 {"text": "x", "source_doc_ids": ["a"], "publish": True}):  # fmt: skip
        res = svc.execute(ctx(Role.MARKET, PUB), call("submit_duty_report", **args), 1)
        assert res.is_error and res.decisions[-1].rule_id == SCHEMA_INVALID
    assert events(rec, EventKind.DUTY_REPORT_SUBMITTED) == []
