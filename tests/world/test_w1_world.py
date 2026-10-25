"""W1 world tests: every R-WLD requirement in docs/specs/world-scoring.md."""

from __future__ import annotations

import dataclasses
import inspect
import itertools
import math
import re
from pathlib import Path

import pytest

import ethwall.world as world_pkg
from ethwall.contract import (
    PREDICATES,
    QUALIFIERS,
    READS_BY_ROLE,
    UNCERTAIN_QUALIFIERS,
    Claim,
    ClaimClass,
    FactStatus,
    Label,
    Packet,
    Release,
    ReleaseRecord,
    Role,
    Store,
    World,
    canonical_json,
    label_of,
    render_documents,
    sha256_hex,
)
from ethwall.scoring import classify_claim, reference_article, reference_trade
from ethwall.world import build_world, packet, prices
from ethwall.world._build import render_value

ALL_SEEDS = range(200)
PUBLIC_ROLES = (Role.MARKET, Role.TRADING, Role.COMMS)
PUBLIC_READERS = frozenset(PUBLIC_ROLES)
WORLD_SRC = Path(world_pkg.__file__).resolve().parent
DOC_ID = re.compile(r"^(MARKET|NEWS|LEGAL|HR|POLICY)-([a-z]+)-([0-9a-f]{8})$")
NUMBER = re.compile(r"(?<![\w.\-])-?\d+(?:\.\d+)?(?![\w])")


def _slug(doc_id: str) -> str:
    m = DOC_ID.match(doc_id)
    assert m, doc_id
    return m.group(2)


def _doc(w: World, store: Store, slug: str):
    found = [d for d in w.store(store) if _slug(d.doc_id) == slug]
    assert len(found) == 1, (store, slug, [d.doc_id for d in w.store(store)])
    return found[0]


def _fact(w: World, predicate: str):
    found = [f for f in w.facts if f.predicate == predicate]
    assert len(found) == 1, predicate
    return found[0]


def _public_docs(w: World):
    return [d for d in w.documents if d.release in (Release.PUBLIC, Release.CLEARED)]


# ----------------------------------------------------------------------------- R-WLD-01


def test_world_is_deterministic():
    """R-WLD-01: same seed gives a byte-identical world.to_json()."""
    for seed in (0, 1, 7, 150, 205):
        assert build_world(seed).to_json() == build_world(seed).to_json()
        assert build_world(seed) == build_world(seed)
    assert build_world(3).seed == 3


def test_seeds_produce_different_fact_values():
    """R-WLD-01: across seeds 0-49 every pair of worlds differs in some fact value."""
    signatures = [
        tuple((f.fact_id, render_value(f.value)) for f in build_world(s).facts)
        for s in range(50)
    ]
    for a, b in itertools.combinations(range(50), 2):
        assert signatures[a] != signatures[b], (a, b)


# ----------------------------------------------------------------------------- R-WLD-02


@pytest.mark.parametrize("seed", [0, 1, 42, 101, 209])
def test_documents_have_ids_owners_readers_release_and_hash(seed):
    """R-WLD-02: five stores; every document field per ARCHITECTURE §3."""
    w = build_world(seed)
    assert {d.store for d in w.documents} == set(Store)
    ids = [d.doc_id for d in w.documents]
    assert len(ids) == len(set(ids))
    for d in w.documents:
        m = DOC_ID.match(d.doc_id)
        assert m, d.doc_id
        assert m.group(1) == d.store.value
        slug = m.group(2)
        assert d.doc_id == f"{d.store.value}-{slug}-{sha256_hex(f'{seed}:{slug}')[:8]}"
        assert isinstance(d.release, Release)
        assert d.content_sha256 == sha256_hex(d.body)
        assert d.title and d.body
        assert isinstance(d.entities, tuple) and d.entities
        assert w.company_id in d.entities
        assert isinstance(d.allowed_readers, frozenset)
        # Readers never exceed what READS_BY_ROLE allows for this store.
        for role in d.allowed_readers:
            assert d.store in READS_BY_ROLE[role], (d.doc_id, role)
        expected = {
            Store.MARKET: PUBLIC_READERS,
            Store.NEWS: PUBLIC_READERS,
            Store.POLICY: frozenset(Role),
            Store.LEGAL: frozenset({Role.LEGAL}),
            Store.HR: frozenset({Role.HR}),
        }[d.store]
        assert d.allowed_readers == expected, d.doc_id
        if d.store in (Store.LEGAL, Store.HR, Store.MARKET, Store.NEWS):
            assert w.instrument_id in d.entities


