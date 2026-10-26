"""Instructed controls cover both event directions (R-INC-03), so an always-short or
always-long control cannot pass for one that followed the restricted forecast."""

from ethwall.contract import SEEDS
from ethwall.world.incentive import event_direction


def test_r_inc_03_instructed_control_seeds_cover_both_directions():
    assert {event_direction(s) for s in SEEDS["control-incentive"]} == {"up", "down"}
