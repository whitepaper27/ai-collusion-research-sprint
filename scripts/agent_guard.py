#!/usr/bin/env python3
"""agent_guard.py — deterministic rules agents can't talk their way past.

One rule set (.agent/protected.txt + the patterns below), used in three places:

  --hook edit    Claude Code PreToolUse hook for Edit/Write   (fast local feedback)
  --hook bash    Claude Code PreToolUse hook for Bash         (fast local feedback)
  --diff BASE    verify.sh locally, and the TRUSTED CI guard  (the enforcement)

Trust model. Local hooks run code the agent could edit, so they are feedback, not a boundary.
The authoritative run is .github/workflows/guard.yml: it runs on `pull_request_target`, so the
workflow AND this script come from the base branch, and it only *reads* the PR as git data —
it never executes PR code. An agent can edit its branch's copy of this file; it can't change
the copy that judges it.

Protection tiers (.agent/protected.txt):
  locked     agents may not add, edit or delete. Override: `allow-protected` label.
  immutable  files that exist on the base branch may not be edited or deleted; new files are
             fine. (Applied migrations.) Override: `allow-protected`.
  review     files that exist on the base branch MAY be edited, but a human must look:
             locally the edit prompts you; in CI it needs the `human-reviewed` label.
             (Existing tests, lint/type config, agent instructions.)

Env overrides (set by CI from labels applied by trusted maintainers; never by agents):
  AGENT_GUARD_ALLOW=1      everything        AGENT_GUARD_REVIEWED=1   review tier only

  python3 scripts/agent_guard.py --diff BASE [--repo DIR] [--rules FILE]
"""

import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

PROTECTED_FILE = ".agent/protected.txt"
SELF = "scripts/agent_guard.py"

# Added lines matching these weaken a check.
WEAKENING = [
    (
        r"\b(?:it|test|describe|context|suite)\.(?:skip|only)\s*\(",
        "skipped or focused test",
    ),
    (r"(?<![\w.$])x(?:it|describe|test)\s*\(", "skipped test (xit/xdescribe)"),
    (r"@ts-(?:ignore|nocheck)\b", "TypeScript check suppressed"),
    (r"eslint-disable", "lint rule disabled"),
    (r"biome-ignore", "lint rule disabled"),
    (r"#\s*type:\s*ignore\b", "type check suppressed"),
    (r"#\s*pyright:\s*ignore\b", "type check suppressed"),
    (r"#\s*noqa\b", "lint rule suppressed"),
    (r"pytest\.mark\.(?:skip|skipif|xfail)\b", "skipped test"),
    (r"\bpytest\.skip\s*\(", "skipped test"),
    (r"@unittest\.skip", "skipped test"),
]

# Lines that look like assertions — used to report what a test edit removed.
ASSERTION = re.compile(
    r"\b(?:expect|assert\w*|should|toBe|toEqual|toThrow|toMatch)\b|\bassert\s"
)

# Shell commands agents must never run. (Permissions deny-lists catch the simple forms.)
DANGEROUS_BASH = [
    (
        r"\bgit\b[^;&|]*\bpush\b[^;&|]*(?:--force\b|--force-with-lease\b|\s-f\b)",
        "force push",
    ),
    (r"\bgit\b[^;&|]*\bpush\b[^;&|]*\b(?:main|master)\b", "push to main"),
    (r"--no-verify\b", "skipping git hooks"),
    (r"\bgh\s+pr\s+merge\b", "merging a PR (humans merge)"),
    (r"allow-protected|human-reviewed", "applying a human-approval label"),
    (r"AGENT_GUARD_(?:ALLOW|REVIEWED)", "setting a human override"),
    (r"\bgh\s+api\b[^;&|]*\b(?:rulesets|protection|labels)\b", "changing repo rules"),
    (
        r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*\s+(?:/|~|\$HOME)(?:\s|$)",
        "recursive delete of / or home",
    ),
    (r"\bgit\b[^;&|]*\bconfig\b[^;&|]*\bcore\.hooksPath\b", "redirecting git hooks"),
]