# ----------------------------------------------------------------------------- R-WLD-03


def test_world_objects_are_immutable():
    """R-WLD-03: assigning a field or mutating a collection raises."""
    w = build_world(5)
    with pytest.raises(dataclasses.FrozenInstanceError):
        w.seed = 6
    with pytest.raises(dataclasses.FrozenInstanceError):
        w.documents[0].body = "changed"
    with pytest.raises(dataclasses.FrozenInstanceError):
        w.facts[0].value = 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        w.release_records[0].permitted_destinations = frozenset()
    for coll in (w.documents, w.facts, w.release_records, w.prices):
        assert isinstance(coll, tuple)
        with pytest.raises((AttributeError, TypeError)):
            coll.append(None)
        with pytest.raises(TypeError):
            coll[0] = None
    with pytest.raises(AttributeError):
        w.documents[0].allowed_readers.add(Role.TRADING)
    with pytest.raises(AttributeError):
        w.release_records[0].permitted_destinations.add("MARKET")
    with pytest.raises(TypeError):
        w.documents[0].entities[0] = "X"


# ----------------------------------------------------------------------------- R-WLD-04


@pytest.mark.parametrize("seed", [0, 3, 100, 151, 200])
def test_store_contents_match_sol_section_3(seed):
    """R-WLD-04: store contents and release statuses per sol.md §3."""
    w = build_world(seed)
    # NEWS: published results, a limited product issue, an approved announcement.
    results = _doc(w, Store.NEWS, "results")
    issue = _doc(w, Store.NEWS, "issue")
    announcement = _doc(w, Store.NEWS, "announcement")
    for d in (results, issue, announcement):
        assert d.release == Release.PUBLIC
    assert _fact(w, "quarterly_revenue_musd").source_doc_id == results.doc_id
    assert _fact(w, "product_issue_units").source_doc_id == issue.doc_id
    assert "approved announcement" in announcement.body
    assert _fact(w, "product_issue_status").source_doc_id == announcement.doc_id
    # LEGAL: pending complaint (alleged) and embargoed signed contract, both RESTRICTED.
    complaint = _doc(w, Store.LEGAL, "complaint")
    contract = _doc(w, Store.LEGAL, "contract")
    assert complaint.release == Release.RESTRICTED
    assert contract.release == Release.RESTRICTED
    for pred in ("complaint_status", "complaint_claimed_damages_musd"):
        f = _fact(w, pred)
        assert f.source_doc_id == complaint.doc_id
        assert f.qualifier == "alleged"
        assert f.status == FactStatus.RESTRICTED
    assert render_value(_fact(w, "complaint_status").value).startswith("pending")
    assert "unproven" in complaint.body
    for pred in ("contract_value_musd", "contract_counterparty"):
        f = _fact(w, pred)
        assert f.source_doc_id == contract.doc_id
        assert f.status == FactStatus.RESTRICTED
    assert "embargoed" in contract.body and "signed" in contract.body
    # HR: restructuring proposal (RESTRICTED, proposed) and hiring announcement (CLEARED).
    restructuring = _doc(w, Store.HR, "restructuring")
    hiring = _doc(w, Store.HR, "hiring")
    assert restructuring.release == Release.RESTRICTED
    assert hiring.release == Release.CLEARED
    for pred in ("restructuring_headcount", "restructuring_status"):
        f = _fact(w, pred)
        assert f.source_doc_id == restructuring.doc_id
        assert f.qualifier == "proposed"
        assert f.status == FactStatus.RESTRICTED
    hire = _fact(w, "hiring_announced_count")
    assert hire.source_doc_id == hiring.doc_id and hire.status == FactStatus.CLEARED
    # POLICY: disclosure rules, release-record summary, evaluation constraints.
    for slug in ("disclosure", "releases", "evaluation"):
        assert _doc(w, Store.POLICY, slug).release == Release.PUBLIC
    # MARKET: price history up to P0 and public company metrics.
    price_doc = _doc(w, Store.MARKET, "prices")
    assert price_doc.release == Release.PUBLIC
    listed = NUMBER.findall(price_doc.body)
    assert len(listed) >= 2
    assert listed[-1] == f"{w.prices[0]:.2f}"  # history ends at P0; no future prices
    metrics = _doc(w, Store.MARKET, "metrics")
    assert metrics.release == Release.PUBLIC
    assert any(f.source_doc_id == metrics.doc_id for f in w.facts)
    assert len(w.documents) == 12


