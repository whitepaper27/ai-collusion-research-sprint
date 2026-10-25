import dataclasses
import re
from pathlib import Path

import pytest

from ethwall import contract as c
from ethwall.testing import MemoryRecorder, chain_ok, tiny_world

SRC = Path(__file__).resolve().parent.parent / "src" / "ethwall"


def test_world_types_are_immutable():
    doc = tiny_world().documents[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        doc.body = "changed"
    with pytest.raises(AttributeError):
        doc.allowed_readers.add(c.Role.TRADING)


def test_entitlements_match_sol_section_2():
    # No analyst trades or publishes; trader/comms never read Legal/HR directly.
    for role in (c.Role.MARKET, c.Role.LEGAL, c.Role.HR):
        assert c.ToolName.PLACE_ORDER not in c.TOOLS_BY_ROLE[role]
        assert c.ToolName.PUBLISH_ARTICLE not in c.TOOLS_BY_ROLE[role]
    for role in (c.Role.TRADING, c.Role.COMMS):
        assert not c.READS_BY_ROLE[role] & {c.Store.LEGAL, c.Store.HR}
        assert c.CLEARANCE_BY_ROLE[role] == {c.Label.PUBLIC}
    assert c.ToolName.PLACE_ORDER in c.TOOLS_BY_ROLE[c.Role.TRADING]
    assert c.ToolName.PUBLISH_ARTICLE in c.TOOLS_BY_ROLE[c.Role.COMMS]
    assert c.SCHEDULE == (
        c.Role.MARKET,
        c.Role.LEGAL,
        c.Role.HR,
        c.Role.TRADING,
        c.Role.COMMS,
    )


def test_caps_match_sol_section_9():
    assert (c.ROUNDS * len(c.SCHEDULE), c.DECISIONS_PER_EPISODE) == (30, 30)
    assert (c.MAX_TOOL_CALLS_PER_RESPONSE, c.MAX_ACTIONS_PER_EPISODE) == (3, 90)
    assert sum(c.BATCH_CAP_USD.values()) == c.TOTAL_CAP_USD == 50.0


def test_labels_follow_rule_2():
    w = tiny_world()
    assert c.label_of(w.get("LEGAL-contract-0001")) == c.Label.LEGAL
    assert c.label_of(w.get("HR-restructuring-0001")) == c.Label.HR
    assert c.label_of(w.get("HR-hiring-0001")) == c.Label.PUBLIC  # cleared


def test_publication_schema_has_no_free_text_fields():
    schema = c.TOOL_SPECS[c.ToolName.PUBLISH_ARTICLE].input_schema
    assert set(schema["properties"]) == {"claims"}
    claim = schema["properties"]["claims"]["items"]
    assert claim["additionalProperties"] is False
    assert set(claim["properties"]) == {
        "entity_id",
        "predicate",
        "value",
        "qualifier",
        "source_doc_ids",
    }


def test_payload_validation_rejects_missing_keys():
    with pytest.raises(c.PayloadError):
        c.validate_payload(c.EventKind.MESSAGE_SUBMITTED, {"message_id": "m"})
    rec = MemoryRecorder()
    with pytest.raises(c.PayloadError):
        rec.append(c.EventKind.ORDER_FILLED, "X", {"order_id": "o"})


def test_event_round_trip_and_chain():
    rec = MemoryRecorder()
    rec.append(
        c.EventKind.PRICE_ADVANCED, "MARKET_SERVICE", {"round": 1, "price": 101.2}
    )
    counts = {
        "decisions": 0,
        "transport_attempts": 0,
        "actions": 0,
        "proposed_calls": 0,
    }
    rec.close(c.TerminalState.COMPLETED, counts)
    events = rec.events()
    assert [c.event_from_dict(c.event_to_dict(e)) for e in events] == list(events)
    assert chain_ok(events)
    with pytest.raises(RuntimeError):
        rec.append(c.EventKind.PRICE_ADVANCED, "X", {"round": 2, "price": 1.0})


def test_config_validation():
    ok = c.EpisodeConfig(c.Condition.BASELINE, 0, c.Mode.MOCK, "mock-dev", "scripted")
    ok.validate()
    bad = [
        c.EpisodeConfig(c.Condition.BASELINE, 0, c.Mode.MOCK, "main", "scripted"),
        c.EpisodeConfig(
            c.Condition.BASELINE, 200, c.Mode.LIVE, "main", "m"
        ),  # no freeze
        c.EpisodeConfig(
            c.Condition.BASELINE, 100, c.Mode.LIVE, "smoke", ""
        ),  # no model
        c.EpisodeConfig(
            c.Condition.BASELINE, 150, c.Mode.LIVE, "control", "m"
        ),  # not control
    ]
    for cfg in bad:
        with pytest.raises(ValueError):
            cfg.validate()


def test_world_json_is_canonical():
    assert tiny_world().to_json() == tiny_world().to_json()
    assert len(tiny_world().sha256()) == 64


def test_source_avoids_post_310_apis_and_literal_model_ids():
    banned = re.compile(
        r"^\s*(import tomllib|from tomllib|from enum import .*StrEnum|from typing import .*\bSelf\b)"
        r"|\(StrEnum\)|\"claude-",
        re.MULTILINE,
    )
    for f in SRC.rglob("*.py"):
        assert not banned.search(f.read_text(encoding="utf-8")), f
