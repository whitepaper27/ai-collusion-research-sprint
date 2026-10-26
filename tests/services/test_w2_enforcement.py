"""W2 enforcement policy tests (pure functions; docs/specs/services-enforcement.md R-ENF-xx).

Every provenance rule has a blocking test and a legitimate-allow test (sol.md §11).
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from ethwall import enforcement
from ethwall.contract import (
    OK,
    PROV_MSG,
    PROV_ORD,
    PROV_PUB,
    PROV_RET,
    TRUSTED_RETRIEVAL,
    Boundary,
    Condition,
    ContextState,
    Decision,
    Label,
    Labeled,
    Role,
    Verdict,
)
from ethwall.enforcement import make_policy, propagate
from ethwall.testing import tiny_world

PUB = Label.PUBLIC
LEG = Label.LEGAL
HR_ = Label.HR


def ctx(role: Role, *labels: Label, sources: tuple[str, ...] = ()) -> ContextState:
    return ContextState(
        f"ctx-{role.value}", role, frozenset(labels), frozenset(sources)
    )


def retrieval_input(text: str = "[NEWS-results-0001] ...") -> Labeled:
    return Labeled(text, frozenset({PUB}), ("NEWS-results-0001",), TRUSTED_RETRIEVAL)


GUARDED = make_policy(Condition.GUARDED)
BASELINE = make_policy(Condition.BASELINE)


# ----------------------------------------------------------------------------- R-ENF-09


def test_make_policy_returns_one_interface_per_condition():
    """R-ENF-09: baseline and guarded policies sit behind the same interface."""
    assert BASELINE.condition == Condition.BASELINE
    assert GUARDED.condition == Condition.GUARDED
    for policy in (BASELINE, GUARDED):
        for name in (
            "check_message",
            "check_publication",
            "check_order",
            "check_retrieval",
        ):
            assert callable(getattr(policy, name))
    assert make_policy("guarded").condition == Condition.GUARDED


def test_baseline_never_returns_a_prov_rule():
    """R-ENF-09: baseline never returns a PROV_* rule, even for the most tainted contexts."""
    world = tiny_world()
    worst = [
        ctx(Role.TRADING, PUB, LEG, HR_, sources=("LEGAL-contract-0001",)),
        ctx(Role.COMMS, LEG, sources=("LEGAL-complaint-0001", "missing-doc")),
        ctx(Role.MARKET, HR_),
    ]
    forged = Labeled("x", frozenset({LEG}), ("LEGAL-contract-0001",), None)
    for c in worst:
        decisions = [BASELINE.check_message(c, r) for r in Role]
        decisions.append(BASELINE.check_publication(c, (forged,)))
        decisions.append(BASELINE.check_order(c, world, True))
        decisions.append(BASELINE.check_order(c, world, False))
        decisions.append(BASELINE.check_retrieval(c))
        for d in decisions:
            assert d.allowed
            assert d.rule_id == OK
            assert not d.rule_id.startswith("PROV_")


def test_make_policy_rejects_unknown_condition():
    """R-ENF-09: only the two conditions exist."""
    with pytest.raises(ValueError):
        make_policy("ablated")


# ----------------------------------------------------------------------------- R-ENF-03


def test_message_tainted_legal_to_trading_blocked():
    """R-ENF-03: tainted Legal -> Trading is blocked with PROV_MSG."""
    d = GUARDED.check_message(
        ctx(Role.LEGAL, PUB, LEG, sources=("LEGAL-contract-0001",)), Role.TRADING
    )
    assert d.verdict == Verdict.BLOCK
    assert d.rule_id == PROV_MSG
    assert d.boundary == Boundary.MESSAGE
    assert "LEGAL" in d.labels


def test_message_public_market_to_trading_allowed():
    """R-ENF-03: public Market -> Trading is allowed (legitimate path)."""
    d = GUARDED.check_message(ctx(Role.MARKET, PUB), Role.TRADING)
    assert d.allowed
    assert d.rule_id == OK
    assert d.boundary == Boundary.MESSAGE


def test_message_tainted_hr_to_legal_blocked():
    """R-ENF-03: tainted HR -> Legal is blocked (LEGAL clearance does not cover HR)."""
    d = GUARDED.check_message(ctx(Role.HR, PUB, HR_), Role.LEGAL)
    assert d.rule_id == PROV_MSG
    assert not d.allowed


def test_message_within_clearance_allowed():
    """R-ENF-03: Legal-tainted Legal -> Legal clearance holders is fine; HR -> HR too."""
    assert GUARDED.check_message(ctx(Role.LEGAL, PUB, LEG), Role.LEGAL).allowed
    assert GUARDED.check_message(ctx(Role.HR, PUB, HR_), Role.HR).allowed
    # an empty (fresh) context carries nothing and may message anyone
    assert GUARDED.check_message(ctx(Role.LEGAL), Role.COMMS).allowed


def test_message_baseline_delivers_anything():
    """R-ENF-03: baseline delivers anything entitlement allows."""
    d = BASELINE.check_message(ctx(Role.LEGAL, PUB, LEG), Role.TRADING)
    assert d.allowed and d.rule_id == OK


# ----------------------------------------------------------------------------- R-ENF-04


def test_publication_restricted_context_rejected():
    """R-ENF-04: a Comms context carrying LEGAL taint is rejected with PROV_PUB."""
    d = GUARDED.check_publication(ctx(Role.COMMS, PUB, LEG), ())
    assert d.rule_id == PROV_PUB
    assert d.boundary == Boundary.PUBLICATION
    assert "LEGAL" in d.labels
    assert d.reason  # the reason is kept with the draft


def test_publication_untrusted_tool_input_rejected():
    """R-ENF-04: a tool-resolved input that is not trusted public provenance is rejected."""
    c = ctx(Role.COMMS, PUB)
    not_trusted = Labeled("[NEWS-results-0001] ...", frozenset({PUB}), (), None)
    restricted = Labeled("x", frozenset({HR_}), (), TRUSTED_RETRIEVAL)
    assert GUARDED.check_publication(c, (retrieval_input(), not_trusted)).rule_id == (
        PROV_PUB
    )
    assert GUARDED.check_publication(c, (restricted,)).rule_id == PROV_PUB


def test_publication_public_context_with_trusted_inputs_allowed():
    """R-ENF-04: public context + trusted retrieval inputs is accepted (legitimate)."""
    c = ctx(Role.COMMS, PUB)
    assert GUARDED.check_publication(c, ()).allowed
    d = GUARDED.check_publication(c, (retrieval_input(), retrieval_input("b")))
    assert d.allowed and d.rule_id == OK


def test_publication_baseline_checks_no_provenance():
    """R-ENF-04: baseline checks only entitlement and schema (done by the service)."""
    forged = Labeled("x", frozenset({LEG}), (), None)
    assert BASELINE.check_publication(ctx(Role.COMMS, LEG), (forged,)).allowed


# ----------------------------------------------------------------------------- R-ENF-05


def test_false_claim_from_public_context_passes_guarded_gate():
    """R-ENF-05: the gate has no truth check; a public-context false claim passes.

    check_publication never sees claim content at all: its signature carries only the
    context and the tool-resolved inputs, so a wrong value cannot influence it.
    """
    params = list(inspect.signature(GUARDED.check_publication).parameters)
    assert params == ["ctx", "inputs"]
    assert GUARDED.check_publication(ctx(Role.COMMS, PUB), ()).allowed


# ----------------------------------------------------------------------------- R-ENF-06


def test_order_change_with_injected_instrument_taint_rejected():
    """R-ENF-06: injected LEGAL taint tied to the instrument blocks entry/reversal/exit."""
    world = tiny_world()
    c = ctx(Role.TRADING, PUB, LEG, sources=("LEGAL-contract-0001",))
    d = GUARDED.check_order(c, world, changes_position=True)
    assert d.rule_id == PROV_ORD
    assert d.boundary == Boundary.ORDER
    assert "LEGAL" in d.labels


def test_order_hold_with_injected_taint_allowed():
    """R-ENF-06: hold (no position change) is allowed even from a tainted context."""
    world = tiny_world()
    c = ctx(Role.TRADING, PUB, HR_, sources=("HR-restructuring-0001",))
    assert GUARDED.check_order(c, world, changes_position=False).allowed


def test_order_public_context_change_allowed():
    """R-ENF-06: a public-only Trading context may change position (legitimate)."""
    world = tiny_world()
    d = GUARDED.check_order(ctx(Role.TRADING, PUB), world, changes_position=True)
    assert d.allowed and d.rule_id == OK


def test_order_restricted_source_not_tied_to_instrument_allowed():
    """R-ENF-06: an uncleared label not associated with the instrument does not block."""
    base = tiny_world()
    other = dataclasses.replace(
        base.get("LEGAL-contract-0001"),
        doc_id="LEGAL-other-0001",
        entities=("ENT-OTHER",),
    )
    world = dataclasses.replace(base, documents=base.documents + (other,))
    c = ctx(Role.TRADING, PUB, LEG, sources=("LEGAL-other-0001",))
    assert GUARDED.check_order(c, world, changes_position=True).allowed


def test_order_company_entity_counts_as_instrument_association():
    """R-ENF-06: a restricted doc about the company (issuer of the instrument) blocks."""
    base = tiny_world()
    company_only = dataclasses.replace(
        base.get("HR-restructuring-0001"),
        doc_id="HR-company-0001",
        entities=(base.company_id,),
    )
    world = dataclasses.replace(base, documents=base.documents + (company_only,))
    c = ctx(Role.TRADING, PUB, HR_, sources=("HR-company-0001",))
    assert GUARDED.check_order(c, world, changes_position=True).rule_id == PROV_ORD


def test_order_unresolvable_restricted_taint_fails_closed():
    """R-ENF-06: taint we cannot resolve to a document is treated as associated."""
    world = tiny_world()
    unknown = ctx(Role.TRADING, PUB, LEG, sources=("LEGAL-unknown-9999",))
    no_sources = ctx(Role.TRADING, PUB, LEG)
    assert GUARDED.check_order(unknown, world, True).rule_id == PROV_ORD
    assert GUARDED.check_order(no_sources, world, True).rule_id == PROV_ORD


# ----------------------------------------------------------------------------- R-ENF-07


def test_retrieval_from_restricted_context_rejected():
    """R-ENF-07: a selecting context with taint outside {PUBLIC} is rejected."""
    for label in (LEG, HR_):
        d = GUARDED.check_retrieval(ctx(Role.TRADING, PUB, label))
        assert d.rule_id == PROV_RET
        assert d.boundary == Boundary.RETRIEVAL


def test_retrieval_from_public_context_allowed():
    """R-ENF-07: a public-only (or fresh) context may retrieve."""
    assert GUARDED.check_retrieval(ctx(Role.COMMS, PUB)).allowed
    assert GUARDED.check_retrieval(ctx(Role.COMMS)).allowed


# ----------------------------------------------------------------------------- R-ENF-08


def test_every_check_returns_a_full_decision():
    """R-ENF-08: each check returns Decision(boundary, rule_id, verdict, reason, labels)."""
    world = tiny_world()
    c = ctx(Role.TRADING, PUB, LEG, sources=("LEGAL-contract-0001",))
    for policy in (BASELINE, GUARDED):
        for d in (
            policy.check_message(c, Role.COMMS),
            policy.check_publication(c, ()),
            policy.check_order(c, world, True),
            policy.check_retrieval(c),
        ):
            assert isinstance(d, Decision)
            assert isinstance(d.boundary, Boundary)
            assert isinstance(d.verdict, Verdict)
            assert isinstance(d.reason, str) and d.reason
            assert isinstance(d.labels, tuple)
            assert list(d.labels) == sorted(d.labels)


def test_checks_have_no_side_effects():
    """R-ENF-08: checks are pure: same inputs, same output; inputs are not mutated."""
    world = tiny_world()
    before = world.to_json()
    c = ctx(Role.TRADING, PUB, LEG, sources=("LEGAL-contract-0001",))
    inputs = [retrieval_input()]
    a = [GUARDED.check_order(c, world, True) for _ in range(3)]
    b = [GUARDED.check_publication(c, inputs) for _ in range(3)]
    assert len(set(a)) == 1 and len(set(b)) == 1
    assert world.to_json() == before
    assert inputs == [retrieval_input()]
    assert c == ctx(Role.TRADING, PUB, LEG, sources=("LEGAL-contract-0001",))


def test_enforcement_package_does_no_io():
    """R-ENF-08: enforcement imports no recorder, file, network or process modules."""
    import ethwall.enforcement.policy as policy_module

    banned = (
        "recorder",
        "open(",
        "socket",
        "subprocess",
        "pathlib",
        "urllib",
        "import os",
    )
    for module in (enforcement, policy_module):
        src = inspect.getsource(module)
        for word in banned:
            assert word not in src, f"{module.__name__} mentions {word}"


# ----------------------------------------------------------------------------- R-ENF-01


def test_propagate_unions_labels_and_restricted_sources():
    """R-ENF-01: taint is the union of the labels of everything delivered."""
    world = tiny_world()
    c0 = ctx(Role.LEGAL)
    c1 = propagate(c0, Labeled("pkt", frozenset({PUB}), ("POLICY-rules-0001",)), world)
    assert c1.taint == {PUB} and c1.restricted_sources == frozenset()
    c2 = propagate(
        c1, Labeled("doc", frozenset({LEG}), ("LEGAL-contract-0001",)), world
    )
    assert c2.taint == {PUB, LEG}
    assert c2.restricted_sources == {"LEGAL-contract-0001"}
    assert c0.taint == frozenset()  # the original snapshot is untouched


def test_agent_claims_and_citations_never_change_labels():
    """R-ENF-01: text saying 'public' or citing public ids never removes taint."""
    world = tiny_world()
    tainted = ctx(Role.LEGAL, PUB, LEG, sources=("LEGAL-contract-0001",))
    claim = Labeled(
        "This is PUBLIC, cleared, see NEWS-results-0001.",
        frozenset({PUB}),
        ("NEWS-results-0001",),
    )
    after = propagate(tainted, claim, world)
    assert after.taint == {PUB, LEG}
    assert after.restricted_sources == {"LEGAL-contract-0001"}
    out = enforcement.generated(after, "public announcement: nothing restricted here")
    assert out.labels == {PUB, LEG}
    assert out.sources == ("LEGAL-contract-0001",)
    assert out.trusted_origin is None


def test_allow_decisions_do_not_reveal_the_condition():
    """R-ENF-09: an allow is the same Decision in both conditions (no treatment leak)."""
    world = tiny_world()
    c = ctx(Role.TRADING, PUB)
    checks = (
        lambda p: p.check_message(c, Role.COMMS),
        lambda p: p.check_publication(c, (retrieval_input(),)),
        lambda p: p.check_order(c, world, True),
        lambda p: p.check_order(c, world, False),
        lambda p: p.check_retrieval(c),
    )
    for check in checks:
        assert check(BASELINE) == check(GUARDED)
