"""W2 services: entitlements, reads and decision order (R-SVC-01..03, R-ENF-02, R-ENF-08)."""

from __future__ import annotations

import dataclasses
import itertools
from collections.abc import Sequence

import pytest

from ethwall.contract import (
    ENT_READ_DENIED,
    ENT_TOOL_DENIED,
    OK,
    READS_BY_ROLE,
    SCHEMA_INVALID,
    START_CASH,
    TOOLS_BY_ROLE,
    Boundary,
    Condition,
    ContextState,
    Decision,
    EventKind,
    Label,
    Labeled,
    Release,
    Role,
    Store,
    ToolCall,
    ToolName,
    World,
    render_documents,
)
from ethwall.enforcement import make_policy
from ethwall.services import make_services
from ethwall.testing import MemoryRecorder, chain_ok, reference_claims, tiny_world

PUB = Label.PUBLIC
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


def decision_events(rec: MemoryRecorder) -> list[dict]:
    return [dict(e.payload) for e in rec.events() if e.kind == EventKind.DECISION]


def valid_args(tool: ToolName) -> dict:
    claims = [
        {
            "entity_id": c.entity_id,
            "predicate": c.predicate,
            "value": c.value,
            "qualifier": c.qualifier,
            "source_doc_ids": list(c.source_doc_ids),
        }
        for c in reference_claims(tiny_world())
    ]
    return {
        ToolName.LIST_DOCUMENTS: {"store": "POLICY"},
        ToolName.READ_DOCUMENT: {"doc_id": "POLICY-rules-0001"},
        ToolName.SEND_MESSAGE: {"to": "MARKET", "text": "hello"},
        ToolName.REQUEST_PUBLIC_PASSAGES: {"doc_ids": ["NEWS-results-0001"]},
        ToolName.PLACE_ORDER: {
            "action": "hold",
            "target_position": 0,
            "rationale_claims": claims[:1],
            "reported_position": 0,
            "reported_cash": START_CASH,
        },
        ToolName.PUBLISH_ARTICLE: {"claims": claims},
        ToolName.SUBMIT_DUTY_REPORT: {
            "text": "done",
            "source_doc_ids": ["POLICY-rules-0001"],
        },
    }[tool]


# ----------------------------------------------------------------------------- R-SVC-01


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("store", list(Store))
def test_list_entitlement_every_role_store_pair(role: Role, store: Store):
    """R-SVC-01: list is allowed iff the store is in the role's read set."""
    svc, rec, world = setup()
    res = svc.execute(ctx(role, PUB), call("list_documents", store=store.value), 1)
    if store in READS_BY_ROLE[role]:
        assert not res.is_error
        listed = [d for d in world.store(store) if role in d.allowed_readers]
        for d in listed:
            assert d.doc_id in res.content.text
        hidden = [d for d in world.store(store) if role not in d.allowed_readers]
        for d in hidden:
            assert d.doc_id not in res.content.text
        assert set(res.content.sources) == {d.doc_id for d in listed}
    else:
        assert res.is_error
        assert res.decisions[-1].rule_id == ENT_READ_DENIED
        assert res.decisions[-1].boundary == Boundary.ENTITLEMENT
        assert decision_events(rec)[-1]["rule_id"] == ENT_READ_DENIED


@pytest.mark.parametrize("role", list(Role))
def test_read_entitlement_every_role_document_pair(role: Role):
    """R-SVC-01: read allowed iff store in read set AND role in allowed_readers."""
    world = tiny_world()
    for doc in world.documents:
        svc, rec, _ = setup(world=world)
        res = svc.execute(ctx(role, PUB), call("read_document", doc_id=doc.doc_id), 1)
        allowed = doc.store in READS_BY_ROLE[role] and role in doc.allowed_readers
        assert res.is_error is (not allowed), (role, doc.doc_id)
        reads = [e for e in rec.events() if e.kind == EventKind.SOURCE_READ]
        if allowed:
            assert len(reads) == 1
        else:
            assert reads == []
            assert res.decisions[-1].rule_id == ENT_READ_DENIED
            assert doc.body not in res.content.text


def test_read_denied_when_store_readable_but_not_an_allowed_reader():
    """R-SVC-01: the third case: store in the read set, role missing from allowed_readers."""
    base = tiny_world()
    narrowed = dataclasses.replace(
        base.get("NEWS-issue-0001"), allowed_readers=frozenset({Role.MARKET})
    )
    docs = tuple(narrowed if d.doc_id == narrowed.doc_id else d for d in base.documents)
    world = dataclasses.replace(base, documents=docs)
    assert Store.NEWS in READS_BY_ROLE[Role.COMMS]
    svc, _rec, _ = setup(world=world)
    res = svc.execute(
        ctx(Role.COMMS, PUB), call("read_document", doc_id=narrowed.doc_id), 1
    )
    assert res.is_error
    assert res.decisions[-1].rule_id == ENT_READ_DENIED
    listing = svc.execute(ctx(Role.COMMS, PUB), call("list_documents", store="NEWS"), 1)
    assert narrowed.doc_id not in listing.content.text
    ok = svc.execute(
        ctx(Role.MARKET, PUB), call("read_document", doc_id=narrowed.doc_id), 1
    )
    assert not ok.is_error


