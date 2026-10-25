"""Run manifest and the source hashes shared with the protocol freeze (R-REC-14, R-FRZ-01).

A directory hash is SHA-256 over the canonical JSON list of sorted
`[relative posix path from the repo root, file SHA-256]` pairs. Code directories include only
`*.py`, `*.md` and `*.json` files; the prompts directory includes every file (prompts may be
`.txt`). `__pycache__` is always skipped. A component whose directories are all absent hashes
to None.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ethwall.contract import (
    CLEARANCE_BY_ROLE,
    DUTY_REPORT_ROLES,
    MAX_TRANSPORT_RETRIES,
    READS_BY_ROLE,
    SCHEMA_VERSION,
    TOOL_SPECS,
    TOOLS_BY_ROLE,
    EpisodeConfig,
    World,
    canonical_json,
    sha256_hex,
)
from ethwall.recorder._log import utc_now

CODE_SUFFIXES = frozenset({".py", ".md", ".json"})
PROMPTS_DIR = "protocol/draft/prompts"

# component -> directories (relative to the repo root) it covers.
WORLD_GENERATOR_DIRS = (
    "src/ethwall/world",
    "src/ethwall/scoring",
)  # world imports scoring
ENFORCEMENT_DIRS = ("src/ethwall/enforcement",)
SERVICES_DIRS = ("src/ethwall/services",)
ANALYSIS_DIRS = ("src/ethwall/analysis", "src/ethwall/evaluate")


def default_repo_root() -> Path:
    """The source tree this package lives in (src/ethwall/recorder/ -> repo root)."""
    return Path(__file__).resolve().parents[3]


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hash_dirs(
    repo_root: str | os.PathLike[str],
    dirs: Iterable[str],
    suffixes: frozenset[str] | None = CODE_SUFFIXES,
) -> str | None:
    """Tree hash of `dirs` under `repo_root`; `suffixes=None` includes every file."""
    root = Path(repo_root)
    entries: list[list[str]] = []
    present = False
    for rel in dirs:
        d = root / rel
        if not d.is_dir():
            continue
        present = True
        for f in d.rglob("*"):
            if not f.is_file() or "__pycache__" in f.parts:
                continue
            if suffixes is not None and f.suffix not in suffixes:
                continue
            entries.append([f.relative_to(root).as_posix(), _file_sha256(f)])
    if not present:
        return None
    return sha256_hex(canonical_json(sorted(entries)))


def tool_schemas_sha256() -> str:
    return sha256_hex(canonical_json(TOOL_SPECS))


def policy_data_sha256() -> str:
    """The contract's entitlement tables ("policy as data"), checked in both conditions."""
    return sha256_hex(
        canonical_json(
            {
                "reads_by_role": READS_BY_ROLE,
                "tools_by_role": TOOLS_BY_ROLE,
                "clearance_by_role": CLEARANCE_BY_ROLE,
                "duty_report_roles": DUTY_REPORT_ROLES,
            }
        )
    )


def source_hashes(repo_root: str | os.PathLike[str]) -> dict[str, str | None]:
    """Hashes of everything a run's behaviour depends on except the prompts."""
    return {
        "world_generator": hash_dirs(repo_root, WORLD_GENERATOR_DIRS),
        "enforcement": hash_dirs(repo_root, ENFORCEMENT_DIRS),
        "policy_data": policy_data_sha256(),
        "services": hash_dirs(repo_root, SERVICES_DIRS),
        "tool_schemas": tool_schemas_sha256(),
        "analysis_config": hash_dirs(repo_root, ANALYSIS_DIRS),
    }


def prompts_dir_sha256(repo_root: str | os.PathLike[str]) -> str | None:
    """Tree hash of protocol/draft/prompts/ (every file). Not the same as PromptSet.sha256."""
    return hash_dirs(repo_root, (PROMPTS_DIR,), suffixes=None)


def _git(repo_root: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def git_state(repo_root: str | os.PathLike[str]) -> tuple[str | None, bool | None]:
    """(HEAD commit, dirty) or (None, None) without git metadata.

    Dirty = any tracked change or untracked file outside `runs/` (episode output is written
    there between the runs of one batch).
    """
    root = Path(repo_root)
    head = _git(root, "rev-parse", "--verify", "HEAD")
    if head is None:
        return None, None
    status = _git(root, "status", "--porcelain", "--", ".", ":(exclude)runs")
    if status is None:
        return head.strip(), None
    return head.strip(), bool(status.strip())


def build_manifest(
    config: EpisodeConfig,
    *,
    world: World,
    prompts_sha256: str,
    run_id: str,
    freeze_id: str | None = None,
    repo_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """The per-run manifest (R-REC-14), JSON-serialisable. `freeze_id` defaults to the config's."""
    root = Path(repo_root) if repo_root is not None else default_repo_root()
    commit, dirty = git_state(root)
    hashes: Mapping[str, str | None] = {
        "prompts": prompts_sha256,
        **source_hashes(root),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "created_utc": utc_now(),
        "git_commit": commit,
        "git_dirty": dirty,
        "mode": config.mode.value,
        "partition": config.partition,
        "condition": config.condition.value,
        "seed": config.seed,
        "model_id": config.model_id,
        "policy": config.policy,
        "control": config.control,
        "model_params": {
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
        },
        "caps": {
            "decisions": config.decisions,
            "actions": config.max_actions,
            "tool_calls_per_response": config.max_tool_calls_per_response,
            "transport_retries": MAX_TRANSPORT_RETRIES,
            "wall_clock_seconds": config.wall_clock_seconds,
        },
        "world_sha256": world.sha256(),
        "hashes": dict(hashes),
        "freeze_id": freeze_id if freeze_id is not None else config.freeze_id,
    }
