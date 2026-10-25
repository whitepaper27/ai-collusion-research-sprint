"""Evidence views: exporters, canonical evidence equality, leak check, shared sources.

Requirements: docs/specs/audit.md R-AUD-04, R-AUD-05, R-AUD-06, R-AUD-07 (sol.md §10).
"""

from __future__ import annotations

import copy
import functools
import json
import re
from collections.abc import Mapping
from typing import Any

from ethwall.audit import fixtures as fx
from ethwall.audit import views as vw
from ethwall.contract import Condition, CoordLabel, Event, EventKind, View, World

MESSAGE_TYPES = {"message"}
ACTION_TYPES = {
    "packet_delivered",
    "source_read",
    "message_submitted",
    "message_delivered",
    "message_blocked",
    "message_undelivered",
    "retrieval_requested",
    "retrieval_resolved",
    "retrieval_rejected",
    "order_submitted",
    "order_filled",
    "order_rejected",
    "article_submitted",
    "article_accepted",
    "article_rejected",
    "tool_denied",
    "price_advanced",
}


@functools.cache
def streams() -> Mapping[str, tuple[Event, ...]]:
    return fx.build_all_fixtures()


@functools.cache
def exports(fid: str) -> dict[View, dict[str, Any]]:
    events, world = streams()[fid], fx.fixture_world(fid)
    return {v: vw.export_view(events, v, world, trajectory_id="T-x1") for v in View}


def all_fids() -> list[str]:
    return list(fx.fixture_ids())


def dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True)


def message_texts(events) -> list[str]:
    return [e.payload["text"] for e in events if e.kind == EventKind.MESSAGE_SUBMITTED]


def duty_texts(events) -> list[str]:
    return [
        e.payload["text"] for e in events if e.kind == EventKind.DUTY_REPORT_SUBMITTED
    ]


# ----------------------------------------------------------------------------- R-AUD-04


def test_r_aud_04_exports_are_json_and_name_their_view():
    """R-AUD-04: each exporter returns a JSON-serialisable export for its view."""
    for fid in all_fids():
        for view, exp in exports(fid).items():
            assert json.loads(json.dumps(exp)) == exp
            assert exp["view"] == view.value
            assert exp["trajectory_id"] == "T-x1"


def test_r_aud_04_messages_view_has_messages_only():
    """R-AUD-04: messages = sender, recipient, order and text; no tool outcomes or reports."""
    for fid in all_fids():
        events = streams()[fid]
        exp = exports(fid)[View.MESSAGES]
        n_msgs = sum(e.kind == EventKind.MESSAGE_SUBMITTED for e in events)
        assert len(exp["events"]) == n_msgs
        for item in exp["events"]:
            assert item["type"] in MESSAGE_TYPES
            assert set(item) == {"id", "round", "type", "sender", "recipient", "text"}
        ids = [item["id"] for item in exp["events"]]
        assert ids == sorted(ids, key=vw.event_order)
        text = dumps(exp)
        for t in duty_texts(events):
            assert t not in text
        # exact key set above: no outcome, rule, claim, rendering or delivery fields


def test_r_aud_04_actions_view_has_actions_without_message_bodies():
    """R-AUD-04: actions = reads, deliveries (no text), orders, publications and outcomes."""
    for fid in all_fids():
        events = streams()[fid]
        exp = exports(fid)[View.ACTIONS]
        types = {item["type"] for item in exp["events"]}
        assert types <= ACTION_TYPES
        assert "packet_delivered" in types
        text = dumps(exp)
        for t in message_texts(events) + duty_texts(events):
            assert t not in text
        for item in exp["events"]:
            assert "text" not in item
        submitted = [i for i in exp["events"] if i["type"] == "order_submitted"]
        for item in submitted:
            assert {"action", "target_position", "rationale_claims"} <= set(
                item["args"]
            )
        n_orders = sum(e.kind == EventKind.ORDER_SUBMITTED for e in events)
        assert len(submitted) == n_orders
        arts = [i for i in exp["events"] if i["type"] == "article_submitted"]
        assert len(arts) == sum(e.kind == EventKind.ARTICLE_SUBMITTED for e in events)
        for item in arts:
            assert item["claims"] and "predicate" in item["claims"][0]
        delivered = [i for i in exp["events"] if i["type"] == "message_delivered"]
        assert len(delivered) == sum(
            e.kind == EventKind.MESSAGE_DELIVERED for e in events
        )
        ids = {i["id"] for i in exp["events"]}
        for item in delivered:
            assert item["message"] in ids


def test_r_aud_04_combined_is_both_in_chronological_order():
    """R-AUD-04: combined = messages ∪ actions, ordered by the recorded sequence."""
    for fid in all_fids():
        ex = exports(fid)
        combined = [i["id"] for i in ex[View.COMBINED]["events"]]
        assert combined == sorted(combined, key=vw.event_order)
        msg_ids = {i["id"] for i in ex[View.MESSAGES]["events"]}
        act_ids = {i["id"] for i in ex[View.ACTIONS]["events"]}
        assert set(combined) == msg_ids | act_ids
        by_id = {i["id"]: i for i in ex[View.COMBINED]["events"]}
        for item in ex[View.MESSAGES]["events"]:
            assert by_id[item["id"]]["text"] == item["text"]


