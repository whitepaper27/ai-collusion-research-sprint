"""Protocol freeze: create and validate `protocol/frozen/<id>/freeze.json`
(docs/specs/recorder-freeze.md R-FRZ-01..03; ARCHITECTURE.md §9).

Exports: `create_freeze(freeze_id, repo_root=None) -> Path`,
`validate_freeze(freeze_id, repo_root=None) -> list[str]` (empty = valid) and
`require_valid_freeze(config, repo_root=None)` for the CLI's main-partition gate.

freeze.json records the git commit, the source hashes of the run manifest (world generator =
world/ + scoring/, enforcement, the contract's entitlement tables `policy_data`, services, tool
schemas, analysis config) plus `prompts_dir` (tree hash of protocol/draft/prompts/, every file), the contract seeds and caps, and the model ids
from `ACTOR_MODEL_ID` / `AUDITOR_MODEL_ID`. Never any API key or the recorder key.

`arm_hashes` (docs/specs/incentive-arm.md R-INC-13) records, per non-core arm, the tree hash of
its prompt directory (incentive: `protocol/draft/prompts-incentive/`) and its tool-schema hash;
the core arm's are `hashes.prompts_dir` and `hashes.tool_schemas`. It is validated whenever the
freeze records it (freezes made before the field existed are validated on their own fields).

Validation recomputes every item and names each one that differs, and treats a dirty working
tree (ignoring `runs/`) as a mismatch. It does not require HEAD to equal the frozen commit:
committing freeze.json itself moves HEAD; the hashes are what must still match.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ethwall.contract import (
    BATCH_CAP_USD,
    DECISIONS_PER_EPISODE,
    EPISODE_CAP_USD,
    MAX_ACTIONS_PER_EPISODE,
    MAX_TOOL_CALLS_PER_RESPONSE,
    MAX_TRANSPORT_RETRIES,
    SCHEMA_VERSION,
    SEEDS,
    TOTAL_CAP_USD,
    WALL_CLOCK_SECONDS,
    EpisodeConfig,
    canonical_json,
    to_jsonable,
)
from ethwall.recorder._log import utc_now
from ethwall.recorder._manifest import (
    arm_hashes,
    default_repo_root,
    git_state,
    prompts_dir_sha256,
    source_hashes,
)

FROZEN_DIR = "protocol/frozen"
FREEZE_FILE = "freeze.json"
MODEL_ID_ENV: Mapping[str, str] = {
    "actor": "ACTOR_MODEL_ID",
    "auditor": "AUDITOR_MODEL_ID",
}


class FreezeError(RuntimeError):
    pass


def _root(repo_root: str | os.PathLike[str] | None) -> Path:
    return Path(repo_root) if repo_root is not None else default_repo_root()


def _freeze_path(root: Path, freeze_id: str) -> Path:
    if not freeze_id or freeze_id in (".", "..") or any(c in freeze_id for c in "/\\"):
        raise ValueError(f"invalid freeze id {freeze_id!r}")
    return root / FROZEN_DIR / freeze_id / FREEZE_FILE


def _hashes(root: Path) -> dict[str, str | None]:
    return {"prompts_dir": prompts_dir_sha256(root), **source_hashes(root)}


def _caps() -> dict[str, Any]:
    return {
        "decisions": DECISIONS_PER_EPISODE,
        "actions": MAX_ACTIONS_PER_EPISODE,
        "tool_calls_per_response": MAX_TOOL_CALLS_PER_RESPONSE,
        "transport_retries": MAX_TRANSPORT_RETRIES,
        "wall_clock_seconds": WALL_CLOCK_SECONDS,
        "episode_cap_usd": dict(EPISODE_CAP_USD),
        "batch_cap_usd": dict(BATCH_CAP_USD),
        "total_cap_usd": TOTAL_CAP_USD,
    }


def _model_ids() -> dict[str, str | None]:
    return {k: (os.environ.get(v) or None) for k, v in MODEL_ID_ENV.items()}


def create_freeze(
    freeze_id: str, repo_root: str | os.PathLike[str] | None = None
) -> Path:
    """Write protocol/frozen/<id>/freeze.json (R-FRZ-01). Refuses an existing id, a working
    tree without git metadata, and a dirty tree (the recorded commit must describe it)."""
    root = _root(repo_root)
    path = _freeze_path(root, freeze_id)
    if path.parent.exists():
        raise FileExistsError(f"freeze {freeze_id!r} already exists: {path.parent}")
    commit, dirty = git_state(root)
    if commit is None or dirty is None:
        raise FreezeError(f"{root} is not a git working tree; cannot freeze")
    if dirty:
        raise FreezeError("working tree is dirty; commit before freezing")
    record = {
        "schema_version": SCHEMA_VERSION,
        "freeze_id": freeze_id,
        "created_utc": utc_now(),
        "git_commit": commit,
        "hashes": _hashes(root),
        "arm_hashes": arm_hashes(root),
        "seeds": to_jsonable(SEEDS),
        "caps": _caps(),
        "model_ids": _model_ids(),
    }
    path.parent.mkdir(parents=True)
    with open(path, "x", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return path


def _differs(name: str, frozen: Any, current: Any) -> str | None:
    if canonical_json(frozen) == canonical_json(current):
        return None
    return f"{name}: frozen {frozen!r} != current {current!r}"


def validate_freeze(
    freeze_id: str, repo_root: str | os.PathLike[str] | None = None
) -> list[str]:
    """Recompute every frozen item; return one message per mismatch, each starting with the
    item's name (`git_dirty`, `hashes.<name>`, `seeds`, `caps`, `model_ids.<role>`)."""
    root = _root(repo_root)
    path = _freeze_path(root, freeze_id)
    try:
        frozen = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [f"freeze_missing: {path} does not exist"]
    except (OSError, ValueError) as exc:
        return [f"freeze_unreadable: {path}: {exc}"]
    if not isinstance(frozen, dict):
        return [f"freeze_unreadable: {path}: not a JSON object"]

    problems: list[str] = []
    _commit, dirty = git_state(root)
    if dirty is None:
        problems.append("git_dirty: no git metadata; cleanliness unknown")
    elif dirty:
        problems.append("git_dirty: working tree has uncommitted changes")
    if frozen.get("freeze_id") != freeze_id:
        problems.append(f"freeze_id: file says {frozen.get('freeze_id')!r}")

    frozen_hashes = frozen.get("hashes") or {}
    for name, current in _hashes(root).items():
        p = _differs(f"hashes.{name}", frozen_hashes.get(name, "<absent>"), current)
        if p:
            problems.append(p)
    if "arm_hashes" in frozen:
        frozen_arms = frozen.get("arm_hashes")
        frozen_arms = frozen_arms if isinstance(frozen_arms, dict) else {}
        for arm, items in arm_hashes(root).items():
            recorded = frozen_arms.get(arm)
            recorded = recorded if isinstance(recorded, dict) else {}
            for name, current in items.items():
                p = _differs(
                    f"arm_hashes.{arm}.{name}", recorded.get(name, "<absent>"), current
                )
                if p:
                    problems.append(p)
    for name, current in (("seeds", to_jsonable(SEEDS)), ("caps", _caps())):
        p = _differs(name, frozen.get(name), current)
        if p:
            problems.append(p)
    frozen_models = frozen.get("model_ids") or {}
    for role, current in _model_ids().items():
        p = _differs(f"model_ids.{role}", frozen_models.get(role), current)
        if p:
            problems.append(p)
    return problems


def require_valid_freeze(
    config: EpisodeConfig, repo_root: str | os.PathLike[str] | None = None
) -> None:
    """R-FRZ-02 gate: main-partition runs need a freeze id whose freeze validates cleanly.

    No-op for every other partition. Raises FreezeError naming each mismatch."""
    if config.partition.split("-")[0] != "main":
        return
    if not config.freeze_id:
        raise FreezeError("main partitions require a freeze id")
    problems = validate_freeze(config.freeze_id, repo_root)
    if config.arm != "core" and not problems:
        # A non-core arm needs a freeze that recorded its prompts and schemas (R-INC-13).
        path = _freeze_path(_root(repo_root), config.freeze_id)
        frozen = json.loads(path.read_text(encoding="utf-8"))
        if config.arm not in (frozen.get("arm_hashes") or {}):
            problems.append(f"arm_hashes.{config.arm}: not recorded by this freeze")
    if problems:
        raise FreezeError(
            f"freeze {config.freeze_id!r} is not valid: " + "; ".join(problems)
        )


__all__ = [
    "FreezeError",
    "create_freeze",
    "require_valid_freeze",
    "validate_freeze",
]
