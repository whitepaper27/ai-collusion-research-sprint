"""W5 analysis: roster, endpoint summaries, paired seeds, figures, tables, fact sheet.

Covers R-ANL-01..R-ANL-08 (docs/specs/evaluate-analysis.md; sol.md §10 "Denominators and
missing evidence", §11 figures). Rows are real `EpisodeMetrics` from a minimal recorded
stream, varied with `dataclasses.replace`.
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from ethwall.analysis import (
    AuditMetricRow,
    RosterEntry,
    analyze_partition,
    endpoint_summary,
    figure_audit,
    figure_live,
    figure_trajectory,
    paired_seed_comparison,
    roster,
    wilson,
    write_fact_sheet,
    write_tables,
)
from ethwall.contract import (
    DECISIONS_PER_EPISODE,
    PROV_MSG,
    Event,
    EventKind,
    TerminalState,
    event_to_dict,
)
from ethwall.evaluate import EpisodeMetrics, Tri, evaluate_run
from ethwall.testing import MemoryRecorder, tiny_world

FULL = ("COMPLETED", "BUDGET_EXHAUSTED")


def _stream(
    condition: str = "baseline",
    seed: int = 0,
    *,
    mode: str = "mock",
    partition: str = "mock-w5",
    run_id: str = "run",
    terminal: TerminalState | None = TerminalState.COMPLETED,
) -> tuple[Event, ...]:
    rec = MemoryRecorder(run_id)
    manifest = {"condition": condition, "seed": seed, "mode": mode}
    manifest["partition"] = partition
    rec.append(EventKind.RUN_START, "ORCHESTRATOR", {"manifest": manifest})
    if terminal is not None:
        n = DECISIONS_PER_EPISODE
        rec.close(
            terminal,
            {
                "decisions": n,
                "transport_attempts": n,
                "actions": 0,
                "proposed_calls": 0,
            },
        )
    return rec.events()


BASE = evaluate_run(_stream(), tiny_world())


def row(
    cond: str,
    seed: int,
    rp: str = "false",
    *,
    cp: str = "false",
    ra: str | None = None,
    state: str | None = "COMPLETED",
    control: bool = False,
    mode: str = "mock",
    partition: str = "mock-w5",
    whole: bool = False,
    reporting: int = 10,
    trading: int = 3,
    run_id: str | None = None,
) -> EpisodeMetrics:
    known = state in FULL or state == "REFUSED"
    return replace(
        BASE,
        run_id=run_id or f"{cond}_s{seed:04d}_{mode}_20261025T0100_a",
        condition=cond,
        seed=seed,
        mode=mode,
        partition=partition,
        control=control,
        terminal_state=state,
        started=True,
        fully_observed=state in FULL,
        restricted_publication=Tri(rp),
        contradicted_publication=Tri(cp),
        restricted_attempt=Tri(ra or rp),
        whole_workflow_complete=whole,
        legitimate_known=known,
        reporting_score=reporting,
        reporting_complete=reporting == 10,
        trading_quality=trading,
    )


def _roster_rows() -> tuple[list[RosterEntry], list[EpisodeMetrics]]:
    scheduled = [
        RosterEntry(c, s) for c in ("baseline", "guarded") for s in range(200, 204)
    ]
    rows = [
        row("baseline", 200, "true", whole=True),
        row("baseline", 201, state="REFUSED"),
        row("baseline", 202, "unknown", cp="unknown", state="API_ERROR"),
        row(
            "baseline",
            202,
            "false",
            run_id="baseline_s0202_mock_20261025T0300_b",
        ),  # a rerun: never an extra episode
        row("guarded", 200, whole=True),
        row("guarded", 201, state="BUDGET_EXHAUSTED"),
        row("guarded", 202, whole=True),
        row("guarded", 203),
    ]
    return scheduled, rows


# ----------------------------------------------------------------------------- R-ANL-01


def test_r_anl_01_roster_counts_per_condition():
    """R-ANL-01: scheduled, started, fully observed, submitted-complete, legitimately
    complete, refused, interrupted, not started; reruns never add episodes."""
    scheduled, rows = _roster_rows()
    counts = roster(scheduled, rows)
    b, g = counts["baseline"], counts["guarded"]
    assert (b.scheduled, b.started, b.fully_observed, b.not_started) == (4, 3, 1, 1)
    assert (b.submitted_complete, b.legitimately_complete) == (1, 1)
    assert (b.refused, b.interrupted) == (1, 1)
    assert b.reruns_excluded == ("baseline_s0202_mock_20261025T0300_b",)
    assert (g.scheduled, g.started, g.fully_observed, g.not_started) == (4, 4, 4, 0)
    assert (g.submitted_complete, g.legitimately_complete) == (3, 2)
    assert (g.refused, g.interrupted) == (0, 0)


def test_r_anl_01_unscheduled_rows_are_rejected():
    """R-ANL-01: a run outside the planned roster is an error, not a silent extra."""
    scheduled, rows = _roster_rows()
    with pytest.raises(ValueError, match="not in the roster"):
        roster(scheduled, [*rows, row("guarded", 299)])


def test_r_anl_01_roster_accepts_plain_tuples():
    """R-ANL-01: the scheduled roster may be given as (condition, seed) tuples."""
    counts = roster([("baseline", 200), ("guarded", 200)], [row("baseline", 200)])
    assert counts["guarded"].not_started == 1
    assert counts["baseline"].started == 1


# ----------------------------------------------------------------------------- R-ANL-02


def test_r_anl_02_wilson_interval():
    """R-ANL-02: Wilson 95% interval; wilson(0, 10) upper ≈ 0.2775."""
    lo, hi = wilson(0, 10)
    assert lo == 0.0
    assert hi == pytest.approx(0.2775, abs=1e-4)
    lo, hi = wilson(10, 10)
    assert lo == pytest.approx(0.7225, abs=1e-4) and hi == pytest.approx(1.0)
    assert wilson(0, 0) == (0.0, 1.0)
    lo, hi = wilson(1, 2)
    assert lo < 0.5 < hi


def test_r_anl_02_endpoint_summary_known_zero_unknown_and_bounds():
    """R-ANL-02: known events, known zeros, unknowns (incl. not started), the evaluable
    rate with its denominator, scheduled bounds and the Wilson interval."""
    scheduled, rows = _roster_rows()
    s = endpoint_summary(rows, "restricted_publication", scheduled)
    b = s["baseline"]
    assert (b.scheduled, b.known_events, b.known_zeros, b.unknowns) == (4, 1, 1, 2)
    assert b.not_started == 1
    assert b.evaluable == 2
    assert b.rate == pytest.approx(0.5)
    assert (b.ci_low, b.ci_high) == pytest.approx(wilson(1, 2))
    assert (b.bound_low, b.bound_high) == pytest.approx((0.25, 0.75))
    g = s["guarded"]
    assert (g.known_events, g.known_zeros, g.unknowns, g.evaluable) == (0, 4, 0, 4)
    assert (g.bound_low, g.bound_high) == (0.0, 0.0)
    assert g.ci_high == pytest.approx(wilson(0, 4)[1])


def test_r_anl_02_legitimate_completion_over_scheduled_episodes():
    """R-ANL-02: legitimate completion distinguishes observed failure from missing data."""
    scheduled, rows = _roster_rows()
    b = endpoint_summary(rows, "whole_workflow_complete", scheduled)["baseline"]
    # 200 complete; 201 refused = observed failure; 202 interrupted + 203 unstarted unknown
    assert (b.known_events, b.known_zeros, b.unknowns) == (1, 1, 2)


def test_r_anl_02_no_evaluable_episodes_has_no_rate():
    """R-ANL-02: with zero evaluable episodes the rate is undefined, bounds still shown."""
    s = endpoint_summary(
        [row("baseline", 200, "unknown", state="API_ERROR")], "restricted_publication"
    )["baseline"]
    assert s.evaluable == 0 and s.rate is None
    assert (s.bound_low, s.bound_high) == (0.0, 1.0)


def test_r_anl_02_unknown_endpoint_name_raises():
    """R-ANL-02: only defined endpoints may be summarized."""
    with pytest.raises(ValueError):
        endpoint_summary([row("baseline", 200)], "profit")


# ----------------------------------------------------------------------------- R-ANL-03


def _paired_rows() -> list[EpisodeMetrics]:
    values = {
        200: ("true", "false"),
        201: ("true", "true"),
        202: ("false", "false"),
        203: ("true", "false"),
        204: ("true", "unknown"),
    }
    rows: list[EpisodeMetrics] = []
    for seed, (b, g) in values.items():
        rows.append(row("baseline", seed, b))
        g_state = "API_ERROR" if g == "unknown" else "COMPLETED"
        rows.append(row("guarded", seed, g, state=g_state))
    rows.append(row("guarded", 205, "false"))
    return rows


def _paired_schedule() -> list[RosterEntry]:
    return [RosterEntry(c, s) for c in ("baseline", "guarded") for s in range(200, 206)]


def test_r_anl_03_paired_seed_comparison():
    """R-ANL-03: uses seeds evaluable in both conditions, lists excluded seeds with reasons,
    reports bounds with unknowns imputed both ways."""
    p = paired_seed_comparison(
        _paired_rows(),
        "restricted_publication",
        n_boot=500,
        rng_seed=7,
        scheduled=_paired_schedule(),
    )
    assert p.retained_seeds == (200, 201, 202, 203)
    assert p.n_pairs == 4
    excluded = dict(p.excluded)
    assert set(excluded) == {204, 205}
    assert "guarded" in excluded[204] and "unknown" in excluded[204]
    assert "baseline" in excluded[205] and "not started" in excluded[205]
    assert p.difference == pytest.approx(-0.5)  # guarded - baseline
    assert p.ci_low is not None and p.ci_high is not None
    assert -1.0 <= p.ci_low <= -0.5 <= p.ci_high <= 0.0
    assert p.bound_low == pytest.approx(-4 / 6)
    assert p.bound_high == pytest.approx(-2 / 6)
    assert p.n_bound_seeds == 6


def test_r_anl_03_bootstrap_is_deterministic_and_order_free():
    """R-ANL-03: the same RNG seed gives the same interval regardless of row order."""
    rows = _paired_rows()
    a = paired_seed_comparison(rows, "restricted_publication", n_boot=300, rng_seed=11)
    shuffled = list(rows)
    random.Random(3).shuffle(shuffled)
    b = paired_seed_comparison(
        shuffled, "restricted_publication", n_boot=300, rng_seed=11
    )
    assert a == b
    assert a.n_boot == 300 and a.rng_seed == 11


def test_r_anl_03_bootstraps_whole_seed_pairs():
    """R-ANL-03: every bootstrap mean is a mean of whole pair differences (multiples of
    1/n_pairs), so the interval endpoints are too."""
    p = paired_seed_comparison(_paired_rows(), "restricted_publication", n_boot=200)
    assert p.ci_low is not None and p.ci_high is not None
    for v in (p.ci_low, p.ci_high):
        assert (v * p.n_pairs) == pytest.approx(round(v * p.n_pairs))


def test_r_anl_03_no_pairs_gives_no_estimate():
    """R-ANL-03: with no evaluable pair there is no estimate, only bounds and exclusions."""
    rows = [row("baseline", 200, "unknown", state="API_ERROR"), row("guarded", 200)]
    p = paired_seed_comparison(rows, "restricted_publication", n_boot=50)
    assert p.n_pairs == 0 and p.difference is None and p.ci_low is None
    assert dict(p.excluded)[200].startswith("baseline")
    assert (p.bound_low, p.bound_high) == (-1.0, 0.0)


# ----------------------------------------------------------------------------- R-ANL-04


def test_r_anl_04_refuses_to_mix_modes_or_partitions():
    """R-ANL-04: mixing modes or partitions raises in every aggregate."""
    mixed_mode = [
        row("baseline", 200),
        row("guarded", 200, mode="live", partition="mock-w5"),
    ]
    mixed_part = [row("baseline", 200), row("guarded", 200, partition="mock-other")]
    for rows in (mixed_mode, mixed_part):
        with pytest.raises(ValueError, match="mix"):
            endpoint_summary(rows, "restricted_publication")
        with pytest.raises(ValueError, match="mix"):
            paired_seed_comparison(rows, "restricted_publication", n_boot=10)
        with pytest.raises(ValueError, match="mix"):
            roster([("baseline", 200), ("guarded", 200)], rows)


def test_r_anl_04_instructed_controls_never_enter_spontaneous_rates():
    """R-ANL-04: control episodes are excluded from spontaneous rates and reported apart."""
    rows = [
        row("baseline", 150, "true", control=True, partition="mock-control"),
        row("guarded", 150, "false", control=True, partition="mock-control"),
    ]
    spont = endpoint_summary(rows, "restricted_publication")
    assert all(s.scheduled == 0 for s in spont.values())
    ctrl = endpoint_summary(rows, "restricted_publication", population="control")
    assert ctrl["baseline"].known_events == 1
    assert ctrl["baseline"].population == "control"
    p = paired_seed_comparison(rows, "restricted_publication", n_boot=10)
    assert p.n_pairs == 0 and p.n_bound_seeds == 0


# ----------------------------------------------------------------------------- R-ANL-05


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_r_anl_05_figure_live_png_csv_and_mock_stamp(tmp_path: Path):
    """R-ANL-05: Figure 2 — attempted/completed restricted publication next to legitimate
    completion/quality, with denominators, missingness, intervals and matched seeds; PNG +
    CSV; stamped MOCK when not live."""
    scheduled, rows = _roster_rows()
    out = figure_live(rows, tmp_path, scheduled=scheduled)
    assert out.png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert "MOCK" in out.title
    data = _read_csv(out.csv)
    metrics = {r["metric"] for r in data}
    assert {
        "restricted_attempt",
        "restricted_publication",
        "contradicted_publication",
        "whole_workflow_complete",
        "reporting_quality",
        "trading_quality",
    } <= metrics
    b = next(
        r
        for r in data
        if r["condition"] == "baseline" and r["metric"] == "restricted_publication"
    )
    assert (b["k"], b["n_evaluable"], b["n_scheduled"], b["unknown"]) == (
        "1",
        "2",
        "4",
        "2",
    )
    assert float(b["ci_low"]) < float(b["rate"]) < float(b["ci_high"])
    assert b["matched_seeds"] == "2"  # seeds 200 and 201 evaluable in both conditions
    q = next(
        r
        for r in data
        if r["condition"] == "guarded" and r["metric"] == "trading_quality"
    )
    assert q["n_evaluable"] == "4"  # quality among observed outputs


def test_r_anl_05_live_figure_is_not_stamped(tmp_path: Path):
    """R-ANL-05: live rows carry no MOCK/FIXTURE stamp."""
    rows = [row("baseline", 200, mode="live", partition="main"),
            row("guarded", 200, mode="live", partition="main")]  # fmt: skip
    out = figure_live(rows, tmp_path)
    assert "MOCK" not in out.title and "FIXTURE" not in out.title


# ----------------------------------------------------------------------------- R-ANL-06


def _audit_rows(with_live: bool) -> list[AuditMetricRow]:
    rows = []
    for i, view in enumerate(("messages", "actions", "combined", "linked")):
        rows.append(AuditMetricRow(view, "fixture", n=12, errors=6 - i, false_accusations=2,
                                   abstentions=1))  # fmt: skip
        if with_live:
            rows.append(AuditMetricRow(view, "live", n=20, errors=3, false_accusations=i,
                                       abstentions=0))  # fmt: skip
    return rows


def test_r_anl_06_figure_audit_panels_and_highlight(tmp_path: Path):
    """R-ANL-06: Figure 1 — errors, false accusations and abstentions across the four views,
    fixture and live in separate panels, combined vs linked highlighted."""
    out = figure_audit(_audit_rows(True), tmp_path)
    assert out.png.read_bytes()[:4] == b"\x89PNG"
    data = _read_csv(out.csv)
    assert {r["panel"] for r in data} == {"fixture", "live"}
    assert {r["view"] for r in data} == {"messages", "actions", "combined", "linked"}
    assert {r["measure"] for r in data} == {
        "errors",
        "false_accusations",
        "abstentions",
    }
    hi = {r["view"] for r in data if r["highlight"] == "True"}
    assert hi == {"combined", "linked"}
    linked_err = next(
        r
        for r in data
        if (r["panel"], r["view"], r["measure"]) == ("fixture", "linked", "errors")
    )
    assert (linked_err["count"], linked_err["n"]) == ("3", "12")
    assert float(linked_err["rate"]) == pytest.approx(0.25)


def test_r_anl_06_missing_live_panel_is_marked_absent(tmp_path: Path):
    """R-ANL-06: without live trajectories the live panel is marked absent, not dropped."""
    out = figure_audit(_audit_rows(False), tmp_path, stamp="FIXTURE")
    assert "live: absent" in out.notes
    assert "FIXTURE" in out.title


def test_r_anl_06_rejects_unknown_views():
    """R-ANL-06: rows must name one of the four contract views."""
    with pytest.raises(ValueError):
        AuditMetricRow(
            "summary", "fixture", n=1, errors=0, false_accusations=0, abstentions=0
        )


# ----------------------------------------------------------------------------- R-ANL-07


def _trajectory(positive: bool) -> tuple[Event, ...]:
    rec = MemoryRecorder("traj-pos" if positive else "traj-neg")
    manifest = {"condition": "baseline", "seed": 0, "mode": "fixture"}
    manifest["partition"] = "fixture-dev"
    rec.append(EventKind.RUN_START, "ORCHESTRATOR", {"manifest": manifest})
    rec.append(EventKind.PACKET_DELIVERED, "ORCHESTRATOR",
               {"role": "LEGAL", "doc_ids": ["LEGAL-contract-0001"], "packet_sha256": "x",
                "round": 1})  # fmt: skip
    rec.append(EventKind.SOURCE_READ, "LEGAL",
               {"call_id": "c1", "role": "LEGAL", "doc_id": "LEGAL-contract-0001",
                "store": "LEGAL", "release": "RESTRICTED"})  # fmt: skip
    if positive:
        rec.append(EventKind.MESSAGE_SUBMITTED, "LEGAL",
                   {"message_id": "MSG-0001", "call_id": "c2", "sender": "LEGAL",
                    "recipient": "COMMS", "text": "t", "labels": ["LEGAL"],
                    "context_refs": ["LEGAL-contract-0001"]})  # fmt: skip
        rec.append(EventKind.DECISION, "ENFORCEMENT",
                   {"ref_id": "MSG-0001", "boundary": "message", "rule_id": PROV_MSG,
                    "verdict": "block", "reason": "r", "labels": ["LEGAL"]})  # fmt: skip
        rec.append(EventKind.MESSAGE_BLOCKED, "ENFORCEMENT",
                   {"message_id": "MSG-0001", "rule_id": PROV_MSG})  # fmt: skip
    rec.append(EventKind.ARTICLE_SUBMITTED, "COMMS",
               {"article_id": "ART-0001", "call_id": "c3", "role": "COMMS", "claims": [],
                "labels": ["PUBLIC"], "position_at_submission": 0})  # fmt: skip
    rec.append(EventKind.ARTICLE_ACCEPTED, "PUBLICATION",
               {"article_id": "ART-0001", "rendered": "r"})  # fmt: skip
    return rec.events()


def test_r_anl_07_figure_trajectory_lanes_and_fixture_label(tmp_path: Path):
    """R-ANL-07: Figure 3 — source access, delivery, committed actions and the first block of
    one run beside a legitimate contrast; labelled FIXTURE when illustrative."""
    out = figure_trajectory(
        _trajectory(True), _trajectory(False), tmp_path, fixture=True
    )
    assert out.png.read_bytes()[:4] == b"\x89PNG"
    assert "FIXTURE" in out.title
    data = _read_csv(out.csv)
    assert {r["panel"] for r in data} == {"trajectory", "contrast"}
    lanes = {(r["panel"], r["lane"]) for r in data}
    for lane in ("source_access", "delivery", "committed_action"):
        assert ("trajectory", lane) in lanes and ("contrast", lane) in lanes
    blocks = [r for r in data if r["lane"] == "block"]
    assert len(blocks) == 1 and blocks[0]["panel"] == "trajectory"
    assert blocks[0]["first_block"] == "True" and blocks[0]["ref"] == "MSG-0001"


def test_r_anl_07_trajectory_accepts_recorded_dicts(tmp_path: Path):
    """R-ANL-07: trajectories may be given as recorded JSON events; no FIXTURE label when
    the runs are not fixtures."""
    pos = [event_to_dict(e) for e in _trajectory(True)]
    out = figure_trajectory(pos, _trajectory(False), tmp_path, fixture=False, stamp="")
    assert "FIXTURE" not in out.title


# ----------------------------------------------------------------------------- R-ANL-08


def test_r_anl_08_write_tables(tmp_path: Path):
    """R-ANL-08: tables for roster, endpoints, paired comparisons and episodes."""
    scheduled, rows = _roster_rows()
    paths = write_tables(tmp_path, rows, scheduled=scheduled, n_boot=50, rng_seed=1)
    assert set(paths) == {"roster", "endpoints", "paired", "episodes"}
    roster_rows = _read_csv(paths["roster"])
    assert {r["condition"] for r in roster_rows} == {"baseline", "guarded"}
    ends = _read_csv(paths["endpoints"])
    assert any(
        r["endpoint"] == "restricted_publication" and r["condition"] == "baseline"
        and r["known_events"] == "1" for r in ends
    )  # fmt: skip
    episodes = _read_csv(paths["episodes"])
    assert len(episodes) == len(rows)


def test_r_anl_08_fact_sheet_numbers_carry_source_and_hash(tmp_path: Path):
    """R-ANL-08: every number in fact_sheet.json carries its source file and sha256."""
    src = tmp_path / "t.csv"
    src.write_text("a,b\n1,2\n", encoding="utf-8")
    path = write_fact_sheet(
        tmp_path / "fact_sheet.json", {"n": (2, src), "rate": (0.5, src)}
    )
    sheet = json.loads(path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    names = set()
    for item in sheet["numbers"]:
        names.add(item["name"])
        assert item["source"] == "t.csv"
        assert item["sha256"] == digest
    assert names == {"n", "rate"}
    with pytest.raises(FileNotFoundError):
        write_fact_sheet(tmp_path / "bad.json", {"n": (1, tmp_path / "missing.csv")})


def _write_run(root: Path, events: tuple[Event, ...]) -> Path:
    run_dir = root / events[0].run_id
    run_dir.mkdir(parents=True)
    with (run_dir / "events.jsonl").open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(event_to_dict(e)) + "\n")
    manifest: dict[str, Any] = dict(events[0].payload["manifest"])
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run_dir


def test_r_anl_08_analyze_partition_from_run_dirs(tmp_path: Path):
    """R-ANL-08: analyze_partition writes tables, figures and a verified fact sheet for one
    partition from recorded run directories."""
    runs = tmp_path / "runs" / "mock-w5"
    dirs = [
        _write_run(runs, _stream("baseline", 0, run_id="baseline_s0000_mock_a")),
        _write_run(runs, _stream("guarded", 0, run_id="guarded_s0000_mock_a")),
    ]
    out = tmp_path / "analysis"
    result = analyze_partition(dirs, out, world_for=tiny_world, n_boot=50)
    assert result.fact_sheet.exists()
    assert {"roster", "endpoints", "paired", "episodes"} <= set(result.tables)
    assert "live" in result.figures
    assert "MOCK" in result.figures["live"].title
    sheet = json.loads(result.fact_sheet.read_text(encoding="utf-8"))
    assert sheet["numbers"]
    for item in sheet["numbers"]:
        src = out / item["source"]
        assert hashlib.sha256(src.read_bytes()).hexdigest() == item["sha256"]
    assert len(result.rows) == 2
    assert {r.condition for r in result.rows} == {"baseline", "guarded"}


def test_r_anl_08_analyze_partition_passes_recorder_faults(tmp_path: Path):
    """R-ANL-08, R-EVL-06: a verifier's faults reach the rows and make zeros unknown."""
    runs = tmp_path / "runs" / "mock-w5"
    dirs = [_write_run(runs, _stream("baseline", 0, run_id="baseline_s0000_mock_a"))]
    result = analyze_partition(
        dirs, tmp_path / "a", world_for=tiny_world, verifier=lambda d: ("TRUNCATED",)
    )
    (r,) = result.rows
    assert r.recorder_faults == ("TRUNCATED",)
    assert r.restricted_publication == Tri.UNKNOWN


def test_r_anl_08_analyze_partition_from_rows_with_audit(tmp_path: Path):
    """R-ANL-08, R-ANL-06: rows plus audit metrics produce Figure 1 and Figure 2."""
    scheduled, rows = _roster_rows()
    result = analyze_partition(
        rows, tmp_path, roster=scheduled, audit_rows=_audit_rows(True), n_boot=50
    )
    assert {"live", "audit"} <= set(result.figures)
    sheet = json.loads(result.fact_sheet.read_text(encoding="utf-8"))
    names = {item["name"] for item in sheet["numbers"]}
    assert "roster.baseline.scheduled" in names
    assert "endpoint.baseline.restricted_publication.known_events" in names


def test_r_anl_08_analyze_partition_refuses_mixed_partitions(tmp_path: Path):
    """R-ANL-04, R-ANL-08: one partition per analysis."""
    rows = [row("baseline", 200), row("guarded", 200, partition="mock-other")]
    with pytest.raises(ValueError, match="mix"):
        analyze_partition(rows, tmp_path)
