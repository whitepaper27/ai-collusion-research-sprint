"""Auditor runner (docs/specs/audit.md R-AUD-08; sol.md §10).

One call = one independent session for one trajectory x view: the frozen prompt file as the
system prompt and the export JSON as the only user message. No earlier judgments, no human
labels, no other views. Fixed generation settings (`AuditorSettings`). The model id comes only
from `AUDITOR_MODEL_ID` (`auditor_from_env`, reusing `AnthropicActor`).

Statuses:

- `ok`              the answer parsed: category is one of the six or `abstain`
- `invalid`         the single parse attempt failed (category `invalid`); never re-asked or
                    reworded
- `transport_error` a non-retryable failure, or a retryable one that failed again after the
                    one identical retry (category None: not an auditor answer)
- `spend_refused`   the ledger refused the reservation; no call was made (category None)

Spend: with a `SpendLedger` for batch `auditor`, each attempt reserves before the call and
settles on usage (missing usage keeps the reservation). Contract quirk: `ModelRequest.role`
must be a `Role`; auditor requests use `AUDITOR_ROLE` as a fixed placeholder (the adapter
ignores it).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ethwall.audit.rubric import (
    AUDITOR_CATEGORIES,
    VIOLATION_KEYS,
    normalize_violations,
)
from ethwall.contract import (
    AUDIT_INVALID,
    MAX_TRANSPORT_RETRIES,
    ActorModel,
    ChatMessage,
    ModelCallError,
    ModelRequest,
    ModelResponse,
    Role,
    TextBlock,
    canonical_json,
    sha256_hex,
    to_jsonable,
)
from ethwall.models import AnthropicActor, ModelConfigError
from ethwall.spend import Pricing, SpendLedger

AUDITOR_MODEL_ENV = "AUDITOR_MODEL_ID"
AUDITOR_BATCH = "auditor"
AUDITOR_PROMPT_PATH = (
    Path(__file__).resolve().parents[3] / "protocol" / "draft" / "auditor_prompt.md"
)
AUDITOR_CALL_CAP_USD = (
    0.25  # per trajectory x view session (contract has no auditor entry)
)
AUDITOR_ROLE = Role.MARKET  # placeholder required by ModelRequest; not sent to the API
ALLOWED_KEYS = frozenset({"category", "violations", "cited_event_ids", "rationale"})


@dataclass(frozen=True)
class AuditorSettings:
    """Fixed generation settings, frozen with the prompt after the development fixtures."""

    max_tokens: int = 1024
    temperature: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"max_tokens": self.max_tokens, "temperature": self.temperature}


@dataclass(frozen=True)
class AuditorResult:
    item_id: str
    view: str
    status: str
    category: str | None
    violations: Mapping[str, bool | None]
    cited_event_ids: tuple[str, ...]
    raw_text: str | None
    model_id: str
    prompt_sha256: str
    settings: Mapping[str, Any]
    attempts: int
    cost_usd: float = 0.0
    error: str | None = None
    rationale: str | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


def load_auditor_prompt(path: str | Path | None = None) -> tuple[str, str]:
    """(prompt text, sha256) of the frozen auditor prompt file."""
    text = Path(path or AUDITOR_PROMPT_PATH).read_text(encoding="utf-8")
    return text, sha256_hex(text)


def auditor_from_env(
    *, client: Any = None, clock: Callable[[], float] | None = None
) -> AnthropicActor:
    """The auditor model from `AUDITOR_MODEL_ID` (never a literal id, never ACTOR_MODEL_ID)."""
    model_id = os.environ.get(AUDITOR_MODEL_ENV, "").strip()
    if not model_id:
        raise ModelConfigError(
            f"{AUDITOR_MODEL_ENV} is not set: export the exact auditor model id"
        )
    return AnthropicActor(model_id, client=client, clock=clock)


def auditor_ledger(
    pricing: Pricing,
    root: str | Path = "runs",
    per_call_cap_usd: float = AUDITOR_CALL_CAP_USD,
) -> SpendLedger:
    """The `auditor` batch ledger at `<root>/audit/_spend.json`.

    It must live under the same `root` as the actor partition ledgers so the batch cap
    ($10) and the $50 total are enforced across all of them. Each trajectory x view session
    is one ledger "episode" (`begin_episode(context_id)`); re-running the same session adds
    to the same entry and its per-session cap.
    """
    return SpendLedger(
        Path(root) / "audit" / "_spend.json", AUDITOR_BATCH, per_call_cap_usd, pricing
    )


def parse_auditor_output(text: str) -> dict[str, Any] | None:
    """The one parse attempt: JSON object on the required schema, else None (= invalid).

    A single surrounding Markdown code fence is tolerated; nothing else is repaired.
    """
    s = text.strip()
    if s.startswith("```") and s.endswith("```") and "\n" in s:
        s = s[s.index("\n") + 1 : -3].strip()
    try:
        data = json.loads(s)
    except ValueError:
        return None
    if not isinstance(data, dict) or not set(data) <= ALLOWED_KEYS:
        return None
    if not {"category", "violations", "cited_event_ids"} <= set(data):
        return None
    category = data["category"]
    if category not in AUDITOR_CATEGORIES:
        return None
    try:
        violations = normalize_violations(data["violations"])
    except (TypeError, ValueError):
        return None
    cited = data["cited_event_ids"]
    if not isinstance(cited, list) or not all(isinstance(x, str) for x in cited):
        return None
    rationale = data.get("rationale")
    if rationale is not None and not isinstance(rationale, str):
        return None
    return {
        "category": category,
        "violations": violations,
        "cited_event_ids": tuple(cited),
        "rationale": rationale,
    }


def build_request(
    export: Mapping[str, Any], prompt_text: str, settings: AuditorSettings
) -> ModelRequest:
    """A fresh single-turn request: frozen prompt + this export, nothing else."""
    body = json.dumps(export, indent=1, ensure_ascii=False)
    return ModelRequest(
        system=prompt_text,
        messages=(ChatMessage("user", (TextBlock(body),)),),
        tools=(),
        max_tokens=settings.max_tokens,
        temperature=settings.temperature,
        role=AUDITOR_ROLE,
        context_id=f"audit:{export['trajectory_id']}:{export['view']}",
        decision_index=0,
    )


def _complete(model: ActorModel, req: ModelRequest) -> ModelResponse:
    """One transport attempt; any non-contract failure is a non-retryable call error."""
    try:
        return model.complete(req)
    except ModelCallError:
        raise
    except Exception as exc:
        raise ModelCallError(
            f"{type(exc).__name__}: {exc}", retryable=False, kind=type(exc).__name__
        ) from exc


def run_auditor(
    export: Mapping[str, Any],
    *,
    model: ActorModel,
    prompt_text: str,
    settings: AuditorSettings | None = None,
    ledger: Any = None,
) -> AuditorResult:
    """Run one independent auditor session on one exported view."""
    settings = settings or AuditorSettings()
    if ledger is not None and getattr(ledger, "batch", None) != AUDITOR_BATCH:
        raise ValueError(f"auditor spend must go to the {AUDITOR_BATCH!r} batch ledger")
    req = build_request(export, prompt_text, settings)
    base = {
        "item_id": str(export["trajectory_id"]),
        "view": str(export["view"]),
        "model_id": str(getattr(model, "model_id", "")),
        "prompt_sha256": sha256_hex(prompt_text),
        "settings": settings.to_dict(),
    }
    empty = {k: None for k in VIOLATION_KEYS}
    cost = 0.0

    def result(status: str, attempts: int, **kw: Any) -> AuditorResult:
        fields: dict[str, Any] = {
            "category": None,
            "violations": empty,
            "cited_event_ids": (),
            "raw_text": None,
            **kw,
        }
        return AuditorResult(
            **base, status=status, attempts=attempts, cost_usd=round(cost, 6), **fields
        )

    if ledger is not None:
        ledger.begin_episode(req.context_id)
    estimate = len(
        canonical_json(
            {"system": req.system, "messages": to_jsonable(req.messages)}
        ).encode("utf-8")
    )
    for attempt in range(1, MAX_TRANSPORT_RETRIES + 2):
        if ledger is not None and not ledger.reserve(estimate, settings.max_tokens):
            return result(
                "spend_refused", attempt - 1, error="reservation refused by cap"
            )
        try:
            resp: ModelResponse = _complete(model, req)
        except ModelCallError as err:
            if ledger is not None:
                cost += float(ledger.settle(None, None))
            if err.retryable and attempt <= MAX_TRANSPORT_RETRIES:
                continue
            return result("transport_error", attempt, error=f"{err.kind}: {err}")
        if ledger is not None:
            cost += float(ledger.settle(resp.input_tokens, resp.output_tokens))
        base["model_id"] = resp.model_id or base["model_id"]
        text = resp.text
        parsed = parse_auditor_output(text)
        if parsed is None:
            return result("invalid", attempt, category=AUDIT_INVALID, raw_text=text)
        return result(
            "ok",
            attempt,
            category=parsed["category"],
            violations=parsed["violations"],
            cited_event_ids=parsed["cited_event_ids"],
            rationale=parsed["rationale"],
            raw_text=text,
        )
    raise AssertionError("unreachable")


def append_prediction(path: str | Path, result: AuditorResult) -> None:
    """Append one auditor result as a JSON line (append-only predictions file)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(canonical_json(result.to_dict()) + "\n")


def read_predictions(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    return [
        json.loads(line)
        for line in target.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


__all__ = [
    "AUDITOR_BATCH",
    "AUDITOR_CALL_CAP_USD",
    "AUDITOR_MODEL_ENV",
    "AUDITOR_PROMPT_PATH",
    "AUDITOR_ROLE",
    "VIOLATION_KEYS",
    "AuditorResult",
    "AuditorSettings",
    "append_prediction",
    "auditor_from_env",
    "auditor_ledger",
    "build_request",
    "load_auditor_prompt",
    "parse_auditor_output",
    "read_predictions",
    "run_auditor",
    "sha256_hex",
]
