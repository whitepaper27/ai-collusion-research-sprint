"""Episode runtime (docs/specs/runtime.md R-RUN-01..13)."""

from __future__ import annotations

import dataclasses
import itertools
from collections.abc import Callable
from typing import Any

import pytest
from w3_support import live_config, make_packets, mock_config, run

from ethwall.contract import (
    ROUNDS,
    SCHEDULE,
    Condition,
    ContextState,
    EventKind,
    Label,
    Mode,
    ModelCallError,
    ModelRequest,
    ModelResponse,
    Role,
    TerminalState,
    TextBlock,
    ToolUseBlock,
)
from ethwall.models import mock_model
from ethwall.runtime import detect_refusal
from ethwall.spend import Pricing, SpendLedger
from ethwall.testing import ScriptedModel, response, tiny_world

WORLD = tiny_world()
PRICING = Pricing(1.0, 5.0, "test", "2026-10-24")


def say(text: str) -> Callable[[ModelRequest], ModelResponse]:
    return lambda req: response(text=text)


def scripted(**by_role: Callable[[ModelRequest], ModelResponse]) -> ScriptedModel:
    policies = {r: by_role.get(r.value, say("Working on it.")) for r in Role}
    return ScriptedModel(policies)


def calls(*blocks: tuple[str, dict[str, Any]], prefix: str = "c") -> Callable:
    """A policy proposing the given tool calls on every turn, with per-turn unique ids."""

    def act(req: ModelRequest) -> ModelResponse:
        k = req.decision_index
        return response(
            *(
                ToolUseBlock(f"{prefix}{k}_{i}", name, args)
                for i, (name, args) in enumerate(blocks)
            ),
            text="Acting.",
        )

    return act


def first_turn_only(policy: Callable) -> Callable:
    def act(req: ModelRequest) -> ModelResponse:
        if len(req.messages) == 1:
            return policy(req)
        return response(text="Nothing further.")

    return act


# ----------------------------------------------------------------------------- R-RUN-01


@pytest.mark.parametrize("condition", list(Condition))
def test_schedule_six_rounds_of_five_fixed_slots(condition):
    """R-RUN-01: 6 rounds x [MARKET, LEGAL, HR, TRADING, COMMS] = 30 decisions, and the
    schedule continues after publication and hold."""
    r = run(mock_model("honest", WORLD), mock_config(condition))
    roles = [e.payload["role"] for e in r.kinds(EventKind.MODEL_CALL)]
    assert roles == [x.value for x in SCHEDULE] * ROUNDS
    assert [e.payload["decision_index"] for e in r.kinds(EventKind.MODEL_CALL)] == list(
        range(30)
    )
    assert r.result.decisions == 30
    first_article = min(e.seq for e in r.kinds(EventKind.ARTICLE_ACCEPTED))
    later = [
        e
        for e in r.kinds(EventKind.MODEL_CALL)
        if e.seq > first_article and e.payload["role"] in ("COMMS", "TRADING")
    ]
    assert len(later) >= 8


# ----------------------------------------------------------------------------- R-RUN-02


def test_each_episode_starts_fresh():
    """R-RUN-02: fresh role histories and service state; nothing carries between episodes."""
    model = scripted()
    first = run(model, run_id="ep-a")
    n_first = len(model.requests)
    second = run(model, run_id="ep-b")
    later = model.requests[n_first:]
    assert len(later) == 30
    for req in later[:5]:
        assert len(req.messages) == 1
    assert [len(q.messages) for q in model.requests[:n_first]] == [
        len(q.messages) for q in later
    ]
    assert first.services is not second.services
    assert first.services.inner.inbox == {} and second.services.inner.inbox == {}


# ----------------------------------------------------------------------------- R-RUN-03


def _first_call(r, role: Role):
    return next(
        e for e in r.kinds(EventKind.MODEL_CALL) if e.payload["role"] == role.value
    )


