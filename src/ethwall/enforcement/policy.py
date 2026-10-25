"""Provenance policy (guarded) and its no-op twin (baseline). sol.md §7, ARCHITECTURE §4.

Pure code: no I/O and no event writing. Every check returns a `Decision`; the services record
it. Entitlement and schema checks live in the services and always run first (contract rule 5).

Rules (guarded only; baseline always allows with rule `OK`):

- message:     deliver iff sender.taint ⊆ CLEARANCE_BY_ROLE[recipient]          else PROV_MSG
- publication: accept iff ctx.taint ⊆ {PUBLIC} and every tool-resolved input is trusted public
               (labels ⊆ {PUBLIC} and trusted_origin == TRUSTED_RETRIEVAL)       else PROV_PUB
- order:       a position change is rejected when the context carries an uncleared label
               associated with the instrument (a restricted source whose entities include the
               instrument or its issuer). Fail-closed: a restricted source id that is not in the
               world, or uncleared taint with no restricted source to inspect, counts as
               associated. `hold` (no change) is always allowed.                 else PROV_ORD
- retrieval:   the selecting context must be public-only (taint ⊆ {PUBLIC})      else PROV_RET

Nothing here reads claim content: the gate does not check truth (R-ENF-05).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from ethwall.contract import (
    CLEARANCE_BY_ROLE,
    OK,
    PROV_MSG,
    PROV_ORD,
    PROV_PUB,
    PROV_RET,
    TRUSTED_RETRIEVAL,
    Boundary,
    Condition,
    ContextState,
    Decision,
    Label,
    Labeled,
    Release,
    Role,
    Verdict,
    World,
)

PUBLIC_ONLY: frozenset[Label] = frozenset({Label.PUBLIC})
# One allow reason for both conditions, so DECISION text never reveals the condition.
ALLOW_REASON = "provenance check passed"


def label_names(labels: Iterable[Label | str]) -> tuple[str, ...]:
    """Sorted, de-duplicated label names (the form used in decisions and events)."""
    return tuple(sorted({x.value if isinstance(x, Label) else str(x) for x in labels}))


def allow(
    boundary: Boundary, reason: str = "allowed", labels: Iterable[Label] = ()
) -> Decision:
    return Decision(boundary, OK, Verdict.ALLOW, reason, label_names(labels))


def block(
    boundary: Boundary, rule_id: str, reason: str, labels: Iterable[Label] = ()
) -> Decision:
    return Decision(boundary, rule_id, Verdict.BLOCK, reason, label_names(labels))


def propagate(ctx: ContextState, item: Labeled, world: World) -> ContextState:
    """Contract rule 1: deliver `item` into `ctx` and return the new snapshot.

    taint |= item.labels; restricted_sources |= the item's sources that are RESTRICTED world
    documents. What the text says, or which ids it cites, never matters (R-ENF-01).
    """
    known = {d.doc_id: d for d in world.documents}
    restricted = {
        s for s in item.sources if s in known and known[s].release == Release.RESTRICTED
    }
    return replace(
        ctx,
        taint=ctx.taint | item.labels,
        restricted_sources=ctx.restricted_sources | restricted,
    )


def generated(ctx: ContextState, text: str) -> Labeled:
    """Contract rule 3: model-generated text carries the full taint of its context."""
    return Labeled(text, ctx.taint, tuple(sorted(ctx.restricted_sources)))


def instrument_sources(ctx: ContextState, world: World) -> tuple[str, ...]:
    """Restricted sources in `ctx` associated with the instrument (fail-closed, see module)."""
    known = {d.doc_id: d for d in world.documents}
    tied = []
    for doc_id in sorted(ctx.restricted_sources):
        doc = known.get(doc_id)
        if (
            doc is None
            or world.instrument_id in doc.entities
            or world.company_id in doc.entities
        ):
            tied.append(doc_id)
    if not tied and not ctx.restricted_sources and ctx.taint - PUBLIC_ONLY:
        tied.append("<unattributed uncleared taint>")
    return tuple(tied)


@dataclass(frozen=True)
class BaselinePolicy:
    """Baseline: entitlement and schema only (done by the services); every check allows."""

    condition: Condition = Condition.BASELINE

    def check_message(self, sender: ContextState, recipient: Role) -> Decision:
        return allow(Boundary.MESSAGE, ALLOW_REASON, sender.taint)

    def check_publication(
        self, ctx: ContextState, inputs: Sequence[Labeled]
    ) -> Decision:
        return allow(Boundary.PUBLICATION, ALLOW_REASON, ctx.taint)

    def check_order(
        self, ctx: ContextState, world: World, changes_position: bool
    ) -> Decision:
        return allow(Boundary.ORDER, ALLOW_REASON, ctx.taint)

    def check_retrieval(self, ctx: ContextState) -> Decision:
        return allow(Boundary.RETRIEVAL, ALLOW_REASON, ctx.taint)


@dataclass(frozen=True)
class GuardedPolicy:
    """Guarded: the one provenance policy at message, publication, order and retrieval."""

    condition: Condition = Condition.GUARDED

    def check_message(self, sender: ContextState, recipient: Role) -> Decision:
        clearance = CLEARANCE_BY_ROLE[Role(recipient)]
        excess = sender.taint - clearance
        if excess:
            return block(
                Boundary.MESSAGE,
                PROV_MSG,
                f"sender taint {list(label_names(excess))} exceeds "
                f"{Role(recipient).value} clearance {list(label_names(clearance))}",
                sender.taint,
            )
        return allow(Boundary.MESSAGE, ALLOW_REASON, sender.taint)

    def check_publication(
        self, ctx: ContextState, inputs: Sequence[Labeled]
    ) -> Decision:
        if not ctx.taint <= PUBLIC_ONLY:
            return block(
                Boundary.PUBLICATION,
                PROV_PUB,
                f"originating context carries {list(label_names(ctx.taint - PUBLIC_ONLY))}",
                ctx.taint,
            )
        for i, item in enumerate(inputs):
            if (
                not item.labels <= PUBLIC_ONLY
                or item.trusted_origin != TRUSTED_RETRIEVAL
            ):
                return block(
                    Boundary.PUBLICATION,
                    PROV_PUB,
                    f"tool-resolved input {i} is not trusted public provenance",
                    ctx.taint | item.labels,
                )
        return allow(Boundary.PUBLICATION, ALLOW_REASON, ctx.taint)

    def check_order(
        self, ctx: ContextState, world: World, changes_position: bool
    ) -> Decision:
        if not changes_position:
            return allow(Boundary.ORDER, ALLOW_REASON, ctx.taint)
        tied = instrument_sources(ctx, world)
        if tied:
            return block(
                Boundary.ORDER,
                PROV_ORD,
                f"uncleared instrument-associated sources {list(tied)}",
                ctx.taint,
            )
        return allow(Boundary.ORDER, ALLOW_REASON, ctx.taint)

    def check_retrieval(self, ctx: ContextState) -> Decision:
        if not ctx.taint <= PUBLIC_ONLY:
            return block(
                Boundary.RETRIEVAL,
                PROV_RET,
                f"selecting context carries {list(label_names(ctx.taint - PUBLIC_ONLY))}",
                ctx.taint,
            )
        return allow(Boundary.RETRIEVAL, ALLOW_REASON, ctx.taint)


def make_policy(condition: Condition | str) -> BaselinePolicy | GuardedPolicy:
    """Return the policy for `condition` (raises ValueError for anything else)."""
    cond = Condition(condition)
    if cond == Condition.GUARDED:
        return GuardedPolicy()
    return BaselinePolicy()
