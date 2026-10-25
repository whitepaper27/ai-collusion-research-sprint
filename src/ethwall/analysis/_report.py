"""Tables, the verified fact sheet and the one-partition pipeline (R-ANL-08).

- `write_tables(out_dir, rows, ...)` writes `roster.csv`, `endpoints.csv` (spontaneous and,
  when present, control populations), `paired.csv` and `episodes.csv`.
- `write_fact_sheet(path, numbers)` writes `fact_sheet.json`; every number carries its source
  file (relative to the fact sheet when possible) and that file's SHA-256, computed from the
  bytes on disk at write time. A missing source file is an error.
- `analyze_partition(source, out_dir, ...)` takes run directories (each with `events.jsonl`;
  the world is rebuilt from the recorded manifest seed) or `EpisodeMetrics` rows for exactly
  one mode/partition, and writes `tables/`, `figures/` and `fact_sheet.json`. The fact sheet
  numbers are read back from the written tables, so each hash covers the reported value.
  Recorder faults come from an injected `verifier(run_dir) -> tuple[str, ...]` (e.g. the
  recorder's `verify_run` bound to its key); without one, only in-stream reconciliation runs.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from ethwall.analysis._figures import (
    AuditMetricRow,
    FigureOutput,
    figure_audit,
    figure_live,
    figure_trajectory,
    stamp_for_modes,
)
from ethwall.analysis._stats import (
    ENDPOINTS,
    RosterCounts,
    RosterEntry,
    RosterLike,
    check_single_partition,
    endpoint_summary,
    paired_seed_comparison,
    select,
)
from ethwall.analysis._stats import (
    roster as roster_counts,
)
from ethwall.contract import (
    SEEDS,
    Condition,
    Event,
    World,
    event_from_dict,
    to_jsonable,
)
from ethwall.evaluate import EpisodeMetrics, Tri, evaluate_run

Number = int | float | bool | None


@dataclass(frozen=True)
class AnalysisResult:
    rows: tuple[EpisodeMetrics, ...]
    tables: Mapping[str, Path]
    figures: Mapping[str, FigureOutput]
    fact_sheet: Path


# ----------------------------------------------------------------------------- tables


def _write(
    path: Path, header: Sequence[str], rows: Iterable[Mapping[str, Any]]
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(header))
        w.writeheader()
        for r in rows:
            w.writerow({k: _cell(r.get(k)) for k in header})
    return path


def _cell(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, Tri):
        return v.value
    if isinstance(v, (Mapping, list, tuple)):
        return json.dumps(to_jsonable(v), sort_keys=True)
    return v


def _record(x: Any) -> dict[str, Any]:
    return {f.name: getattr(x, f.name) for f in fields(x)}


def write_tables(
    out_dir: Path | str,
    rows: Sequence[EpisodeMetrics],
    *,
    scheduled: Iterable[RosterLike] | None = None,
    endpoints: Sequence[str] = ENDPOINTS,
    n_boot: int = 2000,
    rng_seed: int = 0,
    conditions: tuple[str, str] = ("baseline", "guarded"),
) -> dict[str, Path]:
    """Write roster, endpoint, paired and episode tables; return {name: path}."""
    check_single_partition(rows)
    out = Path(out_dir)
    sched = list(scheduled) if scheduled is not None else None
    entries, _, _ = select(sched, rows)
    counts = roster_counts(entries, rows)
    roster_rows = [_record(c) for c in counts.values()]
    roster_header = [f.name for f in fields(RosterCounts)]
    paths = {"roster": _write(out / "roster.csv", roster_header, roster_rows)}
    endpoint_rows: list[dict[str, Any]] = []
    populations = ["spontaneous"] + (
        ["control"] if any(e.control for e in entries) else []
    )
    for population in populations:
        for ep in endpoints:
            for s in endpoint_summary(
                rows, ep, entries, population=population
            ).values():
                endpoint_rows.append(_record(s))
    header = list(endpoint_rows[0]) if endpoint_rows else ["condition", "endpoint"]
    paths["endpoints"] = _write(out / "endpoints.csv", header, endpoint_rows)
    paired_rows = []
    for ep in endpoints:
        p = paired_seed_comparison(
            rows, ep, n_boot, rng_seed, scheduled=entries, conditions=conditions
        )
        rec = _record(p)
        rec["retained_seeds"] = " ".join(str(s) for s in p.retained_seeds)
        rec["excluded"] = " | ".join(f"{s}: {why}" for s, why in p.excluded)
        paired_rows.append(rec)
    paths["paired"] = _write(out / "paired.csv", list(paired_rows[0]), paired_rows)
    ep_header = [f.name for f in fields(EpisodeMetrics)]
    paths["episodes"] = _write(
        out / "episodes.csv",
        ep_header,
        [
            _record(r)
            for r in sorted(rows, key=lambda r: (r.condition, r.seed or 0, r.run_id))
        ],
    )
    return paths


# ----------------------------------------------------------------------------- fact sheet


def _relative(src: Path, base: Path) -> str:
    try:
        return Path(os.path.relpath(src.resolve(), base.resolve())).as_posix()
    except ValueError:  # different drives on Windows
        return src.resolve().as_posix()


def write_fact_sheet(
    path: Path | str, numbers: Mapping[str, tuple[Number, Path | str]]
) -> Path:
    """Write `{"numbers": [{name, value, source, sha256}, ...]}` (R-ANL-08)."""
    out = Path(path)
    items = []
    for name, (value, source) in numbers.items():
        if not (value is None or isinstance(value, (int, float, bool))):
            raise TypeError(f"fact {name!r} is not a number: {value!r}")
        src = Path(source)
        if not src.is_file():
            raise FileNotFoundError(f"fact {name!r}: source {src} does not exist")
        items.append(
            {
                "name": name,
                "value": value,
                "source": _relative(src, out.parent),
                "sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
            }
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"numbers": items}, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")  # fmt: skip
    return out


def _number(text: str) -> Number:
    if text == "":
        return None
    if text in ("True", "False"):
        return text == "True"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return None


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _facts_from_tables(
    tables: Mapping[str, Path], figures: Mapping[str, FigureOutput]
) -> dict[str, tuple[Number, Path]]:
    facts: dict[str, tuple[Number, Path]] = {}
    src = tables["roster"]
    for r in _read(src):
        for k, v in r.items():
            if k not in ("condition", "reruns_excluded"):
                facts[f"roster.{r['condition']}.{k}"] = (_number(v), src)
    src = tables["endpoints"]
    numeric = ("scheduled", "known_events", "known_zeros", "unknowns", "not_started",
               "evaluable", "rate", "ci_low", "ci_high", "bound_low", "bound_high")  # fmt: skip
    for r in _read(src):
        prefix = "endpoint" if r["population"] == "spontaneous" else "endpoint.control"
        for k in numeric:
            facts[f"{prefix}.{r['condition']}.{r['endpoint']}.{k}"] = (
                _number(r[k]),
                src,
            )
    src = tables["paired"]
    for r in _read(src):
        for k in ("n_pairs", "difference", "ci_low", "ci_high", "bound_low", "bound_high",
                  "n_bound_seeds"):  # fmt: skip
            facts[f"paired.{r['endpoint']}.{k}"] = (_number(r[k]), src)
    if "live" in figures:
        src = figures["live"].csv
        for r in _read(src):
            for k in ("rate", "n_evaluable", "n_scheduled"):
                facts[f"figure2.{r['condition']}.{r['metric']}.{k}"] = (
                    _number(r[k]),
                    src,
                )
    return facts


# ----------------------------------------------------------------------------- pipeline


def load_run(
    run_dir: Path | str,
    *,
    world_for: Callable[[int], World] | None = None,
    verifier: Callable[[Path], Sequence[str]] | None = None,
) -> tuple[tuple[Event, ...], EpisodeMetrics]:
    """Read `events.jsonl` from a run directory and evaluate it."""
    d = Path(run_dir)
    lines = (d / "events.jsonl").read_text(encoding="utf-8").splitlines()
    events = tuple(event_from_dict(json.loads(x)) for x in lines if x.strip())
    seed = None
    for e in events:
        m = e.payload.get("manifest") if e.kind.value == "RUN_START" else None
        if isinstance(m, Mapping):
            seed = m.get("seed")
            break
    if seed is None and (d / "manifest.json").is_file():
        seed = json.loads((d / "manifest.json").read_text(encoding="utf-8")).get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise TypeError(f"{d}: no integer seed in the recorded manifest")
    if world_for is None:
        from ethwall.world import build_world

        world_for = build_world
    faults = tuple(verifier(d)) if verifier is not None else ()
    return events, evaluate_run(events, world_for(seed), recorder_faults=faults)


def _default_roster(rows: Sequence[EpisodeMetrics]) -> list[RosterEntry] | None:
    if not rows:
        return None
    partition = rows[0].partition
    if partition not in SEEDS:
        return None
    control = partition.split("-")[0] == "control"
    return [
        RosterEntry(c.value, s, control) for c in Condition for s in SEEDS[partition]
    ]


def _pick_trajectories(
    loaded: Sequence[tuple[tuple[Event, ...], EpisodeMetrics]],
) -> tuple[tuple[Event, ...], tuple[Event, ...]] | None:
    ordered = sorted(loaded, key=lambda x: x[1].run_id)
    positive = next(
        (ev for ev, m in ordered
         if m.restricted_attempt == Tri.TRUE or m.first_block_seq is not None),
        None,
    )  # fmt: skip
    contrast = next(
        (ev for ev, m in ordered
         if m.restricted_attempt != Tri.TRUE and m.first_block_seq is None
         and m.articles_accepted > 0),
        None,
    )  # fmt: skip
    if positive is None or contrast is None:
        return None
    return positive, contrast


def analyze_partition(
    source: Sequence[EpisodeMetrics] | Sequence[Path | str],
    out_dir: Path | str,
    *,
    roster: Iterable[RosterLike] | None = None,
    audit_rows: Sequence[AuditMetricRow] = (),
    world_for: Callable[[int], World] | None = None,
    verifier: Callable[[Path], Sequence[str]] | None = None,
    trajectory: tuple[Sequence[Event], Sequence[Event]] | None = None,
    fixture_trajectory: bool | None = None,
    n_boot: int = 2000,
    rng_seed: int = 0,
) -> AnalysisResult:
    """Tables, figures and fact_sheet.json for one partition (R-ANL-08)."""
    loaded: list[tuple[tuple[Event, ...], EpisodeMetrics]] = []
    rows: list[EpisodeMetrics] = []
    for item in source:
        if isinstance(item, EpisodeMetrics):
            rows.append(item)
        else:
            ev, m = load_run(item, world_for=world_for, verifier=verifier)
            loaded.append((ev, m))
            rows.append(m)
    check_single_partition(rows)
    scheduled = list(roster) if roster is not None else _default_roster(rows)
    out = Path(out_dir)
    tables = write_tables(out / "tables", rows, scheduled=scheduled, n_boot=n_boot,
                          rng_seed=rng_seed)  # fmt: skip
    stamp = stamp_for_modes(r.mode for r in rows)
    figures: dict[str, FigureOutput] = {
        "live": figure_live(rows, out / "figures", scheduled=scheduled, stamp=stamp)
    }
    if audit_rows:
        figures["audit"] = figure_audit(audit_rows, out / "figures", stamp=stamp)
    pair = trajectory if trajectory is not None else _pick_trajectories(loaded)
    if pair is not None:
        is_fixture = (
            fixture_trajectory
            if fixture_trajectory is not None
            else any(r.mode == "fixture" for r in rows)
        )
        figures["trajectory"] = figure_trajectory(
            pair[0], pair[1], out / "figures", fixture=is_fixture, stamp=stamp
        )
    sheet = write_fact_sheet(
        out / "fact_sheet.json", _facts_from_tables(tables, figures)
    )
    return AnalysisResult(tuple(rows), tables, figures, sheet)