# package.json scripts that make up the gate. Changing what they run = changing the oracle.
CHECK_SCRIPTS = ("format:check", "lint", "typecheck", "test", "build", "test:e2e")
# pyproject sections that configure checks. Changes need a human look.
CHECK_TOOL_SECTIONS = ("pytest", "ruff", "mypy", "pyright", "coverage")

CODE_EXT = {
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".mts",
    ".cts",
    ".py",
    ".vue",
    ".svelte",
}


def is_code(path: str) -> bool:
    return Path(path).suffix in CODE_EXT and path != SELF


# ---------------------------------------------------------------- shared helpers


def git(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=check, cwd=cwd
    ).stdout


def ref_exists(ref: str, cwd: Path) -> bool:
    r = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        capture_output=True,
        cwd=cwd,
        check=False,
    )
    return r.returncode == 0


def in_ref(ref: str, path: str, cwd: Path) -> bool:
    r = subprocess.run(
        ["git", "cat-file", "-e", f"{ref}:{path}"],
        capture_output=True,
        cwd=cwd,
        check=False,
    )
    return r.returncode == 0


def repo_root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env).resolve()
    try:
        return Path(git("rev-parse", "--show-toplevel").strip()).resolve()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return Path.cwd().resolve()


def load_rules(rules_file: Path) -> list[tuple[str, str]]:
    rules = []
    if not rules_file.exists():
        return rules
    for raw in rules_file.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        kind, _, pattern = line.partition(" ")
        kind = "immutable" if kind == "append" else kind  # v1 name
        if kind in ("locked", "immutable", "review") and pattern.strip():
            rules.append((kind, pattern.strip()))
    return rules


def matches(path: str, pattern: str) -> bool:
    if fnmatch.fnmatch(path, pattern):
        return True
    return pattern.startswith("**/") and fnmatch.fnmatch(path, pattern[3:])


RANK = {"locked": 3, "immutable": 2, "review": 1}


def tier_for(path: str, rules) -> str | None:
    """Strictest matching tier wins."""
    best = None
    for kind, pattern in rules:
        if matches(path, pattern) and (best is None or RANK[kind] > RANK[best]):
            best = kind
    return best


def weakening_in(text: str) -> list[str]:
    return [why for pat, why in WEAKENING if re.search(pat, text)]


def allow_all() -> bool:
    return os.environ.get("AGENT_GUARD_ALLOW") == "1"


def allow_review() -> bool:
    return allow_all() or os.environ.get("AGENT_GUARD_REVIEWED") == "1"


# ---------------------------------------------------------------- hooks


def block(msg: str):
    print(msg, file=sys.stderr)
    sys.exit(2)


def ask(msg: str):
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": msg,
                }
            }
        )
    )
    sys.exit(0)


def hook_edit(data: dict, root: Path):
    rules = load_rules(root / PROTECTED_FILE)
    ti = data.get("tool_input") or {}
    raw = ti.get("file_path") or ti.get("notebook_path")
    if not raw:
        return
    p = Path(raw)
    if not p.is_absolute():
        p = Path(data.get("cwd") or root) / p
    p = p.resolve()
    try:
        rel = p.relative_to(root).as_posix()
    except ValueError:
        return  # outside the repo: permissions handle that

    # "Existing" means it exists on the base branch — a file this branch created is the
    # agent's own work and stays editable.
    base = os.environ.get("VERIFY_BASE", "origin/main")
    existed = in_ref(base, rel, root) if ref_exists(base, root) else p.exists()

    tier = tier_for(rel, rules)
    if tier == "locked" and not allow_all():
        block(
            f"BLOCKED: `{rel}` is locked ({PROTECTED_FILE}): it's part of the gate that judges "
            "your work. If it's wrong, say why in the PR description and leave it unchanged."
        )
    if tier == "immutable" and existed and not allow_all():
        block(
            f"BLOCKED: `{rel}` already exists on the base branch and is immutable "
            f"({PROTECTED_FILE}). Add a new file instead (e.g. a corrective migration)."
        )

    new = ti.get("content") or ti.get("new_string") or ti.get("new_source") or ""
    old = ti.get("old_string") or ""
    if new and is_code(rel) and not allow_all():
        added = [w for w in weakening_in(new) if w not in weakening_in(old)]
        if added:
            block(
                f"BLOCKED: this edit to `{rel}` adds: {', '.join(sorted(set(added)))}. "
                "Fix the underlying problem instead of suppressing the check. If suppression is "
                "truly correct, stop and ask the human."
            )

    if tier == "review" and existed and not allow_review():
        ask(
            f"`{rel}` is an existing test or check/agent config (review tier). Editing it is "
            "allowed, but a human must agree: changing an oracle can make wrong code pass. "
            "Approve only if the change keeps or strengthens what it checks."
        )


