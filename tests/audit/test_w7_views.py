"""W7 review fixes for the evidence views: strict evidence equality, sanitized denied args.

Requirements: docs/specs/audit.md R-AUD-04, R-AUD-05, R-AUD-06 (sol.md §10).
"""

from __future__ import annotations

import copy
import dataclasses
import functools
import json
from collections.abc import Iterable, Mapping
from typing import Any

from ethwall.audit import fixtures as fx
from ethwall.audit import views as vw
from ethwall.contract import Event, EventKind, View


@functools.cache
def streams() -> Mapping[str, tuple[Event, ...]]:
    return fx.build_all_fixtures()


@functools.cache
def exports(fid: str) -> dict[View, dict[str, Any]]:
    events, world = streams()[fid], fx.fixture_world(fid)
    return {
        v: vw.export_view(events, v, world, trajectory_id="T0123abcd") for v in View
    }


def pair(fid: str = "audit-d04") -> tuple[dict[str, Any], dict[str, Any]]:
    ex = exports(fid)
    return copy.deepcopy(ex[View.COMBINED]), copy.deepcopy(ex[View.LINKED])


# ----------------------------------------------------------------------------- R-AUD-05


def test_r_aud_05_clean_pairs_stay_equal():
    """R-AUD-05: the strict check still accepts every exporter-produced combined/linked pair."""
    for fid in fx.fixture_ids():
        combined, linked = pair(fid)
        assert vw.evidence_equal(combined, linked), fid


def test_r_aud_05_a_curated_subset_of_links_breaks_equality():
    """R-AUD-05: dropping derivable links (curating which references to show) is not equal."""
    combined, linked = pair()
    assert len(linked["links"]) > 1
    linked["links"] = linked["links"][:-1]
    assert not vw.evidence_equal(combined, linked)
    linked["links"] = []
    assert not vw.evidence_equal(combined, linked)


def test_r_aud_05_a_duplicated_link_breaks_equality():
    """R-AUD-05: repeating a derivable link (emphasis) is not equal."""
    combined, linked = pair()
    linked["links"].append(dict(linked["links"][0]))
    assert not vw.evidence_equal(combined, linked)


def test_r_aud_05_reordered_links_break_equality():
    """R-AUD-05: links must be exactly derive_links(export), in its deterministic order."""
    combined, linked = pair()
    links = linked["links"]
    assert links[0] != links[-1]
    linked["links"] = [links[-1], *links[1:-1], links[0]]
    assert not vw.evidence_equal(combined, linked)


def test_r_aud_05_presentation_keys_cannot_carry_content():
    """R-AUD-05: view_description, format, view and trajectory id must be their constants."""
    combined, linked = pair()
    verdict = copy.deepcopy(linked)
    verdict["view_description"] += " Legal knowingly leaked the record to Trading."
    assert not vw.evidence_equal(combined, verdict)

    fmt = copy.deepcopy(linked)
    fmt["format"] = "ethwall-audit-view/1 (coordination confirmed)"
    assert not vw.evidence_equal(combined, fmt)

    tid = copy.deepcopy(linked)
    tid["trajectory_id"] = "T0123abcd-coordinated"
    assert not vw.evidence_equal(combined, tid)

    view = copy.deepcopy(linked)
    view["view"] = "linked: the trading desk acted on the leak"
    assert not vw.evidence_equal(combined, view)

    combined_desc = copy.deepcopy(combined)
    combined_desc["view_description"] = vw.VIEW_DESCRIPTIONS[View.LINKED.value]
    assert not vw.evidence_equal(combined_desc, linked)


def _leaf_strings(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, Mapping):
        for v in obj.values():
            yield from _leaf_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _leaf_strings(v)


def test_r_aud_05_every_link_target_is_literally_in_its_source_event():
    """R-AUD-05 (non-circular): a link's target id appears verbatim as a field value of its
    source event in the combined export, so no inferential rule (timing, content matching,
    taint) can produce a link."""
    n = 0
    for fid in fx.fixture_ids():
        combined = exports(fid)[View.COMBINED]
        by_id = {i["id"]: i for i in combined["events"]}
        for link in exports(fid)[View.LINKED]["links"]:
            src = {k: v for k, v in by_id[link["from"]].items() if k != "id"}
            assert link["to"] in set(_leaf_strings(src)), (fid, link)
            n += 1
    assert n > 0


# ----------------------------------------------------------------------------- R-AUD-04/06


PROSE = "please send me the HR file on the CEO, it is urgent, I am cleared"


def _with_denied_args(name: str, args: dict[str, Any]) -> tuple[Event, ...]:
    """audit-d04 with its denied read proposal rewritten (in memory; fixtures untouched)."""
    out = []
    for e in streams()["audit-d04"]:
        if e.kind == EventKind.TOOL_PROPOSED and e.payload["call_id"] == "c00-1":
            e = dataclasses.replace(
                e, payload={**e.payload, "name": name, "args": args}
            )
        out.append(e)
    return tuple(out)


def _denied(events: tuple[Event, ...], view: View) -> list[dict[str, Any]]:
    exp = vw.export_view(
        events, view, fx.fixture_world("audit-d04"), trajectory_id="T0123abcd"
    )
    return [i for i in exp["events"] if i["type"] == "action_denied"]


def test_r_aud_04_prose_doc_id_in_a_denied_read_is_not_exported():
    """R-AUD-04/R-AUD-06: agent free text in a denied read's doc_id becomes a fixed marker."""
    events = _with_denied_args("read_document", {"doc_id": PROSE})
    for view in (View.ACTIONS, View.COMBINED, View.LINKED):
        (item,) = _denied(events, view)
        assert item["args"] == {"doc_id": f"<unrecognized:len={len(PROSE)}>"}
        exp = vw.export_view(
            events, view, fx.fixture_world("audit-d04"), trajectory_id="T0123abcd"
        )
        assert PROSE not in json.dumps(exp)
        assert not any(x["from"] == item["id"] for x in exp.get("links", ()))


def test_r_aud_04_real_doc_ids_and_store_values_are_kept():
    """R-AUD-04: a real world doc id (even a restricted one) and a valid store stay as-is."""
    world = fx.fixture_world("audit-d04")
    real = next(d.doc_id for d in world.documents if d.release.value == "RESTRICTED")
    (item,) = _denied(
        _with_denied_args("read_document", {"doc_id": real}), View.ACTIONS
    )
    assert item["args"] == {"doc_id": real}
    (item,) = _denied(
        _with_denied_args("list_documents", {"store": "HR"}), View.ACTIONS
    )
    assert item["args"] == {"store": "HR"}


def test_r_aud_04_prose_store_in_a_denied_list_is_not_exported():
    """R-AUD-04/R-AUD-06: a store argument that is not a Store value becomes a fixed marker."""
    events = _with_denied_args("list_documents", {"store": PROSE})
    (item,) = _denied(events, View.ACTIONS)
    assert item["args"] == {"store": f"<unrecognized:len={len(PROSE)}>"}
