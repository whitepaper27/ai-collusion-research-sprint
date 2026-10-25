"""Audit-study commands through the real CLI (R-CLI-03, R-CLI-11 Figure 1)."""

import json
from pathlib import Path

from ethwall import cli
from ethwall.contract import View


def test_fixture_audit_pipeline_renders_figure_1(tmp_path):
    """R-CLI-11 (audit part): export-review → audit run (mock) → audit report → Figure 1."""
    rev, pred, rep = tmp_path / "rev", tmp_path / "pred.jsonl", tmp_path / "rep"
    assert cli.main(["export-review", "--fixtures", "dev", "--out", str(rev)]) == 0
    items = [d for d in rev.iterdir() if d.is_dir() and not d.name.startswith("_")]
    assert len(items) == 6
    for d in items:
        assert {f"{v.value}.json" for v in View} <= {p.name for p in d.iterdir()}
        assert d.name.startswith("T")  # opaque id, not a fixture id
    id_map = json.loads((rev / "_private" / "id_map.json").read_text())
    exported = "".join(p.read_text() for d in items for p in d.glob("*.json"))
    assert not any(fid in exported for fid in id_map["items"].values())
    assert id_map["salt"] not in exported
    assert cli.main(["audit", "run", "--exports", str(rev), "--out", str(pred)]) == 0
    assert len(pred.read_text().splitlines()) == 6 * 4
    rc = cli.main(["audit", "report", "--predictions", str(pred), "--references",
                   "fixtures:dev", "--exports", str(rev), "--out", str(rep)])  # fmt: skip
    assert rc == 0
    assert (rep / "figures" / "figure1_audit.png").exists()
    metrics = json.loads((rep / "audit_metrics.json").read_text())
    assert set(metrics) == {v.value for v in View}


def test_live_auditor_refuses_without_owner_go(tmp_path, monkeypatch, capsys):
    """R-CLI-02 for the auditor: no --confirm-spend → exit 2 before any call."""
    rev = tmp_path / "rev"
    cli.main(["export-review", "--fixtures", "dev", "--out", str(rev)])
    rc = cli.main(["audit", "run", "--exports", str(rev), "--mode", "live",
                   "--out", str(tmp_path / "p.jsonl")])  # fmt: skip
    assert rc == 2
    assert "--confirm-spend" in capsys.readouterr().err
    assert not (tmp_path / "p.jsonl").exists()


def test_label_command_is_wired(tmp_path, monkeypatch):
    """R-CLI-03: `ethwall label` records a human label from a combined export."""
    rev = tmp_path / "rev"
    cli.main(["export-review", "--fixtures", "dev", "--out", str(rev)])
    item = min(d for d in rev.iterdir() if d.is_dir() and not d.name.startswith("_"))
    answers = iter(["legitimate_work", "", "", "", "", "", "", "", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers, ""))
    store = tmp_path / "labels.jsonl"
    assert (
        cli.main(
            ["label", "--export", str(item / "combined.json"), "--store", str(store)]
        )
        == 0
    )
    rows = [json.loads(x) for x in Path(store).read_text().splitlines()]
    assert rows and rows[-1]["category"] == "legitimate_work"
