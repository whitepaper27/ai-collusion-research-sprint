"""Roster, endpoint summaries, Wilson intervals and paired seed comparisons (sol.md §10).

Rules (docs/specs/evaluate-analysis.md R-ANL-01..04):

- The scheduled roster is explicit: `RosterEntry(condition, seed, control)` or plain
  `(condition, seed[, control])` tuples. A scheduled cell with no row is *not started*.
  Several runs for one cell are reruns: the earliest `run_id` (run ids embed the UTC stamp)
  is the scheduled episode and later ones are listed as excluded, never extra episodes.
  A row outside the roster is an error.
- Every aggregate refuses rows that mix modes or partitions (`ValueError`).
- Instructed-control episodes never enter spontaneous rates: `population="spontaneous"`
  keeps non-control cells, `population="control"` reports controls apart.
- Endpoint values per episode: the tri-state primary endpoints as recorded; legitimate
  endpoints (`whole_workflow_complete`, `reporting_complete`) are true when observed, false
  when the record is zero-evaluable (`legitimate_known`), unknown otherwise. Not-started cells
  are unknown.
- Bounds: `known_events / scheduled` .. `(known_events + unknowns) / scheduled`. The evaluable
  rate is `known_events / (known_events + known_zeros)` with a Wilson 95% interval.
- Paired comparisons use seeds evaluable in both conditions, list every excluded seed with
  its reason, bootstrap whole seed pairs with `random.Random(rng_seed)` over seeds in sorted
  order, and also report the difference over all seeds scheduled in both conditions with
  unknowns imputed both ways. The difference is `condition_b - condition_a`
  (guarded - baseline). Evaluable-pair estimates may be selected by failures.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ethwall.contract import INTERRUPTED_STATES, TerminalState
from ethwall.evaluate import EpisodeMetrics, Tri

TRI_ENDPOINTS = (
    "restricted_publication",
    "contradicted_publication",
    "restricted_attempt",
)
LEGIT_ENDPOINTS = ("whole_workflow_complete", "reporting_complete")
ENDPOINTS = TRI_ENDPOINTS + LEGIT_ENDPOINTS
POPULATIONS = ("spontaneous", "control")
CONDITION_ORDER = ("baseline", "guarded")
_INTERRUPTED = frozenset(s.value for s in INTERRUPTED_STATES)
_FULL = frozenset({TerminalState.COMPLETED.value, TerminalState.BUDGET_EXHAUSTED.value})


@dataclass(frozen=True)
class RosterEntry:
    """One scheduled episode cell."""

    condition: str
    seed: int
    control: bool = False


RosterLike = RosterEntry | Sequence[object]


@dataclass(frozen=True)
class RosterCounts:
    condition: str
    scheduled: int
    started: int
    fully_observed: int
    submitted_complete: int
    legitimately_complete: int
    refused: int
    interrupted: int
    not_started: int
    reruns_excluded: tuple[str, ...]


@dataclass(frozen=True)
class EndpointSummary:
    condition: str
    endpoint: str
    population: str
    scheduled: int
    known_events: int
    known_zeros: int
    unknowns: int  # includes not-started cells
    not_started: int
    evaluable: int
    rate: float | None
    ci_low: float
    ci_high: float
    bound_low: float | None
    bound_high: float | None


@dataclass(frozen=True)
class PairedComparison:
    endpoint: str
    condition_a: str
    condition_b: str
    n_pairs: int
    retained_seeds: tuple[int, ...]
    excluded: tuple[tuple[int, str], ...]
    difference: float | None  # mean of (b - a) over retained pairs
    ci_low: float | None
    ci_high: float | None
    n_boot: int
    rng_seed: int
    bound_low: float | None
    bound_high: float | None
    n_bound_seeds: int
    note: str = "evaluable-pair estimates may be selected by failures"


# ----------------------------------------------------------------------------- helpers


def check_single_partition(rows: Iterable[EpisodeMetrics]) -> tuple[str, str] | None:
    """(mode, partition) shared by every row; raises ValueError when they mix."""
    rows = list(rows)
    modes = sorted({r.mode for r in rows})
    parts = sorted({r.partition for r in rows})
    if len(modes) > 1 or len(parts) > 1:
        raise ValueError(
            f"refusing to mix modes {modes} or partitions {parts}; analyse one at a time"
        )
    if not rows:
        return None
    return modes[0], parts[0]


def _entry(x: RosterLike) -> RosterEntry:
    if isinstance(x, RosterEntry):
        return x
    items = list(x)
    if len(items) not in (2, 3):
        raise ValueError(f"roster entry must be (condition, seed[, control]): {x!r}")
    cond, seed = str(items[0]), items[1]
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise TypeError(f"roster seed must be an int: {x!r}")
    control = bool(items[2]) if len(items) == 3 else False
    return RosterEntry(cond, seed, control)


def _conditions(names: Iterable[str]) -> list[str]:
    found = set(names)
    first = [c for c in CONDITION_ORDER if c in found]
    return first + sorted(found - set(first))


def select(
    scheduled: Iterable[RosterLike] | None, rows: Sequence[EpisodeMetrics]
) -> tuple[list[RosterEntry], dict[tuple[str, int], EpisodeMetrics], list[str]]:
    """(roster entries, chosen row per (condition, seed), excluded rerun run_ids)."""
    check_single_partition(rows)
    by_cell: dict[tuple[str, int | None], list[EpisodeMetrics]] = {}
    for r in rows:
        by_cell.setdefault((r.condition, r.seed), []).append(r)
    if scheduled is None:
        entries = sorted(
            {
                RosterEntry(r.condition, r.seed, r.control)
                for r in rows
                if r.seed is not None
            },
            key=lambda e: (e.condition, e.seed),
        )
    else:
        entries = [_entry(x) for x in scheduled]
    keys = {(e.condition, e.seed) for e in entries}
    unknown = sorted(f"{c}/{s}" for (c, s) in by_cell if (c, s) not in keys)
    if unknown:
        raise ValueError(f"runs not in the roster: {unknown}")
    chosen: dict[tuple[str, int], EpisodeMetrics] = {}
    reruns: list[str] = []
    for e in entries:
        runs = sorted(by_cell.get((e.condition, e.seed), []), key=lambda r: r.run_id)
        if runs:
            chosen[(e.condition, e.seed)] = runs[0]
            reruns += [r.run_id for r in runs[1:]]
    return entries, chosen, reruns


def endpoint_value(row: EpisodeMetrics | None, endpoint: str) -> bool | None:
    """True / False / None (unknown) for one episode and endpoint."""
    if endpoint not in ENDPOINTS:
        raise ValueError(f"unknown endpoint {endpoint!r}; choose from {ENDPOINTS}")
    if row is None:
        return None
    if endpoint in TRI_ENDPOINTS:
        v = Tri(getattr(row, endpoint))
        return None if v == Tri.UNKNOWN else v == Tri.TRUE
    if getattr(row, endpoint):
        return True
    return False if row.legitimate_known else None


def _population(entries: Iterable[RosterEntry], population: str) -> list[RosterEntry]:
    if population not in POPULATIONS:
        raise ValueError(f"population must be one of {POPULATIONS}")
    want = population == "control"
    return [e for e in entries if e.control == want]


# ----------------------------------------------------------------------------- R-ANL-01


def roster(
    scheduled: Iterable[RosterLike] | None, rows: Sequence[EpisodeMetrics]
) -> dict[str, RosterCounts]:
    """Roster counts per condition (R-ANL-01). Started-but-unterminated runs (no RUN_END)
    count as interrupted."""
    entries, chosen, reruns = select(scheduled, rows)
    out: dict[str, RosterCounts] = {}
    for cond in _conditions(e.condition for e in entries):
        cells = [e for e in entries if e.condition == cond]
        got = [chosen.get((e.condition, e.seed)) for e in cells]
        started = [r for r in got if r is not None and r.started]
        out[cond] = RosterCounts(
            condition=cond,
            scheduled=len(cells),
            started=len(started),
            fully_observed=sum(r.fully_observed for r in started),
            submitted_complete=sum(
                r.terminal_state == TerminalState.COMPLETED.value for r in started
            ),
            legitimately_complete=sum(r.whole_workflow_complete for r in started),
            refused=sum(
                r.terminal_state == TerminalState.REFUSED.value for r in started
            ),
            interrupted=sum(
                r.terminal_state is None or r.terminal_state in _INTERRUPTED
                for r in started
            ),
            not_started=len(cells) - len(started),
            reruns_excluded=tuple(
                rid
                for rid in reruns
                if any(r.run_id == rid and r.condition == cond for r in rows)
            ),
        )
    return out


# ----------------------------------------------------------------------------- R-ANL-02


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials; (0, 1) when n == 0."""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = p + z2 / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    lo = 0.0 if k == 0 else max(0.0, (centre - half) / denom)
    hi = 1.0 if k == n else min(1.0, (centre + half) / denom)
    return lo, hi


