"""Contract v2 (owner amendment 2026-10-25): arms, control kinds, incentive-only schemas.

The core arm is the frozen main-v1 design: its tool specs and worlds must stay byte-identical.
Golden hashes in tests/golden_core.json were captured before any incentive-arm change.
"""

import json
from pathlib import Path

import pytest

from ethwall import contract as c
from ethwall.world import build_world

GOLDEN = json.loads(
    (Path(__file__).parent / "golden_core.json").read_text(encoding="utf-8")
)


def test_core_tool_specs_are_unchanged():
    assert c.tool_specs("core") is c.TOOL_SPECS
    assert c.sha256_hex(c.canonical_json(c.TOOL_SPECS)) == GOLDEN["tool_specs_sha256"]


def test_core_worlds_are_unchanged_for_every_recorded_seed():
    for seed, digest in GOLDEN["worlds"].items():
        assert build_world(int(seed)).sha256() == digest, seed


def test_incentive_schemas_only_widen_the_predicate_enum():
    core = c.canonical_json(c.TOOL_SPECS)
    inc = c.canonical_json(c.tool_specs("incentive"))
    assert "price_event_direction" in inc and "price_event_direction" not in core
    assert inc.replace(',"price_event_direction"', "") == core
    with pytest.raises(ValueError):
        c.tool_specs("nope")


def test_config_arm_and_control_kind_validation():
    ok = c.EpisodeConfig(
        c.Condition.BASELINE, 0, c.Mode.MOCK, "mock-x", "m", arm="incentive"
    )
    ok.validate()
    legacy = c.EpisodeConfig(
        c.Condition.BASELINE, 0, c.Mode.MOCK, "mock-x", "m", control=True
    )
    assert legacy.effective_control_kind == "instructed_harmful"
    lawful = c.EpisodeConfig(
        c.Condition.BASELINE, 0, c.Mode.MOCK, "mock-x", "m",
        control=True, control_kind="lawful_handoff",
    )  # fmt: skip
    assert lawful.effective_control_kind == "lawful_handoff"
    for bad in (
        c.EpisodeConfig(
            c.Condition.BASELINE, 0, c.Mode.MOCK, "mock-x", "m", arm="other"
        ),
        c.EpisodeConfig(
            c.Condition.BASELINE,
            0,
            c.Mode.MOCK,
            "mock-x",
            "m",
            control_kind="lawful_handoff",
        ),
    ):
        with pytest.raises(ValueError):
            bad.validate()


def test_incentive_seeds_are_disjoint_from_core():
    core = {s for k in ("smoke", "control", "main") for s in c.SEEDS[k]}
    inc = {s for k in ("control-lawful", "control-incentive", "smoke-incentive",
                       "main-incentive") for s in c.SEEDS[k]}  # fmt: skip
    assert not core & inc and len(c.SEEDS["main-incentive"]) == 10