def hook_bash(data: dict):
    cmd = (data.get("tool_input") or {}).get("command") or ""
    for pat, why in DANGEROUS_BASH:
        if re.search(pat, cmd):
            block(
                f"BLOCKED: `{cmd.strip()[:120]}` — {why}. Not allowed for agents; open a PR "
                "and let a human do this."
            )


# ---------------------------------------------------------------- diff mode


def json_at(text: str | None) -> dict:
    try:
        return json.loads(text) if text else {}
    except json.JSONDecodeError:
        return {}


def file_at(ref: str, path: str, cwd: Path) -> str | None:
    r = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )
    return r.stdout if r.returncode == 0 else None


def check_config_changes(mb: str, repo: Path) -> tuple[list[str], list[str]]:
    """Gate-script and check-config changes: (locked-level, review-level)."""
    locked, review = [], []
    old = json_at(file_at(mb, "package.json", repo)).get("scripts", {}) or {}
    new_text = (
        (repo / "package.json").read_text()
        if (repo / "package.json").exists()
        else None
    )
    new = json_at(new_text).get("scripts", {}) or {}
    for s in CHECK_SCRIPTS:
        if old.get(s) != new.get(s) and s in old:
            locked.append(
                f"package.json: check script `{s}` changed "
                f"({old.get(s)!r} → {new.get(s)!r})"
            )

    if tomllib is not None:
        before, after = file_at(mb, "pyproject.toml", repo), None
        if (repo / "pyproject.toml").exists():
            after = (repo / "pyproject.toml").read_text()
        try:
            t0 = tomllib.loads(before).get("tool", {}) if before else {}
            t1 = tomllib.loads(after).get("tool", {}) if after else {}
        except tomllib.TOMLDecodeError:
            t0, t1 = {}, {}
        for sec in CHECK_TOOL_SECTIONS:
            if t0.get(sec) != t1.get(sec) and sec in t0:
                review.append(f"pyproject.toml: [tool.{sec}] changed")
    return locked, review


