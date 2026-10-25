"""Human reference labels (docs/specs/audit.md R-AUD-09; sol.md §10).

`LabelStore` is an append-only JSON-lines file of Sahil's labels. `record` only ever appends:
a second label for the same item is a new line, both are kept and the latest one wins.
`reveal_predictions` refuses (LabelRequiredError) to show auditor predictions for an item that
has no human label yet. `label_session` is the CLI-friendly loop behind `ethwall label`: it
shows the full evidence packet, asks for the label, stores it and only then reveals any
predictions. Labels are made on whole trajectories (`item_id` = the export's trajectory id).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ethwall.audit.rubric import CATEGORIES, VIOLATION_KEYS, normalize_violations
from ethwall.audit.views import render_packet, visible_ids
from ethwall.contract import View, canonical_json


class LabelRequiredError(PermissionError):
    """Predictions were requested for an item that has no human label yet."""


@dataclass(frozen=True)
class HumanLabel:
    item_id: str
    category: str
    violations: Mapping[str, bool | None]
    cited_event_ids: tuple[str, ...]
    uncertainty_note: str
    labeled_utc: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "category": self.category,
            "violations": dict(self.violations),
            "cited_event_ids": list(self.cited_event_ids),
            "uncertainty_note": self.uncertainty_note,
            "labeled_utc": self.labeled_utc,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> HumanLabel:
        return cls(
            item_id=str(d["item_id"]),
            category=str(d["category"]),
            violations=dict(d["violations"]),
            cited_event_ids=tuple(d["cited_event_ids"]),
            uncertainty_note=str(d["uncertainty_note"]),
            labeled_utc=str(d["labeled_utc"]),
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class LabelStore:
    """Append-only human label file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        # Append-only log of every prediction reveal, next to the label file.
        self.reveal_path = self.path.with_name(self.path.stem + "_reveals.jsonl")

    def record(
        self,
        item_id: str,
        category: str,
        violations: Mapping[str, bool | None],
        cited_event_ids: Iterable[str],
        uncertainty_note: str = "",
        *,
        labeled_utc: str | None = None,
    ) -> HumanLabel:
        """Validate and append one label (never rewrites earlier lines)."""
        if not item_id:
            raise ValueError("item_id is required")
        if category not in CATEGORIES:
            raise ValueError(f"category must be one of {CATEGORIES}, got {category!r}")
        label = HumanLabel(
            item_id=item_id,
            category=category,
            violations=normalize_violations(dict(violations)),
            cited_event_ids=tuple(str(x) for x in cited_event_ids),
            uncertainty_note=uncertainty_note,
            labeled_utc=labeled_utc or utc_now(),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(canonical_json(label.to_dict()) + "\n")
        return label

    def all(self) -> tuple[HumanLabel, ...]:
        if not self.path.exists():
            return ()
        return tuple(
            HumanLabel.from_dict(json.loads(line))
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )

    def history(self, item_id: str) -> tuple[HumanLabel, ...]:
        return tuple(x for x in self.all() if x.item_id == item_id)

    def latest(self, item_id: str) -> HumanLabel | None:
        h = self.history(item_id)
        return h[-1] if h else None

    def reveals(self, item_id: str | None = None) -> list[dict[str, Any]]:
        if not self.reveal_path.exists():
            return []
        rows = [
            json.loads(line)
            for line in self.reveal_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return [r for r in rows if item_id is None or r["item_id"] == item_id]

    def _pre_reveal(self, item_id: str) -> tuple[HumanLabel, ...]:
        history = self.history(item_id)
        first = self.reveals(item_id)
        return history if not first else history[: int(first[0]["labels_before"])]

    def post_reveal_labels(self, item_id: str) -> tuple[HumanLabel, ...]:
        """Labels recorded after predictions for the item were first revealed."""
        return self.history(item_id)[len(self._pre_reveal(item_id)) :]

    def references(self, *, include_post_reveal: bool = False) -> dict[str, str]:
        """{item_id: category} - the human reference for metrics.

        The latest label made before the item's predictions were first revealed (sol.md §10:
        labels before predictions). Later relabels are kept in the file and reported by
        `post_reveal_labels`, but used only with `include_post_reveal=True`.
        """
        out: dict[str, str] = {}
        for item_id in dict.fromkeys(x.item_id for x in self.all()):
            usable = (
                self.history(item_id)
                if include_post_reveal
                else self._pre_reveal(item_id)
            )
            if usable:
                out[item_id] = usable[-1].category
        return out

    def reveal_predictions(
        self, item_id: str, predictions_path: str | Path
    ) -> list[dict[str, Any]]:
        """Auditor predictions for `item_id`; refused until a human label exists for it."""
        if self.latest(item_id) is None:
            raise LabelRequiredError(
                f"no human label for {item_id!r}: label it before viewing predictions"
            )
        self.reveal_path.parent.mkdir(parents=True, exist_ok=True)
        with self.reveal_path.open("a", encoding="utf-8") as fh:
            row = {
                "item_id": item_id,
                "revealed_utc": utc_now(),
                "labels_before": len(self.history(item_id)),
            }
            fh.write(canonical_json(row) + "\n")
        path = Path(predictions_path)
        if not path.exists():
            return []
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return [r for r in rows if r.get("item_id") == item_id]


_YES = {"y", "yes", "true", "1"}
_NO = {"n", "no", "false", "0"}
_UNKNOWN = {"?", "unknown", "null", "none", "na", "n/a"}


def parse_violations(text: str) -> dict[str, bool | None]:
    """`key=yes, key2=no, key3=unknown` -> mapping; raises ValueError on anything else."""
    out: dict[str, bool | None] = {}
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        key, sep, value = part.partition("=")
        key, value = key.strip(), value.strip().lower()
        if not sep or key not in VIOLATION_KEYS:
            raise ValueError(f"unknown violation entry {part!r}")
        if value in _YES:
            out[key] = True
        elif value in _NO:
            out[key] = False
        elif value in _UNKNOWN:
            out[key] = None
        else:
            raise ValueError(f"violation value must be yes/no/unknown, got {value!r}")
    return out


def label_session(
    export: Mapping[str, Any],
    store: LabelStore,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], Any] = print,
    predictions_path: str | Path | None = None,
) -> HumanLabel:
    """Show the full packet, take one human label, store it, then reveal any predictions.

    Labels are made on the full packet: the export must be the combined (or linked) view.
    Cited ids must exist in that export.
    """
    if export.get("view") not in (View.COMBINED.value, View.LINKED.value):
        raise ValueError(
            "label from the full packet: pass the combined or linked export"
        )
    item_id = str(export["trajectory_id"])
    visible = visible_ids(export)
    output_fn(render_packet(export))
    output_fn("")
    output_fn("Categories: " + ", ".join(CATEGORIES))
    output_fn("Violation outcomes: " + ", ".join(VIOLATION_KEYS))
    while True:
        category = input_fn("Category: ").strip()
        if category in CATEGORIES:
            break
        output_fn(f"Not a rubric category: {category!r}")
    while True:
        try:
            violations = parse_violations(
                input_fn(
                    "Violations (key=yes/no/unknown, comma separated; blank = none): "
                )
            )
            break
        except ValueError as exc:
            output_fn(str(exc))
    while True:
        raw = input_fn("Cited event ids (space or comma separated): ")
        cited = raw.replace(",", " ").split()
        unknown = [c for c in cited if c not in visible]
        if not unknown:
            break
        output_fn(f"Not in this packet: {unknown}")
    note = input_fn("Uncertainty note: ").strip()
    label = store.record(item_id, category, violations, cited, note)
    output_fn(f"Recorded human label for {item_id}: {category}")
    if predictions_path is not None:
        preds = store.reveal_predictions(item_id, predictions_path)
        output_fn(f"Auditor predictions for {item_id} ({len(preds)}):")
        for p in preds:
            output_fn(json.dumps(p, sort_keys=True))
    return label


__all__ = [
    "HumanLabel",
    "LabelRequiredError",
    "LabelStore",
    "label_session",
    "parse_violations",
    "utc_now",
]
