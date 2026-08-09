"""What moved between two settlement runs, at the grain an invoice is issued at.

`watermark.lineage.restatement` records a *window* moving. This records an *hour* moving, which
is the grain somebody was actually billed at — and it is a different record rather than the
same one aggregated, because the question is different. A window restatement answers "which
reading changed"; an hourly restatement answers "why is this invoice not the number I was sent
last month, and by how much".
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from watermark.core.time import Instant
from watermark.settlement.totals import HourlyTotal


@dataclass(frozen=True, slots=True)
class SettlementRestatement:
    """One settled hour moving, self-contained enough to print beside an invoice line."""

    meter_id: str
    hour_start: Instant
    previous_energy_wh: int
    new_energy_wh: int
    delta_wh: int
    previous_revision: int
    revision: int
    #: Whether the hour became complete, stayed complete, or is still short. A total that moved
    #: *and* is still missing an interval will move again, and saying so is the difference
    #: between a correction and a correction somebody has to chase.
    now_complete: bool

    def as_row(self) -> dict[str, str | int | bool]:
        return {
            "meter_id": self.meter_id,
            "hour_start": self.hour_start.to_iso(),
            "previous_energy_wh": self.previous_energy_wh,
            "new_energy_wh": self.new_energy_wh,
            "delta_wh": self.delta_wh,
            "previous_revision": self.previous_revision,
            "revision": self.revision,
            "now_complete": self.now_complete,
        }


def compare(
    before: Iterable[HourlyTotal], after: Iterable[HourlyTotal]
) -> tuple[SettlementRestatement, ...]:
    """Every settled hour that moved between two runs, in content order.

    Hours that appear only in `after` are new, not restated: nothing was previously stated
    about them, so there is nothing to supersede. Recording them here would put a row reading
    "was 0, now 312" in front of a customer who was never invoiced for the zero.
    """
    prior = {(total.meter_id, total.hour_start.epoch_millis): total for total in before}
    moved = []
    for total in after:
        key = (total.meter_id, total.hour_start.epoch_millis)
        previous = prior.get(key)
        if previous is None or previous.energy_wh == total.energy_wh:
            continue
        moved.append(
            SettlementRestatement(
                meter_id=total.meter_id,
                hour_start=total.hour_start,
                previous_energy_wh=previous.energy_wh,
                new_energy_wh=total.energy_wh,
                delta_wh=total.energy_wh - previous.energy_wh,
                previous_revision=previous.revision,
                revision=total.revision,
                now_complete=total.is_complete,
            )
        )
    return tuple(sorted(moved, key=lambda item: (item.hour_start.epoch_millis, item.meter_id)))


def net_delta_wh(restatements: Iterable[SettlementRestatement]) -> int:
    return sum(restatement.delta_wh for restatement in restatements)
