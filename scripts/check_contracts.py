#!/usr/bin/env python3
"""Validate the entity contract set. `make contracts-validate`, a CI step, a preflight check.

The reasoning is in `src/watermark/contracts/`. This is the runner, and the target of the
gate-proof mutations that strip a purpose from a personal-data entity and point a reference at
an entity that does not exist.
"""

from __future__ import annotations

import sys

from watermark.contracts import ContractError, load


def main() -> int:
    try:
        contracts = load()
    except ContractError as exc:
        print(exc, file=sys.stderr)
        return 1
    personal = contracts.personal_data_entities
    print(f"contracts: {len(contracts.entities)} entities load and cross-check")
    print(f"  personal data in {len(personal)}: {', '.join(personal)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
