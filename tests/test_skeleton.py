"""The walking skeleton proves the contract end to end before the parallel build."""

import pytest

from ethwall.contract import Condition, EventKind, Label
from ethwall.skeleton import evaluate_skeleton, run_skeleton_episode, skeleton_policies
from ethwall.testing import MemoryRecorder, ScriptedModel, chain_ok, tiny_world


def run(condition: Condition, leaky: bool):
    world = tiny_world()
    rec = MemoryRecorder(f"{condition.value}-{leaky}")
    model = ScriptedModel(skeleton_policies(world, leaky))
    run_skeleton_episode(world, condition, model, rec)
    return world, rec.events(), model


@pytest.mark.parametrize("condition", list(Condition))
def test_honest_episode_runs_30_decisions_without_violation(condition):
    world, events, model = run(condition, leaky=False)
    assert len(model.requests) == 30
    assert chain_ok(events)
    result = evaluate_skeleton(events, world)
    assert result == {
        "restricted_publication": False,
        "accepted_articles": 1,
        "blocks": [],
    }


def test_leaky_baseline_publishes_restricted_fact():
    world, events, _ = run(Condition.BASELINE, leaky=True)
    assert evaluate_skeleton(events, world)["restricted_publication"] is True


def test_leaky_guarded_blocks_message_before_delivery():
    world, events, model = run(Condition.GUARDED, leaky=True)
    result = evaluate_skeleton(events, world)
    assert result["restricted_publication"] is False
    assert "PROV_MSG" in result["blocks"]
    assert not any(e.kind == EventKind.MESSAGE_DELIVERED for e in events)
    comms_text = " ".join(
        str(r.messages) for r in model.requests if r.role.value == "COMMS"
    )
    assert "212.4" not in comms_text


def test_taint_reaches_recipient_context_in_baseline():
    _, events, _ = run(Condition.BASELINE, leaky=True)
    comms_obs = [
        e for e in events if e.kind == EventKind.OBSERVATION and e.actor == "COMMS"
    ]
    assert any(Label.LEGAL.value in e.labels for e in comms_obs)
