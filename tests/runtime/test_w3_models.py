"""AnthropicActor adapter (docs/specs/runtime.md R-MOD-01..04). Fake clients only."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest
from anthropic.types import Message, Usage
from anthropic.types import TextBlock as SdkText
from anthropic.types import ToolUseBlock as SdkToolUse

import ethwall.models.anthropic_actor as actor_mod
from ethwall.contract import (
    TOOL_SPECS,
    ChatMessage,
    Mode,
    ModelCallError,
    ModelRequest,
    Role,
    TextBlock,
    ToolName,
    ToolResultBlock,
    ToolUseBlock,
)
from ethwall.models import AnthropicActor, ModelConfigError

SRC = Path(__file__).resolve().parents[2] / "src" / "ethwall"
OWNED = ("runtime", "models", "prompts", "spend")


def sdk_message(
    usage: Usage | None = None, request_id: str | None = "req_abc"
) -> Message:
    msg = Message.model_construct(
        id="msg_1",
        type="message",
        role="assistant",
        model="served-model",
        content=[
            SdkText(type="text", text="Reading the record."),
            SdkToolUse(
                type="tool_use",
                id="toolu_9",
                name="read_document",
                input={"doc_id": "NEWS-results-0001"},
            ),
        ],
        stop_reason="tool_use",
        stop_sequence=None,
        usage=usage if usage is not None else Usage(input_tokens=321, output_tokens=45),
    )
    msg._request_id = request_id
    return msg


class FakeMessages:
    def __init__(self, outcome: Any):
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class FakeClient:
    def __init__(self, outcome: Any):
        self.messages = FakeMessages(outcome)


class Ticker:
    def __init__(self, step: float = 0.25):
        self.t = 0.0
        self.step = step

    def __call__(self) -> float:
        self.t += self.step
        return self.t


def request() -> ModelRequest:
    return ModelRequest(
        system="SYSTEM PROMPT",
        messages=(
            ChatMessage("user", (TextBlock("packet text"),)),
            ChatMessage(
                "assistant",
                (
                    TextBlock("I will read."),
                    ToolUseBlock("toolu_1", "read_document", {"doc_id": "NEWS-x"}),
                ),
            ),
            ChatMessage(
                "user",
                (
                    ToolResultBlock("toolu_1", "doc body", False),
                    TextBlock("Round 2."),
                ),
            ),
        ),
        tools=(TOOL_SPECS[ToolName.READ_DOCUMENT], TOOL_SPECS[ToolName.SEND_MESSAGE]),
        max_tokens=512,
        temperature=1.0,
        role=Role.MARKET,
        context_id="MARKET",
        decision_index=5,
    )


def status_error(cls: type, status: int) -> Exception:
    req = httpx2.Request("POST", "https://example.invalid/v1/messages")
    return cls(
        f"status {status}", response=httpx2.Response(status, request=req), body=None
    )


def test_request_maps_to_sdk_messages_and_tools():
    """R-MOD-01: ModelRequest -> messages.create(model, system, messages with text, tool_use
    and tool_result blocks, tools, max_tokens, temperature)."""
    client = FakeClient(sdk_message())
    actor = AnthropicActor("model-from-env", client=client, clock=Ticker())
    actor.complete(request())
    (kw,) = client.messages.calls
    assert kw["model"] == "model-from-env"
    assert kw["system"] == "SYSTEM PROMPT"
    assert kw["max_tokens"] == 512
    # temperature 1.0 is the API default and is omitted: the installed SDK (2.x) has no
    # `temperature` keyword, and passing one crashed the first live run (see test_w10).
    assert "temperature" not in kw and "extra_body" not in kw
    assert kw["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "packet text"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I will read."},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "read_document",
                    "input": {"doc_id": "NEWS-x"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": "doc body",
                    "is_error": False,
                },
                {"type": "text", "text": "Round 2."},
            ],
        },
    ]
    spec = TOOL_SPECS[ToolName.READ_DOCUMENT]
    assert kw["tools"][0] == {
        "name": "read_document",
        "description": spec.description,
        "input_schema": dict(spec.input_schema),
    }
    assert [t["name"] for t in kw["tools"]] == ["read_document", "send_message"]


def test_sdk_response_maps_back_to_model_response():
    """R-MOD-01: SDK text/tool_use blocks map back to contract blocks and tool calls."""
    actor = AnthropicActor("m", client=FakeClient(sdk_message()), clock=Ticker())
    resp = actor.complete(request())
    assert resp.content == (
        TextBlock("Reading the record."),
        ToolUseBlock("toolu_9", "read_document", {"doc_id": "NEWS-results-0001"}),
    )
    assert resp.stop_reason == "tool_use"
    assert resp.tool_calls[0].args == {"doc_id": "NEWS-results-0001"}
    assert actor.mode == Mode.LIVE


def test_records_tokens_latency_and_request_id():
    """R-MOD-02: input/output tokens, latency (injectable clock) and request id."""
    actor = AnthropicActor("m", client=FakeClient(sdk_message()), clock=Ticker(0.25))
    resp = actor.complete(request())
    assert (resp.input_tokens, resp.output_tokens) == (321, 45)
    assert resp.latency_ms == pytest.approx(250.0)
    assert resp.request_id == "req_abc"
    assert resp.model_id == "served-model"


def test_missing_usage_is_flagged_as_none():
    """R-MOD-02: missing usage is flagged (None tokens), never guessed."""
    msg = sdk_message(request_id=None)
    msg.usage = None
    resp = AnthropicActor("m", client=FakeClient(msg), clock=Ticker()).complete(
        request()
    )
    assert resp.input_tokens is None and resp.output_tokens is None
    assert resp.request_id is None


@pytest.mark.parametrize(
    ("err", "retryable"),
    [
        (lambda: status_error(anthropic.RateLimitError, 429), True),
        (lambda: status_error(anthropic.InternalServerError, 500), True),
        (lambda: status_error(anthropic.ServiceUnavailableError, 503), True),
        (lambda: status_error(anthropic.OverloadedError, 529), True),
        (
            lambda: anthropic.APIConnectionError(
                request=httpx2.Request("POST", "https://example.invalid")
            ),
            True,
        ),
        (
            lambda: anthropic.APITimeoutError(
                request=httpx2.Request("POST", "https://example.invalid")
            ),
            True,
        ),
        (lambda: status_error(anthropic.BadRequestError, 400), False),
        (lambda: status_error(anthropic.AuthenticationError, 401), False),
        (lambda: status_error(anthropic.PermissionDeniedError, 403), False),
        (lambda: status_error(anthropic.NotFoundError, 404), False),
        (lambda: status_error(anthropic.UnprocessableEntityError, 422), False),
    ],
)
def test_error_mapping(err, retryable):
    """R-MOD-03: 429/5xx/connection errors are retryable; other 4xx are not."""
    client = FakeClient(err())
    actor = AnthropicActor("m", client=client, clock=Ticker())
    with pytest.raises(ModelCallError) as info:
        actor.complete(request())
    assert info.value.retryable is retryable
    assert len(client.messages.calls) == 1  # the adapter itself never retries


def test_default_client_is_constructed_without_sdk_retries(monkeypatch):
    """R-MOD-03: the adapter's own client has max_retries=0 (no hidden retries)."""
    made: list[dict[str, Any]] = []

    class FakeAnthropic:
        def __init__(self, **kwargs: Any):
            made.append(kwargs)
            self.messages = FakeMessages(sdk_message())

    monkeypatch.setattr(actor_mod.anthropic, "Anthropic", FakeAnthropic)
    actor = AnthropicActor("m", clock=Ticker())
    actor.complete(request())
    assert made == [{"max_retries": 0}]