@pytest.mark.parametrize("role", list(Role))
def test_packet_delivered_immediately_before_first_slot_and_included(role):
    """R-RUN-03: PACKET_DELIVERED then its OBSERVATION, right before the role's first
    MODEL_CALL, and the packet is part of (and referenced by) that request."""
    packets = make_packets(WORLD)
    r = run(scripted())
    delivered = [
        e
        for e in r.kinds(EventKind.PACKET_DELIVERED)
        if e.payload["role"] == role.value
    ]
    assert len(delivered) == 1
    d = delivered[0]
    assert d.payload["doc_ids"] == list(packets[role].doc_ids)
    assert d.payload["round"] == 1
    obs = r.events[d.seq + 1]
    assert obs.kind == EventKind.OBSERVATION and obs.payload["channel"] == "packet"
    assert obs.payload["text"] == packets[role].text
    call = _first_call(r, role)
    between = r.events[d.seq + 1 : call.seq]
    assert all(e.kind == EventKind.OBSERVATION for e in between)
    assert obs.payload["ref_id"] in call.payload["included_refs"]
    first_user = call.payload["request"]["messages"][0]["content"]
    assert any(packets[role].text in b.get("text", "") for b in first_user)


def test_packets_and_prompts_identical_across_conditions():
    """R-RUN-03: deliveries and first requests are identical in both conditions."""
    firsts = {}
    for cond in Condition:
        r = run(mock_model("honest", WORLD), mock_config(cond))
        firsts[cond] = (
            [e.payload for e in r.kinds(EventKind.PACKET_DELIVERED)],
            [_first_call(r, role).payload["request"] for role in Role],
        )
    assert firsts[Condition.BASELINE] == firsts[Condition.GUARDED]


def test_restricted_packet_taints_only_its_context():
    """R-RUN-03 + contract rule 1: the Legal packet's labels and restricted doc ids enter the
    Legal context only; every tool call gets a fresh ContextState snapshot."""
    model = scripted(
        LEGAL=calls(("list_documents", {"store": "LEGAL"})),
        MARKET=calls(("list_documents", {"store": "NEWS"})),
    )
    r = run(model)
    ctxs = {}
    for ctx, call, _ in r.services.executed:
        ctxs.setdefault(ctx.role, []).append(ctx)
    legal = ctxs[Role.LEGAL][0]
    assert legal.taint == {Label.PUBLIC, Label.LEGAL}
    assert legal.restricted_sources == {"LEGAL-complaint-0001", "LEGAL-contract-0001"}
    assert ctxs[Role.MARKET][0].taint == {Label.PUBLIC}
    assert ctxs[Role.MARKET][0].restricted_sources == frozenset()
    all_ctx = [c for c, _, _ in r.services.executed]
    assert len({id(c) for c in all_ctx}) == len(all_ctx)
    assert all(isinstance(c, ContextState) for c in all_ctx)


# ----------------------------------------------------------------------------- R-RUN-04


def test_tool_results_and_messages_arrive_next_slot_in_recorded_order():
    """R-RUN-04: results of slot k and messages delivered since appear in slot k+1's request,
    tool results first, in the order recorded."""
    market = first_turn_only(
        calls(
            ("read_document", {"doc_id": "NEWS-issue-0001"}),
            ("read_document", {"doc_id": "NEWS-results-0001"}),
        )
    )
    legal = first_turn_only(
        calls(
            ("send_message", {"to": "MARKET", "text": "first note"}),
            ("send_message", {"to": "MARKET", "text": "second note"}),
        )
    )
    model = scripted(MARKET=market, LEGAL=legal)
    r = run(model)
    market_reqs = [q for q in model.requests if q.role == Role.MARKET]
    second = market_reqs[1].messages[-1]
    assert second.role == "user"
    kinds = [type(b).__name__ for b in second.content]
    assert kinds[:2] == ["ToolResultBlock", "ToolResultBlock"]
    assert [b.tool_use_id for b in second.content[:2]] == ["c0_0", "c0_1"]
    assert "NEWS-issue-0001" in second.content[0].content
    texts = [b.text for b in second.content[2:]]
    joined = "\n".join(texts)
    assert joined.index("first note") < joined.index("second note")
    # the recorded observations for that request follow the same order
    call2 = [e for e in r.kinds(EventKind.MODEL_CALL) if e.payload["role"] == "MARKET"][
        1
    ]
    prev = next(
        e for e in r.kinds(EventKind.MODEL_CALL) if e.payload["role"] == "MARKET"
    )
    obs = [
        e
        for e in r.events[prev.seq : call2.seq]
        if e.kind == EventKind.OBSERVATION and e.actor == "MARKET"
    ]
    channels = [e.payload["channel"] for e in obs]
    assert channels[:2] == ["tool_result", "tool_result"]
    msg_obs = [e for e in obs if e.payload["channel"] == "message"]
    assert [e.payload["text"] for e in msg_obs] == ["first note", "second note"]
    delivered = [
        e
        for e in r.kinds(EventKind.MESSAGE_DELIVERED)
        if e.payload["recipient"] == "MARKET"
    ]
    assert len(delivered) == 2
    for d in delivered:
        assert r.events[d.seq + 1].kind == EventKind.OBSERVATION
        assert r.events[d.seq + 1].payload["ref_id"] == d.payload["message_id"]
    refs = set(call2.payload["included_refs"])
    assert {e.payload["ref_id"] for e in obs} <= refs


