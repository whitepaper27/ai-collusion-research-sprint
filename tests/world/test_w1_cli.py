"""W1 world CLI test (R-WLD-13)."""

from __future__ import annotations

import json
import subprocess
import sys

from ethwall.contract import to_jsonable
from ethwall.world import build_world


def test_cli_writes_world_registry_releases_and_prices(tmp_path):
    """R-WLD-13: `python -m ethwall.world --seed N --out DIR` writes four JSON files."""
    out = tmp_path / "w"
    proc = subprocess.run(
        [sys.executable, "-m", "ethwall.world", "--seed", "7", "--out", str(out)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    w = build_world(7)
    names = {"world.json", "registry.json", "release_records.json", "prices.json"}
    assert {p.name for p in out.iterdir()} == names
    assert (out / "world.json").read_text(encoding="utf-8") == w.to_json()
    registry = json.loads((out / "registry.json").read_text(encoding="utf-8"))
    assert registry == to_jsonable(w.facts)
    records = json.loads((out / "release_records.json").read_text(encoding="utf-8"))
    assert records == to_jsonable(w.release_records)
    assert json.loads((out / "prices.json").read_text(encoding="utf-8")) == list(
        w.prices
    )


def test_cli_rejects_missing_seed(tmp_path):
    """R-WLD-13: the CLI requires --seed and --out."""
    proc = subprocess.run(
        [sys.executable, "-m", "ethwall.world", "--out", str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode != 0
    assert not any(tmp_path.iterdir())
