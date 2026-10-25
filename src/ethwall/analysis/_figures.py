"""Figures 1-3 (sol.md §11), each written as PNG plus a CSV of exactly the plotted data.

matplotlib runs on the non-interactive "Agg" backend, selected lazily so importing this
package never touches a display. Titles carry a `MOCK` / `FIXTURE` stamp when the data are
not live (derived from the rows' or events' recorded mode unless `stamp` is given).

- Figure 2 (`figure_live`, R-ANL-05): baseline vs guarded attempted and completed restricted
  publication and contradicted publication (reported separately), next to legitimate
  completion and quality; Wilson 95% intervals, `k/evaluable`, unknowns, scheduled counts and
  matched seeds (evaluable in both conditions) annotated. Controls are excluded.
- Figure 1 (`figure_audit`, R-ANL-06): audit errors, false accusations and abstentions across
  the four views; fixture and live in separate panels; combined and linked highlighted. A
  missing live panel is drawn as "absent", never silently dropped.
- Figure 3 (`figure_trajectory`, R-ANL-07): one run's source access, deliveries, committed
  actions and first block beside a legitimate contrast run; labelled `FIXTURE` when the runs
  are illustrative fixtures.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ethwall.analysis._stats import (
    RosterLike,
    check_single_partition,
    endpoint_summary,
    paired_seed_comparison,
    quality_summary,
    wilson,
)
from ethwall.contract import Event, EventKind, View, event_from_dict
from ethwall.evaluate import EpisodeMetrics

STAMPS = {"mock": "MOCK", "fixture": "FIXTURE"}
AUDIT_MEASURES = ("errors", "false_accusations", "abstentions")
AUDIT_SOURCES = ("fixture", "live")
HIGHLIGHT_VIEWS = (View.COMBINED.value, View.LINKED.value)
LANES = ("source_access", "delivery", "committed_action", "block")
_LANE_OF = {
    EventKind.SOURCE_READ: ("source_access", "doc_id"),
    EventKind.RETRIEVAL_RESOLVED: ("source_access", "request_id"),
    EventKind.PACKET_DELIVERED: ("delivery", "role"),
    EventKind.MESSAGE_DELIVERED: ("delivery", "message_id"),
    EventKind.ORDER_FILLED: ("committed_action", "order_id"),
    EventKind.ARTICLE_ACCEPTED: ("committed_action", "article_id"),
    EventKind.DUTY_REPORT_SUBMITTED: ("committed_action", "report_id"),
}
LIVE_BINARY = (
    "restricted_attempt",
    "restricted_publication",
    "contradicted_publication",
    "whole_workflow_complete",
)


@dataclass(frozen=True)
class FigureOutput:
    png: Path
    csv: Path
    title: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuditMetricRow:
    """Audit metrics for one view in one panel (from the audit metrics module)."""

    view: str
    source: str  # "fixture" | "live"
    n: int
    errors: int
    false_accusations: int
    abstentions: int

    def __post_init__(self) -> None:
        if self.view not in {v.value for v in View}:
            raise ValueError(f"unknown view {self.view!r}")
        if self.source not in AUDIT_SOURCES:
            raise ValueError(f"source must be one of {AUDIT_SOURCES}")
        counts = (self.n, self.errors, self.false_accusations, self.abstentions)
        if any(c < 0 for c in counts):
            raise ValueError("audit counts must be non-negative")


def _plt() -> Any:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def _title(base: str, stamp: str) -> str:
    return f"{base} [{stamp}]" if stamp else base


def _write_csv(
    path: Path, header: Sequence[str], rows: Iterable[Mapping[str, Any]]
) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(header))
        w.writeheader()
        for r in rows:
            w.writerow({k: "" if r.get(k) is None else r.get(k) for k in header})


def stamp_for_modes(modes: Iterable[str]) -> str:
    found = sorted({STAMPS.get(m, "") for m in modes} - {""})
    return "+".join(found)


# ----------------------------------------------------------------------------- Figure 2


LIVE_HEADER = (
    "condition",
    "metric",
    "kind",
    "k",
    "n_evaluable",
    "n_scheduled",
    "unknown",
    "rate",
    "ci_low",
    "ci_high",
    "bound_low",
    "bound_high",
    "matched_seeds",
)


def live_figure_data(
    rows: Sequence[EpisodeMetrics], scheduled: Iterable[RosterLike] | None = None
) -> list[dict[str, Any]]:
    """The rows plotted in Figure 2 (spontaneous episodes only)."""
    sched = list(scheduled) if scheduled is not None else None
    data: list[dict[str, Any]] = []
    for metric in LIVE_BINARY:
        summ = endpoint_summary(rows, metric, sched)
        matched = paired_seed_comparison(
            rows, metric, n_boot=0, scheduled=sched
        ).n_pairs
        for cond, s in summ.items():
            data.append(
                {
                    "condition": cond,
                    "metric": metric,
                    "kind": "rate",
                    "k": s.known_events,
                    "n_evaluable": s.evaluable,
                    "n_scheduled": s.scheduled,
                    "unknown": s.unknowns,
                    "rate": s.rate,
                    "ci_low": s.ci_low,
                    "ci_high": s.ci_high,
                    "bound_low": s.bound_low,
                    "bound_high": s.bound_high,
                    "matched_seeds": matched,
                }
            )
    sched_counts = {
        c: s.scheduled for c, s in endpoint_summary(rows, LIVE_BINARY[0], sched).items()
    }
    for field, maximum, metric in (
        ("reporting_score", 10.0, "reporting_quality"),
        ("trading_quality", 3.0, "trading_quality"),
    ):
        for delivered, suffix in ((False, ""), (True, "_delivered")):
            q = quality_summary(rows, field, maximum, delivered_only=delivered)
            for cond, (n, mean) in q.items():
                data.append(
                    {
                        "condition": cond,
                        "metric": metric + suffix,
                        "kind": "mean",
                        "k": None,
                        "n_evaluable": n,
                        "n_scheduled": sched_counts.get(cond, 0),
                        "unknown": sched_counts.get(cond, 0) - n
                        if not delivered
                        else None,
                        "rate": mean,
                        "ci_low": None,
                        "ci_high": None,
                        "bound_low": None,
                        "bound_high": None,
                        "matched_seeds": None,
                    }
                )
    return data


def figure_live(
    rows: Sequence[EpisodeMetrics],
    out_dir: Path | str,
    *,
    scheduled: Iterable[RosterLike] | None = None,
    stamp: str | None = None,
    name: str = "figure2_live",
) -> FigureOutput:
    """Figure 2 (R-ANL-05): PNG + CSV in `out_dir`."""
    check_single_partition(rows)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = stamp_for_modes(r.mode for r in rows) if stamp is None else stamp
    data = live_figure_data(rows, scheduled)
    csv_path = out / f"{name}.csv"
    _write_csv(csv_path, LIVE_HEADER, data)
    matched = next(
        (d["matched_seeds"] for d in data if d["metric"] == "restricted_publication"), 0
    )
    title = _title(f"Figure 2: baseline vs guarded (matched seeds: {matched})", stamp)

    plt = _plt()
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    panels = (
        ("Restricted / contradicted publication", LIVE_BINARY[:3]),
        (
            "Legitimate work",
            ("whole_workflow_complete", "reporting_quality", "trading_quality"),
        ),
    )
    conditions = sorted(
        {d["condition"] for d in data}, key=lambda c: (c != "baseline", c)
    )
    width = 0.8 / max(1, len(conditions))
    for ax, (panel_title, metrics) in zip(axes, panels):
        for ci, cond in enumerate(conditions):
            xs, ys, errs, labels = [], [], [[], []], []
            for mi, metric in enumerate(metrics):
                d = next(
                    (
                        x
                        for x in data
                        if x["condition"] == cond and x["metric"] == metric
                    ),
                    None,
                )
                if d is None:
                    continue
                y = d["rate"] if d["rate"] is not None else 0.0
                xs.append(mi + ci * width)
                ys.append(y)
                if d["kind"] == "rate":
                    errs[0].append(max(0.0, y - d["ci_low"]))
                    errs[1].append(max(0.0, d["ci_high"] - y))
                    labels.append(
                        f"{d['k']}/{d['n_evaluable']}\nunk {d['unknown']}/{d['n_scheduled']}"
                    )
                else:
                    errs[0].append(0.0)
                    errs[1].append(0.0)
                    rate = "n/a" if d["rate"] is None else f"{d['rate']:.2f}"
                    labels.append(f"{rate}\nn={d['n_evaluable']}/{d['n_scheduled']}")
            bars = ax.bar(xs, ys, width=width, yerr=errs, capsize=3, label=cond)
            for bar, text in zip(bars, labels):
                ax.annotate(
                    text,
                    (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                    xytext=(0, 3),
                    textcoords="offset points",
                    ha="center",
                    va="bottom",
                    fontsize=7,
                )
        ax.set_xticks(
            [i + width * (len(conditions) - 1) / 2 for i in range(len(metrics))]
        )
        ax.set_xticklabels([m.replace("_", "\n") for m in metrics], fontsize=8)
        ax.set_ylim(0, 1.25)
        ax.set_ylabel("rate (Wilson 95%) / mean score fraction")
        ax.set_title(panel_title, fontsize=10)
        ax.legend(fontsize=8)
    fig.suptitle(title)
    fig.text(
        0.01,
        0.01,
        "k/evaluable; unk = unknown (incl. not started) of scheduled; controls excluded",
        fontsize=7,
    )
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    png = out / f"{name}.png"
    fig.savefig(png, dpi=120)
    plt.close(fig)
    return FigureOutput(png, csv_path, title)


# ----------------------------------------------------------------------------- Figure 1


AUDIT_HEADER = (
    "panel",
    "view",
    "measure",
    "count",
    "n",
    "rate",
    "ci_low",
    "ci_high",
    "highlight",
)


def figure_audit(
    rows: Sequence[AuditMetricRow],
    out_dir: Path | str,
    *,
    stamp: str | None = None,
    name: str = "figure1_audit",
) -> FigureOutput:
    """Figure 1 (R-ANL-06): PNG + CSV in `out_dir`."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = stamp or ""
    views = [v.value for v in View]
    data: list[dict[str, Any]] = []
    notes: list[str] = []
    for source in AUDIT_SOURCES:
        panel_rows = [r for r in rows if r.source == source]
        if not panel_rows:
            notes.append(f"{source}: absent")
            continue
        for r in sorted(panel_rows, key=lambda x: views.index(x.view)):
            for measure in AUDIT_MEASURES:
                count = int(getattr(r, measure))
                lo, hi = wilson(count, r.n)
                data.append(
                    {
                        "panel": source,
                        "view": r.view,
                        "measure": measure,
                        "count": count,
                        "n": r.n,
                        "rate": count / r.n if r.n else None,
                        "ci_low": lo,
                        "ci_high": hi,
                        "highlight": r.view in HIGHLIGHT_VIEWS,
                    }
                )
    csv_path = out / f"{name}.csv"
    _write_csv(csv_path, AUDIT_HEADER, data)
    title = _title(
        "Figure 1: audit errors, false accusations, abstentions by view (combined vs linked)",
        stamp,
    )

    plt = _plt()
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), sharey=True)
    width = 0.8 / len(AUDIT_MEASURES)
    for ax, source in zip(axes, AUDIT_SOURCES):
        panel = [d for d in data if d["panel"] == source]
        ax.set_title(f"{source} trajectories", fontsize=10)
        if not panel:
            ax.text(0.5, 0.5, f"{source} trajectories absent", ha="center", va="center",
                    transform=ax.transAxes)  # fmt: skip
            ax.set_xticks([])
            continue
        for vi, view in enumerate(views):
            if view in HIGHLIGHT_VIEWS:
                ax.axvspan(vi - 0.1, vi + 0.9, color="#fff2cc", zorder=0)
        for mi, measure in enumerate(AUDIT_MEASURES):
            xs, ys, err_lo, err_hi, edge = [], [], [], [], []
            for vi, view in enumerate(views):
                d = next((x for x in panel if x["view"] == view and x["measure"] == measure),
                         None)  # fmt: skip
                if d is None:
                    continue
                y = d["rate"] or 0.0
                xs.append(vi + mi * width)
                ys.append(y)
                err_lo.append(max(0.0, y - d["ci_low"]))
                err_hi.append(max(0.0, d["ci_high"] - y))
                edge.append(2.0 if d["highlight"] else 0.5)
            ax.bar(xs, ys, width=width, yerr=[err_lo, err_hi], capsize=2, label=measure,
                   edgecolor="black", linewidth=edge, zorder=2)  # fmt: skip
        ax.set_xticks([i + width for i in range(len(views))])
        ax.set_xticklabels(views)
        ax.set_ylim(0, 1.05)
        ax.legend(fontsize=8)
    axes[0].set_ylabel("rate over trajectories (Wilson 95%)")
    fig.suptitle(title, fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    png = out / f"{name}.png"
    fig.savefig(png, dpi=120)
    plt.close(fig)
    return FigureOutput(png, csv_path, title, tuple(notes))


# ----------------------------------------------------------------------------- Figure 3


TRAJ_HEADER = ("panel", "lane", "seq", "kind", "actor", "ref", "first_block")


def _as_events(events: Iterable[Event | Mapping[str, Any]]) -> list[Event]:
    out = [e if isinstance(e, Event) else event_from_dict(e) for e in events]
    return sorted(out, key=lambda e: e.seq)


def trajectory_points(panel: str, events: Sequence[Event]) -> list[dict[str, Any]]:
    """Plotted points of one run: lane, position and referenced id."""
    points: list[dict[str, Any]] = []
    first_block_seen = False
    for e in events:
        if e.kind == EventKind.DECISION:
            if e.payload.get("verdict") != "block":
                continue
            lane, ref = "block", e.payload.get("ref_id")
            first = not first_block_seen
            first_block_seen = True
        elif e.kind in _LANE_OF:
            lane, key = _LANE_OF[e.kind]
            ref, first = e.payload.get(key), False
        else:
            continue
        points.append(
            {
                "panel": panel,
                "lane": lane,
                "seq": e.seq,
                "kind": e.kind.value,
                "actor": e.actor,
                "ref": ref,
                "first_block": first,
            }
        )
    return points


def _event_modes(events: Sequence[Event]) -> list[str]:
    for e in events:
        if e.kind == EventKind.RUN_START:
            m = e.payload.get("manifest")
            if isinstance(m, Mapping):
                return [str(m.get("mode", ""))]
    return []


def figure_trajectory(
    events: Iterable[Event | Mapping[str, Any]],
    contrast: Iterable[Event | Mapping[str, Any]],
    out_dir: Path | str,
    *,
    fixture: bool = False,
    stamp: str | None = None,
    labels: tuple[str, str] = ("source-linked trajectory", "legitimate contrast"),
    name: str = "figure3_trajectory",
) -> FigureOutput:
    """Figure 3 (R-ANL-07): PNG + CSV in `out_dir`."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    runs = (("trajectory", _as_events(events)), ("contrast", _as_events(contrast)))
    if stamp is None:
        stamp = stamp_for_modes(m for _, ev in runs for m in _event_modes(ev))
    parts = [p for p in stamp.split("+") if p]
    if fixture and "FIXTURE" not in parts:
        parts.insert(0, "FIXTURE")
    stamp = "+".join(parts)
    data = [p for panel, ev in runs for p in trajectory_points(panel, ev)]
    csv_path = out / f"{name}.csv"
    _write_csv(csv_path, TRAJ_HEADER, data)
    title = _title(
        "Figure 3: source access, delivery, committed actions, first block", stamp
    )

    plt = _plt()
    fig, axes = plt.subplots(1, 2, figsize=(13, 4), sharey=True)
    lanes = list(LANES)
    colors = {"source_access": "#1f77b4", "delivery": "#2ca02c",
              "committed_action": "#9467bd", "block": "#d62728"}  # fmt: skip
    for ax, (panel, _), label in zip(axes, runs, labels):
        pts = [p for p in data if p["panel"] == panel]
        for lane in lanes:
            lp = [p for p in pts if p["lane"] == lane]
            ax.scatter([p["seq"] for p in lp], [lanes.index(lane)] * len(lp),
                       color=colors[lane], s=40, zorder=3)  # fmt: skip
            for p in lp:
                text = f"{p['actor']}:{p['ref']}"
                if p["first_block"]:
                    text = "FIRST BLOCK " + text
                    ax.axvline(
                        p["seq"], color=colors["block"], linestyle="--", zorder=1
                    )
                ax.annotate(text, (p["seq"], lanes.index(lane)), xytext=(2, 6),
                            textcoords="offset points", fontsize=6, rotation=20)  # fmt: skip
        ax.set_yticks(range(len(lanes)))
        ax.set_yticklabels([lane.replace("_", " ") for lane in lanes])
        ax.set_ylim(-0.5, len(lanes) - 0.3)
        ax.set_xlabel("event sequence")
        ax.set_title(label + (" (no events)" if not pts else ""), fontsize=10)
    fig.suptitle(title, fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    png = out / f"{name}.png"
    fig.savefig(png, dpi=120)
    plt.close(fig)
    return FigureOutput(png, csv_path, title)
