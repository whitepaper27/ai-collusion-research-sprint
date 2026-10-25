"""Spend ledger (docs/specs/runtime.md R-SPD-01..05)."""

from __future__ import annotations

import dataclasses
import json
import socket
from pathlib import Path

import pytest
from w3_support import live_config, mock_config, run

from ethwall.contract import (
    BATCH_CAP_USD,
    EPISODE_CAP_USD,
    TOTAL_CAP_USD,
    Condition,
    EventKind,
    Mode,
    TerminalState,
)
from ethwall.models import mock_model
from ethwall.spend import Pricing, PricingError, SpendLedger, load_pricing
from ethwall.testing import tiny_world

PRICE_ENV = {
    "ETHWALL_PRICE_INPUT_PER_MTOK": "1.0",
    "ETHWALL_PRICE_OUTPUT_PER_MTOK": "5.0",
    "ETHWALL_PRICE_SOURCE": "test fixture price sheet",
    "ETHWALL_PRICE_DATE": "2026-10-24",
}
PRICING = Pricing(1.0, 5.0, "test fixture price sheet", "2026-10-24")


def ledger(tmp_path: Path, partition: str = "smoke", **kw) -> SpendLedger:
    return SpendLedger.for_partition(partition, kw.pop("pricing", PRICING), tmp_path)


# ----------------------------------------------------------------------------- R-SPD-01


def test_load_pricing_reads_basis_from_config(monkeypatch):
    """R-SPD-01: the pricing basis (USD per million input/output tokens, source, date) comes
    from config (the documented ETHWALL_PRICE_* settings)."""
    for k, v in PRICE_ENV.items():
        monkeypatch.setenv(k, v)
    assert load_pricing() == PRICING


@pytest.mark.parametrize("missing", sorted(PRICE_ENV))
def test_load_pricing_refuses_without_complete_basis(monkeypatch, missing):
    """R-SPD-01: any missing part of the pricing basis is an error naming it."""
    for k, v in PRICE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv(missing)
    with pytest.raises(PricingError, match=missing):
        load_pricing()


@pytest.mark.parametrize("bad", ["", "abc", "-1", "0", "nan", "inf"])
def test_load_pricing_rejects_invalid_prices(monkeypatch, bad):
    """R-SPD-01: prices must be positive finite numbers."""
    for k, v in PRICE_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("ETHWALL_PRICE_INPUT_PER_MTOK", bad)
    with pytest.raises(PricingError):
        load_pricing()


# ----------------------------------------------------------------------------- R-SPD-02


def test_reserve_is_conservative_and_rounded_up(tmp_path):
    """R-SPD-02: reserve (input_token_estimate + max_tokens) x price, rounded up."""
    led = ledger(tmp_path)
    led.begin_episode("ep-1")
    assert led.reserve(1000, 1024) is True
    # 1000 * $1/M + 1024 * $5/M = $0.00612 exactly
    assert led.reserved_usd == pytest.approx(0.00612)
    led.cancel()
    odd = SpendLedger(
        tmp_path / "x" / "_spend.json", "main", 1.0, Pricing(0.3, 0.3, "s", "d")
    )
    odd.begin_episode("ep-odd")
    assert odd.reserve(1, 0) is True
    # 0.3 micro-USD rounds UP to 1 micro-USD
    assert odd.reserved_usd == pytest.approx(0.000001)


def test_reserve_refuses_when_episode_cap_would_be_exceeded(tmp_path):
    """R-SPD-02: a reservation that does not fit the episode cap is refused (no call)."""
    led = SpendLedger(
        tmp_path / "smoke" / "_spend.json", "smoke+control", 0.01, PRICING
    )
    led.begin_episode("ep-1")
    assert led.reserve(0, 1000) is True  # $0.005
    led.settle(0, 1000)
    assert led.reserve(0, 1000) is True  # $0.010 total: fits exactly
    led.settle(0, 1000)
    assert led.reserve(0, 1) is False
    assert led.reserved_usd == 0
    assert led.episode_spent_usd == pytest.approx(0.01)


