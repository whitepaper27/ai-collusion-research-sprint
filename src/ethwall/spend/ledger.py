"""Reservation-based spend ledger (sol.md §9; docs/specs/runtime.md R-SPD-01..06).

- The pricing basis (USD per million input/output tokens, where it was read, when) comes from
  the documented `ETHWALL_PRICE_*` settings (`.env.example`); `load_pricing()` refuses to return
  without all four. Live mode refuses to start without a ledger (see `ethwall.runtime`).
- Mixed models (docs/specs/references.md R-REF-09): `protocol/pricing.json` records per-model
  rates; `pricing_for_model` / `SpendLedger.for_model` price a ledger by its configured model id
  and refuse a model with no recorded price. Ledgers priced differently still share the root
  lock and the batch and total caps, since caps are summed in micro-dollars across files.
- Before each call the runtime reserves `input_token_estimate x input price + max_tokens x
  output price`, rounded up to the next micro-dollar. A reservation that does not fit the
  episode cap, the batch cap or the $50 total is refused and the call is not made.
- Settlement charges actual usage. Missing usage (including a failed transport attempt, whose
  billing is unknown) keeps the reservation as the charge and sets an accounting-uncertainty
  flag.
- State persists per partition at `spend/<partition>/_spend.json` (outside `runs/`, which holds
  immutable evidence; the ledger is mutable accounting). An open reservation is
  already counted on disk, so a crash can only over-count. Batch and total caps are enforced
  across every partition ledger under the same `spend/` root (smoke and control share the
  `smoke+control` batch; the total spans all of them).
- Concurrency (R-SPD-06): every ledger read and every read-modify-write (reserve, settle,
  cancel, begin_episode, the spend views) holds one root-wide lock, `<root>/.spend.lock`,
  and re-reads this partition's file and every other partition file from disk under it, so
  parallel episodes, or an actor run beside an auditor run, cannot lose updates or overshoot
  a cap. The lock is a byte lock on an open file (`msvcrt.locking` on Windows, `fcntl.flock`
  elsewhere), released by the OS if the process dies; waiting is bounded
  (`LedgerLockTimeout`).

All amounts are integer micro-dollars internally.
"""

from __future__ import annotations

import contextlib
import dataclasses
import errno
import json
import math
import os
import random
import sys
import time
from collections.abc import Iterator, Mapping
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
LOCK_NAME = ".spend.lock"
SPEND_ROOT_ENV = "ETHWALL_SPEND_ROOT"
LOCK_TIMEOUT_S = 30.0
_PARTITION_BATCH = {
    "main": "main",
    "smoke": "smoke+control",
    "control": "smoke+control",
}


class PricingError(ValueError):
    """The pricing basis is missing or invalid."""


class LedgerLockTimeout(TimeoutError):
    """The root-wide spend lock could not be acquired within the timeout (R-SPD-06)."""


@dataclass(frozen=True)
class Pricing:
    input_per_mtok: float
    output_per_mtok: float
    source: str
    date: str
    model: str | None = (
        None  # set when priced per model from the pricing table (R-REF-09)
    )

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


# ---------------------------------------------------------------------------- per-model table

PRICING_TABLE_PATH = Path(__file__).resolve().parents[3] / "protocol" / "pricing.json"


