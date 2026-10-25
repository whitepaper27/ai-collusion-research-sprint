"""R-MOD-01: the adapter's request must match the INSTALLED Anthropic SDK, not just a fake client.

A live run failed because `temperature` is no longer a keyword of `Messages.create` in the
installed SDK. These tests check every keyword the adapter sends against the real signature.
"""

import inspect

from anthropic.resources.messages import Messages

from ethwall.contract import (
    TOOL_SPECS,
    ChatMessage,
    ModelRequest,
    Role,
    TextBlock,
    ToolName,
)
from ethwall.models import AnthropicActor

SDK_KEYWORDS = set(inspect.signature(Messages.create).parameters) - {"self"}


def _req(temperature: float) -> ModelRequest:
    return ModelRequest(
        system="sys",
        messages=(ChatMessage("user", (TextBlock("hi"),)),),
        tools=(TOOL_SPECS[ToolName.READ_DOCUMENT],),
        max_tokens=64,
        temperature=temperature,
        role=Role.MARKET,
        context_id="c",
        decision_index=0,
    )


def test_every_keyword_exists_in_the_installed_sdk():
    """R-MOD-01: build_kwargs only uses keywords the installed Messages.create accepts."""
    for t in (0.0, 0.7, 1.0):
        kwargs = AnthropicActor("m", client=object()).build_kwargs(_req(t))
        assert set(kwargs) <= SDK_KEYWORDS, set(kwargs) - SDK_KEYWORDS


def test_temperature_travels_in_the_body_only_when_not_default():
    """R-MOD-01: non-default temperature goes in extra_body; the API default is omitted."""
    actor = AnthropicActor("m", client=object())
    assert actor.build_kwargs(_req(0.0))["extra_body"] == {"temperature": 0.0}
    assert "extra_body" not in actor.build_kwargs(_req(1.0))
    assert "temperature" not in actor.build_kwargs(_req(0.0))
