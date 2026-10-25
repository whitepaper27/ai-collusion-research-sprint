"""Reservation-based spend ledger (sol.md §9; docs/specs/runtime.md R-SPD-01..05).

- The pricing basis (USD per million input/output tokens, where it was read, when) comes from
  the documented `ETHWALL_PRICE_*` settings (`.env.example`); `load_pricing()` refuses to return
  without all four. Live mode refuses to start without a ledger (see `ethwall.runtime`).
- Before each call the runtime reserves `input_token_estimate x input price + max_tokens x
  output price`, rounded up to the next micro-dollar. A reservation that does not fit the
  episode cap, the batch cap or the $50 total is refused and the call is not made.
- Settlement charges actual usage. Missing usage (including a failed transport attempt, whose
  billing is unknown) keeps the reservation as the charge and sets an accounting-uncertainty
  flag.
- State persists per partition at `runs/<partition>/_spend.json`. An open reservation is
  already counted on disk, so a crash can only over-count. Batch and total caps are enforced
  across every partition ledger under the same `runs/` root (smoke and control share the
  `smoke+control` batch; the total spans all of them).

All amounts are integer micro-dollars internally.
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any

from ethwall.contract import BATCH_CAP_USD, EPISODE_CAP_USD, TOTAL_CAP_USD

MICRO = 1_000_000
SCHEMA = 1
PRICE_ENV_KEYS = (
    "ETHWALL_PRICE_INPUT_PER_MTOK",
    "ETHWALL_PRICE_OUTPUT_PER_MTOK",
    "ETHWALL_PRICE_SOURCE",
    "ETHWALL_PRICE_DATE",
)
_PARTITION_BATCH = {
    "main": "main",
    "smoke": "smoke+control",
    "control": "smoke+control",
}


class PricingError(ValueError):
    """The pricing basis is missing or invalid."""


@dataclass(frozen=True)
class Pricing:
    input_per_mtok: float
    output_per_mtok: float
    source: str
    date: str

    def cost_micro(self, input_tokens: int, output_tokens: int) -> int:
        """Exact cost in micro-dollars, rounded up. USD per Mtok == micro-USD per token."""
        exact = Decimal(max(0, int(input_tokens))) * Decimal(
            str(self.input_per_mtok)
        ) + Decimal(max(0, int(output_tokens))) * Decimal(str(self.output_per_mtok))
        return int(exact.to_integral_value(rounding=ROUND_CEILING))


def _price(env: Mapping[str, str], key: str) -> float:
    raw = env.get(key, "").strip()
    if not raw:
        raise PricingError(
            f"{key} is not set; record the pricing basis before live runs"
        )
    try:
        value = float(raw)
    except ValueError as exc:
        raise PricingError(f"{key} must be a number, got {raw!r}") from exc
    if not math.isfinite(value) or value <= 0:
        raise PricingError(f"{key} must be a positive finite number, got {raw!r}")
    return value


def load_pricing(env: Mapping[str, str] | None = None) -> Pricing:
    """Read the pricing basis from the environment (or `env`). Raises PricingError."""
    env = os.environ if env is None else env
    for key in PRICE_ENV_KEYS[2:]:
        if not env.get(key, "").strip():
            raise PricingError(f"{key} is not set; record the pricing basis")
    return Pricing(
        _price(env, PRICE_ENV_KEYS[0]),
        _price(env, PRICE_ENV_KEYS[1]),
        env[PRICE_ENV_KEYS[2]].strip(),
        env[PRICE_ENV_KEYS[3]].strip(),
    )


def batch_for_partition(partition: str) -> str:
    base = partition.split("-")[0]
    if base not in _PARTITION_BATCH:
        raise ValueError(f"no paid batch for partition {partition!r}")
    return _PARTITION_BATCH[base]


def _micro(usd: float) -> int:
    return round(usd * MICRO)


def _empty() -> dict[str, Any]:
    return {"schema": SCHEMA, "batches": {}, "episodes": {}}


class SpendLedger:
    """Per-partition ledger enforcing episode, batch and total caps before every call."""

    def __init__(
        self, path: str | Path, batch: str, episode_cap_usd: float, pricing: Pricing
    ):
        if batch not in BATCH_CAP_USD:
            raise ValueError(f"unknown batch {batch!r}; one of {sorted(BATCH_CAP_USD)}")
        cap = float(episode_cap_usd)
        if not math.isfinite(cap) or cap <= 0:
            raise ValueError("episode_cap_usd must be a positive finite number")
        self.path = Path(path)
        self.batch = batch
        self.episode_cap_usd = cap
        self.pricing = pricing
        self._episode_cap = _micro(cap)
        self._batch_cap = _micro(BATCH_CAP_USD[batch])
        self._total_cap = _micro(TOTAL_CAP_USD)
        self._data = self._read(self.path) if self.path.exists() else _empty()
        self._episode_id: str | None = None
        self._open: int | None = None
        self.last_reserved_usd = 0.0

    # ------------------------------------------------------------------ construction

    @classmethod
    def for_partition(
        cls, partition: str, pricing: Pricing, root: str | Path = "runs"
    ) -> SpendLedger:
        """Ledger at `<root>/<partition>/_spend.json` with that partition's caps."""
        batch = batch_for_partition(partition)
        cap = EPISODE_CAP_USD[partition.split("-")[0]]
        return cls(Path(root) / partition / "_spend.json", batch, cap, pricing)

    @staticmethod
    def batch_cap_usd(batch: str) -> float:
        return float(BATCH_CAP_USD[batch])

    # ------------------------------------------------------------------ episodes

    def begin_episode(self, episode_id: str) -> None:
        if self._open is not None:
            raise RuntimeError("cannot start an episode with an open reservation")
        self._episode_id = episode_id
        self._data["episodes"].setdefault(
            episode_id,
            {"batch": self.batch, "spent_micro": 0, "uncertain": False, "calls": 0},
        )

    def _episode(self) -> dict[str, Any]:
        if self._episode_id is None:
            raise RuntimeError("begin_episode() must be called before reserving")
        return self._data["episodes"][self._episode_id]

    def _batch(self) -> dict[str, Any]:
        return self._data["batches"].setdefault(
            self.batch, {"spent_micro": 0, "uncertain": False}
        )

    # ------------------------------------------------------------------ calls

    def reserve(self, input_token_estimate: int, max_tokens: int) -> bool:
        """Reserve a conservative cost for one call; False (no state change) if it won't fit."""
        if self._open is not None:
            raise RuntimeError(
                "a reservation is already open; settle or cancel it first"
            )
        episode = self._episode()
        cost = self.pricing.cost_micro(input_token_estimate, max_tokens)
        others_batch, others_total = self._others()
        fits = (
            episode["spent_micro"] + cost <= self._episode_cap
            and self._own_batch() + others_batch + cost <= self._batch_cap
            and self._own_total() + others_total + cost <= self._total_cap
        )
        if not fits:
            return False
        self._open = cost
        self.last_reserved_usd = cost / MICRO
        self._add(cost)
        self._write()
        return True

    def settle(self, input_tokens: int | None, output_tokens: int | None) -> float:
        """Charge actual usage; missing usage keeps the reservation and flags uncertainty."""
        if self._open is None:
            raise RuntimeError("settle() without an open reservation")
        if input_tokens is None or output_tokens is None:
            charge = self._open
            self._episode()["uncertain"] = True
            self._batch()["uncertain"] = True
        else:
            charge = self.pricing.cost_micro(input_tokens, output_tokens)
        self._add(charge - self._open)
        self._episode()["calls"] += 1
        self._open = None
        self._write()
        return charge / MICRO

    def cancel(self) -> None:
        """Release an open reservation for a call that was never made."""
        if self._open is None:
            return
        self._add(-self._open)
        self._open = None
        self._write()

    # ------------------------------------------------------------------ views

    @property
    def reserved_usd(self) -> float:
        return (self._open or 0) / MICRO

    @property
    def episode_spent_usd(self) -> float:
        return self._episode()["spent_micro"] / MICRO

    @property
    def batch_spent_usd(self) -> float:
        return (self._own_batch() + self._others()[0]) / MICRO

    @property
    def total_spent_usd(self) -> float:
        return (self._own_total() + self._others()[1]) / MICRO

    @property
    def uncertain(self) -> bool:
        if self._episode_id is None:
            return bool(self._batch()["uncertain"])
        return bool(self._episode()["uncertain"])

    # ------------------------------------------------------------------ internals

    def _add(self, micro: int) -> None:
        self._episode()["spent_micro"] += micro
        self._batch()["spent_micro"] += micro

    def _own_batch(self) -> int:
        return int(self._data["batches"].get(self.batch, {}).get("spent_micro", 0))

    def _own_total(self) -> int:
        return sum(int(b.get("spent_micro", 0)) for b in self._data["batches"].values())

    def _others(self) -> tuple[int, int]:
        """(this batch, all batches) spent in the other partition ledgers under root."""
        batch = total = 0
        root = self.path.parent.parent
        mine = self.path.resolve()
        for p in sorted(root.glob("*/_spend.json")):
            if p.resolve() == mine:
                continue
            for name, b in self._read(p).get("batches", {}).items():
                spent = int(b.get("spent_micro", 0))
                total += spent
                if name == self.batch:
                    batch += spent
        return batch, total

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("batches", {})
        data.setdefault("episodes", {})
        return data

    def _write(self) -> None:
        self._data["schema"] = SCHEMA
        self._data["pricing"] = dataclasses.asdict(self.pricing)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps(self._data, sort_keys=True, indent=1), encoding="utf-8"
        )
        os.replace(tmp, self.path)
