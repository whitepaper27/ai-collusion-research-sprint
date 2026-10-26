"""Reference CLI and the final audit report through the real CLI (never the network).

Requirements: docs/specs/references.md R-REF-03, R-REF-05, R-REF-06, R-REF-08, R-REF-10,
R-REF-11.
"""

from __future__ import annotations

import builtins
import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

from ethwall import cli
from ethwall.audit import reference as ref
from ethwall.contract import Mode, ModelRequest, ModelResponse, TextBlock

GOOD = {
    "category": "legitimate_work",
    "violations": {},
    "cited_event_ids": [],
    "confidence": "high",
    "uncertainty_note": "",
}
PRICE_KEYS = (
    "ETHWALL_PRICE_INPUT_PER_MTOK",
    "ETHWALL_PRICE_OUTPUT_PER_MTOK",
    "ETHWALL_PRICE_SOURCE",
    "ETHWALL_PRICE_DATE",
)


@pytest.fixture(scope="module")
def exports(tmp_path_factory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("rev")
    out = {}
    for which in ("dev", "heldout"):
        d = root / f"fixture-{which}"
        assert cli.main(["export-review", "--fixtures", which, "--out", str(d)]) == 0
        out[which] = d
    return out


class FakeLive:
    """A live-mode adjudicator stand-in (records requests, never touches the network)."""

    mode = Mode.LIVE

    def __init__(self, model_id: str = "claude-sonnet-5"):
        self.model_id = model_id
        self.requests: list[ModelRequest] = []

    def complete(self, req: ModelRequest) -> ModelResponse:
        self.requests.append(req)
        return ModelResponse(
            (TextBlock(json.dumps(GOOD)),), "end_turn", 2000, 100, 1.0,
            self.model_id + "-20261001",
        )  # fmt: skip


def _live_env(monkeypatch, model_id: str = "claude-sonnet-5") -> None:
    monkeypatch.setenv("ADJUDICATOR_MODEL_ID", model_id)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.delenv("ACTOR_MODEL_ID", raising=False)
    for k in PRICE_KEYS:
        monkeypatch.delenv(k, raising=False)


def _no_live_model(monkeypatch) -> None:
    def boom(**_: Any) -> Any:
        raise AssertionError("a model was constructed")

    monkeypatch.setattr(ref, "adjudicator_from_env", boom)


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]


# ----------------------------------------------------------------------------- R-REF-11


def test_mock_run_appends_records_and_skips_done_items(exports, tmp_path):
    """R-REF-11: one record per item; items with an ok record are skipped unless --rerun."""
    labels = tmp_path / "labels"
    base = ["reference", "run", "--exports", str(exports["dev"]), "--partition",
            "fixture-dev", "--labels-dir", str(labels)]  # fmt: skip
    assert cli.main(base) == 0
    path = labels / "adjudicator-fixture-dev.jsonl"
    rows = _rows(path)
    assert len(rows) == 6 and {r["status"] for r in rows} == {"ok"}
    assert {r["mode"] for r in rows} == {"mock"}
    for r in rows:
        raw = (exports["dev"] / r["item_id"] / "combined.json").read_bytes()
        assert r["export_sha256"] == hashlib.sha256(raw).hexdigest()
    assert cli.main(base) == 0
    assert len(_rows(path)) == 6
    assert cli.main([*base, "--rerun"]) == 0
    assert len(_rows(path)) == 12
    assert cli.main(["reference"]) == 2


def test_live_needs_confirm_settings_and_price_before_any_call(
    exports, tmp_path, monkeypatch, capsys
):
    """R-REF-11: live without --confirm-spend, settings or a recorded price exits 2, no call."""
    _no_live_model(monkeypatch)
    out = tmp_path / "adj.jsonl"
    base = ["reference", "run", "--exports", str(exports["dev"]), "--partition",
            "fixture-dev", "--mode", "live", "--out", str(out), "--spend-root",
            str(tmp_path / "spend")]  # fmt: skip
    _live_env(monkeypatch)
    assert cli.main(base) == 2
    assert "--confirm-spend" in capsys.readouterr().err
    monkeypatch.delenv("ADJUDICATOR_MODEL_ID")
    assert cli.main([*base, "--confirm-spend"]) == 2
    assert "ADJUDICATOR_MODEL_ID" in capsys.readouterr().err
    _live_env(monkeypatch, model_id="claude-unpriced-0")
    assert cli.main([*base, "--confirm-spend"]) == 2
    assert "no recorded price" in capsys.readouterr().err
    assert not out.exists() and not (tmp_path / "spend").exists()


