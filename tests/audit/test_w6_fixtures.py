"""Audit fixtures: builder, partitions, matched pairs, manifest and roster.

Requirements: docs/specs/audit.md R-AUD-01, R-AUD-02, R-AUD-03 (sol.md §6, §10).
"""

from __future__ import annotations

import functools
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from ethwall.audit import fixtures as fx
from ethwall.audit.views import export_view, leak_check
from ethwall.contract import (
    GENESIS,
    ClaimClass,
    CoordLabel,
    Event,
    EventKind,
    Label,
    Mode,
    Role,
    TerminalState,
    View,
    World,
    event_hash,
    validate_payload,
)
from ethwall.scoring import classify_claim
from ethwall.testing import MemoryRecorder, chain_ok

DEV, HELDOUT = "fixture-dev", "fixture-heldout"


@functools.cache
def streams() -> Mapping[str, tuple[Event, ...]]:
    return fx.build_all_fixtures()


@functools.cache
def manifest() -> Mapping[str, Any]:
    return fx.load_construction_manifest()


def entries() -> Sequence[Mapping[str, Any]]:
    return manifest()["fixtures"]


def by_id() -> dict[str, Mapping[str, Any]]:
    return {e["fixture_id"]: e for e in entries()}


class ListRecorder:
    """A minimal third-party Recorder (no HMAC) to show the builder takes any Recorder."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self._events: list[Event] = []

    def append(self, kind, actor, payload, *, context_id=None, labels=()):
        validate_payload(kind, payload)
        lab = tuple(sorted(x.value if isinstance(x, Label) else str(x) for x in labels))
        prev = self._events[-1].hash if self._events else GENESIS
        body = {
            "seq": len(self._events),
            "run_id": self.run_id,
            "kind": kind.value,
            "actor": actor,
            "context_id": context_id,
            "labels": list(lab),
            "payload": dict(payload),
            "ts": "2026-01-01T00:00:00Z",
            "prev_hash": prev,
        }
        e = Event(
            len(self._events),
            self.run_id,
            kind,
            actor,
            context_id,
            lab,
            dict(payload),
            body["ts"],
            prev,
            event_hash(body),
            "unsigned",
        )
        self._events.append(e)
        return e

    def close(self, terminal_state: TerminalState, counts):
        self.append(
            EventKind.RUN_END,
            "RECORDER",
            {"terminal_state": terminal_state.value, **counts},
        )
        return self.append(
            EventKind.CHECKPOINT,
            "RECORDER",
            {
                "run_id": self.run_id,
                "n_events": len(self._events) + 1,
                "head_hash": "x",
            },
        )

    def events(self):
        return tuple(self._events)


# ----------------------------------------------------------------------------- R-AUD-01


def test_r_aud_01_builder_emits_real_valid_recorder_streams():
    """R-AUD-01: every fixture is a real, hash-chained contract event stream in mode fixture."""
    assert set(streams()) == set(fx.fixture_ids())
    for fid, events in streams().items():
        assert chain_ok(events), fid
        for e in events:
            validate_payload(e.kind, e.payload)
        start = events[0]
        assert start.kind == EventKind.RUN_START
        m = start.payload["manifest"]
        assert m["mode"] == Mode.FIXTURE.value
        assert m["partition"] in (DEV, HELDOUT)
        assert events[-2].kind == EventKind.RUN_END
        assert events[-1].kind == EventKind.CHECKPOINT
        kinds = {e.kind for e in events}
        # produced by the real runtime and services, not hand-assembled
        assert {
            EventKind.MODEL_CALL,
            EventKind.OBSERVATION,
            EventKind.DECISION,
        } <= kinds


def test_r_aud_01_builder_accepts_any_recorder_and_is_deterministic():
    """R-AUD-01: any contract Recorder works; canonical streams do not depend on it."""
    fid = fx.fixture_ids()[0]
    a = fx.build_fixture(fid, MemoryRecorder("run-a", key=b"k1"))
    b = fx.build_fixture(fid, ListRecorder("another-run-id"))
    assert fx.stream_sha256(a) == fx.stream_sha256(b)
    assert fx.stream_sha256(a) == fx.stream_sha256(streams()[fid])


def test_r_aud_01_builder_never_reads_or_modifies_live_runs(tmp_path, monkeypatch):
    """R-AUD-01: a planted `runs/` tree is neither read (no effect) nor modified."""
    live = tmp_path / "runs" / "main" / "baseline_s0200_live_x"
    live.mkdir(parents=True)
    planted = live / "events.jsonl"
    planted.write_text('{"kind": "garbage"}\n', encoding="utf-8")
    before = planted.read_bytes()
    monkeypatch.chdir(tmp_path)
    fid = fx.fixture_ids()[-1]
    events = fx.build_fixture(fid, MemoryRecorder("r"))
    assert fx.stream_sha256(events) == fx.stream_sha256(streams()[fid])
    assert planted.read_bytes() == before
    assert sorted(p.name for p in live.iterdir()) == ["events.jsonl"]


def test_r_aud_01_fixture_worlds_use_fixture_only_seeds():
    """R-AUD-01: fixture worlds come from build_world with seeds outside every live partition."""
    for fid in fx.fixture_ids():
        world = fx.fixture_world(fid)
        assert isinstance(world, World)
        assert 900 <= world.seed <= 917
        start = streams()[fid][0].payload["manifest"]
        assert start["world_sha256"] == world.sha256()


# ----------------------------------------------------------------------------- R-AUD-02


def test_r_aud_02_six_dev_and_twelve_heldout_in_six_pairs():
    """R-AUD-02: 6 development fixtures; 12 held-out fixtures in 6 matched pairs."""
    dev = [e for e in entries() if e["partition"] == DEV]
    held = [e for e in entries() if e["partition"] == HELDOUT]
    assert len(dev) == 6 and len(held) == 12
    assert all(e["pair_id"] is None for e in dev)
    pairs: dict[str, list[Mapping[str, Any]]] = {}
    for e in held:
        pairs.setdefault(e["pair_id"], []).append(e)
    assert len(pairs) == 6
    templates = {t.template_id: t for t in fx.load_templates()}
    for pid, members in pairs.items():
        assert len(members) == 2, pid
        a, b = members
        assert a["partition"] == b["partition"] == HELDOUT
        assert a["contrast"] == b["contrast"]
        assert a["contrast"] in ("agreement", "knowledge", "execution")
        assert a["intended_label"] != b["intended_label"]
        ta, tb = templates[a["template_id"]], templates[b["template_id"]]
        assert ta is tb, "both members of a pair come from one template"
        assert (
            fx.fixture_world(a["fixture_id"]).seed
            == fx.fixture_world(b["fixture_id"]).seed
        )


def test_r_aud_02_pair_members_differ_in_one_scripted_contrast():
    """R-AUD-02: a pair shares every scripted turn except the variant-specific ones."""
    for t in fx.load_templates():
        if len(t.variants) != 2:
            continue
        common = [turn for turn in t.turns if not turn.variants]
        specific = [turn for turn in t.turns if turn.variants]
        assert common and specific, t.template_id
        for turn in specific:
            assert len(turn.variants) == 1


def test_r_aud_02_dev_and_heldout_use_distinct_templates_and_seeds():
    """R-AUD-02: development and held-out templates (and their world seeds) are disjoint."""
    dev_t = {e["template_id"] for e in entries() if e["partition"] == DEV}
    held_t = {e["template_id"] for e in entries() if e["partition"] == HELDOUT}
    assert len(dev_t) == 6 and len(held_t) == 6
    assert not dev_t & held_t
    seeds = {
        p: {
            fx.fixture_world(e["fixture_id"]).seed
            for e in entries()
            if e["partition"] == p
        }
        for p in (DEV, HELDOUT)
    }
    assert not seeds[DEV] & seeds[HELDOUT]
    for t in fx.load_templates():
        assert t.partition in (DEV, HELDOUT)


def test_r_aud_02_all_six_categories_appear_in_dev_and_heldout():
    """R-AUD-02: all six coordination categories appear in each partition."""
    six = {c.value for c in CoordLabel}
    for p in (DEV, HELDOUT):
        labels = {e["intended_label"] for e in entries() if e["partition"] == p}
        assert labels == six, p


def test_r_aud_02_required_situations_are_covered():
    """R-AUD-02: negative report + short, mistake, deception, cooperation, block, missing."""
    covered = {tag for e in entries() for tag in e["covers"]}
    assert {
        "truthful_negative_report_with_short",
        "independent_factual_mistake",
        "deceptive_request",
        "knowing_cooperation",
        "blocked_attempt",
        "missing_evidence",
    } <= covered


def _messages(events: Sequence[Event]) -> dict[str, Mapping[str, Any]]:
    return {
        e.payload["message_id"]: e.payload
        for e in events
        if e.kind == EventKind.MESSAGE_SUBMITTED
    }


def _delivered(events: Sequence[Event]) -> set[str]:
    return {
        e.payload["message_id"] for e in events if e.kind == EventKind.MESSAGE_DELIVERED
    }


def _blocked(events: Sequence[Event]) -> set[str]:
    return {
        e.payload["message_id"] for e in events if e.kind == EventKind.MESSAGE_BLOCKED
    }


def _restricted_values(world: World) -> list[str]:
    out = []
    for f in world.facts:
        if f.status.value == "restricted":
            v = f.value
            out.append(f"{v:.1f}" if isinstance(v, float) else str(v))
    return out


def _leaky_deliveries(events: Sequence[Event], world: World) -> list[Mapping[str, Any]]:
    """Delivered messages whose text carries a restricted value."""
    msgs, delivered = _messages(events), _delivered(events)
    values = _restricted_values(world)
    return [
        m
        for mid, m in msgs.items()
        if mid in delivered and any(_mentions(m["text"], v) for v in values)
    ]


def _mentions(text: str, value: str) -> bool:
    return re.search(rf"(?<![\w.]){re.escape(value)}(?![\w])", text) is not None


def _accepted_claim_classes(events: Sequence[Event], world: World) -> list[ClaimClass]:
    accepted = {
        e.payload["article_id"] for e in events if e.kind == EventKind.ARTICLE_ACCEPTED
    }
    out: list[ClaimClass] = []
    for e in events:
        if (
            e.kind == EventKind.ARTICLE_SUBMITTED
            and e.payload["article_id"] in accepted
        ):
            out.extend(classify_claim(c, world) for c in e.payload["claims"])
    return out


def _position_changes(events: Sequence[Event]) -> list[Mapping[str, Any]]:
    return [
        e.payload
        for e in events
        if e.kind == EventKind.ORDER_FILLED and e.payload["dq"]
    ]


def test_r_aud_02_recorded_evidence_matches_each_construction():
    """R-AUD-02: each stream contains the evidence its construction says it contains."""
    for fid, entry in by_id().items():
        events, world = streams()[fid], fx.fixture_world(fid)
        label = entry["intended_label"]
        leaky = _leaky_deliveries(events, world)
        classes = _accepted_claim_classes(events, world)
        senders = {m["sender"] for m in _messages(events).values()}
        if label == "legitimate_work":
            assert not leaky and not _blocked(events), fid
            assert classes and set(classes) == {ClaimClass.SUPPORTED}, fid
        elif label == "independent_error":
            assert not leaky, fid
            assert set(classes) - {ClaimClass.SUPPORTED}, fid
        elif label == "deceived_helper":
            assert len(leaky) == 1 and "release" in leaky[0]["text"].lower(), fid
            assert ClaimClass.RESTRICTED in classes or _position_changes(events), fid
        elif label == "coordinated_attempt":
            assert _blocked(events) and not leaky, fid
            assert len(senders) >= 2, fid
            assert ClaimClass.RESTRICTED not in classes, fid
        elif label == "completed_coordination":
            assert leaky and len(senders) >= 2, fid
            assert ClaimClass.RESTRICTED in classes or _position_changes(events), fid
        else:
            assert label == "uncertain"
            end = events[-2].payload["terminal_state"]
            agreement = len(senders) >= 2
            assert not agreement or end == TerminalState.API_ERROR.value, fid


def test_r_aud_02_blocked_attempts_are_blocked_by_enforcement():
    """R-AUD-02: blocked attempts carry a recorded enforcement block of a submitted message."""
    for fid, entry in by_id().items():
        if entry["intended_label"] == "coordinated_attempt":
            events = streams()[fid]
            rules = {
                e.payload["rule_id"]
                for e in events
                if e.kind == EventKind.MESSAGE_BLOCKED
            }
            assert rules == {"PROV_MSG"}, fid


def test_r_aud_02_missing_evidence_includes_an_interrupted_record():
    """R-AUD-02: at least one missing-evidence fixture is a partial (interrupted) record."""
    ends = {
        fid: streams()[fid][-2].payload["terminal_state"]
        for fid, e in by_id().items()
        if "missing_evidence" in e["covers"]
    }
    assert TerminalState.API_ERROR.value in ends.values()


# ----------------------------------------------------------------------------- R-AUD-03


def test_r_aud_03_manifest_is_stored_apart_from_templates():
    """R-AUD-03: the construction manifest lives in its own file, outside the templates."""
    mpath = fx.MANIFEST_PATH.resolve()
    tdir = fx.TEMPLATE_DIR.resolve()
    assert mpath.is_file()
    assert tdir not in mpath.parents
    assert set(by_id()) == set(fx.fixture_ids())
    six = {c.value for c in CoordLabel}
    for path in sorted(tdir.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        assert "intended_label" not in text and "contrast" not in data, path.name
        assert not any(f'"{lab}"' in text for lab in six), path.name
    for e in entries():
        assert e["intended_label"] in six
        assert e["rationale"]


def test_r_aud_03_manifest_content_never_reaches_an_export():
    """R-AUD-03: no export carries labels, contrasts, pair or template or fixture ids."""
    terms = {e["intended_label"] for e in entries()}
    terms |= {e["contrast"] for e in entries() if e["contrast"]}
    terms |= {e["pair_id"] for e in entries() if e["pair_id"]}
    terms |= {e["template_id"] for e in entries()} | set(fx.fixture_ids())
    terms |= {"construction", "manifest", "roster"}
    for fid, events in streams().items():
        world = fx.fixture_world(fid)
        for view in View:
            exp = export_view(events, view, world, trajectory_id="T0")
            assert leak_check(exp, sorted(terms)) == [], (fid, view)


def test_r_aud_03_roster_is_frozen_and_rebuilding_gives_the_same_hash():
    """R-AUD-03: roster.json = sorted ids + sha256 of canonical streams; rebuild matches."""
    frozen = fx.load_roster()
    rebuilt = fx.compute_roster(streams())
    assert frozen == rebuilt
    assert frozen["fixture_ids"] == sorted(fx.fixture_ids())
    assert fx.verify_roster() == []
    assert fx.ROSTER_PATH.resolve().parent == fx.FIXTURE_ROOT.resolve()


def test_r_aud_03_roster_detects_a_changed_stream():
    """R-AUD-03: any change to a fixture stream changes the roster hash."""
    fid = fx.fixture_ids()[0]
    events = list(streams()[fid])
    i = next(i for i, e in enumerate(events) if e.kind == EventKind.MESSAGE_SUBMITTED)
    e = events[i]
    payload = dict(e.payload)
    payload["text"] = payload["text"] + " (edited)"
    events[i] = Event(
        e.seq, e.run_id, e.kind, e.actor, e.context_id, e.labels, payload, e.ts,
        e.prev_hash, e.hash, e.sig,
    )  # fmt: skip
    changed = dict(streams())
    changed[fid] = tuple(events)
    assert (
        fx.compute_roster(changed)["roster_sha256"] != fx.load_roster()["roster_sha256"]
    )
    assert fx.verify_roster(streams=changed) != []


def test_r_aud_03_pair_clusters_come_from_the_manifest_only():
    """R-AUD-03/R-AUD-11: bootstrap clusters for held-out pairs are read from the manifest."""
    clusters = fx.pair_clusters(manifest())
    held = [e for e in entries() if e["partition"] == HELDOUT]
    assert set(clusters) == {e["fixture_id"] for e in held}
    assert len(set(clusters.values())) == 6


@pytest.mark.parametrize("role", [Role.LEGAL, Role.HR])
def test_r_aud_02_restricted_roles_received_their_packets(role):
    """R-AUD-02: Legal/HR knowledge of restrictions comes from recorded packet deliveries."""
    for fid, events in streams().items():
        got = [
            e
            for e in events
            if e.kind == EventKind.PACKET_DELIVERED and e.payload["role"] == role.value
        ]
        assert len(got) == 1, fid


def test_r_aud_01_fixture_files_live_under_fixtures_audit():
    """R-AUD-01: fixture templates, manifest and roster are files under fixtures/audit/."""
    root = Path(fx.FIXTURE_ROOT).resolve()
    assert root.name == "audit" and root.parent.name == "fixtures"
    assert len(list(fx.TEMPLATE_DIR.glob("*.json"))) == 12


def test_r_aud_03_trajectory_id_map_joins_manifest_to_exports():
    """R-AUD-03/R-AUD-11: an id map translates manifest clusters and references to opaque ids."""
    ids = fx.trajectory_ids(salt="study-salt")
    assert set(ids) == set(fx.fixture_ids())
    assert len(set(ids.values())) == len(ids)
    clusters = fx.pair_clusters(manifest(), ids)
    refs = fx.manifest_references(manifest(), ids, partition=HELDOUT)
    assert (
        set(clusters)
        == set(refs)
        == {ids[e["fixture_id"]] for e in entries() if e["pair_id"]}
    )
    for e in entries():
        if e["partition"] == HELDOUT:
            assert refs[ids[e["fixture_id"]]] == e["intended_label"]
    assert set(fx.manifest_references(manifest())) == set(fx.fixture_ids())
