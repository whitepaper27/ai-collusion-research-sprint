"""W7 review fixes for `ethwall export-review`: live leak triage, staging, private outputs.

Requirements: docs/specs/audit.md R-AUD-04, R-AUD-06; docs/specs/cli-integration.md R-CLI-03.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ethwall import cli

PLANTED = "the control and treatment teams were instructed"
WORDS = ("control", "treatment", "instructed")


def mock_runs(tmp_path: Path, partition: str = "mock-w7") -> Path:
    """Two leaky mock episodes (each sends one inter-agent message)."""
    root = tmp_path / "runs"
    rc = cli.main(["run", "--seeds", "0", "--policy", "leaky",
                   "--partition", partition, "--runs-root", str(root)])  # fmt: skip
    assert rc == 0
    return root


def run_dirs(root: Path, partition: str = "mock-w7") -> list[Path]:
    base = root / partition
    return sorted(
        d for d in base.iterdir() if d.is_dir() and not d.name.startswith("_")
    )


def plant(run_dir: Path, suffix: str) -> None:
    """Append `suffix` to every message text in a tmp copy's events.jsonl (never runs/)."""
    path = run_dir / "events.jsonl"
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        ev = json.loads(line)
        if ev["kind"] == "MESSAGE_SUBMITTED":
            ev["payload"]["text"] += " " + suffix
        lines.append(json.dumps(ev))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def export(root: Path, out: Path, *extra: str) -> int:
    return cli.main(["export-review", "--partition", "mock-w7",
                     "--runs-root", str(root), "--out", str(out), *extra])  # fmt: skip


def items(out: Path) -> list[Path]:
    return sorted(d for d in out.iterdir() if d.is_dir() and not d.name.startswith("_"))


def test_r_cli_03_live_export_review_exports_clean_mock_runs(tmp_path):
    """R-CLI-03/R-AUD-04: export-review --partition reads the seed from the recorded manifest
    (it used to crash on load_run's metrics) and promotes clean exports."""
    root, out = mock_runs(tmp_path), tmp_path / "rev"
    assert export(root, out) == 0
    assert len(items(out)) == 2
    assert not Path(f"{out}.staging").exists()
    review = json.loads((out / "_private" / "leak_review.json").read_text())
    assert review["review"] == [] and review["blocking"] == []


def test_r_aud_06_live_review_hits_need_ack_and_are_never_redacted(tmp_path, capsys):
    """R-AUD-06: generic-term hits in agent text are printed, saved to leak_review.json and
    block promotion unless --ack-review; the evidence itself is never redacted."""
    root, out = mock_runs(tmp_path), tmp_path / "rev"
    for d in run_dirs(root):
        plant(d, PLANTED)
    staging = Path(f"{out}.staging")

    assert export(root, out) == 1
    printed = capsys.readouterr().out
    assert not out.exists()
    saved = json.loads((staging / "_private" / "leak_review.json").read_text())
    assert saved["acked"] is False and saved["blocking"] == []
    # 3 words x 3 text-bearing views (messages, combined, linked) x 2 episodes
    assert len(saved["review"]) == 18
    for hit in saved["review"]:
        assert f"REVIEW {hit}" in printed
    for w in WORDS:
        assert any(f"'{w}'" in h for h in saved["review"])

    shutil.rmtree(staging)  # the human triaged the hits
    assert export(root, out, "--ack-review") == 0
    assert not staging.exists()
    saved = json.loads((out / "_private" / "leak_review.json").read_text())
    assert saved["acked"] is True and len(saved["review"]) == 18
    for d in items(out):
        combined = json.loads((d / "combined.json").read_text())
        texts = [i["text"] for i in combined["events"] if i["type"] == "message"]
        assert texts and all(t.endswith(PLANTED) for t in texts)


def test_r_aud_06_blocking_hits_are_never_ackable(tmp_path, capsys):
    """R-AUD-06: an assignment value (the run directory name) in an export blocks even with
    --ack-review, and nothing is promoted."""
    root, out = mock_runs(tmp_path), tmp_path / "rev"
    d = run_dirs(root)[0]
    plant(d, d.name)
    assert export(root, out, "--ack-review") == 1
    assert not out.exists()
    printed = capsys.readouterr().out
    assert "LEAK " in printed and d.name in printed
    saved = json.loads(
        (Path(f"{out}.staging") / "_private" / "leak_review.json").read_text()
    )
    assert saved["blocking"]


def test_r_aud_06_export_review_refuses_to_overwrite(tmp_path):
    """R-AUD-06: an existing export or staging tree is never mixed into: exit 2, untouched."""
    root, out = mock_runs(tmp_path), tmp_path / "rev"
    out.mkdir()
    assert export(root, out) == 2
    assert list(out.iterdir()) == [] and not Path(f"{out}.staging").exists()
    out.rmdir()
    Path(f"{out}.staging").mkdir()
    assert export(root, out) == 2
    assert not out.exists()


def test_r_aud_06_private_outputs_can_live_outside_the_export_tree(tmp_path):
    """R-AUD-06: with --private-out the id map, salt and leak review are written apart from the
    tree a human labels from, and `audit report --private-dir` reads them from there."""
    rev, priv = tmp_path / "rev", tmp_path / "secret"
    pred, rep = tmp_path / "pred.jsonl", tmp_path / "rep"
    rc = cli.main(["export-review", "--fixtures", "dev", "--out", str(rev),
                   "--private-out", str(priv)])  # fmt: skip
    assert rc == 0
    assert not (rev / "_private").exists()
    assert {p.name for p in rev.iterdir()} == {d.name for d in items(rev)}
    id_map = json.loads((priv / "id_map.json").read_text())
    assert set(id_map["items"]) == {d.name for d in items(rev)}
    assert (priv / "leak_review.json").exists()
    exported = "".join(p.read_text() for d in items(rev) for p in d.glob("*"))
    assert id_map["salt"] not in exported
    assert cli.main(["audit", "run", "--exports", str(rev), "--out", str(pred)]) == 0
    rc = cli.main(["audit", "report", "--predictions", str(pred), "--references",
                   "fixtures:dev", "--exports", str(rev), "--private-dir", str(priv),
                   "--out", str(rep)])  # fmt: skip
    assert rc == 0
    assert (rep / "figures" / "figure1_audit.png").exists()