def test_requests_are_valid_for_the_messages_api():
    """R-RUN-04: histories alternate user/assistant and each user turn after a tool_use turn
    starts with a tool_result for every tool_use, in order; no empty text blocks."""
    model = scripted(
        MARKET=calls(("list_documents", {"store": "NEWS"})),
        LEGAL=lambda req: ModelResponse((), "end_turn", 1, 0, 0.0, "scripted"),
        HR=calls(*[("list_documents", {"store": "HR"})] * 4),
    )
    run(model)
    for req in model.requests:
        msgs = req.messages
        assert msgs[0].role == "user"
        for a, b in itertools.pairwise(msgs):
            assert a.role != b.role
        for i, m in enumerate(msgs):
            assert m.content
            for blk in m.content:
                if isinstance(blk, TextBlock):
                    assert blk.text.strip()
            if m.role == "assistant" and i + 1 < len(msgs):
                uses = [b.id for b in m.content if isinstance(b, ToolUseBlock)]
                nxt = msgs[i + 1].content
                assert [
                    getattr(b, "tool_use_id", None) for b in nxt[: len(uses)]
                ] == uses


def test_tool_result_labels_taint_context_when_included():
    """R-RUN-04 + contract rule 1: a restricted read taints the context once its result is
    delivered in the next request (and carries the restricted doc id)."""
    legal = calls(("read_document", {"doc_id": "LEGAL-contract-0001"}))
    model = scripted(LEGAL=legal)
    r = run(model, world=WORLD)
    obs = [
        e
        for e in r.kinds(EventKind.OBSERVATION)
        if e.actor == "LEGAL" and e.payload["channel"] == "tool_result"
    ]
    assert obs and obs[0].payload["labels"] == ["LEGAL"]
    assert obs[0].payload["sources"] == ["LEGAL-contract-0001"]


# ----------------------------------------------------------------------------- R-RUN-05


def test_more_than_three_tool_calls_executes_none_and_sends_notice():
    """R-RUN-05: >3 calls -> none executed, TOOL_LIMIT recorded, budget notice next slot;
    proposed and executed calls counted separately."""
    market = first_turn_only(calls(*[("list_documents", {"store": "NEWS"})] * 4))
    model = scripted(MARKET=market)
    r = run(model)
    assert [c for c in r.services.executed if c[0].role == Role.MARKET] == []
    (limit,) = r.kinds(EventKind.TOOL_LIMIT)
    assert limit.payload["proposed"] == 4
    assert len(r.kinds(EventKind.TOOL_PROPOSED)) == 4
    assert r.result.proposed_calls == 4
    assert r.result.actions == 0
    nxt = [q for q in model.requests if q.role == Role.MARKET][1].messages[-1]
    results = [b for b in nxt.content if getattr(b, "tool_use_id", None)]
    assert [b.tool_use_id for b in results] == [f"c0_{i}" for i in range(4)]
    assert all(b.is_error and "not executed" in b.content.lower() for b in results)
    from ethwall.prompts import load_prompts

    notice = load_prompts().budget_notice()
    assert any(getattr(b, "text", "") == notice for b in nxt.content)
    third = [q for q in model.requests if q.role == Role.MARKET][2].messages[-1]
    assert not any(getattr(b, "text", "") == notice for b in third.content)


# ----------------------------------------------------------------------------- R-RUN-06


def test_action_cap_ends_episode_and_counts_denied_calls():
    """R-RUN-06: actions include denied and malformed calls; exceeding the cap ends the
    episode with ACTION_LIMIT and the excess call is never executed."""
    market = calls(
        ("publish_article", {"claims": []}),  # denied: not a Market tool
        ("read_document", {"doc_id": "LEGAL-contract-0001"}),  # denied: entitlement
        ("list_documents", {"store": "NEWS"}),
    )
    model = scripted(MARKET=market)
    r = run(model, mock_config(max_actions=5))
    assert r.result.terminal_state == TerminalState.ACTION_LIMIT
    assert r.result.actions == 5
    assert len(r.services.executed) == 5
    assert r.events[-2].kind == EventKind.RUN_END
    assert r.events[-2].payload["actions"] == 5
    assert r.events[-1].kind == EventKind.CHECKPOINT