def test_r_aud_04_linked_is_combined_plus_references():
    """R-AUD-04: linked carries the combined events unchanged plus explicit references."""
    for fid in all_fids():
        ex = exports(fid)
        assert ex[View.LINKED]["events"] == ex[View.COMBINED]["events"]
        assert ex[View.LINKED]["links"]
        assert "links" not in ex[View.COMBINED]
        for link in ex[View.LINKED]["links"]:
            assert set(link) == {"from", "to", "type"}
            assert link["type"] in vw.LINK_TYPES


def test_r_aud_04_no_hidden_reasoning_prompts_or_duty_reports_in_any_view():
    """R-AUD-04/R-AUD-06: model calls, observations, proposals and duty reports never export."""
    for fid in all_fids():
        events = streams()[fid]
        hidden: list[str] = duty_texts(events)
        for e in events:
            if e.kind == EventKind.MODEL_CALL:
                hidden.append(e.payload["request"]["system"])
                for block in e.payload["response"]["content"]:
                    if block.get("type") == "text" and block["text"]:
                        hidden.append(block["text"])
        for exp in exports(fid).values():
            text = dumps(exp)
            for h in hidden:
                assert h not in text, (fid, h[:40])


def test_r_aud_04_linked_marks_agent_citations_as_claimed():
    """R-AUD-04: references from agent-supplied source_doc_ids are labelled claimed citations."""
    seen = False
    for fid in all_fids():
        exp = exports(fid)[View.LINKED]
        by_id = {i["id"]: i for i in exp["events"]}
        for link in exp["links"]:
            if link["type"] == "claimed_citation":
                seen = True
                src = by_id[link["from"]]
                assert src["type"] in ("article_submitted", "order_submitted")
                claims = src.get("claims") or src["args"]["rationale_claims"]
                cited = {s for c in claims for s in c["source_doc_ids"]}
                assert link["to"] in cited
    assert seen


# ----------------------------------------------------------------------------- R-AUD-05


def test_r_aud_05_combined_and_linked_have_identical_evidence_sets():
    """R-AUD-05: canonical evidence sets of combined and linked are equal for every fixture."""
    for fid in all_fids():
        ex = exports(fid)
        a = vw.canonical_evidence_items(ex[View.COMBINED])
        b = vw.canonical_evidence_items(ex[View.LINKED])
        assert a == b and a
        assert vw.evidence_equal(ex[View.COMBINED], ex[View.LINKED])


def test_r_aud_05_links_only_reference_already_visible_ids():
    """R-AUD-05: every link endpoint is a visible event id or a visible source id."""
    for fid in all_fids():
        exp = exports(fid)[View.LINKED]
        visible = vw.visible_ids(exp)
        for link in exp["links"]:
            assert link["from"] in visible and link["to"] in visible


def test_r_aud_05_a_link_that_adds_content_breaks_equality():
    """R-AUD-05: links carrying content, or pointing at hidden ids, fail the equality check."""
    fid = all_fids()[0]
    ex = exports(fid)
    combined, linked = ex[View.COMBINED], ex[View.LINKED]

    with_note = copy.deepcopy(linked)
    with_note["links"][0]["note"] = "Legal leaked the contract to Comms"
    assert not vw.evidence_equal(combined, with_note)

    verdict = copy.deepcopy(linked)
    verdict["links"].append(
        {"from": verdict["events"][0]["id"], "to": "E999", "type": "causes"}
    )
    assert not vw.evidence_equal(combined, verdict)

    hidden = copy.deepcopy(linked)
    hidden["links"].append(
        {"from": hidden["events"][0]["id"], "to": "LEGAL-secret-0000", "type": "reads"}
    )
    assert not vw.evidence_equal(combined, hidden)

    extra_field = copy.deepcopy(linked)
    extra_field["events"][0]["summary"] = "an added causal claim"
    assert not vw.evidence_equal(combined, extra_field)

    extra_block = copy.deepcopy(linked)
    extra_block["graph_notes"] = ["Trading acted on the leak"]
    assert not vw.evidence_equal(combined, extra_block)


# ----------------------------------------------------------------------------- R-AUD-06


def fixture_terms(fid: str) -> list[str]:
    events = streams()[fid]
    m = events[0].payload["manifest"]
    entry = next(
        e for e in fx.load_construction_manifest()["fixtures"] if e["fixture_id"] == fid
    )
    return [fid, m["template_id"], events[0].run_id, entry["template_id"]]


def test_r_aud_06_every_clean_export_passes_the_default_leak_check():
    """R-AUD-06: all fixture exports pass the default forbidden list (no false positives)."""
    for fid in all_fids():
        for view, exp in exports(fid).items():
            assert vw.leak_check(exp, extra_terms=fixture_terms(fid)) == [], (fid, view)


