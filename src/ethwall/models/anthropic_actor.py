"""Anthropic Messages API adapter for actor models (docs/specs/runtime.md R-MOD-01..04).

The client is injectable (tests pass a fake; nothing here touches the network on import).
The model id comes only from configuration (`ACTOR_MODEL_ID` via `from_env`). The adapter
never retries: the client it builds has `max_retries=0`, and transport retries are the
runtime's job (one identical retry per decision, recorded).
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from typing import Any

import anthropic

from ethwall.contract import (
    ChatMessage,
    Mode,
    ModelCallError,
    ModelRequest,
    ModelResponse,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

MODEL_ENV = "ACTOR_MODEL_ID"


class ModelConfigError(RuntimeError):
    """The actor model is not configured (e.g. ACTOR_MODEL_ID unset)."""


def _block_to_api(block: Any) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolUseBlock):
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": dict(block.input),
        }
    if isinstance(block, ToolResultBlock):
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": block.content,
            "is_error": block.is_error,
        }
    raise TypeError(f"unsupported content block {type(block).__name__}")


def messages_to_api(messages: tuple[ChatMessage, ...]) -> list[dict[str, Any]]:
    return [
        {"role": m.role, "content": [_block_to_api(b) for b in m.content]}
        for m in messages
    ]


def tools_to_api(req: ModelRequest) -> list[dict[str, Any]]:
    return [
        {
            "name": t.name.value,
            "description": t.description,
            "input_schema": _plain(t.input_schema),
        }
        for t in req.tools
    ]


def _plain(obj: Any) -> Any:
    if isinstance(obj, Mapping):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(x) for x in obj]
    return obj


def _content_from_api(content: Any) -> tuple[TextBlock | ToolUseBlock, ...]:
    out: list[TextBlock | ToolUseBlock] = []
    for b in content or ():
        kind = getattr(b, "type", None)
        if kind == "text":
            out.append(TextBlock(str(b.text)))
        elif kind == "tool_use":
            raw = b.input if isinstance(b.input, Mapping) else {}
            out.append(ToolUseBlock(str(b.id), str(b.name), _plain(raw)))
    return tuple(out)


def _usage(msg: Any) -> tuple[int | None, int | None]:
    usage = getattr(msg, "usage", None)
    if usage is None:
        return None, None
    i = getattr(usage, "input_tokens", None)
    o = getattr(usage, "output_tokens", None)
    return (int(i) if i is not None else None, int(o) if o is not None else None)


def classify_error(exc: BaseException) -> ModelCallError:
    """429, 5xx and connection/timeout errors are retryable; other 4xx are not."""
    if isinstance(exc, anthropic.APITimeoutError):
        return ModelCallError(str(exc), retryable=True, kind="timeout")
    if isinstance(exc, anthropic.APIConnectionError):
        return ModelCallError(str(exc), retryable=True, kind="connection")
    if isinstance(exc, anthropic.APIStatusError):
        status = int(getattr(exc, "status_code", 0) or 0)
        if status == 429:
            return ModelCallError(str(exc), retryable=True, kind="rate_limit")
        if status >= 500:
            return ModelCallError(str(exc), retryable=True, kind=f"server_{status}")
        return ModelCallError(str(exc), retryable=False, kind=f"client_{status}")
    return ModelCallError(str(exc), retryable=False, kind=type(exc).__name__)


class AnthropicActor:
    """`ActorModel` over the Anthropic Messages API."""

    mode = Mode.LIVE

    def __init__(
        self,
        model_id: str,
        *,
        client: Any = None,
        clock: Callable[[], float] | None = None,
    ):
        if not model_id or not model_id.strip():
            raise ModelConfigError("model_id is required")
        self.model_id = model_id.strip()
        self._client = client
        self._clock = clock or time.monotonic

    @classmethod
    def from_env(
        cls, *, client: Any = None, clock: Callable[[], float] | None = None
    ) -> AnthropicActor:
        model_id = os.environ.get(MODEL_ENV, "").strip()
        if not model_id:
            raise ModelConfigError(
                f"{MODEL_ENV} is not set: export the exact, account-verified actor model id"
            )
        return cls(model_id, client=client, clock=clock)

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = anthropic.Anthropic(max_retries=0)
        return self._client

    def build_kwargs(self, req: ModelRequest) -> dict[str, Any]:
        """Keyword arguments for `client.messages.create`.

        The installed SDK (anthropic 2.x) no longer exposes sampling parameters as keywords, so
        a non-default temperature travels in the request body (`extra_body`); the API default
        (1.0) is simply omitted. Every other key must be a real keyword of the SDK's
        `Messages.create` (pinned by a test against the installed signature).
        """
        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "system": req.system,
            "messages": messages_to_api(req.messages),
            "max_tokens": req.max_tokens,
        }
        if req.temperature != 1.0:
            kwargs["extra_body"] = {"temperature": req.temperature}
        if req.tools:
            kwargs["tools"] = tools_to_api(req)
        return kwargs

    def complete(self, req: ModelRequest) -> ModelResponse:
        kwargs = self.build_kwargs(req)
        client = self.client
        start = self._clock()
        try:
            msg = client.messages.create(**kwargs)
        except anthropic.AnthropicError as exc:
            raise classify_error(exc) from exc
        latency_ms = (self._clock() - start) * 1000.0
        input_tokens, output_tokens = _usage(msg)
        served = getattr(msg, "model", None)
        return ModelResponse(
            content=_content_from_api(getattr(msg, "content", ())),
            stop_reason=str(getattr(msg, "stop_reason", "") or ""),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            model_id=served if isinstance(served, str) and served else self.model_id,
            request_id=getattr(msg, "_request_id", None),
        )