def test_live_heldout_needs_a_matching_freeze(exports, tmp_path, monkeypatch, capsys):
    """R-REF-05: off fixture-dev, live runs refuse without a freeze (exit 2, no call)."""
    _no_live_model(monkeypatch)
    _live_env(monkeypatch)
    out = tmp_path / "adj.jsonl"
    rc = cli.main(["reference", "run", "--exports", str(exports["heldout"]),
                   "--partition", "fixture-heldout", "--mode", "live", "--confirm-spend",
                   "--out", str(out), "--freeze-path", str(tmp_path / "none.json"),
                   "--spend-root", str(tmp_path / "spend")])  # fmt: skip
    assert rc == 2
    assert "freeze" in capsys.readouterr().err
    assert not out.exists()


def test_dev_then_freeze_then_heldout_live_flow(exports, tmp_path, monkeypatch, capsys):
    """R-REF-05/R-REF-09/R-REF-11: live dev (fake model) -> freeze -> held-out records carry
    the freeze id; spend lands in the adjudicator ledger of the auditor batch; the freeze is
    never overwritten."""
    _live_env(monkeypatch)
    fake = FakeLive()
    monkeypatch.setattr(ref, "adjudicator_from_env", lambda **_: fake)
    labels, spend = tmp_path / "labels", tmp_path / "spend"
    freeze = tmp_path / "frozen" / "adjudicator.json"
    common = ["--labels-dir", str(labels), "--mode", "live", "--confirm-spend",
              "--spend-root", str(spend), "--freeze-path", str(freeze)]  # fmt: skip
    assert cli.main(["reference", "run", "--exports", str(exports["dev"]),
                     "--partition", "fixture-dev", *common]) == 0  # fmt: skip
    dev = _rows(labels / "adjudicator-fixture-dev.jsonl")
    assert len(dev) == 6 and all(r["freeze_id"] is None for r in dev)
    assert all(r["requested_model_id"] == "claude-sonnet-5" for r in dev)
    assert all(r["model_id"] == "claude-sonnet-5-20261001" for r in dev)
    assert all(r["cost_usd"] == pytest.approx((2000 * 2 + 100 * 10) / 1e6) for r in dev)
    ledger = json.loads((spend / "adjudicator" / "_spend.json").read_text())
    assert set(ledger["batches"]) == {"auditor"}
    assert ledger["pricing"]["model"] == "claude-sonnet-5"
    freeze_args = ["reference", "freeze", "--labels-dir", str(labels), "--freeze-path",
                   str(freeze)]  # fmt: skip
    assert cli.main(freeze_args) == 0
    assert cli.main(freeze_args) == 2
    assert "exists" in capsys.readouterr().err
    assert cli.main(["reference", "run", "--exports", str(exports["heldout"]),
                     "--partition", "fixture-heldout", *common]) == 0  # fmt: skip
    held = _rows(labels / "adjudicator-fixture-heldout.jsonl")
    assert len(held) == 12 and {r["freeze_id"] for r in held} == {"adjudicator-v1"}


def test_adjudicator_never_reads_predictions_labels_or_private_maps(
    exports, tmp_path, monkeypatch
):
    """R-REF-03: the CLI opens only combined.json exports (plus prompt and its own output);
    planted predictions, LLM labels, the manifest and the private id map are never read and
    their sentinels never reach a request."""
    sentinel = "SENTINEL-DO-NOT-READ"
    planted = {
        tmp_path / "audit" / "fixture-dev.jsonl": json.dumps({"item_id": sentinel}),
        tmp_path / "labels" / "llm-dev.jsonl": json.dumps({"category": sentinel}),
        tmp_path / "labels" / "human.jsonl": json.dumps({"category": sentinel}),
    }
    for p, text in planted.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    forbidden = {
        *(str(p.resolve()) for p in planted),
        str(
            (exports["dev"].with_name("fixture-dev.private") / "id_map.json").resolve()
        ),
        str(ref.REPO_ROOT / "fixtures" / "audit" / "manifest" / "construction.json"),
    }
    opened: list[str] = []
    real_open = io.open

    def spy(file: Any, *a: Any, **kw: Any) -> Any:
        if isinstance(file, (str, Path)):
            opened.append(str(Path(file).resolve()))
        return real_open(file, *a, **kw)

    seen: list[str] = []
    real_complete = ref.MockAdjudicator.complete

    def capture(self: Any, req: ModelRequest) -> ModelResponse:
        seen.append(
            req.system + "".join(b.text for m in req.messages for b in m.content)
        )
        return real_complete(self, req)

    monkeypatch.setattr(ref.MockAdjudicator, "complete", capture)
    monkeypatch.setattr(io, "open", spy)
    monkeypatch.setattr(builtins, "open", spy)
    rc = cli.main(["reference", "run", "--exports", str(exports["dev"]), "--partition",
                   "fixture-dev", "--labels-dir", str(tmp_path / "labels")])  # fmt: skip
    monkeypatch.undo()
    assert rc == 0 and len(seen) == 6
    assert not forbidden & set(opened)
    read_exports = [o for o in opened if str(exports["dev"].resolve()) in o]
    assert read_exports and all(o.endswith("combined.json") for o in read_exports)
    assert all(sentinel not in s for s in seen)


