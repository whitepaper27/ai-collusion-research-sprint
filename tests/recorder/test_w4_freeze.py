"""W4 run manifest and protocol freeze (docs/specs/recorder-freeze.md R-REC-14, R-FRZ-01..03).

Every test works in a throwaway git repository under tmp_path with stub source trees; nothing is
written to the real protocol/frozen/ (immutable).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ethwall.contract import (
    CLEARANCE_BY_ROLE,
    DECISIONS_PER_EPISODE,
    DUTY_REPORT_ROLES,
    MAX_ACTIONS_PER_EPISODE,
    MAX_TOOL_CALLS_PER_RESPONSE,
    MAX_TRANSPORT_RETRIES,
    READS_BY_ROLE,
    SCHEMA_VERSION,
    SEEDS,
    TOOL_SPECS,
    TOOLS_BY_ROLE,
    WALL_CLOCK_SECONDS,
    Condition,
    EpisodeConfig,
    Mode,
    canonical_json,
    sha256_hex,
    to_jsonable,
)
from ethwall.freeze import (
    FreezeError,
    create_freeze,
    require_valid_freeze,
    validate_freeze,
)
from ethwall.recorder import build_manifest
from ethwall.testing import tiny_world

STUBS = {
    "src/ethwall/world/_build.py": "SEED = 1\n",
    "src/ethwall/scoring/_score.py": "def score():\n    return 1\n",
    "src/ethwall/enforcement/policy.py": "RULES = ()\n",
    "src/ethwall/services/core.py": "SERVICES = ()\n",
    "protocol/draft/prompts/shared.md": "Keep restricted records confidential.\n",
    "protocol/draft/prompts/budget.txt": "You have used your tool budget.\n",
}
SECRET = "sk-should-never-appear"


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.name=w4", "-c", "user.email=w4@example.invalid",
         "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false", *args],
        cwd=repo, capture_output=True, text=True, check=True,
    )  # fmt: skip
    return proc.stdout.strip()


def commit_all(repo: Path, message: str = "change") -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    for rel, text in STUBS.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    git(root, "init", "-q")
    commit_all(root, "init")
    monkeypatch.setenv("ACTOR_MODEL_ID", "actor-model-x")
    monkeypatch.setenv("AUDITOR_MODEL_ID", "auditor-model-y")
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    monkeypatch.setenv("ETHWALL_RECORDER_KEY", SECRET)
    return root


def edit(repo: Path, rel: str, text: str) -> None:
    (repo / rel).write_bytes(text.encode("utf-8"))


def config(partition: str = "mock-w4", freeze_id: str | None = None) -> EpisodeConfig:
    mode = Mode.MOCK if partition.startswith("mock-") else Mode.LIVE
    return EpisodeConfig(Condition.GUARDED, 200, mode, partition, "actor-model-x",
                         policy="honest" if mode == Mode.MOCK else None,
                         freeze_id=freeze_id)  # fmt: skip


# ----------------------------------------------------------------------------- R-REC-14


def test_manifest_fields_and_hashes(repo):
    """R-REC-14: git commit + dirty flag, model id, condition, seed, partition, mode, caps and
    SHA-256 of prompts, world generator, enforcement, tool schemas, analysis config; freeze id."""
    world = tiny_world()
    m = build_manifest(config(), world=world, prompts_sha256="p" * 64, run_id="r1",
                       freeze_id="frz-1", repo_root=repo)  # fmt: skip
    assert m["schema_version"] == SCHEMA_VERSION
    assert m["run_id"] == "r1" and m["created_utc"]
    assert m["git_commit"] == git(repo, "rev-parse", "HEAD")
    assert m["git_dirty"] is False
    assert (m["mode"], m["partition"], m["condition"], m["seed"]) == (
        "mock", "mock-w4", "guarded", 200
    )  # fmt: skip
    assert m["model_id"] == "actor-model-x"
    assert m["caps"] == {
        "decisions": DECISIONS_PER_EPISODE,
        "actions": MAX_ACTIONS_PER_EPISODE,
        "tool_calls_per_response": MAX_TOOL_CALLS_PER_RESPONSE,
        "transport_retries": MAX_TRANSPORT_RETRIES,
        "wall_clock_seconds": WALL_CLOCK_SECONDS,
    }
    assert m["world_sha256"] == world.sha256()
    h = m["hashes"]
    assert h["prompts"] == "p" * 64
    assert h["tool_schemas"] == sha256_hex(canonical_json(TOOL_SPECS))
    policy = {"reads_by_role": READS_BY_ROLE, "tools_by_role": TOOLS_BY_ROLE,
              "clearance_by_role": CLEARANCE_BY_ROLE, "duty_report_roles": DUTY_REPORT_ROLES}  # fmt: skip
    assert h["policy_data"] == sha256_hex(canonical_json(policy))  # entitlement tables
    for k in ("world_generator", "enforcement", "services"):
        assert isinstance(h[k], str) and len(h[k]) == 64
    assert h["analysis_config"] is None  # no analysis/evaluate package in this repo yet
    assert m["freeze_id"] == "frz-1"
    json.dumps(m)  # JSON-serialisable as written to manifest.json
    assert SECRET not in json.dumps(m)


def test_manifest_hashes_track_sources_and_git_state(repo):
    """R-REC-14: the world-generator hash covers world/ and scoring/; dirty flag tracks edits;
    analysis config is hashed once present; freeze id defaults to the config's."""
    world = tiny_world()

    def manifest(**kw):
        return build_manifest(config(**kw), world=world, prompts_sha256="p" * 64,
                              run_id="r", repo_root=repo)  # fmt: skip

    before = manifest()
    edit(repo, "src/ethwall/scoring/_score.py", "def score():\n    return 2\n")
    dirty = manifest()
    assert dirty["git_dirty"] is True
    assert dirty["hashes"]["world_generator"] != before["hashes"]["world_generator"]
    assert dirty["hashes"]["enforcement"] == before["hashes"]["enforcement"]
    edit(repo, "src/ethwall/enforcement/policy.py", "RULES = (1,)\n")
    assert manifest()["hashes"]["enforcement"] != before["hashes"]["enforcement"]
    (repo / "src/ethwall/analysis").mkdir(parents=True)
    edit(repo, "src/ethwall/analysis/config.json", '{"bootstrap": 1000}\n')
    commit_all(repo)
    after = manifest(partition="main", freeze_id="frz-9")
    assert after["git_dirty"] is False
    assert isinstance(after["hashes"]["analysis_config"], str)
    assert after["freeze_id"] == "frz-9"


