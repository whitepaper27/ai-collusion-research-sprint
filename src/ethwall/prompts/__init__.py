"""Prompt loading and rendering (docs/specs/runtime.md R-PRM-01..05).

Prompt text lives in `protocol/draft/prompts/` (frozen copies under `protocol/frozen/<id>/`).
A role's system prompt is the shared obligations, the role's duty, the team objective, the
pressure notice and the tool guide, joined by blank lines. There is deliberately no condition
parameter: the rendered prompt is byte-identical in `baseline` and `guarded` (R-PRM-02). With
`control=True` the instructed-control objective is appended for the instructed pair only
(Legal and Trading); the other roles' prompts are unchanged in control episodes.

Prompts are static text: no world values are templated in (R-PRM-04).

Arms (docs/specs/incentive-arm.md R-INC-08): `load_prompts(arm="incentive")` reads
`protocol/draft/prompts-incentive/` (the same files; obligations byte-identical to core, the
objective carrying the owner's deployment-selection and handoff text). `arm="core"` (the
default) is unchanged. Whether a role gets the instructed-control text is the caller's
`control` flag; the runtime passes it only for `instructed_harmful` controls (R-INC-10).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from ethwall.contract import ARMS, Role, sha256_hex

DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "protocol" / "draft" / "prompts"
INCENTIVE_ROOT = DEFAULT_ROOT.with_name("prompts-incentive")
ROOTS: Mapping[str, Path] = MappingProxyType(
    {"core": DEFAULT_ROOT, "incentive": INCENTIVE_ROOT}
)

PROMPT_FILES: tuple[str, ...] = (
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
)
CONTROL_PAIR = frozenset({Role.LEGAL, Role.TRADING})


@dataclass(frozen=True)
class PromptSet:
    root: Path
    files: Mapping[str, str]
    sha256: str

    def system(self, role: Role, control: bool = False) -> str:
        parts = [
            self.files["obligations.md"],
            self.files[f"role_{Role(role).value}.md"],
            self.files["objective.md"],
            self.files["pressure.md"],
            self.files["tools.md"],
        ]
        if control and Role(role) in CONTROL_PAIR:
            parts.append(self.files["instructed_control.md"])
        return "\n\n".join(p.strip() for p in parts)

    def budget_notice(self) -> str:
        return self.files["budget_notice.md"].strip()


def load_prompts(root: str | Path | None = None, arm: str = "core") -> PromptSet:
    """Load every prompt file. `sha256` covers all `*.md` files in `root`, in sorted order.

    Without `root`, the directory is the arm's (`ROOTS[arm]`); an unknown arm raises.
    """
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; one of {ARMS}")
    base = Path(root) if root is not None else ROOTS[arm]
    missing = [n for n in PROMPT_FILES if not (base / n).is_file()]
    if missing:
        raise FileNotFoundError(f"missing prompt files in {base}: {', '.join(missing)}")
    texts: dict[str, str] = {}
    digest_parts: list[bytes] = []
    for path in sorted(base.glob("*.md"), key=lambda p: p.name):
        raw = path.read_bytes()
        texts[path.name] = raw.decode("utf-8")
        digest_parts.append(path.name.encode("utf-8") + b"\0" + raw + b"\0")
    return PromptSet(base, MappingProxyType(texts), sha256_hex(b"".join(digest_parts)))


__all__ = [
    "CONTROL_PAIR",
    "DEFAULT_ROOT",
    "INCENTIVE_ROOT",
    "PROMPT_FILES",
    "ROOTS",
    "PromptSet",
    "load_prompts",
]
