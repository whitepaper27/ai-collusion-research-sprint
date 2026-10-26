"""Test support for the W3 runtime tests (not a test module).

`FakeServices` adapts the walking skeleton's services and policy to the contract `Services`
protocol so runtime tests exercise real entitlement and provenance behaviour without depending
on the parallel services workstream.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ethwall.contract import (
    TRUSTED_RETRIEVAL,
    Condition,
    ContextState,
    EpisodeConfig,
    EpisodeResult,
    Event,
    EventKind,
    Label,
    Labeled,
    Mode,
    Packet,
    Release,
    Role,
    Store,
    ToolCall,
    ToolResult,
    World,
    label_of,
    render_documents,
    sha256_hex,
)
from ethwall.prompts import load_prompts
from ethwall.runtime import run_episode
from ethwall.skeleton import SkeletonPolicy, SkeletonServices
from ethwall.testing import MemoryRecorder, tiny_world

PUBLIC = frozenset({Label.PUBLIC})


class FakeServices:
    """Contract `Services` built on the skeleton (records its own events)."""

    def __init__(self, world: World, condition: Condition, recorder: MemoryRecorder):
        self.inner = SkeletonServices(world, SkeletonPolicy(condition), recorder)
        self.world = world
        self.executed: list[tuple[ContextState, ToolCall, int]] = []
        self.advanced: list[int] = []

    def execute(
        self, ctx: ContextState, call: ToolCall, round_index: int
    ) -> ToolResult:
        self.executed.append((ctx, call, round_index))
        self.inner.price_index = round_index - 1
        if call.name == "request_public_passages" and ctx.role in (
            Role.TRADING,
            Role.COMMS,
        ):
            return self._retrieve(ctx, call)
        content, is_error = self.inner.execute(ctx, call, round_index)
        return ToolResult(call.call_id, content, is_error)

    def _retrieve(self, ctx: ContextState, call: ToolCall) -> ToolResult:
        rec = self.inner.recorder
        rid = self.inner._id("RET")
        ids = sorted(set(call.args.get("doc_ids", [])))
        rec.append(
            EventKind.RETRIEVAL_REQUESTED,
            ctx.role.value,
            {"request_id": rid, "call_id": call.call_id, "role": ctx.role.value,
             "doc_ids": ids},
        )  # fmt: skip
        decision = self.inner._decide(rid, self.inner.policy.check_retrieval(ctx))
        if not decision.allowed:
            rec.append(
                EventKind.RETRIEVAL_REJECTED,
                "ENFORCEMENT",
                {"request_id": rid, "rule_id": decision.rule_id},
            )
            return ToolResult(call.call_id, Labeled("rejected", PUBLIC), True)
        docs = [d for d in self.world.documents if d.doc_id in ids]
        if len(docs) != len(ids) or any(d.release == Release.RESTRICTED for d in docs):
            rec.append(
                EventKind.RETRIEVAL_REJECTED,
                "RETRIEVAL",
                {"request_id": rid, "rule_id": "RET_ID_INVALID"},
            )
            return ToolResult(
                call.call_id, Labeled("rejected: invalid ids", PUBLIC), True
            )
        text = render_documents(docs)
        rec.append(
            EventKind.RETRIEVAL_RESOLVED,
            "RETRIEVAL",
            {"request_id": rid, "doc_ids": ids, "text_sha256": sha256_hex(text)},
        )
        return ToolResult(
            call.call_id,
            Labeled(text, PUBLIC, tuple(ids), TRUSTED_RETRIEVAL),
            False,
        )

    def pending_messages(self, role: Role) -> tuple[tuple[str, Labeled], ...]:
        return tuple(self.inner.inbox.pop(role, []))

    def advance_round(self, round_index: int) -> float:
        self.advanced.append(round_index)
        self.inner.price_index = round_index
        return self.world.prices[round_index]

    def ledger(self) -> Mapping[str, float]:
        return {
            "cash": self.inner.cash,
            "position": float(self.inner.position),
            "price": self.world.prices[self.inner.price_index],
        }


def make_packets(world: World) -> dict[Role, Packet]:
    """Legal/HR get their whole store; others get public/cleared MARKET and NEWS docs."""
    out: dict[Role, Packet] = {}
    for role in Role:
        if role in (Role.LEGAL, Role.HR):
            docs = [d for d in world.documents if d.store == Store(role.value)]
        else:
            docs = [
                d
                for d in world.documents
                if d.store in (Store.MARKET, Store.NEWS)
                and d.release != Release.RESTRICTED
            ]
        out[role] = Packet(
            role,
            tuple(d.doc_id for d in docs),
            render_documents(docs),
            frozenset(label_of(d) for d in docs),
        )
    return out


def mock_config(condition: Condition = Condition.BASELINE, **kw: Any) -> EpisodeConfig:
    base: dict[str, Any] = {
        "condition": condition,
        "seed": 0,
        "mode": Mode.MOCK,
        "partition": "mock-test",
        "model_id": "mock",
    }
    base.update(kw)
    return EpisodeConfig(**base)


def live_config(condition: Condition = Condition.BASELINE, **kw: Any) -> EpisodeConfig:
    base: dict[str, Any] = {
        "condition": condition,
        "seed": 100,
        "mode": Mode.LIVE,
        "partition": "smoke",
        "model_id": "actor-under-test",
    }
    base.update(kw)
    return EpisodeConfig(**base)


@dataclass
class Run:
    result: EpisodeResult
    events: tuple[Event, ...]
    services: FakeServices
    world: World
    extra: dict[str, Any] = field(default_factory=dict)

    def kinds(self, kind: EventKind) -> list[Event]:
        return [e for e in self.events if e.kind == kind]

    def requests(self, role: Role | None = None) -> list[Mapping[str, Any]]:
        return [
            e.payload["request"]
            for e in self.kinds(EventKind.MODEL_CALL)
            if role is None or e.payload["role"] == role.value
        ]


def run(
    model: Any,
    config: EpisodeConfig | None = None,
    *,
    world: World | None = None,
    ledger: Any = None,
    clock: Any = None,
    run_id: str = "w3-test",
) -> Run:
    world = world or tiny_world()
    config = config or mock_config()
    rec = MemoryRecorder(run_id)
    svc = FakeServices(world, config.condition, rec)
    result = run_episode(
        config,
        world=world,
        model=model,
        services=svc,
        recorder=rec,
        prompts=load_prompts(),
        ledger=ledger,
        clock=clock,
        packets=make_packets(world),
    )
    return Run(result, rec.events(), svc, world)


def all_text(events: Sequence[Event]) -> str:
    return "\n".join(str(e.payload) for e in events)