def test_from_env_requires_actor_model_id(monkeypatch):
    """R-MOD-04: from_env() refuses to build without ACTOR_MODEL_ID."""
    monkeypatch.delenv("ACTOR_MODEL_ID", raising=False)
    with pytest.raises(ModelConfigError, match="ACTOR_MODEL_ID"):
        AnthropicActor.from_env(client=FakeClient(sdk_message()))
    monkeypatch.setenv("ACTOR_MODEL_ID", "   ")
    with pytest.raises(ModelConfigError, match="ACTOR_MODEL_ID"):
        AnthropicActor.from_env(client=FakeClient(sdk_message()))


def test_from_env_uses_the_configured_id(monkeypatch):
    """R-MOD-04: the model id comes from the environment."""
    monkeypatch.setenv("ACTOR_MODEL_ID", "configured-actor-id")
    client = FakeClient(sdk_message())
    actor = AnthropicActor.from_env(client=client)
    assert actor.model_id == "configured-actor-id"
    actor.complete(request())
    assert client.messages.calls[0]["model"] == "configured-actor-id"


def test_no_literal_model_id_in_source():
    """R-MOD-04: no literal model id in the W3 packages (tests/test_contract.py also bans
    `"claude-` across all of src)."""
    pattern = re.compile(r"claude-[0-9a-z]|haiku-|sonnet-|opus-", re.IGNORECASE)
    files = [f for pkg in OWNED for f in (SRC / pkg).rglob("*.py")]
    assert len(files) >= 8
    for f in files:
        assert not pattern.search(f.read_text(encoding="utf-8")), f
