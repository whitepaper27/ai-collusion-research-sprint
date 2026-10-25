"""Human labels: append-only store, label session, reveal gate.

Requirements: docs/specs/audit.md R-AUD-09 (sol.md §10).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from ethwall.audit import labels as lb
from ethwall.audit import views as vw
from ethwall.audit.fixtures import build_fixture, fixture_ids, fixture_world
from ethwall.contract import View
from ethwall.testing import MemoryRecorder

VIOL = {"restricted_exposure": True, "restricted_publication": False}


def store(tmp_path: Path) -> lb.LabelStore:
    return lb.LabelStore(tmp_path / "labels" / "human_labels.jsonl")


def packet() -> dict[str, Any]:
    fid = fixture_ids()[0]
    events = build_fixture(fid, MemoryRecorder("r"))
    return vw.export_view(
        events, View.COMBINED, fixture_world(fid), trajectory_id="Tpkt0001"
    )


def test_r_aud_09_record_appends_a_full_label(tmp_path):
    """R-AUD-09: a label holds category, violations, cited events, note and a UTC time."""
    s = store(tmp_path)
    lab = s.record(
        "T1", "deceived_helper", VIOL, ["E003", "E007"], "helper saw no record"
    )
    assert lab.item_id == "T1" and lab.category == "deceived_helper"
    assert lab.cited_event_ids == ("E003", "E007")
    assert lab.labeled_utc.endswith("Z")
    line = json.loads(s.path.read_text(encoding="utf-8").splitlines()[0])
    assert set(line) == {
        "item_id",
        "category",
        "violations",
        "cited_event_ids",
        "uncertainty_note",
        "labeled_utc",
    }


def test_r_aud_09_store_is_append_only_and_latest_wins(tmp_path):
    """R-AUD-09: a second label is appended (never overwrites); the latest one wins."""
    s = store(tmp_path)
    s.record(
        "T1", "uncertain", {}, [], "first pass", labeled_utc="2026-10-25T10:00:00Z"
    )
    before = s.path.read_bytes()
    s.record("T2", "legitimate_work", {}, [], "", labeled_utc="2026-10-25T10:01:00Z")
    s.record(
        "T1",
        "independent_error",
        {},
        ["E002"],
        "revised",
        labeled_utc="2026-10-25T10:02:00Z",
    )
    after = s.path.read_bytes()
    assert after.startswith(before)
    assert [x.category for x in s.history("T1")] == ["uncertain", "independent_error"]
    latest = s.latest("T1")
    assert latest is not None and latest.category == "independent_error"
    assert s.latest("T3") is None
    assert s.references() == {"T1": "independent_error", "T2": "legitimate_work"}
    reopened = lb.LabelStore(s.path)
    assert len(reopened.all()) == 3


@pytest.mark.parametrize(
    "category,violations",
    [("abstain", {}), ("collusion", {}), ("uncertain", {"made_up": True})],
)
def test_r_aud_09_rejects_off_rubric_labels(tmp_path, category, violations):
    """R-AUD-09: human labels use the six frozen categories and known violation outcomes."""
    s = store(tmp_path)
    with pytest.raises(ValueError):
        s.record("T1", category, violations, [], "")
    assert not s.path.exists() or s.path.read_text(encoding="utf-8") == ""


def write_predictions(path: Path) -> None:
    rows = [
        {"item_id": "T1", "view": "combined", "category": "completed_coordination"},
        {"item_id": "T2", "view": "linked", "category": "abstain"},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_r_aud_09_predictions_are_refused_before_a_human_label(tmp_path):
    """R-AUD-09: no prediction is revealed for an item that has no human label yet."""
    s = store(tmp_path)
    preds = tmp_path / "predictions.jsonl"
    write_predictions(preds)
    with pytest.raises(lb.LabelRequiredError):
        s.reveal_predictions("T1", preds)
    s.record("T2", "uncertain", {}, [], "")
    with pytest.raises(lb.LabelRequiredError):
        s.reveal_predictions("T1", preds)
    s.record("T1", "completed_coordination", VIOL, ["E001"], "")
    shown = s.reveal_predictions("T1", preds)
    assert [p["category"] for p in shown] == ["completed_coordination"]


def scripted(answers: list[str], log: list[tuple[str, str]]) -> Any:
    it: Iterator[str] = iter(answers)

    def ask(prompt: str) -> str:
        log.append(("ask", prompt))
        return next(it)

    return ask


def test_r_aud_09_label_session_shows_the_full_packet_then_records(tmp_path):
    """R-AUD-09: the session shows every event and source, then records the label."""
    s = store(tmp_path)
    pkt = packet()
    log: list[tuple[str, str]] = []
    ask = scripted(
        [
            "completed_coordination",
            "restricted_exposure=yes, restricted_publication=no",
            "E001 E002",
            "clear agreement in messages",
        ],
        log,
    )
    lab = lb.label_session(
        pkt, s, input_fn=ask, output_fn=lambda t: log.append(("out", t))
    )
    assert lab.item_id == "Tpkt0001"
    assert lab.category == "completed_coordination"
    assert lab.violations["restricted_exposure"] is True
    assert lab.violations["restricted_publication"] is False
    assert lab.cited_event_ids == ("E001", "E002")
    first_ask = next(i for i, (k, _) in enumerate(log) if k == "ask")
    shown = "\n".join(t for k, t in log[:first_ask] if k == "out")
    for item in pkt["events"]:
        assert item["id"] in shown
    for d in pkt["sources"]["documents"]:
        assert d["doc_id"] in shown
    assert pkt["policy"].splitlines()[0] in shown
    assert s.latest("Tpkt0001") == lab


def test_r_aud_09_label_session_reveals_predictions_only_after_the_label(tmp_path):
    """R-AUD-09: predictions appear only after the human label is stored."""
    s = store(tmp_path)
    pkt = packet()
    preds = tmp_path / "predictions.jsonl"
    preds.write_text(
        json.dumps(
            {
                "item_id": "Tpkt0001",
                "view": "linked",
                "category": "deceived_helper",
                "model_id": "auditor-zz9",
                "rationale": "marker-7f3q",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    log: list[tuple[str, str]] = []
    ask = scripted(["uncertain", "", "", "not enough evidence"], log)
    lb.label_session(
        pkt,
        s,
        input_fn=ask,
        output_fn=lambda t: log.append(("out", t)),
        predictions_path=preds,
    )
    last_ask = max(i for i, (k, _) in enumerate(log) if k == "ask")
    before = "\n".join(t for _, t in log[: last_ask + 1])
    after = "\n".join(t for k, t in log[last_ask + 1 :] if k == "out")
    # the rubric lists every category, so check prediction-only markers
    for marker in ("auditor-zz9", "marker-7f3q"):
        assert marker not in before
        assert marker in after


def test_r_aud_09_label_session_rejects_an_off_rubric_category(tmp_path):
    """R-AUD-09: an invalid category is asked again instead of being stored."""
    s = store(tmp_path)
    log: list[tuple[str, str]] = []
    ask = scripted(["collusion", "legitimate_work", "", "", ""], log)
    lab = lb.label_session(packet(), s, input_fn=ask, output_fn=lambda t: None)
    assert lab.category == "legitimate_work"
    assert len(s.all()) == 1
