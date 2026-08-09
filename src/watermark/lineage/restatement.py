"""What a published number was, what it became, and why.

Doctrine 4: a correction never erases what was previously stated. That is a statement about
*records*, not about intent — so the prior value has to exist somewhere a settlement report can
print it, in a shape that survives the run that produced it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from watermark.core.time import Instant
from watermark.core.windows import Emission, WindowResult
from watermark.lineage.identity import LineageId, of_result


@dataclass(frozen=True, slots=True)
class Restatement:
    """One published total moving, with everything needed to explain it to a customer.

    Deliberately self-contained. A restatement that only makes sense when joined against three
    other tables is a restatement nobody reconstructs at the moment somebody is disputing a
    bill.
    """

    meter_id: str
    interval_start: Instant
    revision: int
    #: What was previously stated. This is the field doctrine 4 exists for.
    previous_energy_wh: int
    new_energy_wh: int
    #: Signed. A restatement that lowered a total is a refund and one that raised it is a
    #: charge, and a magnitude cannot tell you which.
    delta_wh: int
    cause: str
    #: The watermark that permitted the restatement to be published — the same evidence for
    #: claim 1 that the original publication carried.
    closed_at: Instant
    lineage_id: LineageId
    supersedes_lineage_id: LineageId | None

    def as_row(self) -> dict[str, str | int | None]:
        """A flat, sorted-key mapping for a golden recording or a report.

        Explicit rather than `asdict`, because a field added to the dataclass should not
        silently change the bytes of every committed recording. It should fail the seed check
        and be added here on purpose.
        """
        return {
            "meter_id": self.meter_id,
            "interval_start": self.interval_start.to_iso(),
            "revision": self.revision,
            "previous_energy_wh": self.previous_energy_wh,
            "new_energy_wh": self.new_energy_wh,
            "delta_wh": self.delta_wh,
            "cause": self.cause,
            "closed_at": self.closed_at.to_iso(),
            "lineage_id": str(self.lineage_id),
            "supersedes_lineage_id": (
                None if self.supersedes_lineage_id is None else str(self.supersedes_lineage_id)
            ),
        }


def restatements_for(
    emission: Emission,
    contributing: dict[tuple[str, int], tuple[LineageId, ...]],
    previous_ids: dict[tuple[str, int], LineageId],
) -> tuple[Restatement, ...]:
    """Build a restatement record for every revised result in an emission.

    `contributing` and `previous_ids` are passed in rather than looked up, so this function
    stays a pure mapping from an emission to records. The caller owning the lineage store is
    the same caller that owns where it is persisted, and neither belongs here.
    """
    return tuple(_restatement(result, contributing, previous_ids) for result in emission.restated)


def _restatement(
    result: WindowResult,
    contributing: dict[tuple[str, int], tuple[LineageId, ...]],
    previous_ids: dict[tuple[str, int], LineageId],
) -> Restatement:
    key = (result.meter_id, result.interval_start.epoch_millis)
    assert result.supersedes is not None  # only revised results reach here
    return Restatement(
        meter_id=result.meter_id,
        interval_start=result.interval_start,
        revision=result.revision,
        previous_energy_wh=result.supersedes,
        new_energy_wh=result.energy_wh,
        delta_wh=result.delta_wh,
        cause=result.restatement_cause or "",
        closed_at=result.closed_at,
        lineage_id=of_result(result, contributing.get(key, ())),
        supersedes_lineage_id=previous_ids.get(key),
    )


def total_delta_wh(restatements: Iterable[Restatement]) -> int:
    """How much a batch of restatements moved the books, net.

    The number somebody asks for first after a three-day-late batch lands, and the one that is
    tedious to compute from a list of revisions at the moment it is wanted.
    """
    return sum(restatement.delta_wh for restatement in restatements)
