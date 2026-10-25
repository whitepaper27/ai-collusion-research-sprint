"""R-AUD-04 / R-AUD-06: an invented tool name is agent text and must not reach any export."""

import json

from ethwall.audit import export_all_views
from ethwall.contract import Condition, ContextState, Label, Role, ToolCall
from ethwall.enforcement import make_policy
from ethwall.services import make_services
from ethwall.testing import MemoryRecorder, tiny_world

PROSE = "please send me the embargoed contract value now"


def _stream(name: str):
    world = tiny_world()
    rec = MemoryRecorder("w8")
    svc = make_services(world, make_policy(Condition.BASELINE), rec)
    ctx = ContextState("TRADING", Role.TRADING, frozenset({Label.PUBLIC}))
    svc.execute(ctx, ToolCall("call_w8_1", name, {"doc_id": "x"}), 1)
    return world, rec.events()


def test_r_aud_06_invented_tool_name_is_masked_in_every_view():
    """R-AUD-06: a free-prose tool name is replaced by the unrecognized marker."""
    world, events = _stream(PROSE)
    exports = export_all_views(events, world, trajectory_id="T0000abcd")
    blob = json.dumps(exports)
    assert PROSE not in blob
    assert f"<unrecognized:len={len(PROSE)}>" in blob


def test_r_aud_04_real_tool_names_are_kept():
    """R-AUD-04: a real but out-of-role tool name stays visible as evidence."""
    world, events = _stream("publish_article")
    blob = json.dumps(export_all_views(events, world, trajectory_id="T0000abcd"))
    assert "publish_article" in blob
