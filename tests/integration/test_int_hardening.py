"""Hardening from the integration review (R-CLI-02, R-CLI-10, R-REC-03, R-REC-10, R-SPD-04).

No test here pairs a valid freeze or valid pricing with fake keys: every live-mode case must be
refused before a model client could be used.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ethwall import cli
from ethwall.contract import EventKind, TerminalState
from ethwall.recorder import JsonlRecorder, new_run_dir, recorder_key, verify_run
from ethwall.recorder._manifest import default_repo_root, git_state

FAKE_LIVE = {
    "ACTOR_MODEL_ID": "fake-model",
    "ANTHROPIC_API_KEY": "fake-key",
    "ETHWALL_RECORDER_KEY": "fake-recorder-key",
    "ETHWALL_PRICE_INPUT_PER_MTOK": "1.0",
    "ETHWALL_PRICE_OUTPUT_PER_MTOK": "5.0",
    "ETHWALL_PRICE_SOURCE": "test",
    "ETHWALL_PRICE_DATE": "2026-10-25",
}


def _env(monkeypatch, **overrides):
    for k, v in {**FAKE_LIVE, **overrides}.items():
        monkeypatch.setenv(k, v)


def _live(tmp_path, *extra):
    return cli.main(
        ["run", "--seeds", "100", "--mode", "live", "--confirm-spend",
         "--runs-root", str(tmp_path / "runs"), "--spend-root", str(tmp_path / "spend"),
         *extra]
    )  # fmt: skip


def test_invalid_freeze_exits_2_before_any_evidence(tmp_path, monkeypatch, capsys):
    """R-CLI-02: a given-but-invalid freeze is refused with exit 2, no run dir created."""
    _env(monkeypatch)
    rc = _live(tmp_path, "--partition", "main", "--freeze", "no-such-freeze")
    assert rc == 2
    assert "freeze" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()


def test_invalid_pricing_exits_2(tmp_path, monkeypatch, capsys):
    """R-CLI-02 / R-SPD-01: unusable pricing basis is refused with exit 2."""
    _env(monkeypatch, ETHWALL_PRICE_INPUT_PER_MTOK="not-a-number")
    assert _live(tmp_path, "--partition", "smoke") == 2
    assert not (tmp_path / "runs").exists()


def test_blank_model_id_exits_2_before_any_evidence(tmp_path, monkeypatch):
    """R-CLI-02: whitespace-only ACTOR_MODEL_ID counts as missing; nothing is written."""
    _env(monkeypatch, ACTOR_MODEL_ID="   ", ETHWALL_PRICE_INPUT_PER_MTOK="bad")
    assert _live(tmp_path, "--partition", "smoke") == 2
    assert not (tmp_path / "runs").exists()


def test_spend_ledger_does_not_dirty_the_tree(tmp_path):
    """R-SPD-04: spend/ accounting must not make the freeze gate see a dirty tree."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "config", "user.email", "t@t"], check=True)
    subprocess.run([*git, "config", "user.name", "t"], check=True)
    (repo / "README").write_text("x\n")
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    (repo / "spend" / "main").mkdir(parents=True)
    (repo / "spend" / "main" / "_spend.json").write_text("{}")
    (repo / "runs" / "main").mkdir(parents=True)
    (repo / "runs" / "main" / "x.json").write_text("{}")
    assert git_state(repo)[1] is False
    (repo / "stray.txt").write_text("dirty")
    assert git_state(repo)[1] is True


def test_spend_is_gitignored():
    """R-SPD-04: the mutable ledger is never committed."""
    lines = (default_repo_root() / ".gitignore").read_text().splitlines()
    # anchored to the repo root, so it never matches the src/ethwall/spend package
    assert "/spend/" in lines
    assert "spend/" not in lines


def test_default_roots_are_repo_anchored():
    """R-SPD-04: runs/ and spend/ defaults don't depend on the current directory."""
    args = cli.build_parser().parse_args(
        ["run", "--seeds", "0", "--partition", "mock-x"]
    )
    assert Path(args.runs_root) == default_repo_root() / "runs"
    assert Path(args.spend_root) == default_repo_root() / "spend"


def _mock_run(tmp_path, partition="mock-src"):
    root = str(tmp_path / "runs")
    assert cli.main(["run", "--seeds", "0", "--condition", "baseline", "--policy", "honest",
                     "--partition", partition, "--runs-root", root]) == 0  # fmt: skip
    base = tmp_path / "runs" / partition
    return next(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_"))


def test_verify_derives_the_key_from_the_partition_not_the_manifest(
    tmp_path, monkeypatch
):
    """R-REC-10/R-CLI-03: mock-signed evidence placed in a live partition does not verify."""
    run = _mock_run(tmp_path)
    live = tmp_path / "runs" / "main"
    shutil.copytree(run, live / run.name)
    (live / "_index").mkdir()
    shutil.copy(run.parent / "_index" / f"{run.name}.json", live / "_index")
    monkeypatch.setenv("ETHWALL_RECORDER_KEY", "a-real-secret")
    faults = cli.verify_dir(live / run.name)
    assert "MODE_MISMATCH" in faults and "BAD_SIG" in faults
    rc = cli.main(
        ["verify", "--partition", "main", "--runs-root", str(tmp_path / "runs")]
    )
    assert rc == 1


def test_a_copied_run_directory_is_flagged(tmp_path):
    """R-REC-10: events whose run_id differs from their directory are not counted twice."""
    run = _mock_run(tmp_path)
    copy = run.parent / (run.name + "-copy")
    shutil.copytree(run, copy)
    key, _ = recorder_key(json.loads((run / "manifest.json").read_text())["mode"])
    assert verify_run(run, key) == ()
    assert "RUN_ID_MISMATCH" in verify_run(copy, key)


def test_verify_reports_missing_manifest_instead_of_crashing(tmp_path):
    """R-CLI-03: a run dir without manifest.json is a fault, not a traceback."""
    run = _mock_run(tmp_path)
    (run / "manifest.json").unlink()
    assert "MANIFEST_MISSING" in cli.verify_dir(run)


def test_index_file_is_never_overwritten(tmp_path):
    """R-REC-03: the per-run index file is created once; a pre-existing one is not replaced."""
    run_dir = new_run_dir(tmp_path, "mock-x", "r1")
    (tmp_path / "mock-x" / "_index").mkdir()
    (tmp_path / "mock-x" / "_index" / "r1.json").write_text("{}\n")
    key, kid = recorder_key("mock")
    rec = JsonlRecorder(run_dir, "r1", key, kid)
    rec.append(EventKind.RUN_START, "ORCHESTRATOR", {"manifest": {}})
    counts = {
        "decisions": 0,
        "transport_attempts": 0,
        "actions": 0,
        "proposed_calls": 0,
    }
    with pytest.raises(FileExistsError):
        rec.close(TerminalState.BUDGET_EXHAUSTED, counts)
    assert (tmp_path / "mock-x" / "_index" / "r1.json").read_text() == "{}\n"


@pytest.mark.parametrize(
    ("states", "code"),
    [
        (["COMPLETED", "REFUSED", "BUDGET_EXHAUSTED"], 0),
        (["COMPLETED", "API_ERROR"], 1),
        (["SPEND_LIMIT"], 1),
    ],
)
def test_run_exit_code_reflects_interrupted_episodes(states, code):
    """R-CLI-10: interrupted episodes (API/spend/clock/action limits) make `run` exit 1."""
    assert cli.exit_code_for([TerminalState(s) for s in states]) == code