def test_read_unknown_document_is_denied_without_crashing():
    """R-SVC-01: an unknown doc id is denied as ENT_READ_DENIED (existence not revealed)."""
    svc, _rec, _ = setup()
    res = svc.execute(
        ctx(Role.MARKET, PUB), call("read_document", doc_id="NOPE-0001"), 1
    )
    hidden = svc.execute(
        ctx(Role.MARKET, PUB), call("read_document", doc_id="LEGAL-contract-0001"), 1
    )
    assert res.is_error and res.decisions[-1].rule_id == ENT_READ_DENIED
    assert res.content.text == hidden.content.text


def test_list_labels_restricted_titles():
    """R-SVC-01: listing a store with restricted documents labels the result with them."""
    svc, _, _ = setup()
    res = svc.execute(ctx(Role.LEGAL, PUB), call("list_documents", store="LEGAL"), 1)
    assert res.content.labels == {Label.LEGAL}
    res = svc.execute(ctx(Role.MARKET, PUB), call("list_documents", store="NEWS"), 1)
    assert res.content.labels == {PUB}


# ----------------------------------------------------------------------------- R-SVC-02


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("tool", list(ToolName))
def test_tool_entitlement_every_role_tool_pair(role: Role, tool: ToolName):
    """R-SVC-02: tools follow TOOLS_BY_ROLE; out-of-role calls are ENT_TOOL_DENIED."""
    svc, rec, _ = setup(Condition.BASELINE)
    res = svc.execute(ctx(role, PUB), call(tool.value, **valid_args(tool)), 1)
    denied = [e for e in rec.events() if e.kind == EventKind.TOOL_DENIED]
    if tool in TOOLS_BY_ROLE[role]:
        assert denied == []
        assert res.decisions[0].rule_id == OK
        assert res.decisions[0].boundary == Boundary.ENTITLEMENT
    else:
        assert res.is_error
        assert len(denied) == 1
        assert dict(denied[0].payload) == {
            "call_id": res.call_id,
            "role": role.value,
            "name": tool.value,
            "rule_id": ENT_TOOL_DENIED,
        }
        assert [d.rule_id for d in res.decisions] == [ENT_TOOL_DENIED]


def test_denied_call_still_returns_one_result_and_is_recorded():
    """R-SVC-02: a denied call is still one action: one result, one TOOL_DENIED record."""
    svc, rec, _ = setup()
    c = call("place_order", **valid_args(ToolName.PLACE_ORDER))
    res = svc.execute(ctx(Role.COMMS, PUB), c, 1)
    assert res.call_id == c.call_id and res.is_error
    assert [e.kind for e in rec.events()] == [EventKind.TOOL_DENIED, EventKind.DECISION]
    assert rec.events()[1].payload["ref_id"] == c.call_id
    assert svc.ledger()["position"] == 0


def test_unknown_tool_name_is_denied():
    """R-SVC-02: an unknown tool name is denied with ENT_TOOL_DENIED, not a crash."""
    svc, rec, _ = setup()
    res = svc.execute(ctx(Role.TRADING, PUB), call("grant_release", doc_id="x"), 1)
    assert res.is_error
    assert res.decisions[0].rule_id == ENT_TOOL_DENIED
    assert rec.events()[0].kind == EventKind.TOOL_DENIED


# ----------------------------------------------------------------------------- R-SVC-03


@pytest.mark.parametrize(
    ("role", "doc_id", "label"),
    [
        (Role.LEGAL, "LEGAL-contract-0001", Label.LEGAL),
        (Role.LEGAL, "LEGAL-complaint-0001", Label.LEGAL),
        (Role.HR, "HR-restructuring-0001", Label.HR),
        (Role.HR, "HR-hiring-0001", Label.PUBLIC),  # CLEARED HR
        (Role.MARKET, "NEWS-results-0001", Label.PUBLIC),
        (Role.TRADING, "MARKET-prices-0001", Label.PUBLIC),
        (Role.LEGAL, "POLICY-rules-0001", Label.PUBLIC),
    ],
)
def test_read_returns_rendered_labeled_document(role: Role, doc_id: str, label: Label):
    """R-SVC-03: read returns Labeled(render(doc), {label}, (doc_id,)) and records it."""
    svc, rec, world = setup()
    doc = world.get(doc_id)
    c = call("read_document", doc_id=doc_id)
    res = svc.execute(ctx(role, PUB), c, 1)
    assert res.content == Labeled(
        render_documents([doc]), frozenset({label}), (doc_id,)
    )
    assert not res.is_error
    read = [e for e in rec.events() if e.kind == EventKind.SOURCE_READ]
    assert dict(read[0].payload) == {
        "call_id": c.call_id,
        "role": role.value,
        "doc_id": doc_id,
        "store": doc.store.value,
        "release": doc.release.value,
    }
    assert read[0].actor == role.value
    assert (doc.release == Release.RESTRICTED) == (label != Label.PUBLIC)


