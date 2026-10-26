"""Analysis of evaluated episodes (docs/specs/evaluate-analysis.md, R-ANL).

Entry points: `roster`, `endpoint_summary`, `wilson`, `paired_seed_comparison`,
`figure_live` (Figure 2), `figure_audit` (Figure 1), `figure_trajectory` (Figure 3),
`write_tables`, `write_fact_sheet` and `analyze_partition` (one partition end to end).
Rules are documented in `ethwall.analysis._stats`, `._figures` and `._report`.
"""

from ethwall.analysis._figures import (
    AuditMetricRow,
    FigureOutput,
    figure_audit,
    figure_live,
    figure_trajectory,
)
from ethwall.analysis._report import (
    AnalysisResult,
    analyze_partition,
    load_run,
    recorded_manifest,
    write_fact_sheet,
    write_tables,
)
from ethwall.analysis._stats import (
    ENDPOINTS,
    EndpointSummary,
    PairedComparison,
    RosterCounts,
    RosterEntry,
    endpoint_summary,
    endpoint_value,
    paired_seed_comparison,
    roster,
    wilson,
)

__all__ = [
    "ENDPOINTS",
    "AnalysisResult",
    "AuditMetricRow",
    "EndpointSummary",
    "FigureOutput",
    "PairedComparison",
    "RosterCounts",
    "RosterEntry",
    "analyze_partition",
    "endpoint_summary",
    "endpoint_value",
    "figure_audit",
    "figure_live",
    "figure_trajectory",
    "load_run",
    "paired_seed_comparison",
    "recorded_manifest",
    "roster",
    "wilson",
    "write_fact_sheet",
    "write_tables",
]