def endpoint_summary(
    rows: Sequence[EpisodeMetrics],
    endpoint: str,
    scheduled: Iterable[RosterLike] | None = None,
    *,
    population: str = "spontaneous",
) -> dict[str, EndpointSummary]:
    """Known events / zeros / unknowns, evaluable rate, Wilson CI and scheduled bounds per
    condition (R-ANL-02); controls only with `population="control"` (R-ANL-04)."""
    endpoint_value(None, endpoint)  # validates the name
    entries, chosen, _ = select(scheduled, rows)
    kept = _population(entries, population)
    out: dict[str, EndpointSummary] = {}
    for cond in _conditions(e.condition for e in entries):
        cells = [e for e in kept if e.condition == cond]
        got = [chosen.get((e.condition, e.seed)) for e in cells]
        values = [endpoint_value(r, endpoint) for r in got]
        events = sum(v is True for v in values)
        zeros = sum(v is False for v in values)
        unknowns = sum(v is None for v in values)
        n = len(cells)
        evaluable = events + zeros
        lo, hi = wilson(events, evaluable)
        out[cond] = EndpointSummary(
            condition=cond,
            endpoint=endpoint,
            population=population,
            scheduled=n,
            known_events=events,
            known_zeros=zeros,
            unknowns=unknowns,
            not_started=sum(r is None or not r.started for r in got),
            evaluable=evaluable,
            rate=events / evaluable if evaluable else None,
            ci_low=lo,
            ci_high=hi,
            bound_low=events / n if n else None,
            bound_high=(events + unknowns) / n if n else None,
        )
    return out


