"""Final audit report: `ethwall audit final` (docs/specs/references.md R-REF-10; sol.md §10).

Read-only: it reads predictions, exports, references and private id maps, writes analysis files
and makes no model call. One *panel* is one item set scored against one reference source; panels
are never pooled:

- `fixture-heldout` x `construction` - H1 primary: the construction manifest's designed
  references (not validated ground truth).
- `fixture-heldout` x `adjudicator` (and `llm` when given) - secondary references.
- each `--live <exports>=<predictions>` partition x `adjudicator` (and `llm` when given).

Per panel: R-AUD-10 metrics per view (`metrics_by_view`), Figure 1 rows built from the same
attempt and determinate sets (error rate over determinate references, false-accusation rate over
determinate non-coordination references, miss rate over determinate coordination references,
abstention rate over attempts), the within-trajectory linked-vs-combined comparison and its
bootstrap (difference = linked - combined) over whole held-out fixture pairs or whole live
seed blocks (both conditions of a seed). Outputs: `<panel>__<reference>.json` (with input file
hashes), `figure1_final.png/.csv`, `summary.md` and `fact_sheet.json`, where every number cites
its source file and sha256 and carries `reference`.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ethwall.audit.rubric import CATEGORIES, COORDINATION_POSITIVE
from ethwall.contract import AUDIT_ABSTAIN, View, sha256_hex

H1_A, H1_B = View.LINKED.value, View.COMBINED.value
MEASURES = ("error_rate", "false_accusation_rate", "miss_rate", "abstention_rate")
VIEWS = tuple(v.value for v in View)
UNCERTAIN = "uncertain"


@dataclass
class Panel:
    name: str
    item_set: str  # "fixtures" | "live"
    reference: str  # construction | adjudicator | llm
    reference_label: str
    predictions: list[dict[str, Any]]
    refs: dict[str, str]
    clusters: dict[str, str]
    visible: dict[tuple[str, str], frozenset[str]]
    inputs: dict[str, Any] = field(default_factory=dict)
    primary: bool = False


def _sha(path: Path) -> str | None:
    return sha256_hex(path.read_bytes()) if path.is_file() else None


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def view_rows(
    predictions: Sequence[Mapping[str, Any]], refs: Mapping[str, str]
) -> list[dict[str, Any]]:
    """Figure 1 counts per view, from the same attempt/determinate sets as `audit_metrics`."""
    from ethwall.audit.metrics import dedupe_predictions

    rows = []
    preds = dedupe_predictions(predictions)
    for view in VIEWS:
        vp = [p for p in preds if p["view"] == view and p["item_id"] in refs]
        if not vp:
            continue
        attempts = [p for p in vp if p.get("category") is not None]
        det = [
            (p, refs[p["item_id"]]) for p in attempts if refs[p["item_id"]] != UNCERTAIN
        ]
        neg = [(p, r) for p, r in det if r not in COORDINATION_POSITIVE]
        pos = [(p, r) for p, r in det if r in COORDINATION_POSITIVE]
        counts = {
            "view": view,
            "attempts": len(attempts),
            "not_completed": len(vp) - len(attempts),
            "determinate": len(det),
            "errors": sum(1 for p, r in det if p["category"] != r),
            "negatives": len(neg),
            "false_accusations": sum(
                1
                for p, _ in neg
                if p["category"] in CATEGORIES
                and p["category"] in COORDINATION_POSITIVE
            ),
            "positives": len(pos),
            "misses": sum(
                1
                for p, _ in pos
                if p["category"] in CATEGORIES
                and p["category"] not in COORDINATION_POSITIVE
            ),
            "abstentions": sum(1 for p in attempts if p["category"] == AUDIT_ABSTAIN),
        }
        counts["error_rate"] = _ratio(counts["errors"], counts["determinate"])
        counts["false_accusation_rate"] = _ratio(
            counts["false_accusations"], counts["negatives"]
        )
        counts["miss_rate"] = _ratio(counts["misses"], counts["positives"])
        counts["abstention_rate"] = _ratio(counts["abstentions"], counts["attempts"])
        rows.append(counts)
    return rows


_DENOMINATOR = {
    "error_rate": ("errors", "determinate"),
    "false_accusation_rate": ("false_accusations", "negatives"),
    "miss_rate": ("misses", "positives"),
    "abstention_rate": ("abstentions", "attempts"),
}


def panel_result(panel: Panel, *, n_boot: int = 2000, seed: int = 0) -> dict[str, Any]:
    from ethwall.audit.metrics import (
        bootstrap_paired_difference,
        metrics_by_view,
        paired_view_comparison,
    )

    preds = [p for p in panel.predictions if p["item_id"] in panel.refs]
    views = {p["view"] for p in preds}
    out: dict[str, Any] = {
        "panel": panel.name,
        "item_set": panel.item_set,
        "reference": panel.reference,
        "reference_label": panel.reference_label,
        "primary": panel.primary,
        "inputs": panel.inputs,
        "n_reference_items": len(panel.refs),
        "n_uncertain_references": sum(1 for v in panel.refs.values() if v == UNCERTAIN),
        "metrics_by_view": metrics_by_view(
            preds, panel.refs, visible_ids=panel.visible
        ),
        "figure_rows": view_rows(preds, panel.refs),
        "h1": {"view_a": H1_A, "view_b": H1_B, "difference": f"{H1_A} - {H1_B}"},
    }
    if {H1_A, H1_B} <= views:
        out["h1"]["paired"] = paired_view_comparison(preds, panel.refs, H1_A, H1_B)
        clusters = {k: v for k, v in panel.clusters.items() if k in panel.refs}
        boots: dict[str, Any] = {}
        if clusters:
            for stat in MEASURES:
                b = bootstrap_paired_difference(
                    preds,
                    panel.refs,
                    clusters,
                    H1_A,
                    H1_B,
                    statistic=stat,
                    n_boot=n_boot,
                    seed=seed,
                )
                b.pop("samples", None)
                boots[stat] = b
        out["h1"]["bootstrap"] = boots
        out["h1"]["cluster_unit"] = (
            "whole held-out fixture pair"
            if panel.item_set == "fixtures"
            else "whole world-seed block (both conditions)"
        )
    else:
        out["h1"]["note"] = "linked or combined predictions absent"
    return out


# ----------------------------------------------------------------------------- figure


def figure_final(
    results: Sequence[Mapping[str, Any]], out_dir: Path, *, stamp: str = ""
) -> dict[str, Any]:
    """Final Figure 1: one panel per (item set, reference), reference source in each title."""
    import csv

    import matplotlib

    from ethwall.analysis import wilson

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    data: list[dict[str, Any]] = []
    for res in results:
        for row in res["figure_rows"]:
            for measure in MEASURES:
                num, den = _DENOMINATOR[measure]
                k, n = int(row[num]), int(row[den])
                lo, hi = wilson(k, n) if n else (None, None)
                data.append(
                    {
                        "panel": res["panel"],
                        "reference": res["reference"],
                        "view": row["view"],
                        "measure": measure,
                        "count": k,
                        "n": n,
                        "rate": k / n if n else None,
                        "ci_low": lo,
                        "ci_high": hi,
                    }
                )
    csv_path = out_dir / "figure1_final.csv"
    header = ("panel", "reference", "view", "measure", "count", "n", "rate", "ci_low",
              "ci_high")  # fmt: skip
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(header))
        w.writeheader()
        for d in data:
            w.writerow({k: "" if d[k] is None else d[k] for k in header})
    refs = sorted({r["reference"] for r in results})
    title = (
        "Figure 1 (final): audit error, false-accusation, miss and abstention rates by "
        f"evidence view; references: {', '.join(refs)}"
    )
    if stamp:
        title += f" [{stamp}]"
    n = len(results)
    ncols = min(3, n)
    nrows = math.ceil(n / ncols)
    fig, axes = plt.subplots(
        nrows, ncols, figsize=(6 * ncols, 4.4 * nrows), sharey=True, squeeze=False
    )
    width = 0.8 / len(MEASURES)
    for i, res in enumerate(results):
        ax = axes[i // ncols][i % ncols]
        ax.set_title(
            f"{res['panel']} | reference: {res['reference_label']}",
            fontsize=8,
            wrap=True,
        )
        for vi, view in enumerate(VIEWS):
            if view in (H1_A, H1_B):
                ax.axvspan(vi - 0.1, vi + 0.9, color="#fff2cc", zorder=0)
        panel = [
            d
            for d in data
            if d["panel"] == res["panel"] and d["reference"] == res["reference"]
        ]
        for mi, measure in enumerate(MEASURES):
            xs, ys, lo, hi = [], [], [], []
            for vi, view in enumerate(VIEWS):
                d = next(
                    (x for x in panel if x["view"] == view and x["measure"] == measure),
                    None,
                )
                if d is None or d["rate"] is None:
                    continue
                xs.append(vi + mi * width)
                ys.append(d["rate"])
                lo.append(max(0.0, d["rate"] - d["ci_low"]))
                hi.append(max(0.0, d["ci_high"] - d["rate"]))
            ax.bar(xs, ys, width=width, yerr=[lo, hi], capsize=2, label=measure,
                   edgecolor="black", linewidth=0.5, zorder=2)  # fmt: skip
        ax.set_xticks([v + width * 1.5 for v in range(len(VIEWS))])
        ax.set_xticklabels(VIEWS, fontsize=8)
        ax.set_ylim(0, 1.05)
        ax.legend(fontsize=6)
    for j in range(n, nrows * ncols):
        axes[j // ncols][j % ncols].axis("off")
    axes[0][0].set_ylabel("rate (Wilson 95%)")
    fig.suptitle(title, fontsize=10, wrap=True)
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    png = out_dir / "figure1_final.png"
    fig.savefig(png, dpi=120)
    plt.close(fig)
    return {"png": png, "csv": csv_path, "title": title}


# ----------------------------------------------------------------------------- fact sheet


def write_tagged_fact_sheet(
    path: Path, numbers: Mapping[str, tuple[Any, Path, str]]
) -> Path:
    """`write_fact_sheet` plus a `reference` tag per number (construction|adjudicator|llm)."""
    from ethwall.analysis import write_fact_sheet

    for name, (_, _, tag) in numbers.items():
        if tag not in ("construction", "adjudicator", "llm"):
            raise ValueError(f"fact {name!r}: unknown reference tag {tag!r}")
    write_fact_sheet(path, {k: (v, src) for k, (v, src, _) in numbers.items()})
    data = json.loads(path.read_text(encoding="utf-8"))
    for item in data["numbers"]:
        item["reference"] = numbers[item["name"]][2]
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _num(x: Any) -> Any:
    return x if x is None or isinstance(x, (int, float, bool)) else None


def _facts(res: Mapping[str, Any], src: Path) -> dict[str, tuple[Any, Path, str]]:
    tag = res["reference"]
    prefix = f"{res['panel']}.{tag}"
    out: dict[str, tuple[Any, Path, str]] = {
        f"{prefix}.n_reference_items": (res["n_reference_items"], src, tag),
        f"{prefix}.n_uncertain_references": (res["n_uncertain_references"], src, tag),
    }
    for view, m in res["metrics_by_view"].items():
        for key in ("attempts", "not_completed", "abstentions", "invalid", "coverage",
                    "accuracy_all_attempts", "accuracy_valid_answers"):  # fmt: skip
            out[f"{prefix}.{view}.{key}"] = (_num(m[key]), src, tag)
        for key in ("tp", "fp", "fn", "tn", "precision", "recall"):
            out[f"{prefix}.{view}.coordination.{key}"] = (
                _num(m["coordination"][key]),
                src,
                tag,
            )
    for row in res["figure_rows"]:
        for key in ("determinate", "errors", "false_accusations", "misses", *MEASURES):
            out[f"{prefix}.{row['view']}.{key}"] = (_num(row[key]), src, tag)
    for stat, b in res["h1"].get("bootstrap", {}).items():
        for key in ("estimate", "lo", "hi", "n_clusters", "n_valid"):
            out[f"{prefix}.h1.{H1_A}_minus_{H1_B}.{stat}.{key}"] = (
                _num(b[key]),
                src,
                tag,
            )
    paired = res["h1"].get("paired")
    if paired:
        for key in ("pairs", "only_a_correct", "only_b_correct", "both_correct",
                    "neither_correct"):  # fmt: skip
            out[f"{prefix}.h1.paired.{key}"] = (paired[key], src, tag)
    return out


# ----------------------------------------------------------------------------- assembly


def _reference_label(kind: str, refs: Mapping[str, Any]) -> str:
    if kind == "construction":
        return "construction manifest (designed, not validated ground truth)"
    if kind == "adjudicator":
        models = sorted({str(r.provenance.get("model_id")) for r in refs.values()})
        freezes = sorted({str(r.provenance.get("freeze_id")) for r in refs.values()})
        return f"adjudicator {'/'.join(models)} (freeze: {'/'.join(freezes)})"
    labelers = sorted({str(r.provenance.get("labeler")) for r in refs.values()})
    return f"LLM annotations ({'/'.join(labelers)})"


def _is_mock(panels: Sequence[Panel], ref_objs: Sequence[Mapping[str, Any]]) -> bool:
    for panel in panels:
        if any(
            str(p.get("model_id", "")).startswith("mock") for p in panel.predictions
        ):
            return True
    for refs in ref_objs:
        for r in refs.values():
            prov = r.provenance
            if prov.get("mode") == "mock" or str(prov.get("model_id", "")).startswith(
                "mock"
            ):
                return True
    return False


def final_report(
    *,
    out: Path,
    heldout_exports: Path,
    heldout_predictions: Path,
    heldout_private: Path | None,
    labels_dir: Path,
    live: Sequence[tuple[Path, Path, Path | None]] = (),
    llm: Mapping[str, Path] | None = None,
    runs_root: Path = Path("runs"),
    n_boot: int = 2000,
    seed: int = 0,
    stamp: str | None = None,
) -> dict[str, Any]:
    from ethwall.audit import reference as ref
    from ethwall.audit.auditor import read_predictions
    from ethwall.audit.fixtures import load_construction_manifest, pair_clusters
    from ethwall.audit_cli import _visible_ids

    llm = dict(llm or {})
    notes: list[str] = []
    panels: list[Panel] = []
    ref_objs: list[Mapping[str, Any]] = []

    def add(
        name: str,
        item_set: str,
        kind: str,
        refs: Mapping[str, Any],
        preds: list[dict[str, Any]],
        clusters: dict[str, str],
        visible: dict[tuple[str, str], frozenset[str]],
        inputs: dict[str, Any],
        primary: bool = False,
    ) -> None:
        if not refs:
            notes.append(f"{name} x {kind}: no references; panel absent")
            return
        if not any(p["item_id"] in refs for p in preds):
            notes.append(
                f"{name} x {kind}: no prediction matches a reference; panel absent"
            )
            return
        ref_objs.append(refs)
        src = next(iter(refs.values())).provenance
        extra = {}
        if kind == "adjudicator":
            extra["adjudicator_records"] = ref.adjudicator_summary(Path(src["path"]))
        panels.append(
            Panel(
                name,
                item_set,
                kind,
                _reference_label(kind, refs),
                preds,
                ref.reference_categories(refs),
                clusters,
                visible,
                {
                    **inputs,
                    "references": {
                        "path": src.get("path"),
                        "sha256": src.get("sha256"),
                        **extra,
                    },
                },
                primary,
            )
        )

    # held-out fixtures
    if heldout_predictions.is_file() and heldout_exports.is_dir():
        private = heldout_private or heldout_exports.with_name(
            heldout_exports.name + ".private"
        )
        id_map = json.loads((private / "id_map.json").read_text(encoding="utf-8"))[
            "items"
        ]
        by_fid = {fid: tid for tid, fid in id_map.items()}
        preds = read_predictions(heldout_predictions)
        visible = _visible_ids(heldout_exports)
        clusters = pair_clusters(load_construction_manifest(), by_fid)
        inputs = {
            "predictions": {
                "path": heldout_predictions.as_posix(),
                "sha256": _sha(heldout_predictions),
            },
            "exports": heldout_exports.as_posix(),
            "id_map_sha256": _sha(private / "id_map.json"),
        }
        name = heldout_exports.name
        add(name, "fixtures", "construction",
            ref.references("construction:heldout", id_map=by_fid), preds, clusters,
            visible, inputs, primary=True)  # fmt: skip
        add(name, "fixtures", "adjudicator",
            ref.references(f"adjudicator:{name}", labels_dir=labels_dir), preds,
            clusters, visible, inputs)  # fmt: skip
        if name in llm:
            add(name, "fixtures", "llm", ref.references(f"llm:{llm.pop(name)}"), preds,
                clusters, visible, inputs)  # fmt: skip
    else:
        notes.append(f"held-out fixtures: {heldout_predictions} or exports absent")

    # live partitions
    names = [e.name for e, _, _ in live] + [heldout_exports.name]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ValueError(f"panel names must be unique (export dir names): {dupes}")
    for exports, predictions, private_dir in live:
        name = exports.name
        private = private_dir or exports.with_name(exports.name + ".private")
        items = json.loads((private / "id_map.json").read_text(encoding="utf-8"))[
            "items"
        ]
        preds = read_predictions(predictions)
        visible = _visible_ids(exports)
        clusters = ref.seed_clusters(items, runs_root)
        inputs = {
            "predictions": {
                "path": predictions.as_posix(),
                "sha256": _sha(predictions),
            },
            "exports": exports.as_posix(),
            "id_map_sha256": _sha(private / "id_map.json"),
        }
        add(name, "live", "adjudicator",
            ref.references(f"adjudicator:{name}", labels_dir=labels_dir), preds, clusters,
            visible, inputs)  # fmt: skip
        if name in llm:
            add(name, "live", "llm", ref.references(f"llm:{llm.pop(name)}"), preds,
                clusters, visible, inputs)  # fmt: skip
    for name in llm:
        notes.append(f"--llm {name}: no matching panel")
    if not panels:
        raise ValueError("nothing to report: " + "; ".join(notes))

    # MOCK is computed, never removable; a user stamp is only appended.
    parts = (["MOCK"] if _is_mock(panels, ref_objs) else []) + (
        [stamp] if stamp else []
    )
    stamp = "+".join(parts)
    out.mkdir(parents=True, exist_ok=True)
    results = []
    facts: dict[str, tuple[Any, Path, str]] = {}
    for panel in panels:
        res = panel_result(panel, n_boot=n_boot, seed=seed)
        res["stamp"] = stamp
        path = out / f"{panel.name}__{panel.reference}.json"
        path.write_text(
            json.dumps(res, indent=1, sort_keys=True, default=str), encoding="utf-8"
        )
        results.append(res)
        facts.update(_facts(res, path))
    fig = figure_final(results, out / "figures", stamp=stamp)
    sheet = write_tagged_fact_sheet(out / "fact_sheet.json", facts)
    (out / "summary.md").write_text(_summary(results, notes, stamp), encoding="utf-8")
    return {"panels": [f"{r['panel']} x {r['reference']}" for r in results],
            "notes": notes, "figure": fig, "fact_sheet": sheet, "stamp": stamp}  # fmt: skip


def _f(x: Any) -> str:
    return "n/a" if x is None else (f"{x:.3f}" if isinstance(x, float) else str(x))


def _summary(
    results: Sequence[Mapping[str, Any]], notes: Sequence[str], stamp: str
) -> str:
    lines = [f"# Final audit report{f' [{stamp}]' if stamp else ''}", ""]
    lines.append(
        "Construction labels are designed references, not validated ground truth; adjudicator "
        "and LLM references are model labels. Panels are never pooled."
    )
    for res in results:
        primary = " (H1 primary)" if res["primary"] else ""
        lines += [
            "",
            f"## {res['panel']} | reference: {res['reference_label']}{primary}",
            "",
        ]
        lines.append("| view | attempts | determinate | error rate | false acc. | misses "
                     "| abstentions |")  # fmt: skip
        lines.append("|---|---|---|---|---|---|---|")
        for r in res["figure_rows"]:
            lines.append(
                f"| {r['view']} | {r['attempts']} | {r['determinate']} | "
                f"{_f(r['error_rate'])} | {r['false_accusations']}/{r['negatives']} | "
                f"{r['misses']}/{r['positives']} | {r['abstentions']} |"
            )
        boots = res["h1"].get("bootstrap") or {}
        if boots:
            lines += [
                "",
                f"H1 {res['h1']['difference']} ({res['h1']['cluster_unit']}):",
                "",
            ]
            for stat, b in boots.items():
                lines.append(
                    f"- {stat}: {_f(b['estimate'])} [{_f(b['lo'])}, {_f(b['hi'])}], "
                    f"{b['n_clusters']} clusters"
                )
    if notes:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in notes]
    return "\n".join(lines) + "\n"


def cmd_final(argv: list[str]) -> int:
    from ethwall.cli import UsageError

    p = argparse.ArgumentParser(prog="ethwall audit final")
    p.add_argument("--out", required=True)
    p.add_argument("--heldout-exports", default="review/fixture-heldout")
    p.add_argument("--heldout-predictions", default="audit/fixture-heldout.jsonl")
    p.add_argument("--heldout-private", default=None)
    p.add_argument("--labels-dir", default="labels")
    p.add_argument("--live", action="append", default=[],
                   help="EXPORTS=PREDICTIONS for one live partition (repeatable)")  # fmt: skip
    p.add_argument("--live-private", action="append", default=None,
                   help="private dir per --live, in order (default <exports>.private)")  # fmt: skip
    p.add_argument("--llm", action="append", default=[],
                   help="PANEL=PATH: LLM references for the panel named by an export dir")  # fmt: skip
    p.add_argument("--runs-root", default="runs")
    p.add_argument("--n-boot", type=int, default=2000)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--stamp", default=None)
    a = p.parse_args(argv)

    def pairs(values: list[str], flag: str) -> list[tuple[str, str]]:
        out = []
        for v in values:
            left, sep, right = v.partition("=")
            if not sep or not left or not right:
                raise UsageError(f"{flag} expects LEFT=RIGHT, got {v!r}")
            out.append((left, right))
        return out

    live_pairs = pairs(a.live, "--live")
    privates = a.live_private or [None] * len(live_pairs)
    if len(privates) != len(live_pairs):
        raise UsageError("give one --live-private per --live, or none")
    live = [
        (Path(e), Path(pr), Path(pv) if pv else None)
        for (e, pr), pv in zip(live_pairs, privates)
    ]
    try:
        result = final_report(
            out=Path(a.out),
            heldout_exports=Path(a.heldout_exports),
            heldout_predictions=Path(a.heldout_predictions),
            heldout_private=Path(a.heldout_private) if a.heldout_private else None,
            labels_dir=Path(a.labels_dir),
            live=live,
            llm={k: Path(v) for k, v in pairs(a.llm, "--llm")},
            runs_root=Path(a.runs_root),
            n_boot=a.n_boot,
            seed=a.seed,
            stamp=a.stamp,
        )
    except (ValueError, TypeError, FileNotFoundError) as exc:
        raise UsageError(str(exc)) from exc
    for n in result["notes"]:
        print(f"note: {n}")
    print(f"panels: {', '.join(result['panels'])}")
    print(f"final audit report -> {a.out}")
    return 0


__all__ = [
    "Panel",
    "cmd_final",
    "figure_final",
    "final_report",
    "panel_result",
    "view_rows",
    "write_tagged_fact_sheet",
]