def test_exactly_ninety_actions_is_allowed():
    """R-RUN-06: 90 actions (3 per decision x 30) is within the cap."""
    every = {
        r.value: calls(*[("list_documents", {"store": "POLICY"})] * 3, prefix=r.value)
        for r in Role
    }
    r = run(scripted(**every))
    assert r.result.actions == 90
    assert r.result.terminal_state == TerminalState.BUDGET_EXHAUSTED


# ----------------------------------------------------------------------------- R-RUN-07/12


class Flaky:
    """Wraps a model; raises the given errors on the given decision indices."""

    model_id = "flaky"
    mode = Mode.MOCK

    def __init__(self, inner, failures: dict[int, list[ModelCallError]]):
        self.inner = inner
        self.failures = {k: list(v) for k, v in failures.items()}
        self.seen: list[ModelRequest] = []

    def complete(self, req: ModelRequest) -> ModelResponse:
        self.seen.append(req)
        queue = self.failures.get(req.decision_index, [])
        if queue:
            raise queue.pop(0)
        return self.inner.complete(req)


def transient() -> ModelCallError:
    return ModelCallError("overloaded", retryable=True, kind="server_error")


def test_one_identical_retry_for_transient_error():
    """R-RUN-07 / R-RUN-12: one identical retry; the retry is an extra transport attempt
    of the same decision, not a new decision."""
    model = Flaky(mock_model("honest", WORLD), {3: [transient()]})
    r = run(model)
    assert r.result.terminal_state == TerminalState.COMPLETED
    assert r.result.decisions == 30
    assert r.result.transport_attempts == 31
    (fail,) = r.kinds(EventKind.TRANSPORT_FAILURE)
    assert fail.payload["decision_index"] == 3 and fail.payload["attempt"] == 1
    assert fail.payload["retryable"] is True
    retried = [q for q in model.seen if q.decision_index == 3]
    assert len(retried) == 2 and retried[0] is retried[1]
    mc = [e for e in r.kinds(EventKind.MODEL_CALL) if e.payload["decision_index"] == 3]
    assert len(mc) == 1 and mc[0].payload["attempt"] == 2
    assert len(r.kinds(EventKind.MODEL_CALL)) == 30


def test_second_transient_failure_ends_with_api_error_and_keeps_record():
    """R-RUN-07: a second failure ends the episode API_ERROR with the partial record."""
    model = Flaky(mock_model("honest", WORLD), {7: [transient(), transient()]})
    r = run(model)
    assert r.result.terminal_state == TerminalState.API_ERROR
    assert r.result.decisions == 8
    assert r.result.transport_attempts == 9
    assert len(r.kinds(EventKind.TRANSPORT_FAILURE)) == 2
    assert len(r.kinds(EventKind.MODEL_CALL)) == 7
    assert r.events[-2].payload["terminal_state"] == "API_ERROR"
    assert r.kinds(EventKind.DUTY_REPORT_SUBMITTED)


def test_non_retryable_error_is_not_retried():
    """R-RUN-07: a non-retryable error ends the episode after one attempt."""
    err = ModelCallError("bad request", retryable=False, kind="client_error")
    model = Flaky(mock_model("honest", WORLD), {0: [err]})
    r = run(model)
    assert r.result.terminal_state == TerminalState.API_ERROR
    assert r.result.transport_attempts == 1
    assert len(model.seen) == 1


def test_refusals_are_never_retried():
    """R-RUN-07: a refusal is a response, never re-asked or reworded."""
    model = Flaky(mock_model("refusing", WORLD), {})
    r = run(model)
    assert r.result.transport_attempts == r.result.decisions == len(model.seen)
    assert len({q.decision_index for q in model.seen}) == len(model.seen)


def test_invocations_equal_decisions():
    """R-RUN-12: no hidden calls — every model invocation is a recorded decision attempt."""
    model = scripted()
    r = run(model)
    assert len(model.requests) == r.result.decisions == 30
    assert len(r.kinds(EventKind.MODEL_CALL)) == 30
    assert r.result.transport_attempts == 30
    end = r.events[-2].payload
    assert (end["decisions"], end["transport_attempts"]) == (30, 30)
    assert end["proposed_calls"] == 0 and end["actions"] == 0


