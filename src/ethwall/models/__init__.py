"""Actor models (docs/specs/runtime.md R-MOD): the Anthropic adapter and mock policies."""

from ethwall.models.anthropic_actor import (
    MODEL_ENV,
    AnthropicActor,
    ModelConfigError,
    classify_error,
)
from ethwall.models.mock import MOCK_POLICIES, MockActor, mock_model

__all__ = [
    "MOCK_POLICIES",
    "MODEL_ENV",
    "AnthropicActor",
    "MockActor",
    "ModelConfigError",
    "classify_error",
    "mock_model",
]