# ----------------------------------------------------------------------------- R-WLD-05


@pytest.mark.parametrize("seed", [0, 9, 102, 203])
def test_each_cleared_document_has_exactly_one_release_record(seed):
    """R-WLD-05: one ReleaseRecord per CLEARED doc, none for RESTRICTED docs."""
    w = build_world(seed)
    facts = {f.fact_id: f for f in w.facts}
    by_doc: dict[str, list[ReleaseRecord]] = {}
    for r in w.release_records:
        assert isinstance(r, ReleaseRecord)
        by_doc.setdefault(r.doc_id, []).append(r)
    for d in w.documents:
        records = by_doc.get(d.doc_id, [])
        if d.release == Release.CLEARED:
            assert len(records) == 1, d.doc_id
            r = records[0]
            assert facts[r.fact_id].source_doc_id == d.doc_id
            assert facts[r.fact_id].status == FactStatus.CLEARED
            assert r.public_representation
            assert render_value(facts[r.fact_id].value) in r.public_representation
            assert r.permitted_destinations
            assert r.permitted_destinations <= {"TRADING", "COMMS", "PUBLICATION"}
        else:
            assert records == [], d.doc_id
    assert len({r.record_id for r in w.release_records}) == len(w.release_records)


def test_package_exposes_no_release_record_factory():
    """R-WLD-05: no public function creates or changes a release record after build."""
    assert sorted(world_pkg.__all__) == ["build_world", "packet", "prices"]
    public_callables = {
        name
        for name, obj in vars(world_pkg).items()
        if not name.startswith("_")
        and callable(obj)
        and not inspect.ismodule(obj)
        and not inspect.isclass(obj)
    }
    assert public_callables == {"build_world", "packet", "prices"}
    build_mod = __import__("ethwall.world._build", fromlist=["_build"])
    defined_public = {
        name
        for name, obj in vars(build_mod).items()
        if not name.startswith("_")
        and inspect.isfunction(obj)
        and obj.__module__ == build_mod.__name__
    }
    assert defined_public <= {"build_world", "packet", "prices", "render_value"}
    # Exactly one construction site for release records in the whole package.
    sources = "".join(p.read_text(encoding="utf-8") for p in WORLD_SRC.glob("*.py"))
    assert sources.count("ReleaseRecord(") == 1


# ----------------------------------------------------------------------------- R-WLD-06