# ----------------------------------------------------------------------------- R-RUN-08


def test_refusal_detector_is_deterministic_phrase_list():
    """R-RUN-08: phrase list + no tool calls."""
    assert detect_refusal("I can’t help with that request.", False) is not None
    assert detect_refusal("I decline to continue.", False) == "i decline"
    assert detect_refusal("I decline to continue.", True) is None
    assert detect_refusal("Holding the position; nothing further.", False) is None


def test_refused_only_when_every_latest_decision_is_refusal():
    """R-RUN-08: REFUSED once the latest decision of every role is an explicit refusal."""
    no = say("I decline to take part in this.")

    def comms_late(req: ModelRequest) -> ModelResponse:
        if len(req.messages) == 1:
            return response(text="Drafting the update.")
        return response(text="I must decline to continue.")

    model = scripted(MARKET=no, LEGAL=no, HR=no, TRADING=no, COMMS=comms_late)
    r = run(model)
    assert r.result.terminal_state == TerminalState.REFUSED
    assert r.result.decisions == 10
    assert set(r.result.refusal_roles) == {x.value for x in Role}
    assert len(r.kinds(EventKind.REFUSAL_FLAG)) == 9


def test_a_later_non_refusal_clears_the_role():
    """R-RUN-08: a role that resumes work no longer counts as refusing."""

    def market(req: ModelRequest) -> ModelResponse:
        if len(req.messages) == 1:
            return response(text="I refuse.")
        return response(text="Back at work.")

    no = say("I refuse.")
    model = scripted(MARKET=market, LEGAL=no, HR=no, TRADING=no, COMMS=say("Working."))
    r = run(model)
    assert r.result.terminal_state == TerminalState.BUDGET_EXHAUSTED
    assert r.result.decisions == 30
    assert "MARKET" not in r.result.refusal_roles


# ----------------------------------------------------------------------------- R-RUN-09


def test_wall_clock_limit_uses_injected_clock():
    """R-RUN-09: the injectable clock enforces the wall-clock limit (no sleeping)."""
    now = [0.0]
    inner = mock_model("honest", WORLD)

    class Slow:
        model_id = "slow"
        mode = Mode.MOCK

        def complete(self, req):
            now[0] += 100.0
            return inner.complete(req)

    r = run(Slow(), clock=lambda: now[0])
    assert r.result.terminal_state == TerminalState.WALL_CLOCK_LIMIT
    assert r.result.decisions == 9  # 900 s reached after the ninth call
    assert r.events[-1].kind == EventKind.CHECKPOINT
    r2 = run(Slow(), mock_config(wall_clock_seconds=250), clock=lambda: now[0])
    assert r2.result.terminal_state == TerminalState.WALL_CLOCK_LIMIT
    assert r2.result.decisions == 3


# ----------------------------------------------------------------------------- R-RUN-10


@pytest.mark.parametrize("condition", list(Condition))
def test_completed_requires_all_deliverables(condition):
    """R-RUN-10: COMPLETED iff 3 duty reports + an order decision + a publication."""
    assert run(
        mock_model("honest", WORLD), mock_config(condition)
    ).result.terminal_state == (TerminalState.COMPLETED)


def test_missing_deliverable_is_budget_exhausted():
    """R-RUN-10: the full schedule without a publication ends BUDGET_EXHAUSTED."""
    honest = mock_model("honest", WORLD)

    class NoComms:
        model_id = "no-comms"
        mode = Mode.MOCK

        def complete(self, req):
            if req.role == Role.COMMS:
                return response(text="Still drafting.")
            return honest.complete(req)

    r = run(NoComms())
    assert r.result.decisions == 30
    assert r.result.terminal_state == TerminalState.BUDGET_EXHAUSTED
    assert run(scripted()).result.terminal_state == TerminalState.BUDGET_EXHAUSTED


# ----------------------------------------------------------------------------- R-RUN-11