# ----------------------------------------------------------------------------- R-REF-08


def test_validate_writes_json_and_table_per_partition(exports, tmp_path):
    """R-REF-08: validation vs construction labels, dev and held-out reported separately."""
    labels = tmp_path / "labels"
    for which in ("dev", "heldout"):
        assert cli.main(["reference", "run", "--exports", str(exports[which]),
                         "--partition", f"fixture-{which}", "--labels-dir",
                         str(labels)]) == 0  # fmt: skip
    out = tmp_path / "val"
    assert cli.main(["reference", "validate", "--exports", str(exports["dev"]),
                     "--exports", str(exports["heldout"]), "--labels-dir", str(labels),
                     "--out", str(out)]) == 0  # fmt: skip
    data = json.loads((out / "validation.json").read_text())
    assert set(data["results"]) == {"fixture-dev", "fixture-heldout"}
    assert data["results"]["fixture-dev"]["planned"] == 6
    assert data["results"]["fixture-heldout"]["planned"] == 12
    assert data["results"]["fixture-heldout"]["coverage"] == 1.0
    assert "not validated" in data["reference"]
    table = (out / "validation.md").read_text()
    assert "fixture-heldout" in table and "Confusion" in table
    assert cli.main(["reference", "validate", "--exports", str(tmp_path / "main"),
                     "--labels-dir", str(labels), "--out", str(out)]) == 2  # fmt: skip


# ----------------------------------------------------------------------------- live mock


@pytest.fixture(scope="module")
def live(tmp_path_factory) -> dict[str, Path]:
    """Mock 'live-like' trajectories: seeds 0-1, both conditions, exported like a partition."""
    root = tmp_path_factory.mktemp("live")
    runs = root / "runs"
    for policy, part in (("honest", "mock-ref"), ("leaky", "mock-ref")):
        seeds = "0" if policy == "honest" else "1"
        assert cli.main(["run", "--seeds", seeds, "--policy", policy, "--partition", part,
                         "--runs-root", str(runs)]) == 0  # fmt: skip
    rev = root / "review" / "mock-ref"
    assert cli.main(["export-review", "--partition", "mock-ref", "--runs-root", str(runs),
                     "--out", str(rev), "--ack-review"]) == 0  # fmt: skip
    return {"runs": runs, "exports": rev}


def test_factual_checks_cli_keys_by_trajectory_only(live, tmp_path):
    """R-REF-06: one record per trajectory, no run names, no coordination category."""
    out = tmp_path / "facts.jsonl"
    args = ["reference", "facts", "--exports", str(live["exports"]), "--runs-root",
            str(live["runs"]), "--out", str(out)]  # fmt: skip
    assert cli.main(args) == 0
    rows = _rows(out)
    id_map = json.loads(
        (live["exports"].with_name("mock-ref.private") / "id_map.json").read_text()
    )["items"]
    assert {r["item_id"] for r in rows} == set(id_map)
    text = out.read_text()
    assert not any(name in text for name in id_map.values())
    assert all("category" not in r for r in rows)
    assert "legitimate_work" not in text
    assert all(r["reference_kind"] == "factual_checks" for r in rows)
    assert cli.main(args) == 2  # written once


# ----------------------------------------------------------------------------- R-REF-10


