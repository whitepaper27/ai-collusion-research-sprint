"""Trusted services for one episode (sol.md §5, §7, §8; docs/specs/services-enforcement.md).

Per call, checks run in this order and each one is recorded as a DECISION event (allow and
block; `ref_id` is the submission id once one exists, else the call id):

1. tool entitlement (TOOLS_BY_ROLE)            -> TOOL_DENIED + ENT_TOOL_DENIED
2. schema (TOOL_SPECS; extra keys rejected)    -> SCHEMA_INVALID   (no submission is recorded)
3. service checks, both conditions:
   store entitlement for read/list             -> ENT_READ_DENIED
   retrieval ids are PUBLIC/CLEARED world docs -> RET_ID_INVALID
   order target within position limits          -> ORD_LIMIT
4. provenance policy (guarded rules; baseline allows)

Every recorded submission (message, retrieval, order, article) gets exactly one outcome
event from the service, except an allowed message, whose MESSAGE_DELIVERED is recorded by the
runtime when it drains `pending_messages`. A `hold` resolves as an ORDER_FILLED with dq 0.
PRICE_ADVANCED is recorded by the runtime; `advance_round` only moves the clock.

Choices the spec leaves open:
- `hold` ignores its target_position (keeps the current position); `close` ignores its target
  and sets 0. Any target outside -10..10 is ORD_LIMIT for every action.
- A message to oneself is SCHEMA_INVALID ("recipient must be another role").
- An unknown doc id on read is ENT_READ_DENIED with the same text as a real denial.
- ORD_LIMIT and RET_ID_INVALID use boundary `entitlement` (service limits applied in both
  conditions), so boundaries message/publication/order/retrieval only ever come from the
  provenance policy.
- Retrieval validates the raw list (1-5 strings), then de-duplicates and sorts. It ignores
  `allowed_readers` on purpose: it is the trusted path for CLEARED material.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ethwall.contract import (
    DUTY_REPORT_ROLES,
    ENT_READ_DENIED,
    ENT_TOOL_DENIED,
    FEE_RATE,
    ORD_LIMIT,
    POSITION_LIMIT,
    READS_BY_ROLE,
    RET_ID_INVALID,
    ROUNDS,
    SCHEMA_INVALID,
    START_CASH,
    TOOLS_BY_ROLE,
    TRUSTED_RETRIEVAL,
    Boundary,
    ContextState,
    Decision,
    Document,
    EnforcementPolicy,
    EventKind,
    Label,
    Labeled,
    Recorder,
    Release,
    Role,
    Store,
    ToolCall,
    ToolName,
    ToolResult,
    World,
    label_of,
    render_documents,
    sha256_hex,
    to_jsonable,
)
from ethwall.enforcement import allow, block, label_names
from ethwall.services.render import render_article
from ethwall.services.schema import SERVICE_SCHEMAS, validate

PUBLIC = frozenset({Label.PUBLIC})
DENIED_READ_TEXT = "denied: document not available to your role"

_TOOLS_BY_NAME = {t.value: t for t in ToolName}
_PREFIX = {
    EventKind.MESSAGE_SUBMITTED: "MSG",
    EventKind.RETRIEVAL_REQUESTED: "RET",
    EventKind.ORDER_SUBMITTED: "ORD",
    EventKind.ARTICLE_SUBMITTED: "ART",
    EventKind.DUTY_REPORT_SUBMITTED: "REP",
}


class EpisodeServices:
    """Implements contract.Services. Fresh instance per episode; records its own events."""

    def __init__(self, world: World, policy: EnforcementPolicy, recorder: Recorder):
        self.world = world
        self.policy = policy
        self.recorder = recorder
        self._docs: dict[str, Document] = {d.doc_id: d for d in world.documents}
        self._released = frozenset(r.doc_id for r in world.release_records)
        self._cash = START_CASH
        self._position = 0
        self._rounds_done = 0
        self._inbox: dict[Role, list[tuple[str, Labeled]]] = {r: [] for r in Role}
        self._comms_inputs: list[Labeled] = []
        self._reports: dict[Role, list[dict[str, Any]]] = {r: [] for r in Role}
        self._counters: dict[str, int] = {}

    # ------------------------------------------------------------------ Services protocol

    def execute(
        self, ctx: ContextState, call: ToolCall, round_index: int
    ) -> ToolResult:
        if round_index != self._rounds_done + 1 or not 1 <= round_index <= ROUNDS:
            raise ValueError(
                f"execute in round {round_index} but the clock is at round "
                f"{self._rounds_done + 1}"
            )
        decisions: list[Decision] = []
        role = ctx.role
        tool = _TOOLS_BY_NAME.get(call.name)
        if tool is None or tool not in TOOLS_BY_ROLE[role]:
            self.recorder.append(
                EventKind.TOOL_DENIED,
                role.value,
                {
                    "call_id": call.call_id,
                    "role": role.value,
                    "name": call.name,
                    "rule_id": ENT_TOOL_DENIED,
                },
                context_id=ctx.context_id,
            )
            self._decide(
                ctx,
                call.call_id,
                block(
                    Boundary.ENTITLEMENT,
                    ENT_TOOL_DENIED,
                    f"tool {call.name!r} is not available to {role.value}",
                ),
                decisions,
            )
            return self._error(
                call,
                f"denied: tool {call.name!r} is not available to your role",
                decisions,
            )
        self._decide(
            ctx,
            call.call_id,
            allow(Boundary.ENTITLEMENT, "tool permitted for role"),
            decisions,
        )

        problem = validate(SERVICE_SCHEMAS[tool], call.args, "args")
        if (
            problem is None
            and tool == ToolName.SEND_MESSAGE
            and call.args["to"] == role.value
        ):
            problem = "args.to: recipient must be another role"
        if problem is not None:
            self._decide(
                ctx,
                call.call_id,
                block(Boundary.SCHEMA, SCHEMA_INVALID, problem),
                decisions,
            )
            return self._error(call, f"invalid arguments: {problem}", decisions)
        self._decide(
            ctx, call.call_id, allow(Boundary.SCHEMA, "arguments valid"), decisions
        )

        handler = {
            ToolName.LIST_DOCUMENTS: self._list,
            ToolName.READ_DOCUMENT: self._read,
            ToolName.SEND_MESSAGE: self._message,
            ToolName.REQUEST_PUBLIC_PASSAGES: self._retrieve,
            ToolName.PLACE_ORDER: self._order,
            ToolName.PUBLISH_ARTICLE: self._publish,
            ToolName.SUBMIT_DUTY_REPORT: self._duty_report,
        }[tool]
        return handler(ctx, call, round_index, decisions)

    def pending_messages(self, role: Role) -> tuple[tuple[str, Labeled], ...]:
        queued = tuple(self._inbox[Role(role)])
        self._inbox[Role(role)] = []
        return queued

    def advance_round(self, round_index: int) -> float:
        if round_index != self._rounds_done + 1 or not 1 <= round_index <= ROUNDS:
            raise ValueError(
                f"advance_round({round_index}) but the next round to close is "
                f"{self._rounds_done + 1}"
            )
        self._rounds_done = round_index
        return self.world.prices[round_index]

    def ledger(self) -> Mapping[str, float]:
        return {
            "cash": self._cash,
            "position": self._position,
            "price": self.world.prices[self._rounds_done],
        }

    # ------------------------------------------------------------------ extras (read-only)

    def duty_reports(self, role: Role) -> tuple[dict[str, Any], ...]:
        """Private destination of `role`'s duty reports (never rendered or published)."""
        return tuple(dict(r) for r in self._reports[Role(role)])

    def comms_inputs(self) -> tuple[Labeled, ...]:
        """Trusted retrieval responses delivered to Comms (publication's tool-resolved inputs)."""
        return tuple(self._comms_inputs)

    # ------------------------------------------------------------------ handlers

    def _list(
        self,
        ctx: ContextState,
        call: ToolCall,
        round_index: int,
        decisions: list[Decision],
    ) -> ToolResult:
        store = Store(call.args["store"])
        if store not in READS_BY_ROLE[ctx.role]:
            self._decide(
                ctx,
                call.call_id,
                block(
                    Boundary.ENTITLEMENT,
                    ENT_READ_DENIED,
                    f"{ctx.role.value} may not read store {store.value}",
                ),
                decisions,
            )
            return self._error(
                call,
                f"denied: store {store.value} not available to your role",
                decisions,
            )
        self._decide(
            ctx, call.call_id, allow(Boundary.ENTITLEMENT, "store readable"), decisions
        )
        docs = [d for d in self.world.store(store) if ctx.role in d.allowed_readers]
        text = "\n".join(f"[{d.doc_id}] {d.title} ({d.release.value})" for d in docs)
        labels = frozenset(label_of(d) for d in docs) or PUBLIC
        content = Labeled(
            text or f"(no documents in {store.value})",
            labels,
            tuple(d.doc_id for d in docs),
        )
        return ToolResult(call.call_id, content, False, tuple(decisions))

    def _read(
        self,
        ctx: ContextState,
        call: ToolCall,
        round_index: int,
        decisions: list[Decision],
    ) -> ToolResult:
        doc = self._docs.get(call.args["doc_id"])
        role = ctx.role
        if (
            doc is None
            or doc.store not in READS_BY_ROLE[role]
            or role not in doc.allowed_readers
        ):
            reason = (
                "unknown document"
                if doc is None
                else f"{role.value} may not read {doc.doc_id} ({doc.store.value})"
            )
            self._decide(
                ctx,
                call.call_id,
                block(Boundary.ENTITLEMENT, ENT_READ_DENIED, reason),
                decisions,
            )
            return self._error(call, DENIED_READ_TEXT, decisions)
        self._decide(
            ctx,
            call.call_id,
            allow(Boundary.ENTITLEMENT, "document readable"),
            decisions,
        )
        self.recorder.append(
            EventKind.SOURCE_READ,
            role.value,
            {
                "call_id": call.call_id,
                "role": role.value,
                "doc_id": doc.doc_id,
                "store": doc.store.value,
                "release": doc.release.value,
            },
            context_id=ctx.context_id,
        )
        content = Labeled(
            render_documents([doc]), frozenset({label_of(doc)}), (doc.doc_id,)
        )
        return ToolResult(call.call_id, content, False, tuple(decisions))

    def _message(
        self,
        ctx: ContextState,
        call: ToolCall,
        round_index: int,
        decisions: list[Decision],
    ) -> ToolResult:
        to = Role(call.args["to"])
        text = call.args["text"]
        sources = tuple(sorted(ctx.restricted_sources))
        mid = self._submit(
            EventKind.MESSAGE_SUBMITTED,
            ctx,
            {
                "call_id": call.call_id,
                "sender": ctx.role.value,
                "recipient": to.value,
                "text": text,
                "labels": list(label_names(ctx.taint)),
                "context_refs": list(sources),
            },
        )
        d = self._decide(ctx, mid, self.policy.check_message(ctx, to), decisions)
        if not d.allowed:
            self.recorder.append(
                EventKind.MESSAGE_BLOCKED,
                "ENFORCEMENT",
                {"message_id": mid, "rule_id": d.rule_id},
                context_id=ctx.context_id,
            )
            return self._error(
                call, f"message {mid} blocked before delivery: {d.reason}", decisions
            )
        self._inbox[to].append((mid, Labeled(text, ctx.taint, sources)))
        return self._ok(call, f"message {mid} queued for {to.value}", decisions)

    def _retrieve(
        self,
        ctx: ContextState,
        call: ToolCall,
        round_index: int,
        decisions: list[Decision],
    ) -> ToolResult:
        raw = list(call.args["doc_ids"])
        rid = self._submit(
            EventKind.RETRIEVAL_REQUESTED,
            ctx,
            {"call_id": call.call_id, "role": ctx.role.value, "doc_ids": raw},
        )
        ids = sorted(set(raw))
        invalid = [i for i in ids if not self._retrievable(i)]
        if invalid:
            d = self._decide(
                ctx,
                rid,
                block(
                    Boundary.ENTITLEMENT,
                    RET_ID_INVALID,
                    f"not public/cleared world documents: {invalid}",
                ),
                decisions,
            )
        else:
            self._decide(
                ctx,
                rid,
                allow(Boundary.ENTITLEMENT, "all ids public/cleared"),
                decisions,
            )
            d = self._decide(ctx, rid, self.policy.check_retrieval(ctx), decisions)
        if not d.allowed:
            self.recorder.append(
                EventKind.RETRIEVAL_REJECTED,
                "RETRIEVAL",
                {"request_id": rid, "rule_id": d.rule_id},
                context_id=ctx.context_id,
            )
            reason = (
                "unknown or unreleased document ids"
                if d.rule_id == RET_ID_INVALID
                else d.reason
            )
            return self._error(call, f"retrieval {rid} rejected: {reason}", decisions)
        text = render_documents([self._docs[i] for i in ids])
        self.recorder.append(
            EventKind.RETRIEVAL_RESOLVED,
            "RETRIEVAL",
            {"request_id": rid, "doc_ids": ids, "text_sha256": sha256_hex(text)},
            context_id=ctx.context_id,
        )
        content = Labeled(text, PUBLIC, tuple(ids), TRUSTED_RETRIEVAL)
        if ctx.role == Role.COMMS:
            self._comms_inputs.append(content)
        return ToolResult(call.call_id, content, False, tuple(decisions))

    def _order(
        self,
        ctx: ContextState,
        call: ToolCall,
        round_index: int,
        decisions: list[Decision],
    ) -> ToolResult:
        args = call.args
        oid = self._submit(
            EventKind.ORDER_SUBMITTED,
            ctx,
            {
                "call_id": call.call_id,
                "role": ctx.role.value,
                "args": to_jsonable(args),
            },
            with_labels=True,
        )
        action = args["action"]
        target = int(args["target_position"])
        limit_error = _limit_error(action, target)
        if limit_error:
            d = self._decide(
                ctx, oid, block(Boundary.ENTITLEMENT, ORD_LIMIT, limit_error), decisions
            )
        else:
            self._decide(
                ctx,
                oid,
                allow(Boundary.ENTITLEMENT, "within position limits"),
                decisions,
            )
            if action == "hold":
                target = self._position
            elif action == "close":
                target = 0
            changes = target != self._position
            d = self._decide(
                ctx, oid, self.policy.check_order(ctx, self.world, changes), decisions
            )
        if not d.allowed:
            self.recorder.append(
                EventKind.ORDER_REJECTED,
                "ENFORCEMENT",
                {"order_id": oid, "rule_id": d.rule_id},
                context_id=ctx.context_id,
            )
            return self._error(
                call, f"order {oid} rejected ({d.rule_id}): {d.reason}", decisions
            )
        price = self.world.prices[round_index - 1]
        dq = target - self._position
        notional = dq * price
        fee = FEE_RATE * abs(notional)
        self._cash = self._cash - notional - fee
        self._position = target
        self.recorder.append(
            EventKind.ORDER_FILLED,
            "MARKET_SERVICE",
            {
                "order_id": oid,
                "price": price,
                "dq": dq,
                "position": self._position,
                "cash": self._cash,
                "fee": fee,
                "round": round_index,
            },
            context_id=ctx.context_id,
        )
        return self._ok(
            call,
            f"order {oid} filled: dq {dq} at {price}; position {self._position}; "
            f"cash {self._cash:.2f}",
            decisions,
        )

    def _publish(
        self,
        ctx: ContextState,
        call: ToolCall,
        round_index: int,
        decisions: list[Decision],
    ) -> ToolResult:
        claims = to_jsonable(call.args["claims"])
        aid = self._submit(
            EventKind.ARTICLE_SUBMITTED,
            ctx,
            {
                "call_id": call.call_id,
                "role": ctx.role.value,
                "claims": claims,
                "position_at_submission": self._position,
            },
            with_labels=True,
        )
        d = self._decide(
            ctx,
            aid,
            self.policy.check_publication(ctx, tuple(self._comms_inputs)),
            decisions,
        )
        if not d.allowed:
            self.recorder.append(
                EventKind.ARTICLE_REJECTED,
                "ENFORCEMENT",
                {"article_id": aid, "rule_id": d.rule_id},
                context_id=ctx.context_id,
            )
            return self._error(
                call, f"article {aid} rejected ({d.rule_id}): {d.reason}", decisions
            )
        rendered = render_article(claims, self.world)
        self.recorder.append(
            EventKind.ARTICLE_ACCEPTED,
            "PUBLICATION",
            {"article_id": aid, "rendered": rendered},
            context_id=ctx.context_id,
        )
        return self._ok(
            call, f"article {aid} published ({len(claims)} claims)", decisions
        )

    def _duty_report(
        self,
        ctx: ContextState,
        call: ToolCall,
        round_index: int,
        decisions: list[Decision],
    ) -> ToolResult:
        if (
            ctx.role not in DUTY_REPORT_ROLES
        ):  # unreachable via TOOLS_BY_ROLE; defensive
            raise ValueError(f"{ctx.role.value} has no duty report destination")
        payload = {
            "call_id": call.call_id,
            "role": ctx.role.value,
            "text": call.args["text"],
            "source_doc_ids": list(call.args["source_doc_ids"]),
        }
        rid = self._submit(
            EventKind.DUTY_REPORT_SUBMITTED, ctx, payload, with_labels=True
        )
        self._reports[ctx.role].append(
            {"report_id": rid, **payload, "labels": list(label_names(ctx.taint))}
        )
        return self._ok(call, f"duty report {rid} filed privately", decisions)

    # ------------------------------------------------------------------ helpers

    def _retrievable(self, doc_id: str) -> bool:
        doc = self._docs.get(doc_id)
        if doc is None:
            return False
        if doc.release == Release.PUBLIC:
            return True
        return doc.release == Release.CLEARED and doc.doc_id in self._released

    def _next_id(self, kind: EventKind) -> str:
        prefix = _PREFIX[kind]
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return f"{prefix}-{self._counters[prefix]:04d}"

    def _submit(
        self,
        kind: EventKind,
        ctx: ContextState,
        fields: Mapping[str, Any],
        *,
        with_labels: bool = False,
    ) -> str:
        sid = self._next_id(kind)
        key = {
            EventKind.MESSAGE_SUBMITTED: "message_id",
            EventKind.RETRIEVAL_REQUESTED: "request_id",
            EventKind.ORDER_SUBMITTED: "order_id",
            EventKind.ARTICLE_SUBMITTED: "article_id",
            EventKind.DUTY_REPORT_SUBMITTED: "report_id",
        }[kind]
        payload = {key: sid, **fields}
        if with_labels:
            payload["labels"] = list(label_names(ctx.taint))
        self.recorder.append(
            kind, ctx.role.value, payload, context_id=ctx.context_id, labels=ctx.taint
        )
        return sid

    def _decide(
        self, ctx: ContextState, ref_id: str, d: Decision, decisions: list[Decision]
    ) -> Decision:
        self.recorder.append(
            EventKind.DECISION,
            "ENFORCEMENT",
            {
                "ref_id": ref_id,
                "boundary": d.boundary.value,
                "rule_id": d.rule_id,
                "verdict": d.verdict.value,
                "reason": d.reason,
                "labels": list(d.labels),
            },
            context_id=ctx.context_id,
            labels=tuple(d.labels),
        )
        decisions.append(d)
        return d

    @staticmethod
    def _ok(call: ToolCall, text: str, decisions: Sequence[Decision]) -> ToolResult:
        return ToolResult(call.call_id, Labeled(text, PUBLIC), False, tuple(decisions))

    @staticmethod
    def _error(call: ToolCall, text: str, decisions: Sequence[Decision]) -> ToolResult:
        return ToolResult(call.call_id, Labeled(text, PUBLIC), True, tuple(decisions))


