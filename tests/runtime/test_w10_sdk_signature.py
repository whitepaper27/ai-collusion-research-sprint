"""R-MOD-01: the adapter's request must match the INSTALLED Anthropic SDK and the live API.

The first paid run found two drifts the fake-client tests missed: `temperature` is no longer a
keyword of `Messages.create` in the installed SDK, and the live API rejects it in the body.
These tests pin every keyword against the real signature and forbid sampling parameters.
"""

import inspect

import pytest
from anthropic.resources.messages import Messages

from ethwall.audit import AuditorSettings
from ethwall.contract import (
    TOOL_SPECS,
    ChatMessage,
    ModelRequest,
    Role,
    TextBlock,
    ToolName,
)
from ethwall.models import AnthropicActor
from ethwall.models.anthropic_actor import ModelConfigError

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
    kwargs = AnthropicActor("m", client=object()).build_kwargs(_req(1.0))
    assert set(kwargs) <= SDK_KEYWORDS, set(kwargs) - SDK_KEYWORDS


def test_no_sampling_parameter_is_ever_sent():
    """R-MOD-01: neither a temperature keyword nor a body field reaches the API."""
    kwargs = AnthropicActor("m", client=object()).build_kwargs(_req(1.0))
    assert "temperature" not in kwargs
    assert "temperature" not in kwargs.get("extra_body", {})


def test_a_non_default_temperature_is_refused_before_any_call():
    """R-MOD-01: a setting the API can't honour fails fast instead of being silently dropped."""
    with pytest.raises(ModelConfigError):
        AnthropicActor("m", client=object()).build_kwargs(_req(0.0))


def test_auditor_default_settings_are_applicable_live():
    """R-AUD-08: the frozen auditor settings are ones the live adapter can actually apply."""
    s = AuditorSettings()
    AnthropicActor("m", client=object()).build_kwargs(_req(s.temperature))
    assert s.to_dict()["temperature"] == 1.0
