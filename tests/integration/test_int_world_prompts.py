"""Integration re-check of R-PRM-04 and R-WLD-08 with the real world and the real prompts."""

import re

from ethwall.contract import Role
from ethwall.prompts import load_prompts
from ethwall.world import build_world


def _forms(value) -> set[str]:
    if isinstance(value, bool):
        return {str(value)}
    if isinstance(value, (int, float)):
        return {str(value), f"{value:,}"}
    return {str(value)}


def test_no_restricted_value_appears_in_any_rendered_prompt_seeds_0_199():
    """R-PRM-04: no restricted fact value from the real world appears in any rendered prompt."""
    prompts = load_prompts()
    texts = [prompts.system(r, control=c) for r in Role for c in (False, True)]
    texts.append(prompts.budget_notice())
    blob = "\n".join(texts)
    hits = []
    for seed in range(200):
        for f in build_world(seed).facts:
            if f.status.value != "restricted":
                continue
            for form in _forms(f.value):
                if re.search(rf"(?<![\w.]){re.escape(form)}(?![\w.]|\d)", blob):
                    hits.append((seed, f.fact_id, form))
    assert hits == []
