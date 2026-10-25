"""`python -m ethwall.world --seed N --out DIR` (R-WLD-13).

Writes `world.json` (canonical `World.to_json()`), `registry.json` (the fact registry),
`release_records.json` and `prices.json` (P0..P6) into DIR.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from ethwall.contract import to_jsonable
from ethwall.world import build_world


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ethwall.world", description="Write a seeded world to disk."
    )
    parser.add_argument("--seed", type=int, required=True, help="world seed")
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    args = parser.parse_args(argv)

    world = build_world(args.seed)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "world.json": world.to_json(),
        "registry.json": json.dumps(to_jsonable(world.facts), indent=2, sort_keys=True),
        "release_records.json": json.dumps(
            to_jsonable(world.release_records), indent=2, sort_keys=True
        ),
        "prices.json": json.dumps(list(world.prices)),
    }
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")
        print(out / name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
