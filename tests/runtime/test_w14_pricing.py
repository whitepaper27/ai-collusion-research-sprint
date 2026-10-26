"""Mixed-model pricing: each call is charged at its own model's rate under shared caps.

Requirements: docs/specs/references.md R-REF-09 (sol.md §9).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ethwall.audit import reference as ref
from ethwall.spend import PricingError, SpendLedger
from ethwall.spend.ledger import (
    PRICING_TABLE_PATH,
    load_pricing_table,
    pricing_for_model,
)

TABLE = {
    "basis_source": "test basis",
    "basis_date": "2026-10-25",
    "models": {
        "claude-haiku-4-5": {"input_per_mtok": 1.0, "output_per_mtok": 5.0},
        "claude-sonnet-5": {"input_per_mtok": 2.0, "output_per_mtok": 10.0},
    },
}
NO_ENV: dict[str, str] = {}


def test_committed_pricing_table_records_both_models():
    """R-REF-09: protocol/pricing.json is committed with a basis and per-model rates."""
    table = load_pricing_table()
    assert PRICING_TABLE_PATH.is_file()
    assert table["basis_source"] and table["basis_date"] == "2026-10-25"
    assert table["models"]["claude-haiku-4-5"] == {
        "input_per_mtok": 1.0,
        "output_per_mtok": 5.0,
    }
    assert table["models"]["claude-sonnet-5"] == {
        "input_per_mtok": 2.0,
        "output_per_mtok": 10.0,
    }


def test_sonnet_reserves_and_settles_at_its_own_rate(tmp_path):
    """R-REF-09: a Sonnet call reserves and settles at $2 / $10 per Mtok."""
    led = SpendLedger.for_model(
        "claude-sonnet-5",
        tmp_path / "adjudicator" / "_spend.json",
        "auditor",
        0.25,
        table=TABLE,
        env=NO_ENV,
    )
    led.begin_episode("adjudicate:T1")
    assert led.reserve(10_000, 1_000)
    assert led.last_reserved_usd == pytest.approx(0.02 + 0.01)
    assert led.settle(5_000, 500) == pytest.approx(0.01 + 0.005)
    assert led.pricing.model == "claude-sonnet-5"


def test_haiku_reserves_and_settles_at_its_own_rate(tmp_path):
    """R-REF-09: a Haiku call reserves and settles at $1 / $5 per Mtok."""
    led = SpendLedger.for_model(
        "claude-haiku-4-5",
        tmp_path / "audit" / "_spend.json",
        "auditor",
        0.25,
        table=TABLE,
        env=NO_ENV,
    )
    led.begin_episode("audit:T1:combined")
    assert led.reserve(10_000, 1_000)
    assert led.last_reserved_usd == pytest.approx(0.01 + 0.005)
    assert led.settle(5_000, 500) == pytest.approx(0.005 + 0.0025)


def test_both_models_count_toward_the_same_batch_and_total_caps(tmp_path):
    """R-REF-09: Haiku (auditor) and Sonnet (adjudicator) spend share the auditor batch cap and
    the $50 total across separate ledger files under one root."""
    haiku = ref.mixed_ledger("claude-haiku-4-5", tmp_path, "audit", table=TABLE)
    sonnet = ref.adjudicator_ledger("claude-sonnet-5", tmp_path, table=TABLE)
    haiku.begin_episode("a")
    sonnet.begin_episode("b")
    assert haiku.reserve(100_000, 0) and haiku.settle(100_000, 0) == pytest.approx(0.1)
    assert sonnet.reserve(100_000, 0) and sonnet.settle(100_000, 0) == pytest.approx(
        0.2
    )
    assert sonnet.batch_spent_usd == pytest.approx(0.3)
    assert haiku.batch_spent_usd == pytest.approx(0.3)
    assert sonnet.total_spent_usd == pytest.approx(0.3)
    # Fill the auditor batch to $0.01 below its $10 cap from another file of the same batch.
    other = tmp_path / "audit-other" / "_spend.json"
    other.parent.mkdir()
    other.write_text(
        json.dumps(
            {
                "schema": 1,
                "batches": {"auditor": {"spent_micro": 9_690_000, "uncertain": False}},
                "episodes": {},
            }
        ),
        encoding="utf-8",
    )
    sonnet.begin_episode("c")
    # $0.02 at Sonnet rates does not fit; the same tokens at Haiku rates ($0.01) do.
    assert not sonnet.reserve(10_000, 0)
    haiku.begin_episode("d")
    assert haiku.reserve(10_000, 0)


def test_total_cap_spans_actor_and_adjudicator_ledgers(tmp_path):
    """R-REF-09: actor (main batch) spend counts toward the total seen by the adjudicator."""
    main = tmp_path / "main" / "_spend.json"
    main.parent.mkdir()
    main.write_text(
        json.dumps(
            {
                "schema": 1,
                "batches": {
                    "main": {"spent_micro": 25_000_000, "uncertain": False},
                    "smoke+control": {"spent_micro": 8_000_000, "uncertain": False},
                    "reserve": {"spent_micro": 7_000_000, "uncertain": False},
                },
                "episodes": {},
            }
        ),
        encoding="utf-8",
    )
    sonnet = ref.adjudicator_ledger("claude-sonnet-5", tmp_path, table=TABLE)
    sonnet.begin_episode("x")
    assert sonnet.total_spent_usd == pytest.approx(40.0)
    assert sonnet.reserve(1_000, 1_000)
    sonnet.cancel()
    # Overspent main ($26) + auditor batch at $9 brings the total to $50 while the auditor
    # batch still has $1 of room: the refusal can only come from the total cap.
    data = json.loads(main.read_text(encoding="utf-8"))
    data["batches"]["main"]["spent_micro"] = 26_000_000
    main.write_text(json.dumps(data), encoding="utf-8")
    other = tmp_path / "audit" / "_spend.json"
    other.parent.mkdir()
    other.write_text(
        json.dumps(
            {
                "schema": 1,
                "batches": {"auditor": {"spent_micro": 9_000_000, "uncertain": False}},
                "episodes": {},
            }
        ),
        encoding="utf-8",
    )
    assert sonnet.batch_spent_usd == pytest.approx(9.0)
    assert sonnet.total_spent_usd == pytest.approx(50.0)
    assert not sonnet.reserve(1_000, 1_000)


def test_unknown_model_is_refused(tmp_path):
    """R-REF-09: a model with no recorded price is refused (no ledger, no call)."""
    with pytest.raises(PricingError, match="no recorded price"):
        pricing_for_model("claude-unknown-9", table=TABLE, env=NO_ENV)
    with pytest.raises(PricingError):
        SpendLedger.for_model(
            "claude-unknown-9",
            tmp_path / "x" / "_spend.json",
            "auditor",
            0.25,
            table=TABLE,
            env=NO_ENV,
        )
    # Exact match only: a served id with a date suffix is not a configured id.
    with pytest.raises(PricingError):
        pricing_for_model("claude-sonnet-5-20261001", table=TABLE, env=NO_ENV)


def test_env_basis_conflicting_with_table_for_actor_model_is_refused():
    """R-REF-09: the env basis is accepted beside the table only when it agrees for the actor
    model it describes."""
    env = {
        "ACTOR_MODEL_ID": "claude-haiku-4-5",
        "ETHWALL_PRICE_INPUT_PER_MTOK": "1.0",
        "ETHWALL_PRICE_OUTPUT_PER_MTOK": "5.0",
        "ETHWALL_PRICE_SOURCE": "x",
        "ETHWALL_PRICE_DATE": "2026-10-25",
    }
    assert (
        pricing_for_model("claude-haiku-4-5", table=TABLE, env=env).input_per_mtok
        == 1.0
    )
    # The env basis describes the actor, so it never blocks pricing another model.
    assert (
        pricing_for_model("claude-sonnet-5", table=TABLE, env=env).input_per_mtok == 2.0
    )
    bad = {**env, "ETHWALL_PRICE_OUTPUT_PER_MTOK": "4.0"}
    with pytest.raises(PricingError, match="conflicts"):
        pricing_for_model("claude-haiku-4-5", table=TABLE, env=bad)


@pytest.mark.parametrize(
    "broken",
    [
        {"basis_date": "2026-10-25", "models": {"m": {"input_per_mtok": 1, "output_per_mtok": 1}}},
        {"basis_source": "s", "basis_date": "d", "models": {}},
        {"basis_source": "s", "basis_date": "d", "models": {"m": {"input_per_mtok": -1, "output_per_mtok": 1}}},
        {"basis_source": "s", "basis_date": "d", "models": {"m": {"input_per_mtok": 1}}},
    ],
)  # fmt: skip
def test_malformed_pricing_table_is_refused(tmp_path: Path, broken):
    """R-REF-09: an incomplete or non-positive table is refused, never half-used."""
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(PricingError):
        load_pricing_table(p)


def test_adjudicator_session_cap_fits_two_attempts_on_the_largest_export():
    """R-REF-09: the per-session cap ($0.25) fits a reservation plus the identical retry for a
    ~28 kB export at Sonnet rates with the adjudicator's max_tokens."""
    pricing = pricing_for_model("claude-sonnet-5", table=TABLE, env=NO_ENV)
    prompt, _ = ref.load_adjudicator_prompt()
    # The reservation estimate is the request's bytes: prompt + export (+ JSON indent slack).
    estimate = len(prompt.encode("utf-8")) + int(28_000 * 1.25)
    one = pricing.cost_micro(estimate, ref.AdjudicatorSettings().max_tokens)
    assert 2 * one / 1_000_000 <= ref.ADJUDICATOR_CALL_CAP_USD