def test_reserve_refuses_when_batch_cap_would_be_exceeded(tmp_path):
    """R-SPD-02: a reservation that does not fit the batch cap is refused."""
    path = tmp_path / "main" / "_spend.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "batches": {"main": {"spent_micro": 24_999_000, "uncertain": False}},
                "episodes": {},
            }
        ),
        encoding="utf-8",
    )
    led = SpendLedger(path, "main", 1.25, PRICING)
    led.begin_episode("ep-1")
    assert led.reserve(0, 200) is True  # $0.001 -> exactly $25
    led.settle(0, 200)
    assert led.reserve(0, 1) is False


def test_only_one_outstanding_reservation(tmp_path):
    """R-SPD-02: every call is reserved individually before it is made."""
    led = ledger(tmp_path)
    led.begin_episode("ep-1")
    assert led.reserve(10, 10)
    with pytest.raises(RuntimeError):
        led.reserve(10, 10)


# ----------------------------------------------------------------------------- R-SPD-03


def test_settle_charges_actual_usage(tmp_path):
    """R-SPD-03: settlement uses actual usage, not the reservation."""
    led = ledger(tmp_path)
    led.begin_episode("ep-1")
    led.reserve(5000, 1024)
    charged = led.settle(2000, 100)
    assert charged == pytest.approx(0.0025)
    assert led.episode_spent_usd == pytest.approx(0.0025)
    assert led.uncertain is False


def test_missing_usage_keeps_reservation_and_flags_uncertainty(tmp_path):
    """R-SPD-03: missing usage keeps the reservation as the charge and sets the flag."""
    led = ledger(tmp_path)
    led.begin_episode("ep-1")
    led.reserve(1000, 1024)
    charged = led.settle(None, 20)
    assert charged == pytest.approx(0.00612)
    assert led.uncertain is True
    data = json.loads(led.path.read_text(encoding="utf-8"))
    assert data["episodes"]["ep-1"]["uncertain"] is True
    assert data["batches"]["smoke+control"]["uncertain"] is True


def test_settle_without_reservation_is_an_error(tmp_path):
    """R-SPD-03: settlement is always against a reservation."""
    led = ledger(tmp_path)
    led.begin_episode("ep-1")
    with pytest.raises(RuntimeError):
        led.settle(1, 1)


# ----------------------------------------------------------------------------- R-SPD-04


def test_ledger_persists_per_partition_and_reloads(tmp_path):
    """R-SPD-04: the ledger persists at runs/<partition>/_spend.json and survives reloads;
    an open reservation is already counted on disk."""
    led = ledger(tmp_path, "smoke")
    assert led.path == tmp_path / "smoke" / "_spend.json"
    led.begin_episode("ep-1")
    led.reserve(1000, 1000)
    on_disk = json.loads(led.path.read_text(encoding="utf-8"))
    assert on_disk["batches"]["smoke+control"]["spent_micro"] == 6000
    led.settle(1000, 0)
    again = ledger(tmp_path, "smoke")
    assert again.batch_spent_usd == pytest.approx(0.001)
    assert again.total_spent_usd == pytest.approx(0.001)
    assert json.loads(again.path.read_text(encoding="utf-8"))["pricing"]["source"] == (
        "test fixture price sheet"
    )


@pytest.mark.parametrize(
    ("partition", "batch", "cap"),
    [
        ("main", "main", 1.25),
        ("main-fallback", "main", 1.25),
        ("smoke", "smoke+control", 0.80),
        ("control", "smoke+control", 0.80),
    ],
)
def test_partition_caps_match_owner_decision(tmp_path, partition, batch, cap):
    """R-SPD-04: per-episode caps main $1.25, smoke/control $0.80; batch per partition."""
    led = ledger(tmp_path, partition)
    assert led.batch == batch
    assert led.episode_cap_usd == pytest.approx(cap)
    assert EPISODE_CAP_USD[partition.split("-")[0]] == pytest.approx(cap)


def test_batch_and_total_caps_are_the_contract_values():
    """R-SPD-04: batches smoke+control $8, main $25, auditor $10, reserve $7; total $50."""
    assert dict(BATCH_CAP_USD) == {
        "smoke+control": 8.0,
        "main": 25.0,
        "auditor": 10.0,
        "reserve": 7.0,
    }
    assert TOTAL_CAP_USD == 50.0
    assert SpendLedger.batch_cap_usd("auditor") == 10.0


