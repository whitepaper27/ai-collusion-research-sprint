"""W5 analyze_partition defaults: planned rosters and Figure 3 run selection.

R-ANL-01 / R-ANL-08: a live partition's planned roster comes from SEEDS (exact key or base
prefix) so not-started cells never vanish; a live partition with no derivable roster raises.
R-ANL-07: the auto-picked Figure 3 positive is source-linked (restricted publication, then
attempt, then a provenance block), never a run whose only block is a service limit.
"""

from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from ethwall.analysis import analyze_partition
from ethwall.contract import (
    DECISIONS_PER_EPISODE,
    ORD_LIMIT,
    PROV_MSG,
    Event,
    EventKind,
    TerminalState,
    event_to_dict,
)
from ethwall.evaluate import evaluate_run
from ethwall.testing import MemoryRecorder, tiny_world


def _stream(
    run_id: str, condition: str, seed: int, block: tuple[str, str, str] | None
) -> tuple[Event, ...]:
    rec = MemoryRecorder(run_id)
    manifest = {"condition": condition, "seed": seed, "mode": "mock"}
    manifest["partition"] = "mock-p"
    rec.append(EventKind.RUN_START, "ORCHESTRATOR", {"manifest": manifest})
    if block is not None:
        boundary, rule, ref = block
        rec.append(
            EventKind.DECISION,
            "ENFORCEMENT",
            {
                "ref_id": ref,
                "boundary": boundary,
                "rule_id": rule,
                "verdict": "block",
                "reason": "r",
                "labels": [],
            },
        )
    rec.append(
        EventKind.ARTICLE_SUBMITTED,
        "COMMS",
        {
            "article_id": "ART-0001",
            "call_id": "c1",
            "role": "COMMS",
            "claims": [],
            "labels": ["PUBLIC"],
            "position_at_submission": 0,
        },
    )
    rec.append(
        EventKind.ARTICLE_ACCEPTED,
        "PUBLICATION",
        {"article_id": "ART-0001", "rendered": ""},
    )
    n = DECISIONS_PER_EPISODE
    rec.close(
        TerminalState.COMPLETED,
        {"decisions": n, "transport_attempts": n, "actions": 1, "proposed_calls": 1},
    )
    return rec.events()


def _write_run(root: Path, events: tuple[Event, ...]) -> Path:
    d = root / events[0].run_id
    d.mkdir(parents=True)
    lines = [json.dumps(event_to_dict(e)) for e in events]
    (d / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest: dict[str, Any] = dict(events[0].payload["manifest"])
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d


def test_r_anl_07_auto_pick_prefers_provenance_block_over_service_limit(tmp_path: Path):
    """R-ANL-07: an ORD_LIMIT-only run is never the source-linked positive."""
    runs = tmp_path / "runs"
    dirs = [
        _write_run(
            runs, _stream("a_run", "baseline", 0, ("entitlement", ORD_LIMIT, "ORD-1"))
        ),
        _write_run(
            runs, _stream("b_run", "guarded", 0, ("message", PROV_MSG, "MSG-0001"))
        ),
        _write_run(runs, _stream("c_run", "baseline", 1, None)),
    ]
    result = analyze_partition(dirs, tmp_path / "out", world_for=tiny_world, n_boot=20)
    fig = result.figures["trajectory"]
    with fig.csv.open(newline="", encoding="utf-8") as f:
        data = list(csv.DictReader(f))
    blocks = [r for r in data if r["lane"] == "block"]
    assert [(r["panel"], r["ref"]) for r in blocks] == [("trajectory", "MSG-0001")]
    assert "MOCK" in fig.title


def _live_row(partition: str):
    base = evaluate_run(_stream("live_run", "baseline", 200, None), tiny_world())
    return replace(base, mode="live", partition=partition, seed=200)


def test_r_anl_01_live_partition_variant_uses_planned_seed_roster(tmp_path: Path):
    """R-ANL-01: `main-r2` schedules the main seeds; missing cells are not started."""
    result = analyze_partition([_live_row("main-r2")], tmp_path, n_boot=20)
    with result.tables["roster"].open(newline="", encoding="utf-8") as f:
        counts = {r["condition"]: r for r in csv.DictReader(f)}
    assert counts["baseline"]["scheduled"] == "10"
    assert counts["baseline"]["not_started"] == "9"
    assert counts["guarded"]["not_started"] == "10"


def test_r_anl_01_live_partition_without_roster_raises(tmp_path: Path):
    """R-ANL-01: a live partition with no planned roster needs an explicit roster."""
    with pytest.raises(ValueError, match="roster"):
        analyze_partition([_live_row("adhoc")], tmp_path, n_boot=20)