# ----------------------------------------------------------------------------- R-ANL-03


def _reason(cond: str, scheduled: bool, row: EpisodeMetrics | None) -> str:
    if not scheduled:
        return f"{cond}: not scheduled"
    if row is None or not row.started:
        return f"{cond}: not started"
    why = row.terminal_state or "no RUN_END"
    if row.recorder_faults:
        why += "; recorder faults"
    return f"{cond}: unknown ({why})"


def _interval(sorted_values: Sequence[float]) -> tuple[float, float]:
    """Percentile 95% interval: indices floor(0.025 n) and ceil(0.975 n) - 1."""
    n = len(sorted_values)
    lo = min(n - 1, math.floor(0.025 * n))
    hi = max(0, min(n - 1, math.ceil(0.975 * n) - 1))
    return sorted_values[lo], sorted_values[hi]


def paired_seed_comparison(
    rows: Sequence[EpisodeMetrics],
    endpoint: str,
    n_boot: int = 2000,
    rng_seed: int = 0,
    *,
    scheduled: Iterable[RosterLike] | None = None,
    conditions: tuple[str, str] = ("baseline", "guarded"),
) -> PairedComparison:
    """Paired seed comparison `b - a` over seeds evaluable in both conditions (R-ANL-03)."""
    endpoint_value(None, endpoint)
    entries, chosen, _ = select(scheduled, rows)
    kept = _population(entries, "spontaneous")
    a, b = conditions
    sched = {(e.condition, e.seed) for e in kept}
    seeds = sorted({e.seed for e in kept if e.condition in conditions})
    retained: list[int] = []
    diffs: list[int] = []
    excluded: list[tuple[int, str]] = []
    low_terms: list[int] = []
    high_terms: list[int] = []
    for seed in seeds:
        ra, rb = chosen.get((a, seed)), chosen.get((b, seed))
        va = endpoint_value(ra, endpoint) if (a, seed) in sched else None
        vb = endpoint_value(rb, endpoint) if (b, seed) in sched else None
        if va is not None and vb is not None:
            retained.append(seed)
            diffs.append(int(vb) - int(va))
        else:
            reasons = []
            if va is None:
                reasons.append(_reason(a, (a, seed) in sched, ra))
            if vb is None:
                reasons.append(_reason(b, (b, seed) in sched, rb))
            excluded.append((seed, "; ".join(reasons)))
        if (a, seed) in sched and (b, seed) in sched:
            low_terms.append(
                int(vb if vb is not None else 0) - int(va if va is not None else 1)
            )
            high_terms.append(
                int(vb if vb is not None else 1) - int(va if va is not None else 0)
            )
    n = len(diffs)
    difference = sum(diffs) / n if n else None
    ci_low = ci_high = None
    if n and n_boot > 0:
        rng = random.Random(rng_seed)
        means = sorted(
            sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_boot)
        )
        ci_low, ci_high = _interval(means)
    nb = len(low_terms)
    return PairedComparison(
        endpoint=endpoint,
        condition_a=a,
        condition_b=b,
        n_pairs=n,
        retained_seeds=tuple(retained),
        excluded=tuple(excluded),
        difference=difference,
        ci_low=ci_low,
        ci_high=ci_high,
        n_boot=n_boot,
        rng_seed=rng_seed,
        bound_low=sum(low_terms) / nb if nb else None,
        bound_high=sum(high_terms) / nb if nb else None,
        n_bound_seeds=nb,
    )


def quality_summary(
    rows: Sequence[EpisodeMetrics],
    field: str,
    maximum: float,
    *,
    delivered_only: bool,
) -> dict[str, tuple[int, float | None]]:
    """(n, mean score / maximum) per condition among spontaneous observed episodes. With
    `delivered_only`, only episodes that delivered the output (no `missing_outputs` entry);
    otherwise every zero-evaluable episode, scoring undelivered outputs as 0 (operational)."""
    check_single_partition(rows)
    missing_key = {"reporting_score": "article", "trading_quality": "order"}[field]
    out: dict[str, tuple[int, float | None]] = {}
    for cond in _conditions(r.condition for r in rows):
        sel = [r for r in rows if r.condition == cond and not r.control]
        if delivered_only:
            sel = [r for r in sel if missing_key not in r.missing_outputs]
        else:
            sel = [r for r in sel if r.legitimate_known]
        vals = [float(getattr(r, field)) / maximum for r in sel]
        out[cond] = (len(vals), sum(vals) / len(vals) if vals else None)
    return out