def test_episode_cap_cannot_exceed_partition_or_batch(tmp_path):
    """R-SPD-04: an unknown batch or a non-positive cap is refused."""
    with pytest.raises(ValueError):
        SpendLedger(tmp_path / "a.json", "lunch", 1.0, PRICING)
    with pytest.raises(ValueError):
        SpendLedger(tmp_path / "a.json", "main", 0.0, PRICING)
    with pytest.raises(ValueError):
        SpendLedger.for_partition("mock-x", PRICING, tmp_path)


def test_smoke_and_control_share_one_batch(tmp_path):
    """R-SPD-04: smoke and control partitions draw on the same $8 batch."""
    smoke = ledger(tmp_path, "smoke")
    smoke.begin_episode("s-1")
    smoke.reserve(0, 1000)
    smoke.settle(0, 1000)
    control = ledger(tmp_path, "control")
    assert control.batch_spent_usd == pytest.approx(0.005)
    assert control.total_spent_usd == pytest.approx(0.005)


def test_total_cap_spans_all_partitions(tmp_path):
    """R-SPD-04: the $50 total is enforced across every partition's ledger."""
    other = tmp_path / "auditor" / "_spend.json"
    other.parent.mkdir(parents=True)
    other.write_text(
        json.dumps(
            {
                "schema": 1,
                "batches": {
                    "auditor": {"spent_micro": 10_000_000, "uncertain": False},
                    "reserve": {"spent_micro": 7_000_000, "uncertain": False},
                    "main": {"spent_micro": 25_000_000, "uncertain": False},
                    "smoke+control": {"spent_micro": 7_999_000, "uncertain": False},
                },
                "episodes": {},
            }
        ),
        encoding="utf-8",
    )
    led = ledger(tmp_path, "smoke")
    assert led.total_spent_usd == pytest.approx(49.999)
    led.begin_episode("ep-1")
    assert led.reserve(0, 200) is True  # $0.001 -> exactly $50
    led.settle(0, 200)
    assert led.reserve(0, 1) is False


def test_spend_limit_ends_live_episode_with_partial_record(tmp_path):
    """R-SPD-02 / R-SPD-04: when the next reservation does not fit, the runtime makes no
    call and ends with SPEND_LIMIT, keeping the partial record."""
    world = tiny_world()
    inner = mock_model("honest", world)

    class LiveFake:
        model_id = "actor-under-test"
        mode = Mode.LIVE

        def __init__(self):
            self.calls = 0

        def complete(self, req):
            self.calls += 1
            resp = inner.complete(req)
            return dataclasses.replace(resp, input_tokens=2000, output_tokens=500)

    model = LiveFake()
    led = SpendLedger(
        tmp_path / "smoke" / "_spend.json", "smoke+control", 0.05, PRICING
    )
    r = run(model, live_config(), world=world, ledger=led)
    assert r.result.terminal_state == TerminalState.SPEND_LIMIT
    assert 0 < model.calls < 30
    assert r.result.decisions == model.calls
    assert led.episode_spent_usd <= 0.05
    assert r.events[-1].kind == EventKind.CHECKPOINT


# ----------------------------------------------------------------------------- R-SPD-05


class ExplodingLedger:
    def __getattr__(self, name):
        raise AssertionError(f"ledger.{name} touched in mock mode")


@pytest.mark.parametrize("condition", list(Condition))
def test_mock_mode_costs_zero_and_never_touches_ledger_or_network(
    monkeypatch, condition
):
    """R-SPD-05: mock episodes cost 0, never reserve, and never open a socket."""

    def no_network(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    r = run(
        mock_model("honest", tiny_world()),
        mock_config(condition),
        ledger=ExplodingLedger(),
    )
    assert r.result.terminal_state == TerminalState.COMPLETED
    assert r.result.cost_usd == 0
    assert r.kinds(EventKind.SPEND) == []
    assert r.result.config.mode == Mode.MOCK


def test_fixture_mode_costs_zero():
    """R-SPD-05: fixture mode also costs 0 and records no spend."""
    r = run(
        mock_model("honest", tiny_world()),
        mock_config(mode=Mode.FIXTURE, partition="fixture-test"),
        ledger=ExplodingLedger(),
    )
    assert r.result.cost_usd == 0
    assert r.kinds(EventKind.SPEND) == []
