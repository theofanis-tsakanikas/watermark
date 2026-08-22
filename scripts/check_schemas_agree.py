#!/usr/bin/env python3
"""The registered payload schema and the normaliser describe the same fleet.

Two files decide which shapes a meter may publish, and until now nothing held them equal.

`infra/streaming/schemas/meter_reading.json` is registered with the Glue Schema Registry, where
it is a **registration-time** gate: it refuses a fourth firmware generation whose shape the
running job could not read, before a single device is flashed. It is not in the data path. The
IoT rule base64-encodes the payload and forwards it; nothing between the device and the stream
validates a message against it.

`src/watermark/core/normalise.py` is the runtime. `_discriminate` picks a generation by the one
key only that generation uses, and `_SHAPES` maps the answer to an extractor. A payload matching
no entry is quarantined as `UNKNOWN_PAYLOAD_SHAPE`.

**The two can drift in both directions and each is silent in its own way.**

*Registered and not implemented.* Somebody adds `fw4` to the schema, the registry accepts it as
backward-compatible, the rollout is approved on that basis, and every reading from the new
cohort is quarantined — correctly, and three weeks after the firmware shipped. The registry's
whole purpose is to answer that question *before* the flash, and it answered about a consumer
that does not exist.

*Implemented and not registered.* The extractor is added, the readings parse, and the registry
still describes a fleet that stopped being real. The next compatibility check is then evaluated
against the wrong union, which is worse than having no registry: it is a gate that reports on a
shape nobody publishes.

So this compares the two on the three things both sides commit to writing down — the set of
generations, the discriminator key each one is recognised by, and that the key is `required` in
the schema rather than merely permitted. A discriminator that a payload may omit is a
discriminator that cannot discriminate.

**Only `meter_reading.json` is compared.** `substation_telemetry.json` is a single shape with no
union and no discriminator, and the telemetry path does not go through `normalise.py` at all —
it lands as whole JSON objects in S3. A check that pretended to cover it would be reporting on a
mechanism that is not there.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
SCHEMA: Final = ROOT / "infra" / "streaming" / "schemas" / "meter_reading.json"
NORMALISE: Final = ROOT / "src" / "watermark" / "core" / "normalise.py"


def _registered() -> dict[str, str | None]:
    """Every generation in the union, and the property that identifies it.

    The discriminator is found rather than named here: it is the one property carrying a
    `const`, which is what makes it a discriminator instead of a field. Naming it in this file
    would be a third place for the vocabulary to live, and a third place is a third thing to
    drift.
    """
    document = json.loads(SCHEMA.read_text(encoding="utf-8"))
    branches = document.get("oneOf")
    if not isinstance(branches, list) or not branches:
        raise SystemExit(f"{SCHEMA.name}: expected a non-empty `oneOf` union of firmware shapes")

    found: dict[str, str | None] = {}
    for index, branch in enumerate(branches):
        title = branch.get("title")
        if not isinstance(title, str) or not title:
            raise SystemExit(f"{SCHEMA.name}: branch {index} has no `title` naming its generation")
        properties = branch.get("properties", {})
        required = set(branch.get("required", []))
        constants = [
            name
            for name, definition in properties.items()
            if isinstance(definition, dict) and "const" in definition and name in required
        ]
        # None rather than an exception: "no discriminator" is a finding this check reports
        # beside the others, not a crash that hides whatever else is wrong.
        found[title] = constants[0] if len(constants) == 1 else None
    return found


def _parsed() -> ast.Module:
    """`normalise.py` as a tree.

    Read with `ast` rather than by importing. The module imports the rest of the core, and a
    check that runs its subject in order to inspect it is a check that passes because the import
    succeeded.
    """
    return ast.parse(NORMALISE.read_text(encoding="utf-8"))


def _discriminators(tree: ast.Module) -> dict[str, str]:
    """Which key selects which generation, read from `_discriminate` and nowhere else.

    Scoped to that function rather than to the module. Walking the whole file for a loop over
    pairs would read whichever one it met first, and a second such loop added later would be
    picked up silently — a check reading the wrong table is worse than no check, because it
    reports agreement about something nobody asked it.
    """
    body = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_discriminate"
        ),
        None,
    )
    if body is None:
        raise SystemExit(f"{NORMALISE.name}: no `_discriminate` to read the vocabulary from")

    found: dict[str, str] = {}
    for node in ast.walk(body):
        # `for key, firmware in (("v", "fw1"), …):` — the discriminator table.
        if not (isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple)):
            continue
        for pair in node.iter.elts:
            if not (isinstance(pair, ast.Tuple) and len(pair.elts) == 2):  # noqa: PLR2004
                continue
            key, firmware = pair.elts
            if isinstance(key, ast.Constant) and isinstance(firmware, ast.Constant):
                found[str(firmware.value)] = str(key.value)

    if not found:
        raise SystemExit(f"{NORMALISE.name}: found no discriminator table to read")
    return found


def _extractors(tree: ast.Module) -> set[str]:
    """The generations `_SHAPES` maps to an extractor — the ones that can actually be parsed."""
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target, value = node.targets[0].id, node.value
        else:
            continue
        if target == "_SHAPES" and isinstance(value, ast.Dict):
            return {str(key.value) for key in value.keys if isinstance(key, ast.Constant)}

    raise SystemExit(f"{NORMALISE.name}: found no `_SHAPES` mapping to read")


def main() -> int:
    registered = _registered()
    tree = _parsed()
    discriminated, shapes = _discriminators(tree), _extractors(tree)

    problems: list[str] = []

    for generation in sorted(set(registered) - shapes):
        problems.append(
            f"`{generation}` is registered in {SCHEMA.name} and `normalise.py` has no extractor "
            f"for it. The registry would accept a rollout of it as compatible; every reading "
            f"from that cohort would be quarantined as an unknown shape."
        )

    for generation in sorted(shapes - set(registered)):
        problems.append(
            f"`{generation}` is implemented in `normalise.py` and is not in {SCHEMA.name}. The "
            f"registry describes a fleet that has stopped being real, and the next compatibility "
            f"check is evaluated against the wrong union."
        )

    for generation, key in sorted(registered.items()):
        if generation not in shapes:
            continue  # already reported above; one finding per drift
        if key is None:
            problems.append(
                f"`{generation}` has no single `required` property carrying a `const` in "
                f"{SCHEMA.name}. A discriminator a payload may omit cannot discriminate, and "
                f"`oneOf` would then match on shape, which is what guessing looks like."
            )
            continue
        implemented = discriminated.get(generation)
        if implemented != key:
            problems.append(
                f"`{generation}` is identified by `{key}` in {SCHEMA.name} and by "
                f"`{implemented}` in `normalise.py`. The two disagree about which field names "
                f"the generation, so a payload valid to the registry is unreadable to the core."
            )

    for generation in sorted(shapes - set(discriminated)):
        problems.append(
            f"`{generation}` has an extractor in `_SHAPES` and no entry in the discriminator "
            f"table, so nothing can ever select it."
        )

    if problems:
        print(
            "schemas-agree: the registered schema and the normaliser describe different fleets\n",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    named = ", ".join(sorted(shapes))
    print(
        f"schemas-agree: {len(shapes)} firmware generations ({named}), each registered, "
        f"implemented, and recognised by the same required discriminator"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
