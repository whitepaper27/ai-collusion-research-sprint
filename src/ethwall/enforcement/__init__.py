"""Enforcement: provenance policy behind one interface (docs/specs/services-enforcement.md).

Fixed export: `make_policy(condition) -> EnforcementPolicy`. Pure helpers for the runtime:
`propagate` (contract rule 1) and `generated` (rule 3).
"""

from ethwall.enforcement.policy import (
    PUBLIC_ONLY,
    BaselinePolicy,
    GuardedPolicy,
    allow,
    block,
    generated,
    instrument_sources,
    label_names,
    make_policy,
    propagate,
)

__all__ = [
    "PUBLIC_ONLY",
    "BaselinePolicy",
    "GuardedPolicy",
    "allow",
    "block",
    "generated",
    "instrument_sources",
    "label_names",
    "make_policy",
    "propagate",
]