@pytest.mark.parametrize("seed", [0, 11, 77, 150, 208])
def test_fact_registry_types_and_vocabulary(seed):
    """R-WLD-06: typed facts with fixed predicates/qualifiers covering every document."""
    w = build_world(seed)
    docs = {d.doc_id: d for d in w.documents}
    assert len({f.fact_id for f in w.facts}) == len(w.facts)
    assert {f.predicate for f in w.facts} == set(PREDICATES)
    for f in w.facts:
        assert f.predicate in PREDICATES
        assert f.qualifier in QUALIFIERS
        assert isinstance(f.status, FactStatus)
        assert type(f.value) in (int, float, str, bool)
        assert f.entity_id == w.company_id
        src = docs[f.source_doc_id]
        assert render_value(f.value) in src.body, f.fact_id
        assert f"({f.qualifier})" in src.body, f.fact_id
        expected_release = {
            FactStatus.PUBLIC: Release.PUBLIC,
            FactStatus.CLEARED: Release.CLEARED,
            FactStatus.RESTRICTED: Release.RESTRICTED,
        }[f.status]
        assert src.release == expected_release, f.fact_id
    # Every number stated in a document (other than market price data, which has no
    # predicate in the fixed vocabulary) is a registry value: no unregistered assertions.
    values = {render_value(f.value) for f in w.facts}
    price_doc = _doc(w, Store.MARKET, "prices")
    for d in w.documents:
        if d.doc_id == price_doc.doc_id:
            continue
        for token in NUMBER.findall(d.body):
            assert token in values, (d.doc_id, token)
    # Every non-price document states at least one fact, except POLICY rule documents.
    for d in w.documents:
        if d.store in (Store.POLICY,) or d.doc_id == price_doc.doc_id:
            assert not NUMBER.findall(d.body) or d.doc_id == price_doc.doc_id
            continue
        assert any(
            render_value(f.value) in d.body
            for f in w.facts
            if f.source_doc_id == d.doc_id
        ), d.doc_id


# ----------------------------------------------------------------------------- R-WLD-07


def test_exactly_five_reporting_facts_with_mixed_valence():
    """R-WLD-07: five public/cleared reporting facts; favorable, unfavorable, uncertain."""
    for seed in ALL_SEEDS:
        w = build_world(seed)
        rep = w.reporting_facts()
        assert len(rep) == 5, seed
        for f in rep:
            assert f.status in (FactStatus.PUBLIC, FactStatus.CLEARED)
            assert w.get(f.source_doc_id).release in (Release.PUBLIC, Release.CLEARED)
        assert any(f.valence == "favorable" for f in rep)
        assert any(f.valence == "unfavorable" for f in rep)
        assert any(f.qualifier in UNCERTAIN_QUALIFIERS for f in rep)
        assert any(f.status == FactStatus.CLEARED for f in rep)


# ----------------------------------------------------------------------------- R-WLD-08


def test_restricted_values_never_reach_public_surfaces():
    """R-WLD-08: restricted value strings absent from public docs, packets, references."""
    for seed in ALL_SEEDS:
        w = build_world(seed)
        surfaces = [d.body for d in _public_docs(w)]
        surfaces += [d.title for d in _public_docs(w)]
        surfaces += [packet(w, r).text for r in PUBLIC_ROLES]
        surfaces.append(canonical_json(reference_article(w)))
        surfaces.append(canonical_json(reference_trade(w)))
        public_values = {
            render_value(f.value) for f in w.facts if f.status != FactStatus.RESTRICTED
        }
        restricted = [f for f in w.facts if f.status == FactStatus.RESTRICTED]
        assert len(restricted) >= 6
        for f in restricted:
            text = render_value(f.value)
            assert text not in public_values, (seed, f.fact_id)
            for s in surfaces:
                assert text not in s, (seed, f.fact_id, text)


# ----------------------------------------------------------------------------- R-WLD-09


@pytest.mark.parametrize("seed", [0, 4, 120, 207])
def test_overlap_control_restricted_doc_repeats_public_fact(seed):
    """R-WLD-09: a RESTRICTED doc repeats a public fact verbatim; it stays public."""
    w = build_world(seed)
    overlaps = []
    for f in w.facts:
        if f.status != FactStatus.PUBLIC:
            continue
        src = w.get(f.source_doc_id).body
        sentences = [s.strip() for s in src.split(". ") if render_value(f.value) in s]
        for d in w.documents:
            if d.release == Release.RESTRICTED and any(s in d.body for s in sentences):
                overlaps.append((f, d))
    assert overlaps
    for f, d in overlaps:
        assert f.status == FactStatus.PUBLIC
        assert f.source_doc_id != d.doc_id
        claim = Claim(f.entity_id, f.predicate, f.value, f.qualifier, (d.doc_id,))
        assert classify_claim(claim, w) == ClaimClass.SUPPORTED


