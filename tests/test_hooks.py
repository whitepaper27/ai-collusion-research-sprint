"""The hook commands in .claude/settings.json must actually block.

Claude Code treats a hook that fails for any reason other than exit 2 as non-blocking, so a
hook whose interpreter is broken (e.g. the Windows Store `python3` stub) silently guards
nothing. These tests run the exact command strings from settings.json through bash.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))


def find_bash() -> str:
    # On Windows, skip WSL's bash.exe (System32) and Store stubs (WindowsApps).
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = shutil.which("bash", path=d) if d else None
        if p and not any(s in p.lower() for s in ("system32", "windowsapps")):
            return p
    raise AssertionError("no usable bash on PATH")


def hook_command(matcher: str) -> str:
    for entry in SETTINGS["hooks"]["PreToolUse"]:
        if entry["matcher"] == matcher:
            return entry["hooks"][0]["command"]
    raise AssertionError(f"no PreToolUse hook for {matcher!r}")


def run_hook(matcher: str, payload: dict) -> subprocess.CompletedProcess:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": ROOT.as_posix()}
    return subprocess.run(
        [find_bash(), "-c", hook_command(matcher)],
        input=json.dumps(payload),
        capture_output=True,
        check=False,
        text=True,
        cwd=ROOT,
        env=env,
        timeout=60,
    )


def test_py_wrapper_finds_python_311_or_newer():
    r = subprocess.run(
        [
            find_bash(),
            "-c",
            'scripts/py.sh -c "import sys; print(sys.version_info >= (3, 11))"',
        ],
        capture_output=True,
        check=False,
        text=True,
        cwd=ROOT,
        timeout=60,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "True"


def test_bash_hook_blocks_force_push():
    r = run_hook(
        "Bash", {"tool_name": "Bash", "tool_input": {"command": "git push --force"}}
    )
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert "BLOCKED" in r.stderr


def test_bash_hook_allows_harmless_command():
    r = run_hook("Bash", {"tool_name": "Bash", "tool_input": {"command": "git status"}})
    assert r.returncode == 0, (r.returncode, r.stderr)


def test_edit_hook_blocks_locked_file():
    target = ROOT / "scripts" / "verify.sh"
    r = run_hook(
        "Edit|Write|NotebookEdit",
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(target),
                "old_string": "a",
                "new_string": "b",
            },
        },
    )
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert "locked" in r.stderr
