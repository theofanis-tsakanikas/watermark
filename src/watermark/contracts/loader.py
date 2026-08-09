"""Loading the contract set, and the checks that only make sense across all of it.

`model.py` validates one contract. This validates the *set*, which is where the interesting
defects live: a reference to an entity that was renamed, two contracts claiming one id, a
personal-data entity reachable from one that says it holds none.

Nothing here imports a contract by name. The set is whatever is in the directory, so adding an
entity is adding a file — and forgetting to register it somewhere is not a failure mode this
repository has.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from watermark.contracts.model import EntityContract

DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "contracts"


class ContractError(Exception):
    """A contract set that cannot be trusted. Loading fails; nothing partial is returned."""


@dataclass(frozen=True, slots=True)
class ContractSet:
    """Every entity contract, indexed and cross-checked."""

    entities: dict[str, EntityContract]

    def __getitem__(self, entity_id: str) -> EntityContract:
        return self.entities[entity_id]

    @property
    def personal_data_entities(self) -> tuple[str, ...]:
        """The erasure scope, derived rather than maintained.

        Claim 6 has to enumerate everywhere a subject appears. A hand-kept list of tables would
        be right on the day it was written; this is right on the day it is read.
        """
        return tuple(
            sorted(
                entity_id for entity_id, contract in self.entities.items() if contract.personal_data
            )
        )


def load(root: Path = DEFAULT_ROOT) -> ContractSet:
    """Load and cross-check every entity contract under `root/entities/`."""
    directory = root / "entities"
    if not directory.is_dir():
        raise ContractError(f"no entity contracts at {directory}")

    contracts: dict[str, EntityContract] = {}
    problems: list[str] = []

    for path in sorted(directory.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            problems.append(f"{path.name}: not valid YAML: {exc}")
            continue
        if not isinstance(raw, dict):
            problems.append(f"{path.name}: a contract is a mapping, not {type(raw).__name__}")
            continue
        try:
            contract = EntityContract.model_validate(raw)
        except ValidationError as exc:
            problems.append(f"{path.name}: {_readable(exc)}")
            continue

        # The filename and the id must agree. They are two names for one thing, and when they
        # drift a reviewer reading the directory sees a set that does not exist.
        if contract.id != path.stem:
            problems.append(f"{path.name}: declares id '{contract.id}'")
        if contract.id in contracts:
            problems.append(f"{path.name}: id '{contract.id}' is already taken")
        contracts[contract.id] = contract

    problems.extend(_dangling_references(contracts.values(), set(contracts)))
    problems.extend(_leaking_references(contracts))

    if problems:
        raise ContractError(
            "the entity contract set does not load:\n  " + "\n  ".join(sorted(problems))
        )
    return ContractSet(contracts)


def _readable(error: ValidationError) -> str:
    """Pydantic's message, without the URL and the noise, keeping our own text intact."""
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or 'contract'}: {item['msg']}"
        for item in error.errors()
    )


def _dangling_references(contracts: Iterable[EntityContract], known: set[str]) -> Iterable[str]:
    for contract in contracts:
        for reference in contract.references:
            if reference.entity not in known:
                yield (
                    f"{contract.id}: references entity '{reference.entity}', which does not "
                    "exist. A join written against it compiles, returns nothing, and reads as "
                    "a customer with no consumption."
                )


def _leaking_references(contracts: dict[str, EntityContract]) -> Iterable[str]:
    """An entity that says it holds no personal data must not point at one that does.

    The reach of GDPR follows joins, not table names. `substation` holds nothing personal and
    is correctly declared so; if it grew a reference to `customer`, every substation row would
    become linkable to a household and the erasure scope in claim 6 would silently be one table
    short. This is the check that turns that from an oversight into a failed build.
    """
    for contract in contracts.values():
        if contract.personal_data:
            continue
        for reference in contract.references:
            target = contracts.get(reference.entity)
            if target is not None and target.personal_data:
                yield (
                    f"{contract.id}: declares personal_data: false and references "
                    f"'{target.id}', which holds personal data. The reach of GDPR follows the "
                    "join, so either this entity is personal data too or the reference does "
                    "not belong here — and leaving it decides that the erasure scope is one "
                    "table short."
                )
