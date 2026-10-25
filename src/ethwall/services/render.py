"""Deterministic article renderer (sol.md §7 publication boundary, R-SVC-08).

Renders only validated claim fields and safe citation identifiers. Free-form strings
(entity ids, string values) are JSON-escaped so they cannot add lines or markup; a cited id
that is not a world document id is replaced by a fixed marker instead of being echoed. No
document body, headline or raw tool argument ever reaches the output.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from ethwall.contract import World

UNRECOGNIZED_CITATION = "<unrecognized-id>"


def _scalar(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False)


def render_article(claims: Sequence[Mapping[str, Any]], world: World) -> str:
    """Render schema-validated claims. Same claims and world -> byte-identical output."""
    known = {d.doc_id for d in world.documents}
    lines = []
    for i, c in enumerate(claims, start=1):
        cites = ", ".join(
            sid if isinstance(sid, str) and sid in known else UNRECOGNIZED_CITATION
            for sid in c["source_doc_ids"]
        )
        lines.append(
            f"{i}. {_scalar(c['entity_id'])} {c['predicate']} = {_scalar(c['value'])} "
            f"({c['qualifier']}) [sources: {cites}]"
        )
    return "\n".join(lines)
