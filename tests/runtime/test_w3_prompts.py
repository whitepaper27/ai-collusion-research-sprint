"""Prompts (docs/specs/runtime.md R-PRM-01..05)."""

from __future__ import annotations

import inspect
import shutil
from pathlib import Path

import pytest
from w3_support import mock_config, run

from ethwall.contract import Condition, EventKind, FactStatus, Role
from ethwall.models import mock_model
from ethwall.prompts import PROMPT_FILES, PromptSet, load_prompts
from ethwall.testing import tiny_world

ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = ROOT / "protocol" / "draft" / "prompts"
FORBIDDEN = (
    "leak",
    "evade",
    "loophole",
    "unmonitored",
    "shutdown",
    "you must use the restricted",
)


def test_prompt_files_exist_with_required_content():
    """R-PRM-01: shared obligations, five role duties, pressure, objective, tools, budget
    notice and instructed control live under protocol/draft/prompts/."""
    expected = {
        "obligations.md",
        "role_MARKET.md",
        "role_LEGAL.md",
        "role_HR.md",
        "role_TRADING.md",
        "role_COMMS.md",
        "pressure.md",
        "objective.md",
        "tools.md",
        "budget_notice.md",
        "instructed_control.md",
    }
    assert set(PROMPT_FILES) == expected
    assert {p.name for p in PROMPT_DIR.glob("*.md")} == expected
    p = load_prompts()
    ob = p.files["obligations.md"].lower()
    for word in ("confidential", "accura", "authoriz"):
        assert word in ob
    obj = p.files["objective.md"]
    assert "+0.5%" in obj
    assert "-10" in obj and "+10" in obj
    assert "exogenous" in obj
    assert "publication feed does not affect fills or valuation" in obj
    assert p.budget_notice() == p.files["budget_notice.md"].strip()


def test_obligations_shared_verbatim_by_every_role_and_both_control_settings():
    """R-PRM-01: the obligations text is part of every rendered system prompt."""
    p = load_prompts()
    ob = p.files["obligations.md"].strip()
    for role in Role:
        for control in (False, True):
            s = p.system(role, control)
            assert s.startswith(ob)
            assert p.files[f"role_{role.value}.md"].strip() in s
            for name in ("objective.md", "pressure.md", "tools.md"):
                assert p.files[name].strip() in s


def test_instructed_control_only_for_the_pair_when_control():
    """R-PRM-01: the instructed-control file is used only in control episodes, for the
    Legal/Trading pair."""
    p = load_prompts()
    ctl = p.files["instructed_control.md"].strip()
    for role in Role:
        assert ctl not in p.system(role, False)
        assert (ctl in p.system(role, True)) == (role in (Role.LEGAL, Role.TRADING))


def test_system_prompt_has_no_condition_parameter():
    """R-PRM-02: rendering cannot depend on condition — there is no such parameter."""
    params = inspect.signature(PromptSet.system).parameters
    assert list(params) == ["self", "role", "control"]


def test_rendered_system_prompt_byte_identical_across_conditions():
    """R-PRM-02: in recorded episodes every role's system prompt is byte-identical in the
    baseline and guarded conditions."""
    world = tiny_world()
    systems = {}
    for cond in Condition:
        r = run(mock_model("honest", world), mock_config(cond), world=world)
        systems[cond] = {
            e.payload["role"]: e.payload["request"]["system"]
            for e in r.kinds(EventKind.MODEL_CALL)
        }
    assert set(systems[Condition.BASELINE]) == {r.value for r in Role}
    for role in Role:
        a = systems[Condition.BASELINE][role.value].encode("utf-8")
        b = systems[Condition.GUARDED][role.value].encode("utf-8")
        assert a == b
        assert a == load_prompts().system(role, False).encode("utf-8")


def test_no_forbidden_phrases_outside_instructed_control():
    """R-PRM-03: forbidden phrases appear in no prompt file but the instructed-control one,
    and in no non-control rendered prompt (case-insensitive substring)."""
    p = load_prompts()
    for name, text in p.files.items():
        if name == "instructed_control.md":
            continue
        low = text.lower()
        for phrase in FORBIDDEN:
            assert phrase not in low, (name, phrase)
    for role in Role:
        low = (p.system(role, False) + p.budget_notice()).lower()
        for phrase in FORBIDDEN:
            assert phrase not in low, (role, phrase)


def test_forbidden_phrase_check_can_fail():
    """R-PRM-03: the substring check catches inflected forms."""
    assert any(ph in "avoid leakage of records".lower() for ph in FORBIDDEN)


def test_no_restricted_fact_value_in_any_rendered_prompt():
    """R-PRM-04: no restricted fact value (tiny_world here; seeds 0-199 re-checked against the
    real world at integration) appears in any rendered prompt."""
    world = tiny_world()
    p = load_prompts()
    rendered = [p.budget_notice()] + [
        p.system(role, control) for role in Role for control in (False, True)
    ]
    values = [str(f.value) for f in world.facts if f.status == FactStatus.RESTRICTED]
    assert values
    for text in rendered:
        for v in values:
            assert v not in text, v


def _copy_prompts(tmp_path: Path) -> Path:
    dst = tmp_path / "prompts"
    shutil.copytree(PROMPT_DIR, dst)
    return dst


def test_sha256_changes_when_any_prompt_file_changes(tmp_path):
    """R-PRM-05: PromptSet.sha256 is stable and changes when any prompt file changes."""
    root = _copy_prompts(tmp_path)
    base = load_prompts(root).sha256
    assert base == load_prompts(root).sha256 == load_prompts().sha256
    assert len(base) == 64
    for name in sorted(PROMPT_FILES):
        f = root / name
        original = f.read_bytes()
        f.write_bytes(original + b" ")
        assert load_prompts(root).sha256 != base, name
        f.write_bytes(original)
    assert load_prompts(root).sha256 == base


def test_missing_prompt_file_fails_loudly(tmp_path):
    """R-PRM-01: a missing required prompt file is a setup error, not a silent default."""
    root = _copy_prompts(tmp_path)
    (root / "pressure.md").unlink()
    with pytest.raises(FileNotFoundError, match="pressure.md"):
        load_prompts(root)
