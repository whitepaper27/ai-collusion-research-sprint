"""Report figures 2-4 from committed analysis tables and hash-verified audit fixtures.

Run from the repo root:  uv run --frozen python analysis/make_report_figures.py

Inputs (read only):
- analysis/main/tables/{endpoints,episodes,roster}.csv            -> fig2 (frozen main-v1)
- fixtures/audit/templates/tpl-d1.json, tpl-d5.json, rebuilt offline with
  ethwall.audit.fixtures.build_fixture and checked against the committed
  fixtures/audit/roster.json stream hashes                        -> fig3
- analysis/control-{lawful,incentive}/tables/episodes.csv          -> fig4 (exploratory)

Outputs: analysis/report-figures/*.png. No model calls; nothing under runs/ is touched.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import matplotlib

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "analysis" / "report-figures"
CONDS = ("baseline", "guarded")
COLORS = {"baseline": "#4C72B0", "guarded": "#DD8452"}
ENDPOINTS = (
    "restricted_attempt",
    "restricted_publication",
    "contradicted_publication",
    "reporting_complete",
    "whole_workflow_complete",
)


def _plt():
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# ----------------------------------------------------------------------------- fig 2


def fig2() -> Path:
    plt = _plt()
    tables = ROOT / "analysis" / "main" / "tables"
    ep = {
        (r["condition"], r["endpoint"]): r for r in read_csv(tables / "endpoints.csv")
    }
    episodes = read_csv(tables / "episodes.csv")
    roster = {r["condition"]: r for r in read_csv(tables / "roster.csv")}
    seeds = sorted({int(r["seed"]) for r in episodes if r["condition"] == "baseline"})
    for c in CONDS:
        cs = sorted(int(r["seed"]) for r in episodes if r["condition"] == c)
        assert cs == seeds, f"unmatched seeds for {c}"
    assert all(r["partition"] == "main" and r["mode"] == "live" for r in episodes)

    panels = [
        (
            "Restricted publication endpoints",
            ["restricted_attempt", "restricted_publication"],
            ["restricted\nattempt", "restricted\npublication"],
        ),
        (
            "Contradicted publication (separate)",
            ["contradicted_publication"],
            ["contradicted\npublication"],
        ),
        (
            "Legitimate completion",
            ["reporting_complete", "whole_workflow_complete"],
            ["reporting\ncomplete", "whole workflow\ncomplete"],
        ),
    ]
    fig, axes = plt.subplots(
        1, 4, figsize=(15, 4.8), gridspec_kw={"width_ratios": [2, 1, 2, 2]}
    )
    off = {"baseline": -0.12, "guarded": 0.12}
    for ax, (title, endpoints, labels) in zip(axes[:3], panels):
        for c in CONDS:
            for i, e in enumerate(endpoints):
                r = ep[(c, e)]
                rate, lo, hi = float(r["rate"]), float(r["ci_low"]), float(r["ci_high"])
                x = i + off[c]
                ax.errorbar(
                    x,
                    rate,
                    yerr=[[rate - lo], [hi - rate]],
                    fmt="o",
                    color=COLORS[c],
                    capsize=4,
                    ms=7,
                    label=c if i == 0 else None,
                )
                ax.annotate(
                    f"{r['known_events']}/{r['evaluable']}",
                    (x, hi),
                    xytext=(0, 4),
                    textcoords="offset points",
                    ha="center",
                    fontsize=8,
                )
        ax.set_xticks(range(len(endpoints)), labels, fontsize=9)
        ax.set_xlim(-0.6, len(endpoints) - 0.4)
        ax.set_ylim(-0.03, 1.08)
        ax.set_title(title, fontsize=10)
        ax.grid(axis="y", alpha=0.3)
    axes[0].set_ylabel("Episode rate (Wilson 95% CI)")
    axes[0].legend(loc="upper left", fontsize=8)

    ax = axes[3]
    metrics = [
        ("reporting_score", 10.0, "reporting\nquality"),
        ("trading_quality", 3.0, "trading\nquality"),
    ]
    for i, (col, scale, _) in enumerate(metrics):
        for c in CONDS:
            vals = [float(r[col]) / scale for r in episodes if r["condition"] == c]
            x = i + off[c]
            xs = [x + (k % 5 - 2) * 0.018 for k in range(len(vals))]
            ax.scatter(xs, vals, s=12, color=COLORS[c], alpha=0.45)
            m = sum(vals) / len(vals)
            ax.hlines(m, x - 0.08, x + 0.08, color=COLORS[c], lw=2.5)
            ax.annotate(
                f"{m:.2f}",
                (x, 1.0),
                xytext=(0, 6),
                textcoords="offset points",
                ha="center",
                fontsize=8,
            )
    ax.set_xticks(range(2), [m[2] for m in metrics], fontsize=9)
    ax.set_xlim(-0.6, 1.6)
    ax.set_ylim(-0.03, 1.08)
    ax.set_ylabel("Normalised score (dots = episodes, bar = mean)")
    ax.set_title("Legitimate-work quality", fontsize=10)
    ax.grid(axis="y", alpha=0.3)

    miss = "; ".join(
        f"{c}: scheduled {roster[c]['scheduled']}, started {roster[c]['started']}, "
        f"fully observed {roster[c]['fully_observed']}, interrupted {roster[c]['interrupted']}, "
        f"not started {roster[c]['not_started']}, "
        f"unknown endpoints {max(int(ep[(c, e)]['unknowns']) for e in ENDPOINTS)}"
        for c in CONDS
    )
    fig.suptitle(
        f"Baseline vs guarded, frozen main-v1, {len(episodes)} episodes "
        f"(matched seeds {seeds[0]}-{seeds[-1]}, n={len(seeds)} per condition)",
        fontsize=12,
    )
    fig.text(
        0.5,
        0.01,
        miss + ".  Quality: reporting score/10, trading quality/3.",
        ha="center",
        fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.04, 1, 0.94))
    path = OUT / "fig2_live_endpoints.png"
    fig.savefig(path, dpi=200)
    plt.close(fig)
    return path


# ----------------------------------------------------------------------------- fig 3

ROLES = ("MARKET", "LEGAL", "HR", "TRADING", "COMMS")
RESTRICTED = "#C44E52"
PUBLIC = "#55A868"


def _fixture_events(fid: str):
    sys.path.insert(0, str(ROOT / "src"))
    from ethwall.audit.fixtures import build_fixture, load_roster, stream_sha256
    from ethwall.testing import MemoryRecorder

    events = build_fixture(fid, MemoryRecorder(fid))
    want = load_roster()["streams"][fid]
    got = stream_sha256(events)
    if got != want:
        raise SystemExit(f"{fid}: stream hash {got} != committed roster {want}")
    return events


def _restricted(labels) -> bool:
    return any(lab != "PUBLIC" for lab in labels)


def _draw_trajectory(ax, fid: str, title: str) -> dict:
    events = _fixture_events(fid)
    lane = {r: i for i, r in enumerate(ROLES)}
    sub_labels: dict = {}
    msg_sub: dict = {}
    counts: dict = {"delivered": 0, "blocked": 0, "committed": 0, "first_block": None}
    for e in events:
        k, p, ctx = e.kind.value, e.payload, e.context_id
        if k == "ORDER_SUBMITTED":
            sub_labels[p["order_id"]] = e.labels
        elif k == "ARTICLE_SUBMITTED":
            sub_labels[p["article_id"]] = e.labels
        if (
            k in ("PACKET_DELIVERED", "SOURCE_READ", "RETRIEVAL_RESOLVED")
            and ctx in lane
        ):
            col = RESTRICTED if _restricted(e.labels) else PUBLIC
            ax.scatter(
                e.seq,
                lane[ctx],
                marker="s",
                s=60,
                color=col,
                edgecolor="k",
                lw=0.5,
                zorder=3,
            )
        elif k == "MESSAGE_SUBMITTED":
            msg_sub[p["message_id"]] = (e.seq, p["sender"], e.labels)
        elif k == "MESSAGE_DELIVERED" and p["message_id"] in msg_sub:
            s_seq, sender, labels = msg_sub[p["message_id"]]
            col = RESTRICTED if _restricted(labels) else PUBLIC
            ax.annotate(
                "",
                xy=(e.seq, lane[p["recipient"]]),
                xytext=(s_seq, lane[sender]),
                arrowprops={"arrowstyle": "->", "color": col, "lw": 1.8},
                zorder=2,
            )
            counts["delivered"] += 1
        elif (
            k == "DECISION" and p.get("verdict") not in (None, "allow") and ctx in lane
        ):
            counts["blocked"] += 1
            if counts["first_block"] is None:
                counts["first_block"] = e.seq
                ax.scatter(e.seq, lane[ctx], marker="X", s=140, color="k", zorder=4)
                ax.annotate(
                    f"first block ({p['boundary']})",
                    (e.seq, lane[ctx]),
                    xytext=(4, 8),
                    textcoords="offset points",
                    fontsize=7,
                )
        elif k in ("ORDER_FILLED", "ARTICLE_ACCEPTED") and ctx in lane:
            ref = p.get("order_id") or p.get("article_id")
            col = RESTRICTED if _restricted(sub_labels.get(ref, ())) else PUBLIC
            name = (
                f"order filled (dq {p.get('dq')})"
                if k == "ORDER_FILLED"
                else "article published"
            )
            ax.scatter(
                e.seq,
                lane[ctx],
                marker="*",
                s=260,
                color=col,
                edgecolor="k",
                lw=0.6,
                zorder=4,
            )
            ax.annotate(
                name,
                (e.seq, lane[ctx]),
                xytext=(6, -12),
                textcoords="offset points",
                fontsize=7,
            )
            counts["committed"] += 1
    if counts["first_block"] is None:
        ax.text(
            0.99,
            0.95,
            "first block: none",
            transform=ax.transAxes,
            ha="right",
            va="top",
            fontsize=8,
        )
    ax.set_yticks(range(len(ROLES)), ROLES)
    ax.set_ylim(-0.6, len(ROLES) - 0.4)
    ax.invert_yaxis()
    ax.set_title(title, fontsize=10)
    ax.grid(axis="x", alpha=0.25)
    return counts


def fig3() -> tuple[Path, dict, dict]:
    plt = _plt()
    from matplotlib.lines import Line2D

    fig, axes = plt.subplots(2, 1, figsize=(12, 7.5), sharex=True)
    c5 = _draw_trajectory(
        axes[0],
        "audit-d05",
        "audit-d05, baseline (construction label: completed_coordination)",
    )
    c1 = _draw_trajectory(
        axes[1],
        "audit-d01",
        "audit-d01, baseline (construction label: legitimate_work)",
    )
    axes[1].set_xlabel("Recorded event sequence number")
    handles = [
        Line2D(
            [],
            [],
            marker="s",
            ls="",
            color=RESTRICTED,
            mec="k",
            label="source access (owner label, e.g. HR/LEGAL)",
        ),
        Line2D(
            [],
            [],
            marker="s",
            ls="",
            color=PUBLIC,
            mec="k",
            label="source access (PUBLIC label only)",
        ),
        Line2D(
            [],
            [],
            color=RESTRICTED,
            label="message delivered (sender context has owner label)",
        ),
        Line2D(
            [], [], color=PUBLIC, label="message delivered (sender context PUBLIC only)"
        ),
        Line2D(
            [],
            [],
            marker="*",
            ls="",
            ms=12,
            color="grey",
            mec="k",
            label="committed action (colour = submitting context labels)",
        ),
        Line2D([], [], marker="X", ls="", ms=10, color="k", label="first block"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=3, fontsize=8, frameon=False)
    fig.suptitle(
        "Scripted audit fixtures (not model behavior): positive trajectory vs legitimate contrast",
        fontsize=11,
    )
    fig.tight_layout(rect=(0, 0.07, 1, 0.95))
    path = OUT / "fig3_trajectory_fixture.png"
    fig.savefig(path, dpi=200)
    plt.close(fig)
    return path, c5, c1


# ----------------------------------------------------------------------------- fig 4


def fig4() -> Path:
    plt = _plt()
    from matplotlib.patches import Patch

    rows = []
    for part in ("control-lawful", "control-incentive"):
        for r in read_csv(ROOT / "analysis" / part / "tables" / "episodes.csv"):
            rows.append((part, r))
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ticks = []
    for i, (part, r) in enumerate(rows):
        c = r["condition"]
        dlv, blk = int(r["messages_delivered"]), int(r["messages_blocked"])
        ax.bar(i - 0.18, dlv, 0.36, color=COLORS[c], edgecolor="k", lw=0.5)
        ax.bar(i + 0.18, blk, 0.36, color="#999999", edgecolor="k", lw=0.5)
        ax.text(i - 0.18, dlv + 0.1, str(dlv), ha="center", fontsize=8)
        ax.text(i + 0.18, blk + 0.1, str(blk), ha="center", fontsize=8)
        ax.text(
            i,
            -1.2,
            f"handoff complete:\n{r['handoff_complete']}",
            ha="center",
            fontsize=7,
        )
        ticks.append(f"{part}\n{c} s{r['seed']}")
    ax.set_xticks(range(len(rows)), ticks, fontsize=8)
    ax.set_ylim(-1.9, max(int(r["messages_delivered"]) for _, r in rows) + 1.5)
    ax.axhline(0, color="k", lw=0.6)
    ax.set_ylabel("Messages per episode")
    ax.legend(
        handles=[
            Patch(color=COLORS["baseline"], label="delivered (baseline)"),
            Patch(color=COLORS["guarded"], label="delivered (guarded)"),
            Patch(color="#999999", label="blocked"),
        ],
        fontsize=8,
        loc="upper right",
    )
    ax.set_title(
        f"Incentive-arm control episodes (exploratory pilot, not pooled; n={len(rows)} episodes)",
        fontsize=10,
    )
    fig.tight_layout()
    path = OUT / "fig4_incentive_pilot.png"
    fig.savefig(path, dpi=200)
    plt.close(fig)
    return path


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    p2 = fig2()
    p3, c5, c1 = fig3()
    p4 = fig4()
    print(p2)
    print(p3, "d05:", c5, "d01:", c1)
    print(p4)


if __name__ == "__main__":
    main()
