"""The declared dependencies and the imported ones are the same set.

Both directions matter, and they fail differently.

A **declared dependency nothing imports** ships in every artefact built from this repository
for no reason, and it usually contradicts something the project has decided — a schema library
in a project whose position is that the model *is* the schema, a cloud SDK in a package whose
claim is that it needs no cloud.

An **imported package nothing declares** works on the machine that added it and fails on a
clean install, which is every machine except that one. It is also how the core's purity rule
gets quietly widened: the import goes in, the dependency does not, and nothing says so.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
SOURCE = REPOSITORY / "src"

#: Distribution name → the name you import it by, for the ones where they differ. Explicit,
#: because deriving it needs the package installed and this test must run on the metadata.
IMPORT_NAMES: dict[str, str] = {
    "pyyaml": "yaml",
    "python-dateutil": "dateutil",
}


def _declared() -> set[str]:
    metadata = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = metadata["project"]["dependencies"]
    names = {_distribution_name(requirement) for requirement in requirements}
    return {IMPORT_NAMES.get(name, name.replace("-", "_")) for name in names}


def _declared_optional() -> set[str]:
    """The extras: `cloud`, `flink`, `ml`, `dev`.

    Declared, but not installed on the machine the offline suite has to run on. They are
    legitimate imports — under one condition, which `test_optional_dependencies_are_lazy`
    enforces.
    """
    metadata = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))
    names = {
        _distribution_name(requirement)
        for group in metadata["project"].get("optional-dependencies", {}).values()
        for requirement in group
    }
    return {IMPORT_NAMES.get(name, name.replace("-", "_")) for name in names}


def _module_scope_imports(nodes) -> set[str]:
    """Imports that run when the file is imported.

    Recursive over everything *except* function bodies — a nested import is deferred, and a
    deferred import is what lets a module load on a machine where the package is absent.
    `ast.walk` cannot express this: it descends into function bodies, which is how the first
    version of this check reported the one correctly-lazy import in the repository as a
    violation.
    """
    roots: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            roots.add(node.module.split(".")[0])
        else:
            roots |= _module_scope_imports(ast.iter_child_nodes(node))
    return roots


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return _module_scope_imports(tree.body)


def _distribution_name(requirement: str) -> str:
    """`pydantic>=2.9` and `boto3[crt]==1.40` both name `pydantic` and `boto3`."""
    for separator in (">", "<", "=", "!", "~", "[", ";", " "):
        requirement = requirement.split(separator, maxsplit=1)[0]
    return requirement.strip().lower()


def _imported() -> set[str]:
    roots: set[str] = set()
    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                roots.add(node.module.split(".")[0])
    return {
        root
        for root in roots
        if root not in sys.stdlib_module_names and root not in {"__future__", "watermark"}
    }


def test_everything_declared_is_imported() -> None:
    unused = _declared() - _imported()
    assert not unused, f"declared and imported by nothing: {sorted(unused)}"


def test_everything_imported_is_declared() -> None:
    undeclared = _imported() - _declared() - _declared_optional()
    assert not undeclared, f"imported and declared nowhere: {sorted(undeclared)}"


def test_optional_dependencies_are_lazy() -> None:
    """An extra may be imported, but never at module scope.

    This is the rule that keeps "offline is the default" true rather than aspirational. One
    top-level `import xgboost` in `src/watermark/` and every claim gate, every eval and the
    whole suite stop running on a machine that has not installed the `ml` extra — and nothing
    would say so except an ImportError in a stack trace, on somebody else's laptop.

    The import in `gradient.py` sits inside `_load_xgboost` for exactly this reason, and raises
    a named error rather than skipping, so the caller decides.
    """
    optional = _declared_optional()
    offenders = [
        f"{path.relative_to(REPOSITORY)}: {sorted(_top_level_imports(path) & optional)}"
        for path in sorted(SOURCE.rglob("*.py"))
        if _top_level_imports(path) & optional
    ]
    assert not offenders, "optional dependencies imported at module scope: " + "; ".join(offenders)
