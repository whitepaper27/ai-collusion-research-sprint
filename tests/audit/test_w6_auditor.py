"""Auditor runner with a mock auditor model (never the network).

Requirements: docs/specs/audit.md R-AUD-08 (sol.md §10).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ethwall.audit import auditor as au
from ethwall.contract import (
    CoordLabel,
    Mode,
    ModelCallError,
    ModelRequest,
    ModelResponse,
    TextBlock,
)
from ethwall.models import AnthropicActor, ModelConfigError
from ethwall.spend import Pricing, SpendLedger

EXPORT: dict[str, Any] = {
    "format": "ethwall-audit-view/1",
    "trajectory_id": "Tabc12345",
    "view": "combined",
    "record_status": "COMPLETED",
    "policy": "policy text",
    "sources": {"documents": [], "release_records": []},
    "events": [
        {"id": "E001", "round": 1, "type": "message", "sender": "LEGAL",
         "recipient": "TRADING", "text": "hello"},
        {"id": "E002", "round": 1, "type": "order_submitted", "role": "TRADING",
         "args": {"action": "hold"}},
    ],
}  # fmt: skip

GOOD = {
    "category": "completed_coordination",
    "violations": {"restricted_exposure": True, "restricted_publication": None},
    "cited_event_ids": ["E001", "E002"],
}


def reply(text: str) -> ModelResponse:
    return ModelResponse((TextBlock(text),), "end_turn", 1000, 200, 5.0, "auditor-mock")


class MockAuditor:
    """Scripted auditor model; each step returns text or raises an error."""

    model_id = "auditor-mock"
    mode = Mode.MOCK

    def __init__(self, *steps: str | Exception | Callable[[ModelRequest], str]):
        self.steps = list(steps)
        self.requests: list[ModelRequest] = []

    def complete(self, req: ModelRequest) -> ModelResponse:
        self.requests.append(req)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return reply(step(req))
        return reply(step)


def run(
    model: MockAuditor, export: dict[str, Any] | None = None, **kw: Any
) -> au.AuditorResult:
    return au.run_auditor(
        export or EXPORT, model=model, prompt_text="FROZEN PROMPT", **kw
    )


def test_r_aud_08_valid_output_is_parsed_into_the_schema():
    """R-AUD-08: category, violation judgments and cited event ids are returned."""
    model = MockAuditor(json.dumps(GOOD))
    r = run(model)
    assert r.status == "ok"
    assert r.category == "completed_coordination"
    assert r.violations["restricted_exposure"] is True
    assert r.violations["restricted_publication"] is None
    assert set(r.violations) == set(au.VIOLATION_KEYS)
    assert r.cited_event_ids == ("E001", "E002")
    assert r.item_id == "Tabc12345" and r.view == "combined"
    assert len(model.requests) == 1


def test_r_aud_08_abstain_is_a_valid_answer():
    """R-AUD-08: explicit abstention is allowed and distinct from invalid."""
    r = run(MockAuditor(json.dumps({**GOOD, "category": "abstain"})))
    assert r.status == "ok" and r.category == "abstain"


@pytest.mark.parametrize(
    "text",
    [
        "I think this is completed coordination.",
        json.dumps({**GOOD, "category": "collusion"}),
        json.dumps({**GOOD, "violations": {"restricted_exposure": "yes"}}),
        json.dumps({**GOOD, "violations": {"made_up_outcome": True}}),
        json.dumps({**GOOD, "cited_event_ids": "E001"}),
        json.dumps({"violations": {}, "cited_event_ids": []}),
        json.dumps([GOOD]),
        "",
    ],
)
def test_r_aud_08_one_parse_attempt_then_invalid_without_rewording(text):
    """R-AUD-08: an unparseable or off-schema answer is `invalid` after exactly one call."""
    model = MockAuditor(text, json.dumps(GOOD))
    r = run(model)
    assert r.status == "invalid" and r.category == "invalid"
    assert len(model.requests) == 1
    assert r.raw_text == text


def test_r_aud_08_one_identical_transport_retry():
    """R-AUD-08: a retryable transport error gets one identical retry, never a reworded one."""
    model = MockAuditor(ModelCallError("overloaded", retryable=True), json.dumps(GOOD))
    r = run(model)
    assert r.status == "ok" and r.attempts == 2
    assert model.requests[0] == model.requests[1]


def test_r_aud_08_second_transport_failure_is_an_error_not_invalid():
    """R-AUD-08: at most one retry; a transport failure is not counted as an invalid answer."""
    model = MockAuditor(
        ModelCallError("overloaded", retryable=True),
        ModelCallError("overloaded", retryable=True),
        json.dumps(GOOD),
    )
    r = run(model)
    assert r.status == "transport_error" and r.category is None
    assert len(model.requests) == 2


def test_r_aud_08_non_retryable_error_is_not_retried():
    """R-AUD-08: non-retryable transport errors stop after one attempt."""
    model = MockAuditor(
        ModelCallError("bad request", retryable=False), json.dumps(GOOD)
    )
    r = run(model)
    assert r.status == "transport_error" and len(model.requests) == 1


def test_r_aud_08_independent_session_per_trajectory_and_view():
    """R-AUD-08: each call is a fresh session: frozen prompt + one export, nothing earlier."""
    model = MockAuditor(json.dumps(GOOD), json.dumps({**GOOD, "category": "abstain"}))
    other = {**EXPORT, "view": "linked", "links": []}
    run(model)
    run(model, other)
    first, second = model.requests
    for req, exp in ((first, EXPORT), (second, other)):
        assert req.system == "FROZEN PROMPT"
        assert len(req.messages) == 1 and req.messages[0].role == "user"
        (block,) = req.messages[0].content
        assert isinstance(block, TextBlock)
        assert json.loads(block.text) == exp
        assert req.tools == ()
    assert "completed_coordination" not in second.messages[0].content[0].text
    assert first.context_id != second.context_id


def test_r_aud_08_fixed_settings_and_provenance_are_recorded():
    """R-AUD-08: fixed generation settings; result records model id and prompt hash."""
    settings = au.AuditorSettings(max_tokens=777, temperature=0.0)
    model = MockAuditor(json.dumps(GOOD))
    r = run(model, settings=settings)
    assert model.requests[0].max_tokens == 777
    assert model.requests[0].temperature == 0.0
    assert r.model_id == "auditor-mock"
    assert r.prompt_sha256 == au.sha256_hex("FROZEN PROMPT")
    assert r.settings == {"max_tokens": 777, "temperature": 0.0}
    d = r.to_dict()
    assert json.loads(json.dumps(d)) == d


def test_r_aud_08_frozen_prompt_file_defines_categories_and_schema():
    """R-AUD-08: the prompt file defines the six categories, abstention and the JSON schema."""
    text, digest = au.load_auditor_prompt()
    assert au.AUDITOR_PROMPT_PATH.name == "auditor_prompt.md"
    assert au.AUDITOR_PROMPT_PATH.parent.name == "draft"
    assert digest == au.sha256_hex(text)
    for c in CoordLabel:
        assert c.value in text
    for key in (
        "abstain",
        "category",
        "violations",
        "cited_event_ids",
        *au.VIOLATION_KEYS,
    ):
        assert key in text
    for word in ("baseline", "guarded"):
        assert word not in text.lower()


def test_r_aud_08_model_id_comes_from_auditor_model_id(monkeypatch):
    """R-AUD-08: the auditor model id comes only from AUDITOR_MODEL_ID (no literal ids)."""
    monkeypatch.delenv(au.AUDITOR_MODEL_ENV, raising=False)
    monkeypatch.setenv("ACTOR_MODEL_ID", "actor-model-x")
    with pytest.raises(ModelConfigError):
        au.auditor_from_env(client=object())
    monkeypatch.setenv(au.AUDITOR_MODEL_ENV, "auditor-model-y")
    model = au.auditor_from_env(client=object())
    assert isinstance(model, AnthropicActor)
    assert model.model_id == "auditor-model-y"
    assert au.AUDITOR_MODEL_ENV == "AUDITOR_MODEL_ID"


def _pricing() -> Pricing:
    return Pricing(1.0, 5.0, "test basis", "2026-10-24")


def test_r_aud_08_spend_is_charged_to_the_auditor_batch(tmp_path: Path):
    """R-AUD-08: reservations and settlement go to the `auditor` batch ledger."""
    ledger = SpendLedger(tmp_path / "audit" / "_spend.json", "auditor", 0.5, _pricing())
    r = run(MockAuditor(json.dumps(GOOD)), ledger=ledger)
    assert r.status == "ok"
    assert r.cost_usd > 0
    assert ledger.batch_spent_usd == pytest.approx(r.cost_usd)
    data = json.loads((tmp_path / "audit" / "_spend.json").read_text(encoding="utf-8"))
    assert "auditor" in data["batches"]


def test_r_aud_08_refuses_a_ledger_for_another_batch(tmp_path: Path):
    """R-AUD-08: auditor spend never lands in an actor batch."""
    ledger = SpendLedger(tmp_path / "main" / "_spend.json", "main", 0.5, _pricing())
    with pytest.raises(ValueError):
        run(MockAuditor(json.dumps(GOOD)), ledger=ledger)


def test_r_aud_08_refused_reservation_makes_no_call(tmp_path: Path):
    """R-AUD-08: a reservation that does not fit the cap is not invalid and makes no call."""
    ledger = SpendLedger(
        tmp_path / "audit" / "_spend.json", "auditor", 0.000001, _pricing()
    )
    model = MockAuditor(json.dumps(GOOD))
    r = run(model, ledger=ledger)
    assert r.status == "spend_refused" and r.category is None
    assert model.requests == []


def test_r_aud_08_predictions_append_to_jsonl(tmp_path: Path):
    """R-AUD-08: results are appended as JSON lines for later reveal and metrics."""
    path = tmp_path / "predictions.jsonl"
    r1 = run(MockAuditor(json.dumps(GOOD)))
    r2 = run(MockAuditor("not json"))
    au.append_prediction(path, r1)
    au.append_prediction(path, r2)
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert [x["category"] for x in lines] == ["completed_coordination", "invalid"]


def test_r_aud_08_auditor_ledger_shares_the_runs_root(tmp_path: Path):
    """R-AUD-08: the auditor ledger sits beside the actor ledgers so all caps apply."""
    ledger = au.auditor_ledger(_pricing(), root=tmp_path / "runs")
    assert ledger.batch == "auditor"
    assert ledger.path == tmp_path / "runs" / "audit" / "_spend.json"
    assert ledger.episode_cap_usd == au.AUDITOR_CALL_CAP_USD
    r = run(MockAuditor(json.dumps(GOOD)), ledger=ledger)
    assert r.status == "ok" and ledger.total_spent_usd == pytest.approx(r.cost_usd)


def test_r_aud_08_refused_retry_reservation_keeps_the_transport_error(tmp_path: Path):
    """R-AUD-08: a spend refusal before the retry still reports the first transport error."""

    class OneShot:
        batch = "auditor"

        def __init__(self) -> None:
            self.n = 0

        def begin_episode(self, _: str) -> None:
            pass

        def reserve(self, *_: Any) -> bool:
            self.n += 1
            return self.n == 1

        def settle(self, *_: Any) -> float:
            return 0.0

    model = MockAuditor(ModelCallError("overloaded", retryable=True), json.dumps(GOOD))
    r = run(model, ledger=OneShot())
    assert r.status == "spend_refused" and r.attempts == 1
    assert r.error is not None and "overloaded" in r.error
