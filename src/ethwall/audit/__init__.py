"""Audit study (docs/specs/audit.md R-AUD-01..11; sol.md §6, §10).

- `ethwall.audit.fixtures`: scripted fixture trajectories recorded as real event streams,
  construction manifest (hidden) and frozen roster.
- `ethwall.audit.views`: the four evidence views, canonical evidence equality, leak check.
- `ethwall.audit.auditor`: one independent auditor session per trajectory x view.
- `ethwall.audit.labels`: append-only human labels and the reveal gate.
- `ethwall.audit.metrics`: confusion matrix, coordination errors, coverage, citations,
  paired view comparisons and cluster bootstrap.
"""

from ethwall.audit.auditor import (
    AuditorResult,
    AuditorSettings,
    append_prediction,
    auditor_from_env,
    load_auditor_prompt,
    read_predictions,
    run_auditor,
)
from ethwall.audit.fixtures import (
    build_all_fixtures,
    build_fixture,
    fixture_ids,
    fixture_world,
    load_construction_manifest,
    pair_clusters,
    verify_roster,
    write_roster,
)
from ethwall.audit.labels import LabelRequiredError, LabelStore, label_session
from ethwall.audit.metrics import (
    audit_metrics,
    bootstrap_paired_difference,
    confusion_matrix,
    metrics_by_view,
    paired_view_comparison,
)
from ethwall.audit.views import (
    canonical_evidence_items,
    evidence_equal,
    export_all_views,
    export_view,
    leak_check,
    opaque_trajectory_id,
    render_packet,
)

__all__ = [
    "AuditorResult",
    "AuditorSettings",
    "LabelRequiredError",
    "LabelStore",
    "append_prediction",
    "audit_metrics",
    "auditor_from_env",
    "bootstrap_paired_difference",
    "build_all_fixtures",
    "build_fixture",
    "canonical_evidence_items",
    "confusion_matrix",
    "evidence_equal",
    "export_all_views",
    "export_view",
    "fixture_ids",
    "fixture_world",
    "label_session",
    "leak_check",
    "load_auditor_prompt",
    "load_construction_manifest",
    "metrics_by_view",
    "opaque_trajectory_id",
    "pair_clusters",
    "paired_view_comparison",
    "read_predictions",
    "render_packet",
    "run_auditor",
    "verify_roster",
    "write_roster",
]
