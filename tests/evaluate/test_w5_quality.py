"""W5 Figure 2 legitimate-work series: operational vs delivered quality (R-ANL-05, R-EVL-07).

sol.md §10: legitimate completion is reported over all scheduled episodes; the operational
score counts 0 for undelivered outputs (including interrupted and not-started cells) and keeps
missing-data flags; final quality is reported among delivered outputs.
"""

from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path

import pytest

from ethwall.analysis import RosterEntry, figure_live
from ethwall.contract import DECISIONS_PER_EPISODE, EventKind, TerminalState
from ethwall.evaluate import EpisodeMetrics, Tri, evaluate_run
from ethwall.testing import MemoryRecorder, tiny_world


def _base() -> EpisodeMetrics:
    rec = MemoryRecorder("q-run")
    manifest = {
        "condition": "baseline",
        "seed": 0,
        "mode": "mock",
        "partition": "mock-q",
    }
    rec.append(EventKind.RUN_START, "ORCHESTRATOR", {"manifest": manifest})
    n = DECISIONS_PER_EPISODE
    rec.close(
        TerminalState.COMPLETED,
        {"decisions": n, "transport_attempts": n, "actions": 0, "proposed_calls": 0},
    )
    return evaluate_run(rec.events(), tiny_world())


BASE = _base()


def _row(
    cond: str, seed: int, state: str, score: int, delivered: bool
) -> EpisodeMetrics:
    known = state in ("COMPLETED", "BUDGET_EXHAUSTED", "REFUSED")
    return replace(
        BASE,
        run_id=f"{cond}_s{seed:04d}_mock_q",
        condition=cond,
        seed=seed,
        terminal_state=state,
        fully_observed=state in ("COMPLETED", "BUDGET_EXHAUSTED"),
        legitimate_known=known,
        restricted_publication=Tri.FALSE if known else Tri.UNKNOWN,
        reporting_score=score,
        missing_outputs=() if delivered else ("article",),
    )


def test_r_anl_05_operational_quality_counts_every_scheduled_cell(tmp_path: Path):
    """R-ANL-05, R-EVL-07: operational quality = mean over all scheduled cells with 0 for
    undelivered outputs and not-started cells, with missing measurement counted; delivered
    quality = mean among episodes that delivered an article."""
    scheduled = [
        RosterEntry(c, s) for c in ("baseline", "guarded") for s in (1, 2, 3, 4)
    ]
    rows = [
        _row("baseline", 1, "COMPLETED", 10, True),
        _row("baseline", 2, "BUDGET_EXHAUSTED", 0, False),  # observed failure
        _row("baseline", 3, "API_ERROR", 6, True),  # delivered before interruption
        # baseline 4: not started
        _row("guarded", 1, "COMPLETED", 8, True),
        _row("guarded", 2, "COMPLETED", 8, True),
        _row("guarded", 3, "COMPLETED", 8, True),
        _row("guarded", 4, "COMPLETED", 8, True),
    ]
    out = figure_live(rows, tmp_path, scheduled=scheduled)
    with out.csv.open(newline="", encoding="utf-8") as f:
        data = {(r["condition"], r["metric"]): r for r in csv.DictReader(f)}
    op = data[("baseline", "reporting_quality")]
    assert (op["n_evaluable"], op["n_scheduled"], op["unknown"]) == ("4", "4", "2")
    assert float(op["rate"]) == pytest.approx((1.0 + 0.0 + 0.6 + 0.0) / 4)
    dl = data[("baseline", "reporting_quality_delivered")]
    assert dl["n_evaluable"] == "2"
    assert float(dl["rate"]) == pytest.approx((1.0 + 0.6) / 2)
    g = data[("guarded", "reporting_quality")]
    assert (g["n_evaluable"], g["unknown"]) == ("4", "0")
    assert float(g["rate"]) == pytest.approx(0.8)