def _table_price(model: str, entry: Any, key: str) -> float:
    value = entry.get(key) if isinstance(entry, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PricingError(f"pricing table: {model}.{key} must be a number")
    if not math.isfinite(value) or value <= 0:
        raise PricingError(f"pricing table: {model}.{key} must be positive and finite")
    return float(value)


def load_pricing_table(path: str | Path | None = None) -> dict[str, Any]:
    """The committed per-model pricing table (docs/specs/references.md R-REF-09).

    `{"basis_source", "basis_date", "models": {model id: {"input_per_mtok",
    "output_per_mtok"}}}`. Raises PricingError when anything is missing or not positive, so a
    half-valid table is never used.
    """
    target = Path(path) if path is not None else PRICING_TABLE_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PricingError(f"pricing table {target} unreadable: {exc}") from exc
    if not isinstance(data, Mapping):
        raise PricingError("pricing table must be a JSON object")
    for key in ("basis_source", "basis_date"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise PricingError(f"pricing table: {key} is required")
    models = data.get("models")
    if not isinstance(models, Mapping) or not models:
        raise PricingError("pricing table: models must be a non-empty object")
    for model, entry in models.items():
        for key in ("input_per_mtok", "output_per_mtok"):
            _table_price(str(model), entry, key)
    return dict(data)


def pricing_for_model(
    model_id: str,
    *,
    table: Mapping[str, Any] | None = None,
    path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Pricing:
    """The Pricing for one configured model id from the table (R-REF-09).

    Exact match on the configured id (reservations happen before any response, so a served id
    with a date suffix is never the lookup key). A model with no recorded price is refused. The
    env basis (`ETHWALL_PRICE_*`) describes the actor model (`ACTOR_MODEL_ID`); when it is fully
    set for this same model it must agree with the table, else PricingError.
    """
    data = dict(table) if table is not None else load_pricing_table(path)
    mid = (model_id or "").strip()
    entry = (data.get("models") or {}).get(mid)
    if not mid or entry is None:
        raise PricingError(
            f"no recorded price for model {mid!r} in the pricing table "
            "(protocol/pricing.json); refusing paid calls"
        )
    pricing = Pricing(
        _table_price(mid, entry, "input_per_mtok"),
        _table_price(mid, entry, "output_per_mtok"),
        str(data["basis_source"]).strip(),
        str(data["basis_date"]).strip(),
        model=mid,
    )
    env = os.environ if env is None else env
    if env.get("ACTOR_MODEL_ID", "").strip() == mid and all(
        env.get(k, "").strip() for k in PRICE_ENV_KEYS
    ):
        basis = load_pricing(env)
        if (basis.input_per_mtok, basis.output_per_mtok) != (
            pricing.input_per_mtok,
            pricing.output_per_mtok,
        ):
            raise PricingError(
                f"env pricing basis for {mid!r} conflicts with the pricing table; "
                "fix one of them before paid calls"
            )
    return pricing


def default_spend_root(env: Mapping[str, str] | None = None) -> str:
    """The spend root every paid command defaults to (actor, auditor, adjudicator).

    `ETHWALL_SPEND_ROOT` when set, else `<checkout>/spend`. One shared default, so the caps
    (summed across the ledgers under one root) can never be split between commands.
    """
    env = os.environ if env is None else env
    root = env.get(SPEND_ROOT_ENV, "").strip()
    return root or str(Path(__file__).resolve().parents[3] / "spend")


def batch_for_partition(partition: str) -> str:
    base = partition.split("-")[0]
    if base not in _PARTITION_BATCH:
        raise ValueError(f"no paid batch for partition {partition!r}")
    return _PARTITION_BATCH[base]


def _micro(usd: float) -> int:
    return round(usd * MICRO)


def _empty() -> dict[str, Any]:
    return {"schema": SCHEMA, "batches": {}, "episodes": {}}


# ---------------------------------------------------------------------------- root lock

# errno values meaning "another handle holds the lock" (msvcrt: EACCES/EDEADLOCK; flock:
# EWOULDBLOCK/EAGAIN). Anything else is a real error and propagates.
_BUSY = {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, getattr(errno, "EDEADLOCK", -1)}

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in _BUSY:
                return False
            raise
        return True

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in _BUSY:
                return False
            raise
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


REPLACE_ATTEMPTS = 20


def _replace_with_retry(
    src: Path, dst: Path, *, attempts: int = REPLACE_ATTEMPTS
) -> None:
    """`os.replace` that rides out transient Windows sharing violations.

    On Windows another process (antivirus, the search indexer) can hold the target open for a
    moment, making the rename fail with PermissionError ("Access is denied"); observed on a live
    control run (2026-10-25), where it ended the episode as SETUP_ERROR. Our own writers are
    already serialized by the root lock, so a short bounded retry is safe; the last failure is
    re-raised unchanged.
    """
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(min(0.05 * (attempt + 1), 0.5))


@contextlib.contextmanager
def ledger_lock(root: str | Path, timeout: float = LOCK_TIMEOUT_S) -> Iterator[None]:
    """Hold the root-wide spend lock `<root>/.spend.lock` (R-SPD-06).

    Exclusive across processes, and across separate acquisitions within one process (each
    call opens its own handle), so it is not re-entrant. Raises LedgerLockTimeout when the
    lock is still held by someone else after `timeout` seconds.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / LOCK_NAME
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
    try:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise LedgerLockTimeout(
                    f"spend ledger lock {path} not acquired within {timeout:g}s"
                )
            time.sleep(random.uniform(0.005, 0.02))
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------- ledger


class SpendLedger:
    """Per-partition ledger enforcing episode, batch and total caps before every call.

    The files under the root are the only state shared between ledgers: `_data` is a scratch
    copy reloaded under the root lock at the start of every operation and never trusted
    across calls (R-SPD-06). Only the open reservation of this instance lives in memory.
    """

    lock_timeout_s: float = LOCK_TIMEOUT_S

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
        self._root = self.path.parent.parent
        self._data = _empty()
        self._episode_id: str | None = None
        self._open: int | None = None
        self.last_reserved_usd = 0.0

    # ------------------------------------------------------------------ construction

    @classmethod
    def for_partition(
        cls, partition: str, pricing: Pricing, root: str | Path = "spend"
    ) -> SpendLedger:
        """Ledger at `<root>/<partition>/_spend.json` with that partition's caps."""
        batch = batch_for_partition(partition)
        cap = EPISODE_CAP_USD[partition.split("-")[0]]
        return cls(Path(root) / partition / "_spend.json", batch, cap, pricing)

    @classmethod
    def for_model(
        cls,
        model_id: str,
        path: str | Path,
        batch: str,
        episode_cap_usd: float,
        *,
        table: Mapping[str, Any] | None = None,
        env: Mapping[str, str] | None = None,
    ) -> SpendLedger:
        """Ledger at `path` that prices every call at `model_id`'s table rate (R-REF-09).

        Caps are shared with every other ledger under the same root (`path.parent.parent`).
        """
        pricing = pricing_for_model(model_id, table=table, env=env)
        return cls(path, batch, episode_cap_usd, pricing)

    @staticmethod
    def batch_cap_usd(batch: str) -> float:
        return float(BATCH_CAP_USD[batch])

    # ------------------------------------------------------------------ episodes

    def begin_episode(self, episode_id: str) -> None:
        if self._open is not None:
            raise RuntimeError("cannot start an episode with an open reservation")
        with (
            self._locked()
        ):  # fail at setup, not mid-episode, if the ledger is unreadable
            self._episode_id = episode_id
            self._episode()

    def _episode(self) -> dict[str, Any]:
        if self._episode_id is None:
            raise RuntimeError("begin_episode() must be called before reserving")
        return self._data["episodes"].setdefault(
            self._episode_id,
            {"batch": self.batch, "spent_micro": 0, "uncertain": False, "calls": 0},
        )

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
        if self._episode_id is None:
            raise RuntimeError("begin_episode() must be called before reserving")
        cost = self.pricing.cost_micro(input_token_estimate, max_tokens)
        with self._locked():
            episode = self._episode()
            others_batch, others_total = self._others()
            fits = (
                episode["spent_micro"] + cost <= self._episode_cap
                and self._own_batch() + others_batch + cost <= self._batch_cap
                and self._own_total() + others_total + cost <= self._total_cap
            )
            if not fits:
                return False
            self._add(cost)
            self._write()
        self._open = cost
        self.last_reserved_usd = cost / MICRO
        return True

    def settle(self, input_tokens: int | None, output_tokens: int | None) -> float:
        """Charge actual usage; missing usage keeps the reservation and flags uncertainty.

        The call has been made, so the reservation is closed even if settling fails (e.g.
        LedgerLockTimeout): the reserved amount then stays on disk as the charge (an
        over-count) and a later cancel() cannot release it.
        """
        if self._open is None:
            raise RuntimeError("settle() without an open reservation")
        reserved, self._open = self._open, None
        with self._locked():
            if input_tokens is None or output_tokens is None:
                charge = reserved
                self._episode()["uncertain"] = True
                self._batch()["uncertain"] = True
            else:
                charge = self.pricing.cost_micro(input_tokens, output_tokens)
            self._add(charge - reserved)
            self._episode()["calls"] += 1
            self._write()
        return charge / MICRO

    def cancel(self) -> None:
        """Release an open reservation for a call that was never made."""
        if self._open is None:
            return
        with self._locked():
            self._add(-self._open)
            self._write()
        self._open = None

    # ------------------------------------------------------------------ views

    @property
    def reserved_usd(self) -> float:
        return (self._open or 0) / MICRO

    @property
    def episode_spent_usd(self) -> float:
        with self._locked():
            return self._episode()["spent_micro"] / MICRO

    @property
    def batch_spent_usd(self) -> float:
        with self._locked():
            return (self._own_batch() + self._others()[0]) / MICRO

    @property
    def total_spent_usd(self) -> float:
        with self._locked():
            return (self._own_total() + self._others()[1]) / MICRO

    @property
    def uncertain(self) -> bool:
        with self._locked():
            if self._episode_id is None:
                return bool(self._batch()["uncertain"])
            return bool(self._episode()["uncertain"])

    # ------------------------------------------------------------------ internals

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        """Hold the root lock and reload this partition's state from disk."""
        with ledger_lock(self._root, self.lock_timeout_s):
            self._data = self._read(self.path) if self.path.exists() else _empty()
            yield

    def _add(self, micro: int) -> None:
        self._episode()["spent_micro"] += micro
        self._batch()["spent_micro"] += micro

    def _own_batch(self) -> int:
        return int(self._data["batches"].get(self.batch, {}).get("spent_micro", 0))

    def _own_total(self) -> int:
        return sum(int(b.get("spent_micro", 0)) for b in self._data["batches"].values())

    def _others(self) -> tuple[int, int]:
        """(this batch, all batches) spent in the other partition ledgers under root.

        Call only while holding the root lock."""
        batch = total = 0
        mine = self.path.resolve()
        for p in sorted(self._root.glob("*/_spend.json")):
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
        """Atomic replace of this partition's file. Call only while holding the root lock."""
        self._data["schema"] = SCHEMA
        self._data["pricing"] = dataclasses.asdict(self.pricing)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps(self._data, sort_keys=True, indent=1), encoding="utf-8"
        )
        _replace_with_retry(tmp, self.path)
