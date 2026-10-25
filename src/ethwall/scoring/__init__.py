"""Claim classification and legitimate-work scoring (spec docs/specs/world-scoring.md, R-SCR).

Exports the contract's fixed names plus one helper, `final_reporting_quality`, for R-SCR-04.
Rules and precision are documented in `ethwall.scoring._score`.
"""

from ethwall.scoring._score import (
    classify_claim,
    duty_report_complete,
    final_reporting_quality,
    reference_article,
    reference_trade,
    reporting_complete,
    score_report,
    trading_quality,
    whole_workflow_complete,
)

__all__ = [
    "classify_claim",
    "duty_report_complete",
    "final_reporting_quality",
    "reference_article",
    "reference_trade",
    "reporting_complete",
    "score_report",
    "trading_quality",
    "whole_workflow_complete",
]
