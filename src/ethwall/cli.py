"""ethwall command line (docs/specs/cli-integration.md).

    ethwall run --condition baseline,guarded --seeds 0-2 --mode mock --policy honest \
                --partition mock-demo
    ethwall verify --partition mock-demo
    ethwall analyze --partition mock-demo
    ethwall freeze create|validate --id <freeze_id>
    ethwall doctor
    ethwall export-review | label | audit   (audit study; see `ethwall <cmd> -h`)

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
    Condition,
    EpisodeConfig,
    Mode,
    new_run_id,
    to_jsonable,
)

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


def check_live_preconditions(args: argparse.Namespace, env: os._Environ | dict) -> None:
    if not args.confirm_spend:
        raise UsageError(
            "live mode spends money: pass --confirm-spend (owner's explicit go)"
        )
    missing = [k for k in (*LIVE_ENV, *PRICE_ENV) if not env.get(k)]
    if missing:
        raise UsageError(f"live mode needs these settings: {', '.join(missing)}")
    if args.partition.split("-")[0] == "main" and not args.freeze:
        raise UsageError("main partitions need --freeze <id>")


def cmd_run(args: argparse.Namespace) -> int:
    from ethwall.enforcement import make_policy
    from ethwall.models import AnthropicActor, mock_model
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

    mode = Mode(args.mode)
    conditions = [Condition(c) for c in args.condition.split(",")]
    root = Path(args.runs_root)
    ledger = None
    if mode == Mode.LIVE:
        check_live_preconditions(args, os.environ)
        from ethwall.spend import SpendLedger, load_pricing

        ledger = SpendLedger.for_partition(
            args.partition, load_pricing(), args.spend_root
        )
    elif not args.policy:
        raise UsageError("mock/fixture runs need --policy")
    prompts = load_prompts()
    key, key_id = recorder_key(mode)
    model_id = (
        os.environ.get("ACTOR_MODEL_ID", "")
        if mode == Mode.LIVE
        else f"mock-{args.policy}"
    )
    worst = 0
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
            )
            try:
                config.validate()
            except ValueError as e:
                raise UsageError(str(e)) from e
            if mode == Mode.LIVE and args.partition.split("-")[0] == "main":
                from ethwall.freeze import require_valid_freeze

                require_valid_freeze(config)
            world = build_world(seed)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            run_id = new_run_id(config, stamp, secrets.token_hex(2))
            run_dir = new_run_dir(root, args.partition, run_id)
            manifest = build_manifest(
                config,
                world=world,
                prompts_sha256=prompts.sha256,
                run_id=run_id,
                freeze_id=args.freeze,
            )
            manifest["control"] = args.control
            (run_dir / "manifest.json").write_text(
                json.dumps(manifest, sort_keys=True, indent=1), encoding="utf-8"
            )
            recorder = JsonlRecorder(run_dir, run_id, key, key_id)
            model = (
                AnthropicActor.from_env()
                if mode == Mode.LIVE
                else mock_model(args.policy, world)
            )
            result = run_episode(
                config,
                world=world,
                model=model,
                services=make_services(world, make_policy(condition), recorder),
                recorder=recorder,
                prompts=prompts,
                ledger=ledger,
                manifest=manifest,
            )
            (run_dir / "result.json").write_text(
                json.dumps(to_jsonable(result), sort_keys=True, indent=1),
                encoding="utf-8",
            )
            print(
                f"{run_id}  {result.terminal_state.value}  decisions={result.decisions}"
            )
    return worst


# ----------------------------------------------------------------------------- verify / analyze


def cmd_verify(args: argparse.Namespace) -> int:
    from ethwall.recorder import recorder_key, verify_run

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
        manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        key, _ = recorder_key(Mode(manifest["mode"]))
        faults = verify_run(d, key)
        bad += bool(faults)
        print(f"{d.name}  {'clean' if not faults else ','.join(faults)}")
    return 1 if bad else 0


def cmd_analyze(args: argparse.Namespace) -> int:
    from ethwall.analysis import analyze_partition
    from ethwall.recorder import recorder_key, verify_run

    dirs = _run_dirs(Path(args.runs_root), args.partition)
    if not dirs:
        print("no runs found", file=sys.stderr)
        return 1
    mode = Mode(
        json.loads((dirs[0] / "manifest.json").read_text(encoding="utf-8"))["mode"]
    )
    key, _ = recorder_key(mode)
    out = Path(args.out or Path("analysis") / args.partition)
    result = analyze_partition(dirs, out, verifier=lambda d: verify_run(d, key))
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
    r.add_argument("--control", action="store_true", help="instructed-control episodes")
    r.add_argument("--freeze", help="freeze id (required for main partitions)")
    r.add_argument(
        "--confirm-spend", action="store_true", help="owner's go for paid calls"
    )
    r.add_argument("--runs-root", default="runs")
    r.add_argument("--spend-root", default="spend")
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("verify", help="verify recorded runs")
    v.add_argument("--partition")
    v.add_argument("--run-dir")
    v.add_argument("--runs-root", default="runs")
    v.set_defaults(func=cmd_verify)

    a = sub.add_parser("analyze", help="tables, figures and fact sheet for a partition")
    a.add_argument("--partition", required=True)
    a.add_argument("--out")
    a.add_argument("--runs-root", default="runs")
    a.set_defaults(func=cmd_analyze)

    f = sub.add_parser("freeze", help="create or validate a protocol freeze")
    f.add_argument("action", choices=["create", "validate"])
    f.add_argument("--id", required=True)
    f.set_defaults(func=cmd_freeze)

    d = sub.add_parser(
        "doctor", help="report which settings are present (never values)"
    )
    d.set_defaults(func=cmd_doctor)

    for name in ("export-review", "label", "audit"):
        s = sub.add_parser(name, help="audit study command")
        s.add_argument("rest", nargs=argparse.REMAINDER)
        s.set_defaults(func=cmd_audit_family)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except UsageError as e:
        print(f"ethwall: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
