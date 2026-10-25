"""Audit fixture builder (docs/specs/audit.md R-AUD-01..03; sol.md §6, §10).

A fixture is a short *scripted* trajectory recorded as a real event stream. The builder runs
the real runtime (`run_episode`, mode `fixture`) with the real services and enforcement on a
real world (`build_world`, fixture-only seeds 900-917), driven by a deterministic script actor
instead of a model. So every event (observations, deliveries, decisions, blocks, fills) has
exactly the shape a live run would produce. The builder never reads or writes `runs/`.

Files (under `fixtures/audit/`):

- `templates/*.json` - the scripted turns, one file per template. A template has one variant
  (development) or two (a held-out matched pair); variant-specific turns carry `variants`.
  Templates hold no labels or contrasts.
- `manifest/construction.json` - the construction manifest (intended reference label, the
  contrast a pair tests, pair id, rationale). It is stored apart, read only by analysis code
  and never by the builder or the exporters (R-AUD-03).
- `roster.json` - the frozen roster: sorted fixture ids and the sha256 of each canonical
  event stream (R-AUD-03).

Template arguments are JSON with `{placeholders}` filled from the world (doc ids, restricted
values; see `world_vars`). Special forms: `{"$claims": {...}}` (claims built from registry
facts) and `{"$order": {...}}` (place_order args whose reported position and cash are
computed from the service ledger at call time, so they reconcile).

The canonical stream used for hashing drops recorder-specific fields (run id, timestamp,
hashes, signature, checkpoint head), so any contract Recorder gives the same roster hash.

Fixture authorship and any artificial agreement are disclosed: fixtures validate auditing and
cannot establish spontaneous model behavior (sol.md §6).
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ethwall.contract import (
    FEE_RATE,
    MAX_TOOL_CALLS_PER_RESPONSE,
    SCHEDULE,
    Condition,
    EpisodeConfig,
    Event,
    EventKind,
    Mode,
    ModelCallError,
    ModelRequest,
    ModelResponse,
    Recorder,
    Role,
    TextBlock,
    ToolName,
    ToolUseBlock,
    World,
    canonical_json,
    sha256_hex,
)
from ethwall.enforcement import make_policy
from ethwall.runtime import run_episode
from ethwall.services import make_services
from ethwall.world import build_world

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = REPO_ROOT / "fixtures" / "audit"
TEMPLATE_DIR = FIXTURE_ROOT / "templates"
MANIFEST_PATH = FIXTURE_ROOT / "manifest" / "construction.json"
ROSTER_PATH = FIXTURE_ROOT / "roster.json"

DEV_PARTITION = "fixture-dev"
HELDOUT_PARTITION = "fixture-heldout"
PARTITIONS = (DEV_PARTITION, HELDOUT_PARTITION)
FIXTURE_SEEDS = range(900, 918)
FIXTURE_MODEL_ID = "scripted-trajectory"
IDLE_TEXT = "Nothing further this turn."
ROSTER_SCHEMA = 1


# ----------------------------------------------------------------------------- templates


@dataclass(frozen=True)
class Turn:
    round: int
    role: Role
    calls: tuple[Mapping[str, Any], ...]
    variants: tuple[str, ...] = ()  # empty = every variant
    fail: bool = False  # a non-retryable transport failure ends the record (API_ERROR)


@dataclass(frozen=True)
class Variant:
    key: str
    fixture_id: str
    condition: Condition


@dataclass(frozen=True)
class Template:
    template_id: str
    partition: str
    seed: int
    decisions: int
    variants: Mapping[str, Variant]
    turns: tuple[Turn, ...]
    sha256: str

    def turns_for(self, variant: str) -> tuple[Turn, ...]:
        return tuple(t for t in self.turns if not t.variants or variant in t.variants)


class TemplateError(ValueError):
    """A fixture template is malformed."""


def _parse_template(path: Path) -> Template:
    raw = json.loads(path.read_text(encoding="utf-8"))
    tid = raw["template_id"]
    if raw["partition"] not in PARTITIONS:
        raise TemplateError(f"{tid}: partition must be one of {PARTITIONS}")
    seed = int(raw["seed"])
    if seed not in FIXTURE_SEEDS:
        raise TemplateError(f"{tid}: seed {seed} is not a fixture-only seed")
    variants = {
        key: Variant(key, v["fixture_id"], Condition(v["condition"]))
        for key, v in raw["variants"].items()
    }
    if len(variants) not in (1, 2):
        raise TemplateError(f"{tid}: a template has one variant or a matched pair")
    decisions = int(raw["decisions"])
    turns: list[Turn] = []
    for t in raw["turns"]:
        turn = Turn(
            round=int(t["round"]),
            role=Role(t["role"]),
            calls=tuple(t.get("calls", ())),
            variants=tuple(t.get("variants", ())),
            fail=bool(t.get("fail", False)),
        )
        if not set(turn.variants) <= set(variants):
            raise TemplateError(f"{tid}: unknown variant in {turn}")
        slot = (turn.round - 1) * len(SCHEDULE) + SCHEDULE.index(turn.role)
        if slot >= decisions:
            raise TemplateError(
                f"{tid}: turn {turn.round}/{turn.role.value} is past the cap"
            )
        for c in turn.calls:
            ToolName(c["tool"])
        turns.append(turn)
    tpl = Template(
        tid,
        raw["partition"],
        seed,
        decisions,
        variants,
        tuple(turns),
        sha256_hex(canonical_json(raw)),
    )
    for key in variants:
        per_slot: dict[tuple[int, Role], int] = {}
        for t in tpl.turns_for(key):
            per_slot[(t.round, t.role)] = per_slot.get((t.round, t.role), 0) + len(
                t.calls
            )
        if any(n > MAX_TOOL_CALLS_PER_RESPONSE for n in per_slot.values()):
            raise TemplateError(f"{tid}/{key}: more than 3 calls in one response")
    return tpl


@functools.cache
def _templates(directory: str) -> tuple[Template, ...]:
    paths = sorted(Path(directory).glob("*.json"))
    out = tuple(_parse_template(p) for p in paths)
    ids = [v.fixture_id for t in out for v in t.variants.values()]
    if len(ids) != len(set(ids)):
        raise TemplateError("duplicate fixture ids across templates")
    return out


def load_templates(directory: str | Path | None = None) -> tuple[Template, ...]:
    """Every fixture template, sorted by file name."""
    return _templates(str(Path(directory) if directory is not None else TEMPLATE_DIR))


def _variant_of(fixture_id: str) -> tuple[Template, Variant]:
    for t in load_templates():
        for v in t.variants.values():
            if v.fixture_id == fixture_id:
                return t, v
    raise KeyError(f"unknown fixture {fixture_id!r}")


def fixture_ids() -> tuple[str, ...]:
    """All fixture ids (development and held-out), sorted."""
    return tuple(
        sorted(v.fixture_id for t in load_templates() for v in t.variants.values())
    )


@functools.cache
def _world(seed: int) -> World:
    return build_world(seed)


def fixture_world(fixture_id: str) -> World:
    """The real seeded world a fixture is built on (both members of a pair share it)."""
    return _world(_variant_of(fixture_id)[0].seed)


# ----------------------------------------------------------------------------- world values


def _render(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


_FACT_VARS = {
    "revenue": "F-rev",
    "growth": "F-growth",
    "units": "F-issue-units",
    "rating": "F-rating",
    "hiring": "F-hiring",
    "issue_status": "F-issue-status",
    "complaint_status": "F-complaint-status",
    "damages": "F-damages",
    "contract_value": "F-contract-value",
    "counterparty": "F-counterparty",
    "headcount": "F-cuts",
    "restructuring_status": "F-restructuring-status",
}


def world_vars(world: World) -> dict[str, str]:
    """Placeholder values for templates: `<slug>_doc` ids, fact values, release id."""
    out: dict[str, str] = {
        "company_id": world.company_id,
        "instrument": world.instrument_id,
        "release_id": world.release_records[0].record_id,
    }
    for d in world.documents:
        out[f"{d.doc_id.split('-')[1]}_doc"] = d.doc_id
    facts = {f.fact_id: f for f in world.facts}
    for var, fact_id in _FACT_VARS.items():
        out[var] = _render(facts[fact_id].value)
    return out


# ----------------------------------------------------------------------------- script actor


class _FixturePrompts:
    """Neutral stand-in for the actor prompt set: fixtures are authored, not prompted.

    Keeps fixture streams independent of edits to `protocol/draft/prompts/`.
    """

    text = (
        "Scripted audit trajectory. The turns of this role are authored, not generated."
    )
    sha256 = sha256_hex(text)

    def system(self, role: Role, control: bool = False) -> str:
        return f"{self.text} Role: {Role(role).value}."

    def budget_notice(self) -> str:
        return "Tool-call limit notice."


class _ScriptActor:
    """Deterministic ActorModel replaying one template variant (never touches the network)."""

    model_id = FIXTURE_MODEL_ID
    mode = Mode.FIXTURE

    def __init__(self, template: Template, variant: str, world: World, services: Any):
        self._world = world
        self._vars = world_vars(world)
        self._facts = {f.fact_id: f for f in world.facts}
        self._services = services
        self._turns: dict[tuple[int, Role], list[Turn]] = {}
        for t in template.turns_for(variant):
            self._turns.setdefault((t.round, t.role), []).append(t)

    def complete(self, req: ModelRequest) -> ModelResponse:
        rnd = req.decision_index // len(SCHEDULE) + 1
        turns = self._turns.get((rnd, req.role), [])
        if any(t.fail for t in turns):
            raise ModelCallError("connection reset", retryable=False, kind="connection")
        calls = [c for t in turns for c in t.calls]
        if not calls:
            return ModelResponse(
                (TextBlock(IDLE_TEXT),), "end_turn", 0, 0, 0.0, self.model_id
            )
        blocks = tuple(
            ToolUseBlock(
                f"c{req.decision_index:02d}-{i}", str(c["tool"]), self._args(c["args"])
            )
            for i, c in enumerate(calls, start=1)
        )
        return ModelResponse(blocks, "tool_use", 0, 0, 0.0, self.model_id)

    # -- argument resolution

    def _args(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        if "$order" in spec:
            return self._order(spec["$order"])
        resolved = self._resolve(spec)
        assert isinstance(resolved, dict)
        return resolved

    def _resolve(self, spec: Any) -> Any:
        if isinstance(spec, str):
            return spec.format_map(self._vars)
        if isinstance(spec, list):
            return [self._resolve(x) for x in spec]
        if isinstance(spec, dict):
            if "$claims" in spec:
                return self._claims(spec["$claims"])
            return {k: self._resolve(v) for k, v in spec.items()}
        return spec

    def _claim(self, spec: str | Mapping[str, Any]) -> dict[str, Any]:
        s: Mapping[str, Any] = {"fact": spec} if isinstance(spec, str) else spec
        f = self._facts[s["fact"]]
        value: Any = f.value
        if "value" in s:
            value = self._resolve(s["value"])
        if "value_add" in s:
            value = f.value + s["value_add"]
        sources = (
            [self._resolve(x) for x in s["sources"]]
            if "sources" in s
            else [f.source_doc_id]
        )
        return {
            "entity_id": f.entity_id,
            "predicate": f.predicate,
            "value": value,
            "qualifier": s.get("qualifier", f.qualifier),
            "source_doc_ids": sources,
        }

    def _claims(self, spec: Mapping[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if spec.get("base") == "reference":
            replace = spec.get("replace", {})
            for f in self._world.reporting_facts():
                out.append(
                    self._claim({"fact": f.fact_id, **replace.get(f.fact_id, {})})
                )
        out.extend(self._claim(x) for x in spec.get("extra", ()))
        return out

    def _order(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        book = self._services.ledger()
        position, cash, price = (
            int(book["position"]),
            float(book["cash"]),
            float(book["price"]),
        )
        action = spec["action"]
        target = {"hold": position, "close": 0}.get(action, int(spec.get("target", 0)))
        notional = (target - position) * price
        new_cash = cash - notional - FEE_RATE * abs(notional)
        return {
            "action": action,
            "target_position": target,
            "rationale_claims": [self._claim(x) for x in spec.get("rationale", ())],
            "reported_position": target,
            "reported_cash": round(new_cash, 2),
        }


# ----------------------------------------------------------------------------- building


def build_fixture(fixture_id: str, recorder: Recorder) -> tuple[Event, ...]:
    """Record one fixture into `recorder` (any contract Recorder) and return its events."""
    template, variant = _variant_of(fixture_id)
    world = fixture_world(fixture_id)
    services = make_services(world, make_policy(variant.condition), recorder)
    config = EpisodeConfig(
        condition=variant.condition,
        seed=template.seed,
        mode=Mode.FIXTURE,
        partition=template.partition,
        model_id=FIXTURE_MODEL_ID,
        decisions=template.decisions,
    )
    manifest = {
        "fixture_id": fixture_id,
        "template_id": template.template_id,
        "variant": variant.key,
        "template_sha256": template.sha256,
        "mode": Mode.FIXTURE.value,
        "partition": template.partition,
        "condition": variant.condition.value,
        "seed": template.seed,
        "model_id": FIXTURE_MODEL_ID,
        "decisions": template.decisions,
        "world_sha256": world.sha256(),
        "prompts_sha256": _FixturePrompts.sha256,
        "authorship": "scripted audit trajectory (not model behavior)",
    }
    run_episode(
        config,
        world=world,
        model=_ScriptActor(template, variant.key, world, services),
        services=services,
        recorder=recorder,
        prompts=_FixturePrompts(),
        manifest=manifest,
    )
    return recorder.events()


def _memory_recorder(run_id: str) -> Recorder:
    from ethwall.testing import MemoryRecorder

    return MemoryRecorder(run_id)


def build_all_fixtures(
    recorder_factory: Callable[[str], Recorder] | None = None,
) -> dict[str, tuple[Event, ...]]:
    """Build every fixture; `recorder_factory(fixture_id)` supplies each recorder."""
    factory = recorder_factory or _memory_recorder
    return {fid: build_fixture(fid, factory(fid)) for fid in fixture_ids()}


# ----------------------------------------------------------------------------- roster


def canonical_stream(events: Sequence[Event]) -> str:
    """Recorder-independent canonical JSON of a stream (no run id, ts, hashes or signature)."""
    rows = []
    for e in events:
        payload = dict(e.payload)
        if e.kind == EventKind.CHECKPOINT:
            payload.pop("run_id", None)
            payload.pop("head_hash", None)
        rows.append(
            {
                "seq": e.seq,
                "kind": e.kind.value,
                "actor": e.actor,
                "context_id": e.context_id,
                "labels": list(e.labels),
                "payload": payload,
            }
        )
    return canonical_json(rows)


def stream_sha256(events: Sequence[Event]) -> str:
    return sha256_hex(canonical_stream(events))


def compute_roster(streams: Mapping[str, Sequence[Event]]) -> dict[str, Any]:
    """The frozen roster: sorted fixture ids, per-stream hashes and one roster hash."""
    ids = sorted(streams)
    hashes = {fid: stream_sha256(streams[fid]) for fid in ids}
    body = {"fixture_ids": ids, "streams": hashes}
    return {
        "schema": ROSTER_SCHEMA,
        **body,
        "roster_sha256": sha256_hex(canonical_json(body)),
    }


def write_roster(
    path: str | Path | None = None,
    streams: Mapping[str, Sequence[Event]] | None = None,
) -> dict[str, Any]:
    """Freeze the roster to `roster.json` (call once, before any auditor testing)."""
    roster = compute_roster(streams if streams is not None else build_all_fixtures())
    target = Path(path) if path is not None else ROSTER_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(roster, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    return roster


def load_roster(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else ROSTER_PATH
    return json.loads(target.read_text(encoding="utf-8"))


def verify_roster(
    path: str | Path | None = None,
    streams: Mapping[str, Sequence[Event]] | None = None,
) -> list[str]:
    """Problems found when rebuilding against the frozen roster ([] = matches)."""
    frozen = load_roster(path)
    rebuilt = compute_roster(streams if streams is not None else build_all_fixtures())
    problems = []
    if frozen.get("fixture_ids") != rebuilt["fixture_ids"]:
        problems.append("fixture ids differ from the frozen roster")
    for fid, digest in rebuilt["streams"].items():
        if frozen.get("streams", {}).get(fid) != digest:
            problems.append(f"{fid}: stream hash differs from the frozen roster")
    if frozen.get("roster_sha256") != rebuilt["roster_sha256"]:
        problems.append("roster hash differs from the frozen roster")
    return problems


# ----------------------------------------------------------------------------- manifest


def load_construction_manifest(path: str | Path | None = None) -> dict[str, Any]:
    """The hidden construction manifest (intended labels, contrasts, pairs). Never exported."""
    target = Path(path) if path is not None else MANIFEST_PATH
    return json.loads(target.read_text(encoding="utf-8"))


def trajectory_ids(salt: str) -> dict[str, str]:
    """{fixture id: opaque trajectory id} for exports, predictions and labels.

    Exports, auditor predictions and human labels are keyed by the opaque trajectory id;
    the construction manifest is keyed by fixture id. This map joins them. Like the manifest,
    keep it (and the salt) apart from anything shown to the auditor.
    """
    from ethwall.audit.views import opaque_trajectory_id

    return {fid: opaque_trajectory_id(fid, salt=salt) for fid in fixture_ids()}


def _key(fixture_id: str, id_map: Mapping[str, str] | None) -> str:
    return id_map[fixture_id] if id_map is not None else fixture_id


def pair_clusters(
    manifest: Mapping[str, Any], id_map: Mapping[str, str] | None = None
) -> dict[str, str]:
    """{held-out item: pair id} - the bootstrap unit for held-out fixtures (R-AUD-11).

    Items are fixture ids, or trajectory ids when `id_map` (see `trajectory_ids`) is given.
    """
    return {
        _key(e["fixture_id"], id_map): e["pair_id"]
        for e in manifest["fixtures"]
        if e["partition"] == HELDOUT_PARTITION
    }


def manifest_references(
    manifest: Mapping[str, Any],
    id_map: Mapping[str, str] | None = None,
    partition: str | None = None,
) -> dict[str, str]:
    """{item: intended label} from the construction manifest (analysis only, never exported)."""
    return {
        _key(e["fixture_id"], id_map): e["intended_label"]
        for e in manifest["fixtures"]
        if partition is None or e["partition"] == partition
    }


__all__ = [
    "DEV_PARTITION",
    "FIXTURE_MODEL_ID",
    "FIXTURE_ROOT",
    "HELDOUT_PARTITION",
    "MANIFEST_PATH",
    "ROSTER_PATH",
    "TEMPLATE_DIR",
    "Template",
    "TemplateError",
    "Turn",
    "Variant",
    "build_all_fixtures",
    "build_fixture",
    "canonical_stream",
    "compute_roster",
    "fixture_ids",
    "fixture_world",
    "load_construction_manifest",
    "load_templates",
    "manifest_references",
    "pair_clusters",
    "stream_sha256",
    "trajectory_ids",
    "verify_roster",
    "world_vars",
    "write_roster",
]
