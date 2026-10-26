"""R-AUD-10: `ethwall audit report` computes citation validity against each export's visible ids."""

import json

from ethwall import cli


def test_audit_report_includes_citation_validity(tmp_path):
    """R-AUD-10: citation validity is reported per view (not left empty)."""
    rev, pred, rep = tmp_path / "rev", tmp_path / "pred.jsonl", tmp_path / "rep"
    assert cli.main(["export-review", "--fixtures", "dev", "--out", str(rev)]) == 0
    assert cli.main(["audit", "run", "--exports", str(rev), "--out", str(pred)]) == 0
    rows = [json.loads(x) for x in pred.read_text().splitlines()]
    rows[0]["cited_event_ids"] = ["E001", "NOT-A-VISIBLE-ID"]
    pred.write_text("".join(json.dumps(r) + "\n" for r in rows))
    rc = cli.main(["audit", "report", "--predictions", str(pred), "--references",
                   "fixtures:dev", "--exports", str(rev), "--out", str(rep)])  # fmt: skip
    assert rc == 0
    metrics = json.loads((rep / "audit_metrics.json").read_text())
    view = rows[0]["view"]
    cites = metrics[view]["citations"]
    assert cites is not None and cites["cited"] >= 2
    assert cites["valid"] < cites["cited"]  # the invented id is counted as invalid