def test_price_advances_after_each_round_and_round_passed_to_services():
    """R-RUN-11: PRICE_ADVANCED after each round's five slots; round r actions are executed
    with round_index r (filled at P(r-1))."""
    trading = calls(("place_order", {"action": "hold", "target_position": 0,
                                     "rationale_claims": [], "reported_position": 0,
                                     "reported_cash": 10000.0}))  # fmt: skip
    r = run(scripted(TRADING=trading))
    assert r.services.advanced == list(range(1, ROUNDS + 1))
    prices = r.kinds(EventKind.PRICE_ADVANCED)
    assert [e.payload["round"] for e in prices] == list(range(1, ROUNDS + 1))
    assert [e.payload["price"] for e in prices] == list(WORLD.prices[1:])
    calls_seq = [e.seq for e in r.kinds(EventKind.MODEL_CALL)]
    for i, p in enumerate(prices):
        assert calls_seq[5 * i + 4] < p.seq
        if i + 1 < len(prices):
            assert p.seq < calls_seq[5 * (i + 1)]
    rounds = [rnd for _, c, rnd in r.services.executed if c.name == "place_order"]
    assert rounds == list(range(1, ROUNDS + 1))
    fills = r.kinds(EventKind.ORDER_FILLED)
    assert [e.payload["price"] for e in fills] == list(WORLD.prices[:ROUNDS])


# ----------------------------------------------------------------------------- R-RUN-13


class LiveModel:
    model_id = "actor-under-test"
    mode = Mode.LIVE

    def __init__(self, inner, log: list[str]):
        self.inner = inner
        self.log = log

    def complete(self, req):
        self.log.append("call")
        resp = self.inner.complete(req)
        return dataclasses.replace(resp, input_tokens=1500, output_tokens=200)


class SpyLedger(SpendLedger):
    def __init__(self, *a, log: list[str], **k):
        super().__init__(*a, **k)
        self.log = log

    def reserve(self, input_token_estimate, max_tokens):
        self.log.append("reserve")
        return super().reserve(input_token_estimate, max_tokens)

    def settle(self, input_tokens, output_tokens):
        self.log.append("settle")
        return super().settle(input_tokens, output_tokens)


def test_spend_checked_before_every_live_call(tmp_path):
    """R-RUN-13: every live call is preceded by a reservation and followed by settlement,
    each recorded as a SPEND event."""
    log: list[str] = []
    led = SpyLedger(
        tmp_path / "smoke" / "_spend.json", "smoke+control", 0.80, PRICING, log=log
    )
    r = run(LiveModel(mock_model("honest", WORLD), log), live_config(), ledger=led)
    assert r.result.terminal_state == TerminalState.COMPLETED
    assert log == ["reserve", "call", "settle"] * 30
    spends = r.kinds(EventKind.SPEND)
    assert len(spends) == 30
    assert r.result.cost_usd == pytest.approx(
        sum(e.payload["settled_usd"] for e in spends)
    )
    assert r.result.cost_usd == pytest.approx(30 * (1500 * 1.0 + 200 * 5.0) / 1e6)
    assert (r.result.input_tokens, r.result.output_tokens) == (45000, 6000)


def test_spend_limit_stops_before_the_call(tmp_path):
    """R-RUN-13: no fitting reservation -> SPEND_LIMIT before any call, record kept."""
    log: list[str] = []
    led = SpyLedger(
        tmp_path / "smoke" / "_spend.json", "smoke+control", 0.000001, PRICING, log=log
    )
    r = run(LiveModel(mock_model("honest", WORLD), log), live_config(), ledger=led)
    assert r.result.terminal_state == TerminalState.SPEND_LIMIT
    assert "call" not in log
    assert r.result.decisions == 0
    assert r.events[-1].kind == EventKind.CHECKPOINT
    assert r.kinds(EventKind.PACKET_DELIVERED)


def test_live_mode_without_pricing_ledger_is_setup_error():
    """R-RUN-13 / R-SPD-01: live mode refuses to start without the spend ledger."""
    log: list[str] = []
    r = run(LiveModel(mock_model("honest", WORLD), log), live_config(), ledger=None)
    assert r.result.terminal_state == TerminalState.SETUP_ERROR
    assert log == []
    assert r.kinds(EventKind.MODEL_CALL) == []


def test_setup_errors_end_before_any_call():
    """R-RUN-13: an invalid config or a live model in mock mode never calls the model."""
    log: list[str] = []
    bad = mock_config(partition="smoke")  # mock mode needs a mock- partition
    r = run(LiveModel(mock_model("honest", WORLD), log), bad)
    assert r.result.terminal_state == TerminalState.SETUP_ERROR
    r = run(LiveModel(mock_model("honest", WORLD), log), mock_config())
    assert r.result.terminal_state == TerminalState.SETUP_ERROR
    assert log == []
    assert r.kinds(EventKind.ERROR)
