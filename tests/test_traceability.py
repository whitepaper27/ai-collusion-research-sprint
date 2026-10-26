"""Spec-driven build: every requirement ID in docs/specs/ must be cited by a test.

A spec's `Module:` line maps requirement prefixes to paths, e.g.
`Module: src/ethwall/world/ (R-WLD), src/ethwall/scoring/ (R-SCR)`. Once a mapped path exists,
every ID with that prefix must appear in at least one test file (in a name, comment, docstring
or `req` marker). Tests may never cite an ID that no spec defines.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQ_DEF = re.compile(r"\*\*(R-[A-Z]{3}-\d{2})\*\*")
REQ_REF = re.compile(r"R-[A-Z]{3}-\d{2}")
MODULE_ITEM = re.compile(r"([^\s,]+)\s+\((R-[A-Z]{3})\)")


def load_specs(spec_dir: Path) -> tuple[dict[str, str], dict[str, Path]]:
    """Return ({requirement id: spec file name}, {prefix: module path})."""
    reqs: dict[str, str] = {}
    prefixes: dict[str, Path] = {}
    for spec in sorted(spec_dir.glob("*.md")):
        text = spec.read_text(encoding="utf-8")
        for rid in REQ_DEF.findall(text):
            assert rid not in reqs, f"{rid} defined in both {reqs[rid]} and {spec.name}"
            reqs[rid] = spec.name
        for line in text.splitlines():
            if line.startswith("Module:"):
                for path, prefix in MODULE_ITEM.findall(line):
                    prefixes[prefix] = Path(path)
    return reqs, prefixes


def cited_ids(test_dir: Path, exclude: Path | None = None) -> dict[str, set[str]]:
    """Return {requirement id: {test file names citing it}}."""
    cites: dict[str, set[str]] = {}
    for f in sorted(test_dir.rglob("*.py")):
        if exclude is not None and f.resolve() == exclude.resolve():
            continue
        for rid in REQ_REF.findall(f.read_text(encoding="utf-8")):
            cites.setdefault(rid, set()).add(f.name)
    return cites


def problems(root: Path, exclude: Path | None = None) -> list[str]:
    reqs, prefixes = load_specs(root / "docs" / "specs")
    cites = cited_ids(root / "tests", exclude)
    out = [f"test cites unknown requirement {rid}" for rid in cites if rid not in reqs]
    for rid in sorted(reqs):
        prefix = rid[:5]
        module = prefixes.get(prefix)
        if module is None:
            out.append(f"{rid}: no Module path declared for prefix {prefix}")
        elif (root / module).exists() and rid not in cites:
            out.append(
                f"{rid} ({reqs[rid]}): module {module.as_posix()} exists but no test cites it"
            )
    return out


def test_every_requirement_of_an_existing_module_is_tested():
    assert problems(ROOT, exclude=Path(__file__)) == []


def test_specs_define_requirements():
    reqs, prefixes = load_specs(ROOT / "docs" / "specs")
    assert len(reqs) >= 100
    assert {rid[:5] for rid in reqs} <= set(prefixes)


# --- the checker itself must be able to fail ---------------------------------------------


def _mini_repo(tmp_path: Path, test_body: str, make_module: bool) -> Path:
    (tmp_path / "docs" / "specs").mkdir(parents=True)
    (tmp_path / "docs" / "specs" / "x.md").write_text(
        "Module: src/pkg/x/ (R-XYZ)\n- **R-XYZ-01** one\n- **R-XYZ-02** two\n",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(test_body, encoding="utf-8")
    if make_module:
        (tmp_path / "src" / "pkg" / "x").mkdir(parents=True)
    return tmp_path


def test_checker_flags_untested_requirement_once_module_exists(tmp_path):
    root = _mini_repo(tmp_path, "# R-XYZ-01\n", make_module=True)
    assert problems(root) == [
        "R-XYZ-02 (x.md): module src/pkg/x exists but no test cites it"
    ]


def test_checker_ignores_modules_not_yet_built(tmp_path):
    root = _mini_repo(tmp_path, "# R-XYZ-01\n", make_module=False)
    assert problems(root) == []


def test_checker_flags_unknown_ids(tmp_path):
    root = _mini_repo(tmp_path, "# R-XYZ-01 R-XYZ-02 R-NOP-07\n", make_module=True)
    assert problems(root) == ["test cites unknown requirement R-NOP-07"]
