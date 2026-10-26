"""The episode scheduler (sol.md §5, §9; docs/specs/runtime.md R-RUN-01..13).

One episode = `ROUNDS` rounds x `SCHEDULE` slots; one slot = one decision = one scheduled model
invocation (plus at most one identical transport retry). The runtime owns context taint
(contract propagation rules 1-3): every item that enters a role's context - tool result,
round status, budget notice, evidence packet, delivered message - is first recorded as an
OBSERVATION and only then added to the taint and to `restricted_sources`. Every tool call gets
a fresh `ContextState` snapshot. Services record their own submission/decision events; the
runtime records deliveries, observations, model calls, transport failures, proposals, limits,
refusal flags, spend and price advances.

User turns are laid out for the Messages API: tool results for every tool_use of the previous
assistant turn first (calls not executed get an error result saying so), then the round
status, any budget notice, the packet (first slot only) and delivered messages, in recorded
order.

Arms and control kinds (docs/specs/incentive-arm.md R-INC-09, R-INC-10): each role gets
`tool_specs(config.arm)`; setup refuses a world whose arm (incentive iff it carries the
forecast fact) differs from `config.arm`. The instructed-control text is rendered only when
`config.effective_control_kind == "instructed_harmful"`; `lawful_handoff` controls get the
normal prompts.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ethwall.contract import (
    DUTY_REPORT_ROLES,
    MAX_TRANSPORT_RETRIES,
    ROUNDS,
    SCHEDULE,
    TOOLS_BY_ROLE,
    ActorModel,
    ChatMessage,
    ContextState,
    EpisodeConfig,
    EpisodeResult,
    EventKind,
    Label,
    Labeled,
    Mode,
    ModelCallError,
    ModelRequest,
    ModelResponse,
    Packet,
    Recorder,
    Release,
    Role,
    Services,
    TerminalState,
    TextBlock,
    ToolName,
    ToolResultBlock,
    World,
    canonical_json,
    sha256_hex,
    to_jsonable,
    tool_specs,
)
from ethwall.runtime.refusal import detect_refusal

PUBLIC = frozenset({Label.PUBLIC})
ORCHESTRATOR = "ORCHESTRATOR"
EMPTY_ASSISTANT = "(no response)"


class _Stop(Exception):
    def __init__(self, state: TerminalState, error: str | None = None):
        super().__init__(state.value)
        self.state = state
        self.error = error


@dataclass
class _RoleState:
    role: Role
    context_id: str
    system: str
    tools: tuple[Any, ...]
    history: list[ChatMessage] = field(default_factory=list)
    taint: set[Label] = field(default_factory=set)
    restricted: set[str] = field(default_factory=set)
    refs: list[str] = field(default_factory=list)
    sent_refs: int = 0
    pending: list[tuple[str, Labeled, bool]] = field(default_factory=list)
    notice: bool = False
    packet_delivered: bool = False
    refused: bool = False

    def snapshot(self) -> ContextState:
        return ContextState(
            self.context_id,
            self.role,
            frozenset(self.taint),
            frozenset(self.restricted),
        )


def _labels(labels: frozenset[Label] | set[Label]) -> list[str]:
    return sorted(x.value for x in labels)


class _Episode:
    def __init__(
        self,
        config: EpisodeConfig,
        world: World,
        model: ActorModel,
        services: Services,
        recorder: Recorder,
        prompts: Any,
        ledger: Any,
        clock: Callable[[], float] | None,
        packets: Mapping[Role, Packet] | None,
        manifest: Mapping[str, Any] | None,
    ):
        self.config = config
        self.world = world
        self.model = model
        self.services = services
        self.rec = recorder
        self.prompts = prompts
        self.ledger = ledger
        self.clock = clock or time.monotonic
        self.packets: Mapping[Role, Packet] | None = packets
        self.manifest = manifest
        self.live = config.mode == Mode.LIVE
        self.states: dict[Role, _RoleState] = {}
        self.decisions = 0
        self.transport_attempts = 0
        self.actions = 0
        self.proposed = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = 0.0
        self.start = 0.0

    # ------------------------------------------------------------------ entry

    def run(self) -> EpisodeResult:
        state = TerminalState.SETUP_ERROR
        error: str | None = None
        try:
            state = self._body()
        except _Stop as stop:
            state, error = stop.state, stop.error
        finally:
            if self.live and self.ledger is not None and self.ledger.reserved_usd > 0:
                self.ledger.cancel()
            self.rec.close(
                state,
                {
                    "decisions": self.decisions,
                    "transport_attempts": self.transport_attempts,
                    "actions": self.actions,
                    "proposed_calls": self.proposed,
                },
            )
        return EpisodeResult(
            run_id=self.rec.run_id,
            config=self.config,
            terminal_state=state,
            decisions=self.decisions,
            transport_attempts=self.transport_attempts,
            actions=self.actions,
            proposed_calls=self.proposed,
            refusal_roles=tuple(r.value for r in SCHEDULE if self._refused(r)),
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cost_usd=round(self.cost_usd, 6),
            error=error,
        )

    def _body(self) -> TerminalState:
        try:
            self.start = self.clock()
            if not self.rec.events():
                self.rec.append(
                    EventKind.RUN_START, ORCHESTRATOR, {"manifest": self._manifest()}
                )
            self._setup()
            self._schedule()
            return self._final_state()
        except _Stop:
            raise
        except Exception as exc:  # harness fault: record it, keep the partial record
            self._error("runtime", exc)
            raise _Stop(
                TerminalState.SETUP_ERROR, f"{type(exc).__name__}: {exc}"
            ) from exc

    def _manifest(self) -> dict[str, Any]:
        if self.manifest is not None:
            return dict(self.manifest)
        c = self.config
        return {
            "run_id": self.rec.run_id,
            "condition": c.condition.value,
            "seed": c.seed,
            "mode": c.mode.value,
            "partition": c.partition,
            "model_id": c.model_id,
            "policy": c.policy,
            "control": c.control,
            "arm": c.arm,
            "control_kind": c.effective_control_kind,
            "freeze_id": c.freeze_id,
            "caps": {
                "decisions": c.decisions,
                "max_actions": c.max_actions,
                "max_tool_calls_per_response": c.max_tool_calls_per_response,
                "wall_clock_seconds": c.wall_clock_seconds,
                "max_tokens": c.max_tokens,
                "temperature": c.temperature,
            },
            "prompts_sha256": getattr(self.prompts, "sha256", None),
            "world_sha256": self.world.sha256(),
        }

    def _error(self, where: str, exc: BaseException | str) -> None:
        etype = type(exc).__name__ if isinstance(exc, BaseException) else "Error"
        self.rec.append(
            EventKind.ERROR,
            ORCHESTRATOR,
            {"where": where, "error_type": etype, "message": str(exc)},
        )

    # ------------------------------------------------------------------ setup

    def _setup(self) -> None:
        try:
            self.config.validate()
            model_mode = getattr(self.model, "mode", None)
            if self.live and model_mode != Mode.LIVE:
                raise ValueError("live mode requires a live actor model")
            if not self.live and model_mode == Mode.LIVE:
                raise ValueError(
                    f"{self.config.mode.value} mode refuses a live actor model"
                )
            if self.live:
                if self.ledger is None:
                    raise ValueError(
                        "live mode requires a spend ledger with a recorded pricing basis"
                    )
                if getattr(self.model, "model_id", None) != self.config.model_id:
                    raise ValueError(
                        "actor model id differs from the configured model_id"
                    )
            from ethwall.world.incentive import world_arm

            if world_arm(self.world) != self.config.arm:
                raise ValueError(
                    f"world arm {world_arm(self.world)!r} differs from the configured "
                    f"arm {self.config.arm!r}"
                )
            if self.packets is None:
                from ethwall.world import packet

                self.packets = {r: packet(self.world, r) for r in SCHEDULE}
            missing = [r.value for r in SCHEDULE if r not in self.packets]
            if missing:
                raise ValueError(f"no evidence packet for {missing}")
            specs = tool_specs(self.config.arm)
            instructed = self.config.effective_control_kind == "instructed_harmful"
            for role in SCHEDULE:
                self.states[role] = _RoleState(
                    role=role,
                    context_id=role.value,
                    system=self.prompts.system(role, instructed),
                    tools=tuple(specs[t] for t in ToolName if t in TOOLS_BY_ROLE[role]),
                )
            if self.live:
                self.ledger.begin_episode(self.rec.run_id)
        except Exception as exc:
            self._error("setup", exc)
            raise _Stop(
                TerminalState.SETUP_ERROR, f"{type(exc).__name__}: {exc}"
            ) from exc

    # ------------------------------------------------------------------ schedule

    def _schedule(self) -> None:
        for rnd in range(1, ROUNDS + 1):
            for role in SCHEDULE:
                if self.decisions >= self.config.decisions:
                    return
                self._slot(self.states[role], rnd)
            price = self.services.advance_round(rnd)
            self.rec.append(
                EventKind.PRICE_ADVANCED,
                "MARKET_SERVICE",
                {"round": rnd, "price": price},
            )

    def _final_state(self) -> TerminalState:
        events = self.rec.events()
        reporters = {
            e.payload.get("role")
            for e in events
            if e.kind == EventKind.DUTY_REPORT_SUBMITTED
        }
        duty_ok = {r.value for r in DUTY_REPORT_ROLES} <= reporters
        order_ok = any(e.kind == EventKind.ORDER_SUBMITTED for e in events)
        article_ok = any(e.kind == EventKind.ARTICLE_SUBMITTED for e in events)
        if duty_ok and order_ok and article_ok:
            return TerminalState.COMPLETED
        return TerminalState.BUDGET_EXHAUSTED

    def _check_clock(self) -> None:
        if self.clock() - self.start >= self.config.wall_clock_seconds:
            raise _Stop(TerminalState.WALL_CLOCK_LIMIT, "wall-clock limit reached")

    def _refused(self, role: Role) -> bool:
        st = self.states.get(role)
        return st is not None and st.refused

    # ------------------------------------------------------------------ observations

    def _observe(self, st: _RoleState, ref: str, channel: str, item: Labeled) -> None:
        self.rec.append(
            EventKind.OBSERVATION,
            st.role.value,
            {
                "role": st.role.value,
                "ref_id": ref,
                "channel": channel,
                "text": item.text,
                "labels": _labels(item.labels),
                "sources": list(item.sources),
                "trusted_origin": item.trusted_origin,
            },
            context_id=st.context_id,
            labels=item.labels,
        )
        st.taint |= set(item.labels)
        for doc_id in item.sources:
            try:
                doc = self.world.get(doc_id)
            except KeyError:
                continue
            if doc.release == Release.RESTRICTED:
                st.restricted.add(doc_id)
        st.refs.append(ref)

    def _status_text(self, role: Role, rnd: int) -> str:
        book = self.services.ledger()
        text = (
            f"Round {rnd} of {ROUNDS}, {role.value} turn. "
            f"Orders placed this round fill at {float(book['price']):.2f}."
        )
        if role == Role.TRADING:
            text += (
                f" Team position: {int(book['position'])} shares;"
                f" cash: {float(book['cash']):.2f}."
            )
        return text

    def _user_turn(self, st: _RoleState, rnd: int) -> tuple[Any, ...]:
        role = st.role
        blocks: list[Any] = []
        for call_id, item, is_error in st.pending:
            self._observe(st, call_id, "tool_result", item)
            blocks.append(
                ToolResultBlock(call_id, item.text or "(no output)", is_error)
            )
        st.pending = []
        status = Labeled(self._status_text(role, rnd), PUBLIC)
        self._observe(st, f"STATUS-{role.value}-{rnd}", "status", status)
        blocks.append(TextBlock(status.text))
        if st.notice:
            notice = Labeled(self.prompts.budget_notice(), PUBLIC)
            self._observe(st, f"NOTICE-{role.value}-{rnd}", "notice", notice)
            blocks.append(TextBlock(notice.text))
            st.notice = False
        if not st.packet_delivered:
            assert self.packets is not None
            pkt = self.packets[role]
            self.rec.append(
                EventKind.PACKET_DELIVERED,
                ORCHESTRATOR,
                {
                    "role": role.value,
                    "doc_ids": list(pkt.doc_ids),
                    "packet_sha256": sha256_hex(pkt.text),
                    "round": rnd,
                },
                context_id=st.context_id,
                labels=pkt.labels,
            )
            item = Labeled(pkt.text, pkt.labels, tuple(pkt.doc_ids))
            self._observe(st, f"PKT-{role.value}", "packet", item)
            blocks.append(TextBlock(pkt.text or "(empty evidence packet)"))
            st.packet_delivered = True
        for message_id, item in self.services.pending_messages(role):
            self.rec.append(
                EventKind.MESSAGE_DELIVERED,
                ORCHESTRATOR,
                {"message_id": message_id, "recipient": role.value},
                context_id=st.context_id,
                labels=item.labels,
            )
            self._observe(st, message_id, "message", item)
            blocks.append(TextBlock(f"[Message {message_id}]\n{item.text}"))
        return tuple(blocks)

    # ------------------------------------------------------------------ one slot

    def _slot(self, st: _RoleState, rnd: int) -> None:
        self._check_clock()
        st.history.append(ChatMessage("user", self._user_turn(st, rnd)))
        req = ModelRequest(
            system=st.system,
            messages=tuple(st.history),
            tools=st.tools,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            role=st.role,
            context_id=st.context_id,
            decision_index=self.decisions,
        )
        resp = self._invoke(st, req)
        content = tuple(
            b
            for b in resp.content
            if not (isinstance(b, TextBlock) and not b.text.strip())
        )
        st.history.append(
            ChatMessage("assistant", content or (TextBlock(EMPTY_ASSISTANT),))
        )
        calls = resp.tool_calls
        phrase = detect_refusal(resp.text, bool(calls))
        st.refused = phrase is not None
        if phrase is not None:
            self.rec.append(
                EventKind.REFUSAL_FLAG,
                st.role.value,
                {
                    "role": st.role.value,
                    "decision_index": req.decision_index,
                    "phrase": phrase,
                },
                context_id=st.context_id,
            )
            if all(self._refused(r) for r in SCHEDULE):
                raise _Stop(
                    TerminalState.REFUSED, "every role's latest decision is a refusal"
                )
        self._tools(st, req, calls, rnd)

    def _tools(self, st: _RoleState, req: ModelRequest, calls: Any, rnd: int) -> None:
        role = st.role
        self.proposed += len(calls)
        for c in calls:
            self.rec.append(
                EventKind.TOOL_PROPOSED,
                role.value,
                {
                    "role": role.value,
                    "decision_index": req.decision_index,
                    "call_id": c.call_id,
                    "name": c.name,
                    "args": to_jsonable(c.args),
                },
                context_id=st.context_id,
                labels=frozenset(st.taint),
            )
        limit = self.config.max_tool_calls_per_response
        if len(calls) > limit:
            self.rec.append(
                EventKind.TOOL_LIMIT,
                role.value,
                {
                    "role": role.value,
                    "decision_index": req.decision_index,
                    "proposed": len(calls),
                },
                context_id=st.context_id,
            )
            st.notice = True
            text = (
                f"Not executed: the response proposed {len(calls)} tool calls and the limit "
                f"is {limit} per response, so none of them was executed."
            )
            st.pending = [(c.call_id, Labeled(text, PUBLIC), True) for c in calls]
            return
        for c in calls:
            if self.actions >= self.config.max_actions:
                raise _Stop(TerminalState.ACTION_LIMIT, "tool-action limit reached")
            self.actions += 1
            try:
                result = self.services.execute(st.snapshot(), c, rnd)
            except Exception as exc:
                self._error("services.execute", exc)
                raise _Stop(
                    TerminalState.SETUP_ERROR, f"services failed: {exc}"
                ) from exc
            st.pending.append((c.call_id, result.content, result.is_error))

    # ------------------------------------------------------------------ model calls

    def _complete(self, req: ModelRequest) -> ModelResponse:
        """One transport attempt; any non-contract failure is a non-retryable call error."""
        try:
            return self.model.complete(req)
        except ModelCallError:
            raise
        except Exception as exc:
            raise ModelCallError(
                f"{type(exc).__name__}: {exc}", retryable=False, kind=type(exc).__name__
            ) from exc

    def _invoke(self, st: _RoleState, req: ModelRequest) -> ModelResponse:
        for attempt in range(1, MAX_TRANSPORT_RETRIES + 2):
            self._check_clock()
            reserved = self._reserve(req)
            if attempt == 1:
                self.decisions += 1
            self.transport_attempts += 1
            try:
                resp = self._complete(req)
            except ModelCallError as exc:
                err = exc
            else:
                settled = self._settle(resp.input_tokens, resp.output_tokens)
                self._record_call(st, req, resp, attempt)
                self._record_spend(req, attempt, reserved, settled, resp)
                return resp
            self.rec.append(
                EventKind.TRANSPORT_FAILURE,
                st.role.value,
                {
                    "role": st.role.value,
                    "decision_index": req.decision_index,
                    "attempt": attempt,
                    "error_kind": err.kind,
                    "retryable": err.retryable,
                    "message": str(err),
                },
                context_id=st.context_id,
            )
            settled = self._settle(None, None)
            self._record_spend(req, attempt, reserved, settled, None)
            if not (err.retryable and attempt <= MAX_TRANSPORT_RETRIES):
                raise _Stop(TerminalState.API_ERROR, f"{err.kind}: {err}")
        raise AssertionError("unreachable")

    def _record_call(
        self, st: _RoleState, req: ModelRequest, resp: ModelResponse, attempt: int
    ) -> None:
        new_refs = st.refs[st.sent_refs :]
        st.sent_refs = len(st.refs)
        if resp.input_tokens is not None:
            self.input_tokens += resp.input_tokens
        if resp.output_tokens is not None:
            self.output_tokens += resp.output_tokens
        self.rec.append(
            EventKind.MODEL_CALL,
            st.role.value,
            {
                "role": st.role.value,
                "decision_index": req.decision_index,
                "attempt": attempt,
                "request": to_jsonable(req),
                "response": to_jsonable(resp),
                "input_tokens": resp.input_tokens,
                "output_tokens": resp.output_tokens,
                "latency_ms": resp.latency_ms,
                "model_id": resp.model_id,
                "request_id": resp.request_id,
                "included_refs": list(st.refs),
                "new_refs": new_refs,
                "usage_missing": resp.input_tokens is None
                or resp.output_tokens is None,
            },
            context_id=st.context_id,
            labels=frozenset(st.taint),
        )

    # ------------------------------------------------------------------ spend

    def _reserve(self, req: ModelRequest) -> float:
        if not self.live:
            return 0.0
        body = {
            "system": req.system,
            "messages": to_jsonable(req.messages),
            "tools": to_jsonable(req.tools),
        }
        estimate = len(canonical_json(body).encode("utf-8"))
        if not self.ledger.reserve(estimate, req.max_tokens):
            raise _Stop(
                TerminalState.SPEND_LIMIT,
                "the next reservation does not fit the episode, batch or total cap",
            )
        return float(self.ledger.reserved_usd)

    def _settle(self, input_tokens: int | None, output_tokens: int | None) -> float:
        if not self.live:
            return 0.0
        settled = float(self.ledger.settle(input_tokens, output_tokens))
        self.cost_usd += settled
        return settled

    def _record_spend(
        self,
        req: ModelRequest,
        attempt: int,
        reserved: float,
        settled: float,
        resp: ModelResponse | None,
    ) -> None:
        if not self.live:
            return
        uncertain = (
            resp is None or resp.input_tokens is None or resp.output_tokens is None
        )
        self.rec.append(
            EventKind.SPEND,
            ORCHESTRATOR,
            {
                "decision_index": req.decision_index,
                "attempt": attempt,
                "reserved_usd": reserved,
                "settled_usd": settled,
                "uncertain": uncertain,
            },
        )


def run_episode(
    config: EpisodeConfig,
    *,
    world: World,
    model: ActorModel,
    services: Services,
    recorder: Recorder,
    prompts: Any,
    ledger: Any = None,
    clock: Callable[[], float] | None = None,
    packets: Mapping[Role, Packet] | None = None,
    manifest: Mapping[str, Any] | None = None,
) -> EpisodeResult:
    """Run one episode to a terminal state; always closes `recorder`.

    `prompts` is a `PromptSet`; `ledger` a `SpendLedger` (required in live mode, never touched
    otherwise); `clock` returns seconds (default `time.monotonic`); `packets` maps each role to
    its evidence packet (default: `ethwall.world.packet(world, role)`); `manifest` is written
    into RUN_START when the recorder is still empty (default: a summary of the config).
    """
    return _Episode(
        config,
        world,
        model,
        services,
        recorder,
        prompts,
        ledger,
        clock,
        packets,
        manifest,
    ).run()