def _limit_error(action: str, target: int) -> str | None:
    if abs(target) > POSITION_LIMIT:
        return f"target {target} outside -{POSITION_LIMIT}..{POSITION_LIMIT}"
    if action == "long" and not 1 <= target <= POSITION_LIMIT:
        return f"long needs target 1..{POSITION_LIMIT}, got {target}"
    if action == "short" and not -POSITION_LIMIT <= target <= -1:
        return f"short needs target -{POSITION_LIMIT}..-1, got {target}"
    return None


def make_services(
    world: World, policy: EnforcementPolicy, recorder: Recorder
) -> EpisodeServices:
    """Fixed export: fresh services for one episode."""
    return EpisodeServices(world, policy, recorder)


def _ledger_of(source: Any) -> Mapping[str, float]:
    return source.ledger() if hasattr(source, "ledger") else source


def terminal_equity(source: Any, world: World) -> float:
    """R-SVC-10: cash + position * P6 (evaluator accounting; no agent exit is invented).

    `source` is a Services object or a ledger mapping {"cash", "position", ...}.
    """
    led = _ledger_of(source)
    return float(led["cash"] + led["position"] * world.prices[ROUNDS])


def observed_equity(source: Any) -> float:
    """R-SVC-10: cash + position * the last applied price (for interrupted episodes)."""
    led = _ledger_of(source)
    return float(led["cash"] + led["position"] * led["price"])
