"""ethwall command line (docs/specs/cli-integration.md).

    ethwall run --condition baseline,guarded --seeds 0-2 --mode mock --policy honest \
                --partition mock-demo
    ethwall verify --partition mock-demo
    ethwall analyze --partition mock-demo
    ethwall freeze create|validate --id <freeze_id>
    ethwall doctor
    ethwall export-review | label | audit   (audit study; see `ethwall <cmd> -h`)

Arms and control kinds (docs/specs/incentive-arm.md R-INC-10): `run --arm core|incentive`
(default core) and `--control-kind none|instructed_harmful|lawful_handoff` (default
instructed_harmful with `--control`, else none; a kind other than none needs `--control`).
Live arms never share partitions: `*-incentive` and `control-lawful` (and their reruns) take
only `--arm incentive` (control-lawful: lawful_handoff; control-incentive: instructed_harmful),
every other live partition only core.

Live (paid) runs need `--confirm-spend`, ACTOR_MODEL_ID, ANTHROPIC_API_KEY,
ETHWALL_RECORDER_KEY and the ETHWALL_PRICE_* pricing basis; main partitions also need a valid
`--freeze`. Anything missing exits 2 before any model call. `doctor` reports presence only,
never values.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from ethwall.contract import (
    ARMS,
    CONTROL_KINDS,
    INTERRUPTED_STATES,
    LIVE_PARTITIONS,
    Condition,
    EpisodeConfig,
    Mode,
    TerminalState,
    new_run_id,
    to_jsonable,
)
from ethwall.recorder._manifest import default_repo_root

REPO_ROOT = default_repo_root()

LIVE_ENV = ("ACTOR_MODEL_ID", "ANTHROPIC_API_KEY", "ETHWALL_RECORDER_KEY")
PRICE_ENV = (
    "ETHWALL_PRICE_INPUT_PER_MTOK",
    "ETHWALL_PRICE_OUTPUT_PER_MTOK",
    "ETHWALL_PRICE_SOURCE",
    "ETHWALL_PRICE_DATE",
)
DOCTOR_ENV = (*LIVE_ENV, "AUDITOR_MODEL_ID", *PRICE_ENV)


class UsageError(Exception):
    """Refused before any model call (exit 2)."""


def parse_seeds(text: str) -> list[int]:
    seeds: list[int] = []
    for part in text.split(","):
        if "-" in part:
            lo, hi = part.split("-", 1)
            seeds.extend(range(int(lo), int(hi) + 1))
        elif part:
            seeds.append(int(part))
    return seeds


def _partition_dir(root: Path, partition: str) -> Path:
    return root / partition


def _run_dirs(root: Path, partition: str) -> list[Path]:
    base = _partition_dir(root, partition)
    if not base.is_dir():
        return []
    return sorted(
        p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_")
    )


# ----------------------------------------------------------------------------- run


def check_live_preconditions(args: argparse.Namespace, env) -> object:
    """Every live-mode refusal happens here, before any evidence or model client exists.

    Returns the validated pricing basis. Raises UsageError (exit 2) on anything missing or
    invalid: no --confirm-spend, unset/blank settings, unusable pricing, a main partition
    without --freeze, or a freeze that does not validate against the current tree.
    """
    if not args.confirm_spend:
        raise UsageError(
            "live mode spends money: pass --confirm-spend (owner's explicit go)"
        )
    missing = [k for k in (*LIVE_ENV, *PRICE_ENV) if not str(env.get(k) or "").strip()]
    if missing:
        raise UsageError(f"live mode needs these settings: {', '.join(missing)}")
    if args.partition.split("-")[0] == "main" and not args.freeze:
        raise UsageError("main partitions need --freeze <id>")
    from ethwall.spend import PricingError, load_pricing

    try:
        pricing = load_pricing(env)
    except PricingError as e:
        raise UsageError(f"pricing basis unusable: {e}") from e
    if args.freeze:
        from ethwall.freeze import validate_freeze

        mismatches = validate_freeze(args.freeze)
        if mismatches:
            raise UsageError(
                f"freeze {args.freeze!r} does not validate: " + "; ".join(mismatches)
            )
    return pricing


def exit_code_for(states: Sequence[TerminalState]) -> int:
    """0 if every episode ended without an interruption; 1 otherwise (R-CLI-10)."""
    return 1 if any(s in INTERRUPTED_STATES for s in states) else 0


def resolve_control_kind(args: argparse.Namespace) -> str:
    """The run's control kind: explicit `--control-kind`, else instructed_harmful with
    `--control`, else none. A kind other than none needs `--control`; `--control` with kind
    none is contradictory. Raises UsageError."""
    kind = args.control_kind or ("instructed_harmful" if args.control else "none")
    if kind != "none" and not args.control:
        raise UsageError(f"--control-kind {kind} needs --control")
    if args.control and kind == "none":
        raise UsageError("--control needs a control kind other than none")
    return kind


def cmd_run(args: argparse.Namespace) -> int:
    from ethwall.enforcement import make_policy
    from ethwall.models import AnthropicActor, mock_model
    from ethwall.models.anthropic_actor import ModelConfigError
    from ethwall.prompts import load_prompts
    from ethwall.recorder import (
        JsonlRecorder,
        build_manifest,
        new_run_dir,
        recorder_key,
    )
    from ethwall.runtime import run_episode
    from ethwall.services import make_services
    from ethwall.world import build_world
    from ethwall.world.incentive import live_partition_problem, world_from_manifest

    mode = Mode(args.mode)
    control_kind = resolve_control_kind(args)
    conditions = [Condition(c) for c in args.condition.split(",")]
    root = Path(args.runs_root)
    if expected_mode(args.partition) != mode:
        raise UsageError(
            f"partition {args.partition!r} is not a {mode.value} partition"
        )
    ledger = live_model = None
    if mode == Mode.LIVE:
        # Arms never share live partitions; checked before any client or ledger exists.
        problem = live_partition_problem(args.partition, args.arm, control_kind)
        if problem:
            raise UsageError(problem)
        pricing = check_live_preconditions(args, os.environ)
        try:
            live_model = AnthropicActor.from_env()
        except ModelConfigError as e:
            raise UsageError(str(e)) from e
        from ethwall.spend import SpendLedger

        ledger = SpendLedger.for_partition(args.partition, pricing, args.spend_root)
    elif not args.policy:
        raise UsageError("mock/fixture runs need --policy")
    prompts = load_prompts(arm=args.arm)
    key, key_id = recorder_key(mode)
    model_id = live_model.model_id if live_model else f"mock-{args.policy}"
    states: list[TerminalState] = []
    for seed in parse_seeds(args.seeds):
        for condition in conditions:
            config = EpisodeConfig(
                condition,
                seed,
                mode,
                args.partition,
                model_id,
                policy=args.policy if mode != Mode.LIVE else None,
                control=args.control,
                freeze_id=args.freeze,
                arm=args.arm,
                control_kind=control_kind,
            )
            try:
                config.validate()
                if mode == Mode.LIVE and args.freeze:
                    from ethwall.freeze import require_valid_freeze

                    # Re-checked per episode so code cannot drift mid-batch.
                    require_valid_freeze(config)
            except Exception as e:  # every refusal here is a usage error
                if states:
                    print(f"ethwall: stopping batch: {e}", file=sys.stderr)
                    return max(exit_code_for(states), 2)
                raise UsageError(str(e)) from e
            world = build_world(seed, args.arm)
            model = live_model or mock_model(args.policy, world)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            run_id = new_run_id(config, stamp, secrets.token_hex(2))
            manifest = build_manifest(
                config,
                world=world,
                prompts_sha256=prompts.sha256,
                run_id=run_id,
                freeze_id=args.freeze,
            )
            manifest["control"] = args.control
            manifest["recorder_key_id"] = key_id
            # Every later reader rebuilds the world from this manifest (R-INC-11); checked
            # before any run directory exists (runs/ is append-only).
            if world_from_manifest(manifest).sha256() != world.sha256():
                raise RuntimeError("manifest does not reconstruct the episode's world")
            run_dir = new_run_dir(root, args.partition, run_id)
            (run_dir / "manifest.json").write_text(
                json.dumps(manifest, sort_keys=True, indent=1), encoding="utf-8"
            )
            recorder = JsonlRecorder(run_dir, run_id, key, key_id)
            result = run_episode(
                config,
                world=world,
                model=model,
                services=make_services(
                    world, make_policy(condition), recorder, arm=args.arm
                ),
                recorder=recorder,
                prompts=prompts,
                ledger=ledger,
                manifest=manifest,
            )
            (run_dir / "result.json").write_text(
                json.dumps(to_jsonable(result), sort_keys=True, indent=1),
                encoding="utf-8",
            )
            states.append(result.terminal_state)
            print(
                f"{run_id}  {result.terminal_state.value}  decisions={result.decisions}"
            )
            if result.terminal_state == TerminalState.SPEND_LIMIT:
                print("ethwall: spend cap reached; stopping the batch", file=sys.stderr)
                return exit_code_for(states)
    return exit_code_for(states)


# ----------------------------------------------------------------------------- verify / analyze


def expected_mode(partition: str) -> Mode:
    """The mode a partition must hold, from its name alone (never from unsigned files)."""
    base = partition.split("-")[0]
    if base in LIVE_PARTITIONS:
        return Mode.LIVE
    if base in (Mode.MOCK.value, Mode.FIXTURE.value):
        return Mode(base)
    raise UsageError(
        f"partition {partition!r} must start with mock-, fixture-, smoke, control or main"
    )


def verify_dir(run_dir: Path) -> tuple[str, ...]:
    """Recorder faults plus CLI-level checks for one run directory (never raises).

    The HMAC key comes from the partition name, not from manifest.json (which is unsigned);
    the signed RUN_START manifest must name the same recorder key id.
    """
    from ethwall.recorder import recorder_key, verify_run

    extra: list[str] = []
    try:
        mode = expected_mode(run_dir.parent.name)
        key, key_id = recorder_key(mode)
    except (UsageError, RuntimeError, KeyError) as e:
        return (f"KEY_UNAVAILABLE:{e}",)
    faults = list(verify_run(run_dir, key))
    if not (run_dir / "manifest.json").exists():
        extra.append("MANIFEST_MISSING")
    else:
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("mode") != mode.value:
            extra.append("MODE_MISMATCH")
    start = _run_start_manifest(run_dir)
    if start is not None and start.get("recorder_key_id") not in (None, key_id):
        extra.append("KEY_ID_MISMATCH")
    return tuple(faults + [x for x in extra if x not in faults])


def _run_start_manifest(run_dir: Path) -> dict | None:
    try:
        first = (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()[0]
        event = json.loads(first)
    except (OSError, IndexError, ValueError):
        return None
    if event.get("kind") != "RUN_START":
        return None
    manifest = event.get("payload", {}).get("manifest")
    return manifest if isinstance(manifest, dict) else None


def cmd_verify(args: argparse.Namespace) -> int:
    dirs = (
        [Path(args.run_dir)]
        if args.run_dir
        else _run_dirs(Path(args.runs_root), args.partition)
    )
    if not dirs:
        print("no runs found", file=sys.stderr)
        return 1
    bad = 0
    for d in dirs:
        faults = verify_dir(d)
        bad += bool(faults)
        print(f"{d.name}  {'clean' if not faults else ','.join(faults)}")
    return 1 if bad else 0


def cmd_analyze(args: argparse.Namespace) -> int:
    from ethwall.analysis import analyze_partition

    dirs = _run_dirs(Path(args.runs_root), args.partition)
    if not dirs:
        print("no runs found", file=sys.stderr)
        return 1
    out = Path(args.out or REPO_ROOT / "analysis" / args.partition)
    result = analyze_partition(dirs, out, verifier=verify_dir)
    print(f"analysis written to {out} ({len(result.rows)} episodes)")
    return 0


# ----------------------------------------------------------------------------- freeze / doctor


def cmd_freeze(args: argparse.Namespace) -> int:
    from ethwall.freeze import create_freeze, validate_freeze

    if args.action == "create":
        print(create_freeze(args.id))
        return 0
    mismatches = validate_freeze(args.id)
    for m in mismatches:
        print(m)
    print("valid" if not mismatches else f"{len(mismatches)} mismatch(es)")
    return 0 if not mismatches else 1


def doctor_report(env: os._Environ | dict) -> dict[str, str]:
    report = {k: ("set" if env.get(k) else "missing") for k in DOCTOR_ENV}
    docker = shutil.which("docker")
    ok = False
    if docker:
        try:
            ok = (
                subprocess.run(
                    [docker, "info"], check=False, capture_output=True, timeout=30
                ).returncode
                == 0
            )
        except (OSError, subprocess.TimeoutExpired):
            ok = False
    report["docker"] = "running" if ok else "unavailable"
    return report


def cmd_doctor(args: argparse.Namespace) -> int:
    for name, status in doctor_report(os.environ).items():
        print(f"{name:32} {status}")
    return 0


# ----------------------------------------------------------------------------- audit study


def cmd_audit_family(args: argparse.Namespace) -> int:
    from ethwall import audit_cli

    return audit_cli.dispatch(args)


# ----------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ethwall", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="run episodes into runs/<partition>/")
    r.add_argument("--condition", default="baseline,guarded")
    r.add_argument("--seeds", required=True)
    r.add_argument("--mode", choices=[m.value for m in Mode], default="mock")
    r.add_argument("--policy", help="mock policy (mock/fixture mode)")
    r.add_argument("--partition", required=True)
    r.add_argument("--control", action="store_true", help="control episodes")
    r.add_argument("--arm", choices=list(ARMS), default="core")
    r.add_argument(
        "--control-kind",
        choices=list(CONTROL_KINDS),
        default=None,
        help="default: instructed_harmful with --control, else none",
    )
    r.add_argument("--freeze", help="freeze id (required for main partitions)")
    r.add_argument(
        "--confirm-spend", action="store_true", help="owner's go for paid calls"
    )
    r.add_argument("--runs-root", default=str(REPO_ROOT / "runs"))
    r.add_argument("--spend-root", default=str(REPO_ROOT / "spend"))
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("verify", help="verify recorded runs")
    v.add_argument("--partition")
    v.add_argument("--run-dir")
    v.add_argument("--runs-root", default=str(REPO_ROOT / "runs"))
    v.set_defaults(func=cmd_verify)

    a = sub.add_parser("analyze", help="tables, figures and fact sheet for a partition")
    a.add_argument("--partition", required=True)
    a.add_argument("--out")
    a.add_argument("--runs-root", default=str(REPO_ROOT / "runs"))
    a.set_defaults(func=cmd_analyze)

    f = sub.add_parser("freeze", help="create or validate a protocol freeze")
    f.add_argument("action", choices=["create", "validate"])
    f.add_argument("--id", required=True)
    f.set_defaults(func=cmd_freeze)

    d = sub.add_parser(
        "doctor", help="report which settings are present (never values)"
    )
    d.set_defaults(func=cmd_doctor)

    for name in AUDIT_COMMANDS:
        s = sub.add_parser(name, help="audit study command (own options; see its -h)")
        s.add_argument("rest", nargs=argparse.REMAINDER)
        s.set_defaults(func=cmd_audit_family)
    return p


AUDIT_COMMANDS = ("export-review", "label", "audit")


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in AUDIT_COMMANDS:
        # These parse their own options (see ethwall.audit_cli).
        args = argparse.Namespace(command=argv[0], rest=argv[1:], func=cmd_audit_family)
    else:
        args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except UsageError as e:
        print(f"ethwall: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