def test_audit_final_panels_figure_and_tagged_fact_sheet(
    exports, live, tmp_path, monkeypatch
):
    """R-REF-10: held-out vs construction (primary) and adjudicator, live vs adjudicator and
    LLM; bootstrap over pairs / seed blocks; Figure 1 per panel with the reference source;
    every fact cites a file whose sha256 matches and carries a reference tag; MOCK stamp; no
    model is constructed."""
    labels = tmp_path / "labels"
    held_pred, live_pred = tmp_path / "held.jsonl", tmp_path / "live.jsonl"
    assert cli.main(["audit", "run", "--exports", str(exports["heldout"]), "--out",
                     str(held_pred)]) == 0  # fmt: skip
    assert cli.main(["audit", "run", "--exports", str(live["exports"]), "--out",
                     str(live_pred)]) == 0  # fmt: skip
    for d, part in (
        (exports["heldout"], "fixture-heldout"),
        (live["exports"], "mock-ref"),
    ):
        assert cli.main(["reference", "run", "--exports", str(d), "--partition", part,
                         "--labels-dir", str(labels)]) == 0  # fmt: skip
    llm = tmp_path / "llm-main.jsonl"
    items = sorted(json.loads(
        (live["exports"].with_name("mock-ref.private") / "id_map.json").read_text()
    )["items"])  # fmt: skip
    llm.write_text("".join(json.dumps({"item_id": t, "category": "legitimate_work",
                                       "labeler": "LLM Z", "reference_kind": "llm"}) + "\n"
                           for t in items), encoding="utf-8")  # fmt: skip
    from ethwall.audit import auditor as au

    def boom(**_: Any) -> Any:
        raise AssertionError("no model may be constructed by audit final")

    monkeypatch.setattr(au, "auditor_from_env", boom)
    monkeypatch.setattr(ref, "adjudicator_from_env", boom)
    out = tmp_path / "final"
    rc = cli.main(["audit", "final", "--out", str(out), "--heldout-exports",
                   str(exports["heldout"]), "--heldout-predictions", str(held_pred),
                   "--labels-dir", str(labels), "--live", f"{live['exports']}={live_pred}",
                   "--llm", f"mock-ref={llm}", "--runs-root", str(live["runs"]),
                   "--n-boot", "200"])  # fmt: skip
    assert rc == 0
    names = {p.name for p in out.glob("*.json")}
    assert {"fixture-heldout__construction.json", "fixture-heldout__adjudicator.json",
            "mock-ref__adjudicator.json", "mock-ref__llm.json",
            "fact_sheet.json"} <= names  # fmt: skip
    primary = json.loads((out / "fixture-heldout__construction.json").read_text())
    assert primary["primary"] and "designed" in primary["reference_label"]
    assert primary["stamp"] == "MOCK"
    assert primary["h1"]["difference"] == "linked - combined"
    assert primary["h1"]["bootstrap"]["error_rate"]["n_clusters"] == 6
    livep = json.loads((out / "mock-ref__adjudicator.json").read_text())
    assert livep["h1"]["bootstrap"]["error_rate"]["n_clusters"] == 2  # seeds 0 and 1
    assert not livep["primary"]
    row = livep["figure_rows"][0]
    assert row["attempts"] == livep["metrics_by_view"][row["view"]]["attempts"]
    assert (out / "figures" / "figure1_final.png").exists()
    csv = (out / "figures" / "figure1_final.csv").read_text()
    assert "construction" in csv and "adjudicator" in csv and "llm" in csv
    sheet = json.loads((out / "fact_sheet.json").read_text())["numbers"]
    assert sheet
    for item in sheet:
        assert item["reference"] in {"construction", "adjudicator", "llm"}
        src = (out / item["source"]).resolve()
        assert hashlib.sha256(src.read_bytes()).hexdigest() == item["sha256"]
    tags = {i["reference"] for i in sheet}
    assert tags == {"construction", "adjudicator", "llm"}
    assert "MOCK" in (out / "summary.md").read_text()


def test_audit_final_without_any_panel_exits_2(tmp_path):
    """R-REF-10: nothing to report is a usage error, not an empty report."""
    rc = cli.main(["audit", "final", "--out", str(tmp_path / "o"), "--heldout-exports",
                   str(tmp_path / "none"), "--heldout-predictions",
                   str(tmp_path / "none.jsonl")])  # fmt: skip
    assert rc == 2


def test_doctor_reports_adjudicator_presence(monkeypatch, capsys):
    """R-REF-11: doctor confirms ADJUDICATOR_MODEL_ID presence (never its value)."""
    monkeypatch.setenv("ADJUDICATOR_MODEL_ID", "secret-model-name")
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "secret-model-name" not in out
    lines = {ln.split()[0]: ln.split()[-1] for ln in out.splitlines() if ln.strip()}
    assert lines["ADJUDICATOR_MODEL_ID"] == "set"
