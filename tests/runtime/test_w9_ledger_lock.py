"""Spend ledger safe for concurrent processes (docs/specs/runtime.md R-SPD-06)."""

from __future__ import annotations

import json
import multiprocessing
import threading
import time
from pathlib import Path

import pytest

from ethwall.spend import (
    LedgerLockTimeout,
    Pricing,
    SpendLedger,
    ledger_lock,
)

PRICING = Pricing(1.0, 5.0, "test fixture price sheet", "2026-10-24")
SMOKE_CAP_MICRO = 8_000_000  # smoke+control batch cap from the contract


def _seed(root: Path, partition: str, batches: dict[str, int]) -> None:
    """Write another partition's ledger with prior spend (micro-dollars per batch)."""
    path = root / partition / "_spend.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "batches": {
                    k: {"spent_micro": v, "uncertain": False}
                    for k, v in batches.items()
                },
                "episodes": {},
            }
        ),
        encoding="utf-8",
    )


def _files(root: Path) -> list[dict]:
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(root.glob("*/_spend.json"))
    ]


# ----------------------------------------------------------------------------- no stale cache


def test_same_partition_instances_see_each_others_spend(tmp_path):
    """R-SPD-06: two ledgers on the same partition file never act on a stale copy; the
    second sees the first's spend at once and a reservation that only fits if that spend
    were ignored is refused."""
    _seed(tmp_path, "seed", {"smoke+control": SMOKE_CAP_MICRO - 10_000})
    a = SpendLedger.for_partition("smoke", PRICING, tmp_path)
    b = SpendLedger.for_partition("smoke", PRICING, tmp_path)  # created before a spends
    a.begin_episode("ep-a")
    b.begin_episode("ep-b")
    assert a.reserve(0, 1000) is True  # $0.005 reserved
    assert b.batch_spent_usd == pytest.approx(7.995)
    assert b.reserve(0, 1001) is False  # would fit only if a's reservation were ignored
    a.settle(0, 1000)
    assert b.reserve(0, 1000) is True  # exactly $8.00
    b.settle(0, 1000)
    assert a.reserve(0, 1) is False
    data = json.loads(a.path.read_text(encoding="utf-8"))
    assert data["batches"]["smoke+control"]["spent_micro"] == 10_000  # no lost update
    assert data["episodes"]["ep-a"]["spent_micro"] == 5_000
    assert data["episodes"]["ep-b"]["spent_micro"] == 5_000
    assert data["episodes"]["ep-a"]["calls"] == data["episodes"]["ep-b"]["calls"] == 1


def test_cross_partition_instances_see_each_others_spend(tmp_path):
    """R-SPD-06: a ledger on another partition of the same root sees spend made after it
    was constructed, for both the batch and the total cap."""
    _seed(tmp_path, "seed", {"smoke+control": SMOKE_CAP_MICRO - 10_000})
    smoke = SpendLedger.for_partition("smoke", PRICING, tmp_path)
    control = SpendLedger.for_partition("control", PRICING, tmp_path)
    smoke.begin_episode("s-1")
    control.begin_episode("c-1")
    assert smoke.reserve(0, 1000) is True
    smoke.settle(0, 1000)
    assert control.batch_spent_usd == pytest.approx(7.995)
    assert control.total_spent_usd == pytest.approx(7.995)
    assert control.reserve(0, 1001) is False
    assert control.reserve(0, 1000) is True
    control.settle(0, 1000)
    assert smoke.batch_spent_usd == pytest.approx(8.0)
    assert smoke.reserve(0, 1) is False


def test_total_cap_sees_concurrent_spend_in_another_batch(tmp_path):
    """R-SPD-06: the $50 total includes spend another ledger made after construction."""
    _seed(
        tmp_path,
        "seed",
        {"main": 25_000_000, "reserve": 7_000_000, "smoke+control": 8_000_000},
    )
    smoke = SpendLedger.for_partition("smoke", PRICING, tmp_path)  # batch already full
    auditor = SpendLedger(tmp_path / "audit" / "_spend.json", "auditor", 10.0, PRICING)
    other = SpendLedger(tmp_path / "audit2" / "_spend.json", "auditor", 10.0, PRICING)
    auditor.begin_episode("a-1")
    other.begin_episode("a-2")
    assert auditor.reserve(0, 1_999_000) is True  # $9.995
    auditor.settle(0, 1_999_000)
    assert other.total_spent_usd == pytest.approx(49.995)
    assert other.reserve(0, 1001) is False
    assert other.reserve(0, 1000) is True
    other.settle(0, 1000)
    assert smoke.total_spent_usd == pytest.approx(50.0)


