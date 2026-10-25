"""W4 Docker clean room (docs/specs/recorder-freeze.md R-DKR-01).

Runs scripts/docker-verify.sh, which builds the locked image (`uv sync --frozen`) and runs the
test suite inside it with `--network none`. Slow, so marked e2e (`verify.sh --fast` skips it).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def find_bash() -> str:
    # Same rule as tests/test_hooks.py: skip WSL's bash.exe (System32) and Store stubs.
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = shutil.which("bash", path=d) if d else None
        if p and not any(s in p.lower() for s in ("system32", "windowsapps")):
            return p
    raise AssertionError("no usable bash on PATH")


def test_dockerfile_installs_the_locked_environment():
    """R-DKR-01: the image installs exactly uv.lock and the script runs without network."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "uv sync --frozen" in dockerfile
    script = (ROOT / "scripts" / "docker-verify.sh").read_text(encoding="utf-8")
    assert "--network none" in script and "set -euo pipefail" in script


@pytest.mark.e2e
def test_docker_verify_script_passes():
    """R-DKR-01: scripts/docker-verify.sh exits 0 (suite green inside the clean room)."""
    proc = subprocess.run(
        [find_bash(), str(ROOT / "scripts" / "docker-verify.sh")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
        check=False,
    )
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
    assert proc.returncode == 0, tail
    assert "docker-verify: OK" in proc.stdout
