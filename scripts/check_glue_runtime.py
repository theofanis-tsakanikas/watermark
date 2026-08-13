"""Refuse a Glue job that uses a Python this repository has and Glue does not.

**The failure this exists for.** `pipelines/jobs/delete_orphan_files.py` imported `UTC` from
`datetime` — correct, idiomatic, and what `ruff`'s `UP017` rule actively asks for on this
repository's target of 3.12. Glue 4.0 runs Python **3.10**, where the name does not exist:

    ImportError: cannot import name 'UTC' from 'datetime'

The job died thirty-three seconds in, having started a Spark cluster to do it, inside an erasure
that was waiting on it synchronously.

**Nothing else could have caught it.** `ruff`, `mypy` and the whole test suite read these files
with whatever interpreter the laptop or the runner has, and every one of them is 3.12 or later.
The files are never imported by a test — they need `awsglue`, which exists only inside Glue — so
the first execution of any line in them happens in the cloud. That is the same shape as the
other findings this repository has collected: a path nothing exercises until it matters.

**A name list, and its limits stated.** This does not typecheck against 3.10; it looks for names
and syntax added after it. That catches the class of mistake a linter *encourages* — the modern
alias, the newer standard-library member — and it does not catch everything. It is a floor, not
a proof, and the honest thing is to say so here rather than to imply a guarantee. The real
guarantee would be running the suite under 3.10 against a stub `awsglue`, which is worth doing
and is not what this is.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Final

ROOT = Path(__file__).resolve().parents[1]
JOBS = ROOT / "pipelines" / "jobs"

#: The Python version Glue 4.0 runs. Not a preference — it is fixed by the `GlueVersion` in
#: `infra/lakehouse/maintenance.tf`, and moving one without the other is what this file is for.
GLUE_PYTHON: Final = (3, 10)

#: Names added to the standard library after 3.10, mapped to what works on both.
#:
#: Deliberately short. A long list assembled from release notes is a list nobody maintains and
#: everybody trusts; these are the ones a linter on a 3.12 codebase will actively push somebody
#: towards, which makes them the ones that get written by accident.
TOO_NEW: Final[dict[tuple[str, str], str]] = {
    ("datetime", "UTC"): "datetime.timezone.utc — `datetime.UTC` is 3.11+",
    ("typing", "Self"): "typing_extensions.Self, or a string annotation — `Self` is 3.11+",
    ("typing", "assert_never"): "a plain `raise` — `assert_never` is 3.11+",
    ("typing", "LiteralString"): "`str` — `LiteralString` is 3.11+",
    ("typing", "TypeAliasType"): "a plain alias — `TypeAliasType` is 3.12+",
    ("enum", "StrEnum"): "`class X(str, Enum)` — `StrEnum` is 3.11+",
    ("asyncio", "TaskGroup"): "`asyncio.gather` — `TaskGroup` is 3.11+",
    ("itertools", "batched"): "a manual slice — `itertools.batched` is 3.12+",
}

#: Builtins added after 3.10, for the same reason.
TOO_NEW_BUILTINS: Final[dict[str, str]] = {
    "ExceptionGroup": "a single exception — `ExceptionGroup` is 3.11+",
    "BaseExceptionGroup": "a single exception — `BaseExceptionGroup` is 3.11+",
}


def problems_in(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    found: list[str] = []

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as broken:
        # A syntax error under *this* interpreter is a different problem, and reporting it here
        # rather than crashing is what keeps a check from being the thing that breaks the build.
        return [f"{path.relative_to(ROOT)}: does not parse — {broken}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                replacement = TOO_NEW.get((node.module, alias.name))
                if replacement:
                    found.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: "
                        f"`from {node.module} import {alias.name}` — Glue runs Python "
                        f"{GLUE_PYTHON[0]}.{GLUE_PYTHON[1]}. Use {replacement}"
                    )
        # `datetime.UTC` reached through the module rather than imported by name.
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            replacement = TOO_NEW.get((node.value.id, node.attr))
            if replacement:
                found.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: "
                    f"`{node.value.id}.{node.attr}` — Glue runs Python "
                    f"{GLUE_PYTHON[0]}.{GLUE_PYTHON[1]}. Use {replacement}"
                )
        if isinstance(node, ast.Name) and node.id in TOO_NEW_BUILTINS:
            found.append(
                f"{path.relative_to(ROOT)}:{node.lineno}: `{node.id}` — Glue runs Python "
                f"{GLUE_PYTHON[0]}.{GLUE_PYTHON[1]}. Use {TOO_NEW_BUILTINS[node.id]}"
            )

    # `match` statements are 3.10, so they are fine; `except*` is 3.11 and is a syntax error
    # under it, which `ast.parse` here cannot see because this interpreter accepts it.
    if "except*" in source:
        found.append(
            f"{path.relative_to(ROOT)}: `except*` is 3.11+ and Glue runs "
            f"{GLUE_PYTHON[0]}.{GLUE_PYTHON[1]}"
        )
    return found


def main() -> int:
    jobs = sorted(JOBS.glob("*.py"))
    if not jobs:
        print(f"glue-runtime: no jobs found under {JOBS.relative_to(ROOT)}", file=sys.stderr)
        return 1

    problems = [problem for path in jobs for problem in problems_in(path)]
    for problem in problems:
        print(f"::error::{problem}")
    if problems:
        print(f"glue-runtime: {len(problems)} uses of Python newer than Glue runs")
        return 1

    print(
        f"glue-runtime: {len(jobs)} Glue jobs use nothing newer than Python "
        f"{GLUE_PYTHON[0]}.{GLUE_PYTHON[1]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