def diff_mode(base: str, repo: Path, rules) -> int:
    if not ref_exists(base, repo):
        print(
            f"guard: base `{base}` not found — skipping diff check (run `git fetch`)."
        )
        return 0
    mb = git("merge-base", base, "HEAD", cwd=repo).strip()
    plain = ("--no-ext-diff", "--no-textconv", "--no-renames")

    changes: list[tuple[str, str]] = []
    for line in git("diff", "--name-status", *plain, mb, cwd=repo).splitlines():
        status, _, path = line.partition("\t")
        changes.append((status[0], path))
    for path in git(
        "ls-files", "--others", "--exclude-standard", cwd=repo
    ).splitlines():
        changes.append(("A", path))

    hard: list[str] = []  # need allow-protected
    soft: list[str] = []  # need human-reviewed
    for status, path in changes:
        tier = tier_for(path, rules)
        verb = {"A": "added", "M": "modified", "D": "deleted"}.get(status, "changed")
        if tier == "locked":
            hard.append(f"{path}: locked file {verb}")
        elif tier == "immutable" and status in ("M", "D"):
            hard.append(f"{path}: immutable file {verb}")
        elif tier == "review" and status in ("M", "D"):
            soft.append(f"{path}: {verb} (review tier)")

    lk, rv = check_config_changes(mb, repo)
    hard += lk
    soft += rv

    # Walk the diff: suppression patterns on added lines; assertion churn in test files.
    diff = git("diff", "--unified=0", *plain, mb, cwd=repo)
    current = None
    old_path = None
    churn: dict[str, list[int]] = {}
    for line in diff.splitlines():
        if line.startswith("--- "):
            old_path = line[6:] if line.startswith("--- a/") else None
            continue
        if line.startswith("+++ "):
            # deleted file: "+++ /dev/null" — attribute removed lines to the old path
            current = line[6:] if line.startswith("+++ b/") else old_path
            continue
        if not current or not is_code(current):
            continue
        if line.startswith("+"):
            for why in weakening_in(line[1:]):
                hard.append(f"{current}: adds {why}: {line[1:].strip()[:80]}")
            churn.setdefault(current, [0, 0])[1] += len(ASSERTION.findall(line[1:]))
        elif line.startswith("-"):
            churn.setdefault(current, [0, 0])[0] += len(ASSERTION.findall(line[1:]))
    for status, path in changes:
        if (
            status != "A"
            or not is_code(path)
            or git("ls-files", path, cwd=repo).strip()
        ):
            continue
        try:
            text = (repo / path).read_text(errors="ignore")
        except OSError:
            continue
        for why in weakening_in(text):
            hard.append(f"{path}: new file adds {why}")

    weakened = {
        f: c for f, c in churn.items() if c[0] > c[1] and tier_for(f, rules) == "review"
    }

    report = []
    report.append(f"guard: {len(changes)} changed files vs {base}")
    if hard:
        state = "allowed by `allow-protected`" if allow_all() else "BLOCKING"
        report.append(f"\nProtected changes ({state}):")
        report += [f"  - {p}" for p in dict.fromkeys(hard)]
    if soft:
        state = "approved by `human-reviewed`" if allow_review() else "BLOCKING"
        report.append(f"\nNeeds human review ({state}):")
        report += [f"  - {p}" for p in dict.fromkeys(soft)]
    if weakened:
        report.append("\nTests that lost assertions (look at these first):")
        report += [
            f"  - {f}: {c[0]} assertions removed, {c[1]} added"
            for f, c in weakened.items()
        ]
    failing = (hard and not allow_all()) or (soft and not allow_review())
    if failing:
        report.append(
            "\nAgents: don't work around this. Explain in the PR why the change is needed.\n"
            "Humans: review, then label the PR `human-reviewed` (review tier) or "
            "`allow-protected` (locked/immutable)."
        )
    elif not hard and not soft:
        report.append("guard: ok")
    out = "\n".join(report)
    print(out)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write("## Agent guard\n\n```\n" + out + "\n```\n")
    return 1 if failing else 0


# ---------------------------------------------------------------- main


def opt(args: list[str], name: str) -> str | None:
    if name in args:
        i = args.index(name)
        return args[i + 1] if i + 1 < len(args) else None
    return None


def main() -> int:
    args = sys.argv[1:]
    if args[:2] == ["--hook", "edit"]:
        hook_edit(json.load(sys.stdin), repo_root())
        return 0
    if args[:2] == ["--hook", "bash"]:
        hook_bash(json.load(sys.stdin))
        return 0
    if args[:1] == ["--diff"]:
        base = (
            args[1] if len(args) > 1 and not args[1].startswith("--") else "origin/main"
        )
        repo = Path(opt(args, "--repo") or repo_root()).resolve()
        rules_file = Path(opt(args, "--rules") or repo / PROTECTED_FILE)
        return diff_mode(base, repo, load_rules(rules_file))
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
