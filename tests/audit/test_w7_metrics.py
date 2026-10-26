"""W7 review fix for audit metrics: duplicate prediction records are counted, not hidden.

Requirements: docs/specs/audit.md R-AUD-08, R-AUD-10 (sol.md §10).
"""

from __future__ import annotations

from typing import Any

from ethwall.audit import metrics as mt

CC, CA, LW, IE = (
    "completed_coordination",
    "coordinated_attempt",
    "legitimate_work",
    "independent_error",
)
REFS = {"a": CC, "c": LW, "d": IE}


def p(item: str, cat: str | None, view: str = "combined") -> dict[str, Any]:
    return {"item_id": item, "view": view, "category": cat, "cited_event_ids": []}


def test_r_aud_08_superseded_records_are_reported():
    """R-AUD-08/R-AUD-10: run_auditor already folds its one identical transport retry into a
    single record, so any further record for the same (item, view) is a re-run. Re-runs are
    counted in `superseded_records` so they can never be silently absorbed."""
    preds = [
        p("a", None),
        p("a", CC),  # a re-run after a transport_error record
        p("c", CA),
        p("c", LW),  # a re-run after an answer
        p("c", LW),
        p("d", IE),
    ]
    m = mt.audit_metrics(preds, REFS)
    assert m["superseded_records"] == 3
    assert mt.audit_metrics([p("a", CC), p("c", LW)], REFS)["superseded_records"] == 0


def test_r_aud_10_superseded_records_are_per_view():
    """R-AUD-10: the same item in two views is two planned decisions, not a duplicate."""
    preds = [p("a", CC, "combined"), p("a", CC, "linked"), p("a", LW, "linked")]
    by_view = mt.metrics_by_view(preds, REFS)
    assert by_view["combined"]["superseded_records"] == 0
    assert by_view["linked"]["superseded_records"] == 1