def test_cancel_and_begin_episode_preserve_other_writers(tmp_path):
    """R-SPD-06: cancel() and begin_episode() re-read under the lock and never overwrite
    another instance's spend in the same file."""
    a = SpendLedger.for_partition("main", PRICING, tmp_path)
    b = SpendLedger.for_partition("main", PRICING, tmp_path)
    a.begin_episode("ep-a")
    assert a.reserve(0, 1000)
    b.begin_episode("ep-b")  # after a's reservation hit disk
    assert b.reserve(0, 2000)
    a.cancel()
    b.settle(0, 2000)
    data = json.loads(a.path.read_text(encoding="utf-8"))
    assert data["batches"]["main"]["spent_micro"] == 10_000
    assert data["episodes"]["ep-a"]["spent_micro"] == 0
    assert data["episodes"]["ep-b"]["spent_micro"] == 10_000


# ----------------------------------------------------------------------------- real processes

WORKERS = 8
HEADROOM_MICRO = 200_000  # $0.20 left in the smoke+control batch


def _spend_worker(root: str, partition: str, episode_id: str, start, results) -> None:
    led = SpendLedger.for_partition(partition, PRICING, Path(root))
    led.begin_episode(episode_id)
    start.wait(30)
    calls = 0
    while led.reserve(0, 1000):  # reserve $0.005 ...
        led.settle(0, 600)  # ... settle $0.003
        calls += 1
    results.put((episode_id, calls))


def test_parallel_processes_never_overshoot_or_lose_updates(tmp_path):
    """R-SPD-06: N processes reserving and settling at once against a nearly full batch
    never push it past its cap, and every settled charge is recorded (no lost update)."""
    _seed(tmp_path, "seed", {"smoke+control": SMOKE_CAP_MICRO - HEADROOM_MICRO})
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    results = ctx.Queue()
    procs = [
        ctx.Process(
            target=_spend_worker,
            args=(
                str(tmp_path),
                "smoke" if i % 2 == 0 else "control",
                f"ep-{i}",
                start,
                results,
            ),
        )
        for i in range(WORKERS)
    ]
    for p in procs:
        p.start()
    time.sleep(0.5)
    start.set()
    calls = dict(results.get(timeout=60) for _ in procs)
    for p in procs:
        p.join(timeout=30)
        assert p.exitcode == 0

    files = _files(tmp_path)
    batch = sum(
        f["batches"].get("smoke+control", {}).get("spent_micro", 0) for f in files
    )
    episodes = {k: v for f in files for k, v in f["episodes"].items()}
    assert batch <= SMOKE_CAP_MICRO
    assert set(calls) == {f"ep-{i}" for i in range(WORKERS)}
    # an episode reaches disk with its first reservation; a worker starved of headroom
    # before it got one has nothing to record
    assert set(episodes) == {eid for eid, n in calls.items() if n > 0}
    recorded = sum(e["spent_micro"] for e in episodes.values())
    assert recorded == batch - (SMOKE_CAP_MICRO - HEADROOM_MICRO)
    assert recorded == 3_000 * sum(calls.values())
    for eid, n in calls.items():
        if n == 0:
            continue
        assert episodes[eid]["calls"] == n
        assert episodes[eid]["spent_micro"] == 3_000 * n
    assert SMOKE_CAP_MICRO - batch < 5_000  # headroom exhausted, not stranded


# ----------------------------------------------------------------------------- timeout


def test_lock_wait_is_bounded(tmp_path):
    """R-SPD-06: a second acquire of the root lock while it is held gives up after the
    timeout with LedgerLockTimeout, and succeeds once the lock is released."""
    outcomes: list[object] = []

    def contender(timeout: float) -> None:
        try:
            with ledger_lock(tmp_path, timeout=timeout):
                outcomes.append("acquired")
        except LedgerLockTimeout as exc:
            outcomes.append(exc)

    with ledger_lock(tmp_path):
        assert (tmp_path / ".spend.lock").exists()
        t0 = time.monotonic()
        t = threading.Thread(target=contender, args=(0.3,))
        t.start()
        t.join(10)
        assert 0.25 <= time.monotonic() - t0 < 5
    assert len(outcomes) == 1 and isinstance(outcomes[0], LedgerLockTimeout)
    outcomes.clear()
    t = threading.Thread(target=contender, args=(1.0,))
    t.start()
    t.join(10)
    assert outcomes == ["acquired"]


def test_reserve_raises_when_lock_is_held_and_changes_nothing(tmp_path):
    """R-SPD-06: a ledger that cannot get the root lock raises LedgerLockTimeout instead of
    acting on unlocked state, and leaves no reservation open."""
    led = SpendLedger.for_partition("smoke", PRICING, tmp_path)
    led.begin_episode("ep-1")
    led.lock_timeout_s = 0.2
    with ledger_lock(tmp_path), pytest.raises(LedgerLockTimeout):
        led.reserve(0, 1000)
    assert led.reserved_usd == 0
    assert led.batch_spent_usd == 0
    assert led.reserve(0, 1000) is True
