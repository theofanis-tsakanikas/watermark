"""Everywhere a subject appears — derived from the contracts, never maintained by hand.

A hand-kept list of tables is right on the day it is written. This is right on the day it is
read, which is the only day that matters: an erasure request arrives against whatever the
platform looks like then, and the table added last month is exactly the one nobody remembers.
"""

from __future__ import annotations

from dataclasses import dataclass

from watermark.contracts import ContractSet


@dataclass(frozen=True, slots=True)
class ErasureScope:
    """Everything one erasure has to reach, and the one thing it cannot."""

    #: Entity contracts declaring personal data.
    entities: tuple[str, ...]
    #: Feature contracts derived from personal data. These are the offline and online store
    #: records — the leg that is easy to forget because a feature does not look like a table.
    features: tuple[str, ...]
    #: Lake Formation resources tagged `personal`. Derived from the policy so that a table
    #: nobody wrote a contract for is still in scope if it is tagged.
    resources: tuple[str, ...]

    #: Models whose training set included the subject. **This leg cannot be completed by
    #: deletion** — see the package docstring.
    models: tuple[str, ...]

    @property
    def legs(self) -> tuple[str, ...]:
        return (
            "crypto_shred",
            "lakehouse_rows",
            "offline_store",
            "online_store",
            "training_sets",
            "model_artefacts",
        )


def scope_from_contracts(
    contracts: ContractSet,
    personal_resources: tuple[str, ...] = (),
    models: tuple[str, ...] = (),
) -> ErasureScope:
    """Derive the scope. Nothing here is a literal list of tables."""
    return ErasureScope(
        entities=contracts.personal_data_entities,
        features=contracts.personal_data_features,
        resources=tuple(sorted(personal_resources)),
        models=tuple(sorted(models)),
    )