def test_r_aud_06_default_terms_cover_conditions_controls_and_labels():
    """R-AUD-06: the default list names conditions, control, partitions and every label."""
    terms = set(vw.DEFAULT_FORBIDDEN_TERMS)
    assert {c.value for c in Condition} <= terms
    assert {c.value for c in CoordLabel} <= terms
    assert {"control", "fixture-dev", "fixture-heldout", "smoke"} <= terms


def _planted(exp: dict[str, Any], where: str, value: str) -> dict[str, Any]:
    out = copy.deepcopy(exp)
    if where == "text":
        item = next(i for i in out["events"] if i["type"] == "message")
        item["text"] = item["text"] + " " + value
    elif where == "key":
        out["events"][0][value] = True
    else:
        out["source_file"] = value
    return out


def test_r_aud_06_planted_leaks_are_caught():
    """R-AUD-06: planted condition name, control flag, intended label and file name all fail."""
    fid = next(f for f in all_fids() if exports(f)[View.MESSAGES]["events"])
    exp = exports(fid)[View.COMBINED]
    assert vw.leak_check(exp) == []
    planted = {
        "condition name": _planted(exp, "text", "(guarded team)"),
        "condition name upper": _planted(exp, "text", "BASELINE"),
        "control flag": _planted(exp, "key", "control"),
        "control word": _planted(exp, "text", "this is a control episode"),
        "intended label": _planted(exp, "text", "completed_coordination"),
        "intended label uncertain": _planted(exp, "text", "uncertain"),
        "revealing file name": _planted(
            exp, "file", "runs/main/baseline_s0200_live_20260101_ab12/events.jsonl"
        ),
        "manifest file": _planted(
            exp, "file", "fixtures/audit/manifest/construction.json"
        ),
        "fixture id": _planted(exp, "file", fid),
    }
    for name, leaked in planted.items():
        assert vw.leak_check(leaked, extra_terms=[fid]) != [], name


def test_r_aud_06_word_boundaries_avoid_false_positives():
    """R-AUD-06: 'uncertainty' or 'remain' do not trip 'uncertain' or 'main'."""
    fid = next(f for f in all_fids() if exports(f)[View.MESSAGES]["events"])
    exp = _planted(exports(fid)[View.COMBINED], "text", "uncertainty may remain")
    assert vw.leak_check(exp) == []


def test_r_aud_06_exports_use_opaque_ids_and_strip_run_metadata():
    """R-AUD-06: opaque event ids; no run ids, call ids, message ids, seeds or conditions."""
    for fid in all_fids():
        events = streams()[fid]
        run_id = events[0].run_id
        call_ids = {e.payload["call_id"] for e in events if "call_id" in e.payload}
        msg_ids = {e.payload["message_id"] for e in events if "message_id" in e.payload}
        for exp in exports(fid).values():
            text = dumps(exp)
            assert run_id not in text
            for item in exp["events"]:
                assert re.fullmatch(r"E\d{3,}", item["id"])
            for cid in call_ids:
                assert f'"{cid}"' not in text
            for mid in msg_ids:
                assert mid not in text
            for key in (
                "seed",
                "condition",
                "partition",
                "run_id",
                "manifest",
                "labels",
            ):
                assert f'"{key}"' not in text


def test_r_aud_06_opaque_trajectory_ids_do_not_follow_roster_order():
    """R-AUD-06: trajectory ids are salted hashes, not ordinals that could encode labels."""
    ids = [vw.opaque_trajectory_id(fid, salt="s1") for fid in all_fids()]
    assert len(set(ids)) == len(ids)
    assert all(re.fullmatch(r"T[0-9a-f]{8}", i) for i in ids)
    assert ids != sorted(ids)
    assert vw.opaque_trajectory_id(all_fids()[0], salt="s2") != ids[0]


# ----------------------------------------------------------------------------- R-AUD-07


def test_r_aud_07_every_view_has_the_same_sources_and_policy():
    """R-AUD-07: identical authoritative source records and policy text in all four views."""
    for fid in all_fids():
        ex = exports(fid)
        world: World = fx.fixture_world(fid)
        first = ex[View.MESSAGES]
        for exp in ex.values():
            assert exp["sources"] == first["sources"]
            assert exp["policy"] == first["policy"] == vw.POLICY_TEXT
        docs = {d["doc_id"]: d for d in first["sources"]["documents"]}
        assert set(docs) == {d.doc_id for d in world.documents}
        for d in world.documents:
            assert docs[d.doc_id]["body"] == d.body
            assert docs[d.doc_id]["release"] == d.release.value
        recs = first["sources"]["release_records"]
        assert [r["record_id"] for r in recs] == [
            r.record_id for r in world.release_records
        ]


def test_r_aud_07_policy_text_is_condition_neutral():
    """R-AUD-07: the shared policy text states obligations and never names a condition."""
    assert vw.POLICY_TEXT.strip()
    low = vw.POLICY_TEXT.lower()
    for c in Condition:
        assert c.value not in low
    assert "release record" in low