def test_manifest_outside_git_records_unknown_commit(tmp_path):
    """R-REC-14: without git metadata (e.g. the Docker image) commit and dirty are None."""
    m = build_manifest(config(), world=tiny_world(), prompts_sha256="p" * 64,
                       run_id="r", repo_root=tmp_path)  # fmt: skip
    assert m["git_commit"] is None and m["git_dirty"] is None
    assert m["hashes"]["world_generator"] is None


# ----------------------------------------------------------------------------- R-FRZ-01..03


def test_create_freeze_writes_hashes_seeds_caps_and_model_ids(repo):
    """R-FRZ-01: protocol/frozen/<id>/freeze.json with git commit, the R-REC-14 hashes, seeds,
    caps and model ids (never API keys); a second create with the same id is refused."""
    head = git(repo, "rev-parse", "HEAD")
    path = create_freeze("frz-1", repo_root=repo)
    assert path == repo / "protocol" / "frozen" / "frz-1" / "freeze.json"
    text = path.read_text(encoding="utf-8")
    assert SECRET not in text
    f = json.loads(text)
    assert f["freeze_id"] == "frz-1" and f["git_commit"] == head
    assert set(f["hashes"]) == {
        "prompts_dir",
        "world_generator",
        "enforcement",
        "policy_data",
        "services",
        "tool_schemas",
        "analysis_config",
    }
    assert f["hashes"]["tool_schemas"] == sha256_hex(canonical_json(TOOL_SPECS))
    assert f["seeds"] == to_jsonable(SEEDS)
    assert f["caps"]["decisions"] == DECISIONS_PER_EPISODE
    assert f["caps"]["actions"] == MAX_ACTIONS_PER_EPISODE
    assert f["model_ids"] == {"actor": "actor-model-x", "auditor": "auditor-model-y"}
    with pytest.raises(FileExistsError):
        create_freeze("frz-1", repo_root=repo)


