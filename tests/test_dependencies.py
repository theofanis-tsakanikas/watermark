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
    undeclared = _imported() - _declared()
    assert not undeclared, f"imported and declared nowhere: {sorted(undeclared)}"
