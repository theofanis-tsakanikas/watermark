#!/usr/bin/env python3
"""The two feature mechanisms share the contract and nothing else.

ADR-0004's enforcement. Without it, "two mechanisms" is a sentence in a document, and the first
refactor to notice that `offline._aggregate` and `online._fold` look alike will merge them —
reasonably, tidily, and claim 3 will compare a function with itself from that day on, reporting
green in eleven milliseconds forever.

The check reads the import graph. `offline.py` and `online.py` may import the contract model
and `watermark.core.time`; they may not import each other, and they may not import a third
module that is not on the allowed list, because a shared helper is the same merge with an extra
file in it.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FEATURES = ROOT / "src" / "watermark" / "features"

MECHANISMS = ("offline.py", "online.py")

#: What both sides may share. The contract is the definition they are both compilations *of* —
#: sharing it is the design. `core.time` is arithmetic on instants, which is not a feature
#: definition and would be perverse to duplicate.
ALLOWED = {
    "watermark.contracts.features",
    "watermark.core.time",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return {module for module in modules if module.startswith("watermark")}


def main() -> int:
    problems: list[str] = []

    for name in MECHANISMS:
        path = FEATURES / name
        if not path.exists():
            problems.append(
                f"{name} is missing; there is only one mechanism, so there is no claim 3"
            )
            continue
        other = MECHANISMS[1] if name == MECHANISMS[0] else MECHANISMS[0]
        for module in sorted(_imports(path)):
            if module in ALLOWED:
                continue
            if module.endswith(other.removesuffix(".py")):
                problems.append(
                    f"{name} imports {module}. The two mechanisms would then be one mechanism "
                    "with two names, and claim 3 would compare it with itself."
                )
            else:
                problems.append(
                    f"{name} imports {module}, which is not the shared contract. A helper both "
                    "sides call is the same merge with an extra file in front of it — and the "
                    "agreement it produces is arithmetic agreeing with itself."
                )

    if problems:
        print("parity-independence: the two mechanisms are not independent\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print("parity-independence: the two feature mechanisms share the contract and nothing else")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