def test_create_freeze_refuses_dirty_tree(repo):
    """R-FRZ-01: a freeze records a commit, so uncommitted changes are refused."""
    edit(repo, "protocol/draft/prompts/shared.md", "changed\n")
    with pytest.raises(FreezeError, match="dirty"):
        create_freeze("frz-2", repo_root=repo)
    assert not (repo / "protocol" / "frozen" / "frz-2").exists()


def test_validate_freeze_clean_then_dirty(repo):
    """R-FRZ-02: a committed freeze validates; a dirty tree is a named mismatch."""
    create_freeze("frz-1", repo_root=repo)
    assert any(
        m.startswith("git_dirty") for m in validate_freeze("frz-1", repo_root=repo)
    )
    commit_all(repo, "freeze frz-1")
    assert validate_freeze("frz-1", repo_root=repo) == []
    edit(repo, "src/ethwall/services/core.py", "SERVICES = (1,)\n")
    problems = validate_freeze("frz-1", repo_root=repo)
    assert any(m.startswith("git_dirty") for m in problems)
    assert any(m.startswith("hashes.services") for m in problems)


def test_validate_freeze_names_each_changed_item(repo, monkeypatch):
    """R-FRZ-02: every recomputed item is compared and named (hashes, model ids, missing)."""
    create_freeze("frz-1", repo_root=repo)
    commit_all(repo, "freeze frz-1")
    edit(repo, "src/ethwall/world/_build.py", "SEED = 2\n")
    commit_all(repo)
    monkeypatch.setenv("ACTOR_MODEL_ID", "another-model")
    problems = validate_freeze("frz-1", repo_root=repo)
    assert [m.split(":")[0] for m in problems] == [
        "hashes.world_generator",
        "model_ids.actor",
    ]
    assert validate_freeze("no-such-freeze", repo_root=repo)[0].startswith("freeze")


def test_one_prompt_byte_changed_fails_validation(repo):
    """R-FRZ-03: changing one prompt byte after the freeze makes validation fail."""
    create_freeze("frz-1", repo_root=repo)
    commit_all(repo, "freeze frz-1")
    assert validate_freeze("frz-1", repo_root=repo) == []
    p = repo / "protocol/draft/prompts/budget.txt"
    data = bytearray(p.read_bytes())
    data[0] ^= 0x01
    p.write_bytes(bytes(data))
    commit_all(repo, "one byte")
    problems = validate_freeze("frz-1", repo_root=repo)
    assert len(problems) == 1 and problems[0].startswith("hashes.prompts_dir")


def test_main_partition_requires_valid_freeze(repo):
    """R-FRZ-02: main-partition runs require a valid freeze; other partitions don't."""
    require_valid_freeze(
        config("mock-w4"), repo_root=repo
    )  # not main: no freeze needed
    with pytest.raises(FreezeError, match="freeze"):
        require_valid_freeze(config("main", freeze_id=None), repo_root=repo)
    with pytest.raises(FreezeError):
        require_valid_freeze(config("main", freeze_id="frz-1"), repo_root=repo)
    create_freeze("frz-1", repo_root=repo)
    commit_all(repo, "freeze frz-1")
    require_valid_freeze(config("main", freeze_id="frz-1"), repo_root=repo)
    edit(repo, "protocol/draft/prompts/shared.md", "changed\n")
    with pytest.raises(FreezeError, match="hashes.prompts_dir"):
        require_valid_freeze(config("main", freeze_id="frz-1"), repo_root=repo)