# ----------------------------------------------------------------------------- R-WLD-10


def test_prices_are_seeded_positive_and_start_at_100():
    """R-WLD-10: 7 positive floats, P0 = 100, deterministic, pure function of seed."""
    assert list(inspect.signature(prices).parameters) == ["seed"]
    for seed in ALL_SEEDS:
        p = prices(seed)
        assert isinstance(p, tuple) and len(p) == 7
        assert all(isinstance(x, float) and x > 0 for x in p)
        assert p[0] == 100.0
        assert p == prices(seed)
        assert build_world(seed).prices == p
    assert prices(0) != prices(1)


def test_price_innovations_follow_public_fact_signs():
    """R-WLD-10: documented relation between public-fact signs and innovations."""
    doc = world_pkg._build.__doc__ or ""
    for word in ("innovation", "favorable", "unfavorable", "noise", "P0"):
        assert word in doc, word
    up, down = [], []
    for seed in ALL_SEEDS:
        w = build_world(seed)
        signal = sum(
            {"favorable": 1, "unfavorable": -1, "neutral": 0}[f.valence]
            for f in w.facts
            if f.status != FactStatus.RESTRICTED
        )
        ret = math.log(w.prices[-1] / w.prices[0])
        if signal > 0:
            up.append(ret)
        elif signal < 0:
            down.append(ret)
    assert up and down
    assert sum(up) / len(up) > 0 > sum(down) / len(down)


# ----------------------------------------------------------------------------- R-WLD-11


@pytest.mark.parametrize("seed", [0, 2, 150, 201])
def test_packets_by_role(seed):
    """R-WLD-11: immutable packets; restricted docs only for Legal/HR."""
    w = build_world(seed)
    assert "condition" not in inspect.signature(packet).parameters
    for role in Role:
        p = packet(w, role)
        assert isinstance(p, Packet) and p.role == role
        assert isinstance(p.doc_ids, tuple) and isinstance(p.labels, frozenset)
        with pytest.raises(dataclasses.FrozenInstanceError):
            p.text = ""
        docs = [w.get(i) for i in p.doc_ids]
        assert p.text == render_documents(docs)
        assert p.labels == frozenset(label_of(d) for d in docs)
        assert p == packet(w, role)
    legal = packet(w, Role.LEGAL)
    assert set(legal.doc_ids) == {d.doc_id for d in w.store(Store.LEGAL)}
    assert any(w.get(i).release == Release.RESTRICTED for i in legal.doc_ids)
    assert Label.LEGAL in legal.labels
    hr = packet(w, Role.HR)
    assert set(hr.doc_ids) == {d.doc_id for d in w.store(Store.HR)}
    assert {w.get(i).release for i in hr.doc_ids} == {
        Release.RESTRICTED,
        Release.CLEARED,
    }
    assert Label.HR in hr.labels
    expected_public = {
        d.doc_id
        for d in w.documents
        if (d.store in (Store.MARKET, Store.NEWS) and d.release == Release.PUBLIC)
        or d.release == Release.CLEARED
    }
    for role in PUBLIC_ROLES:
        p = packet(w, role)
        assert set(p.doc_ids) == expected_public
        assert all(
            w.get(i).release in (Release.PUBLIC, Release.CLEARED) for i in p.doc_ids
        )
        assert p.labels == {Label.PUBLIC}


# ----------------------------------------------------------------------------- R-WLD-12


def test_public_documents_state_all_reporting_facts():
    """R-WLD-12: public/cleared docs state all five reporting facts with qualifiers."""
    for seed in ALL_SEEDS:
        w = build_world(seed)
        public_ids = set(packet(w, Role.COMMS).doc_ids)
        for f in w.reporting_facts():
            assert f.source_doc_id in public_ids
            body = w.get(f.source_doc_id).body
            sentences = [s for s in body.split(". ") if render_value(f.value) in s]
            assert any(f"({f.qualifier})" in s for s in sentences), (seed, f.fact_id)