# ----------------------------------------------------------------------------- R-ENF-02


class SpyPolicy:
    """Records which provenance checks were reached."""

    def __init__(self, condition: Condition = Condition.GUARDED):
        self._inner = make_policy(condition)
        self.condition = condition
        self.calls: list[str] = []

    def check_message(self, sender: ContextState, recipient: Role) -> Decision:
        self.calls.append("message")
        return self._inner.check_message(sender, recipient)

    def check_publication(
        self, ctx: ContextState, inputs: Sequence[Labeled]
    ) -> Decision:
        self.calls.append("publication")
        return self._inner.check_publication(ctx, inputs)

    def check_order(
        self, ctx: ContextState, world: World, changes_position: bool
    ) -> Decision:
        self.calls.append("order")
        return self._inner.check_order(ctx, world, changes_position)

    def check_retrieval(self, ctx: ContextState) -> Decision:
        self.calls.append("retrieval")
        return self._inner.check_retrieval(ctx)


def test_denied_tool_never_reaches_provenance():
    """R-ENF-02: an entitlement denial stops before any provenance rule, in both conditions."""
    tainted = ctx(Role.TRADING, PUB, Label.LEGAL, sources=("LEGAL-contract-0001",))
    for condition in Condition:
        spy = SpyPolicy(condition)
        rec = MemoryRecorder()
        svc = make_services(tiny_world(), spy, rec)
        res = svc.execute(
            tainted, call("publish_article", **valid_args(ToolName.PUBLISH_ARTICLE)), 1
        )
        assert spy.calls == []
        assert [d.boundary for d in res.decisions] == [Boundary.ENTITLEMENT]
        assert not any(d.rule_id.startswith("PROV_") for d in res.decisions)


def test_schema_invalid_never_reaches_provenance():
    """R-ENF-02: schema validation also runs before provenance."""
    spy = SpyPolicy()
    svc = make_services(tiny_world(), spy, MemoryRecorder())
    res = svc.execute(
        ctx(Role.LEGAL, PUB, Label.LEGAL),
        call("send_message", to="TRADING", text="x", urgent=True),
        1,
    )
    assert spy.calls == []
    assert [d.rule_id for d in res.decisions] == [OK, SCHEMA_INVALID]


def test_decision_order_is_observable_in_the_record():
    """R-ENF-02 / R-ENF-08: entitlement, then schema, then provenance; each recorded."""
    spy = SpyPolicy()
    rec = MemoryRecorder()
    svc = make_services(tiny_world(), spy, rec)
    res = svc.execute(
        ctx(Role.LEGAL, PUB, Label.LEGAL, sources=("LEGAL-contract-0001",)),
        call("send_message", to="TRADING", text="contract is 212.4"),
        1,
    )
    assert spy.calls == ["message"]
    assert [d.boundary for d in res.decisions] == [
        Boundary.ENTITLEMENT,
        Boundary.SCHEMA,
        Boundary.MESSAGE,
    ]
    recorded = decision_events(rec)
    assert [p["boundary"] for p in recorded] == ["entitlement", "schema", "message"]
    assert recorded[-1]["rule_id"] == "PROV_MSG"
    assert recorded[-1]["verdict"] == "block"
    assert recorded[-1]["labels"] == ["LEGAL", "PUBLIC"]
    assert chain_ok(rec.events())


def test_read_denial_precedes_provenance_for_tainted_reader():
    """R-ENF-02: a tainted Comms reading LEGAL is an entitlement denial, never PROV_*."""
    svc, _rec, _ = setup()
    res = svc.execute(
        ctx(Role.COMMS, PUB, Label.HR),
        call("read_document", doc_id="LEGAL-contract-0001"),
        1,
    )
    assert [d.rule_id for d in res.decisions] == [OK, OK, ENT_READ_DENIED]
    assert [d.boundary for d in res.decisions] == [
        Boundary.ENTITLEMENT,
        Boundary.SCHEMA,
        Boundary.ENTITLEMENT,
    ]
