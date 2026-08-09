"""Why a record did not become a reading — as a closed vocabulary.

A rejected record is not an error. It is an *outcome*, and it is one somebody has to account
for: a settlement total that is short by three meters is short for a reason, and "the pipeline
logged an exception" is not one. So every way a record can fail to enter the system has a
name, the name is in this list, and nothing may invent a new one at the call site.

The vocabulary is closed on purpose. Free-text reasons are unaggregatable — a hundred
variations of "bad timestamp" cannot be counted, alerted on, or compared between two runs, and
the first thing anybody asks of a quarantine queue is *how many, of what*.

**Recoverable or terminal** is the second thing each reason carries, and it decides what the
operator does rather than how the record is filed:

- **Recoverable** — the record is well-formed and the system could accept it later. A reading
  that arrived after its window closed is a real measurement of real electricity, and it will
  restate a published total (doctrine 4). It is quarantined, not discarded.
- **Terminal** — the record cannot become a reading no matter what happens next. An unknown
  firmware shape is not going to become known by waiting.

The distinction matters because a recoverable record that is silently dropped is lost revenue
and a wrong settlement, while a terminal one that is retried forever is a queue that never
drains.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final


class Disposition(Enum):
    """What can still happen to a quarantined record."""

    RECOVERABLE = "recoverable"
    TERMINAL = "terminal"


class Reason(Enum):
    """Every way a record fails to become a reading. Nothing outside this enum is a reason."""

    #: The payload does not match any firmware shape the normaliser knows. Terminal: a shape
    #: does not become known by waiting. It becomes known by somebody reading the payload and
    #: adding a variant, which is a commit.
    UNKNOWN_PAYLOAD_SHAPE = "unknown_payload_shape"

    #: A field the shape requires is absent, or present and unusable — a null meter id, an
    #: energy value that is not a number.
    MALFORMED_FIELD = "malformed_field"

    #: The energy unit is not one the normaliser converts. Terminal, and deliberately not
    #: "assume kWh": a factor of a thousand in a settlement total is not a rounding error.
    UNKNOWN_UNIT = "unknown_unit"

    #: The device's clock claims a time far enough in the future to be impossible. Terminal
    #: rather than clamped: clamping would place a real measurement in the wrong interval and
    #: leave nothing to notice, which is precisely how clock skew becomes invisible.
    CLOCK_SKEW_FUTURE = "clock_skew_future"

    #: Negative interval energy from a meter that cannot export, or a quantity no meter of this
    #: class could record in fifteen minutes. Terminal — it is a fault or a tamper signature,
    #: and either way it is evidence rather than a measurement.
    IMPLAUSIBLE_VALUE = "implausible_value"

    #: The device reported energy at a finer resolution than the canonical unit can hold —
    #: 0.3125 kWh is 312.5 Wh. Its own reason rather than a malformed field, because the
    #: question it answers is a different one: a null meter id is a broken device, while this
    #: is a fleet-wide statement that the canonical unit is too coarse, and it is only visible
    #: if it can be counted separately. Terminal, and deliberately not rounded: rounding here
    #: loses energy that a settlement total is supposed to balance.
    PRECISION_BEYOND_CANONICAL_UNIT = "precision_beyond_canonical_unit"

    #: The reading is older than the allowed lateness for its window, which has closed and been
    #: published. Recoverable: it is a real measurement, and it restates rather than vanishes.
    TOO_LATE_FOR_WINDOW = "too_late_for_window"


#: The disposition of every reason, exhaustively. A dict rather than a property on the enum so
#: that adding a member without deciding its disposition fails a test rather than defaulting to
#: whichever answer the author happened to be thinking about.
DISPOSITIONS: Final[dict[Reason, Disposition]] = {
    Reason.UNKNOWN_PAYLOAD_SHAPE: Disposition.TERMINAL,
    Reason.MALFORMED_FIELD: Disposition.TERMINAL,
    Reason.UNKNOWN_UNIT: Disposition.TERMINAL,
    Reason.CLOCK_SKEW_FUTURE: Disposition.TERMINAL,
    Reason.IMPLAUSIBLE_VALUE: Disposition.TERMINAL,
    Reason.PRECISION_BEYOND_CANONICAL_UNIT: Disposition.TERMINAL,
    Reason.TOO_LATE_FOR_WINDOW: Disposition.RECOVERABLE,
}


@dataclass(frozen=True, slots=True)
class Quarantined:
    """A record that did not become a reading, and everything needed to act on it.

    `detail` is free text and `reason` is not. The reason is what gets counted; the detail is
    what gets read by the one person investigating a specific record, and it exists so that
    nobody is ever tempted to encode the specifics into a new reason code.
    """

    reason: Reason
    detail: str
    #: The record as it arrived, untouched. A quarantine queue holding a partially normalised
    #: record is a queue nobody can reprocess, because the normalisation is the thing under
    #: suspicion.
    payload: str

    @property
    def disposition(self) -> Disposition:
        return DISPOSITIONS[self.reason]

    @property
    def is_recoverable(self) -> bool:
        return self.disposition is Disposition.RECOVERABLE

    def __str__(self) -> str:
        return f"{self.reason.value} [{self.disposition.value}] {self.detail}"
