"""Episode evaluation (docs/specs/evaluate-analysis.md, R-EVL).

Fixed export: `evaluate_run(events, world, *, recorder_faults=()) -> EpisodeMetrics`, plus the
`EpisodeMetrics` row type and the `Tri` known/zero/unknown endpoint value. Rules are documented
in `ethwall.evaluate._metrics`.
"""

from ethwall.evaluate._metrics import (
    AWARENESS_PHRASES,
    REFUSAL_PHRASES,
    EpisodeMetrics,
    Tri,
    evaluate_run,
)

__all__ = [
    "AWARENESS_PHRASES",
    "REFUSAL_PHRASES",
    "EpisodeMetrics",
    "Tri",
    "evaluate_run",
]
