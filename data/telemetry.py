"""Substation load, the second stream — and the input the curtailment decision cannot do without.

**Why this file did not exist until now, and what its absence meant.** `docs/SCENARIO.md` names
three decisions and `CLAUDE.md` puts curtailment first: throttle EV charging when a substation
approaches its thermal limit, on a horizon of seconds, argued high-risk under AI Act Annex III(2).
The core has carried `SubstationTelemetry` since phase 1 and `contracts/decisions/curtailment.yaml`
has declared a fallback rule computed from it. Nothing ever produced one. The estate published
meter readings and only meter readings, so the curtailment engine ran against `telemetry=None`,
withheld every time, and the *one decision in this system with a physical consequence* had never
been taken.

Withholding was the correct answer to the question it was asked. It was the wrong question.

**The load profile is deterministic and it crosses a limit on purpose.** A profile that stays
comfortably under every threshold exercises the arithmetic and proves nothing: every decision
comes back `release` and a broken comparison looks identical to a working one. `SUB-01` is driven
over its declared limit in the afternoon, so a capture contains both answers and the fallback has
something to be conservative about.

**Sampled once a minute, not once a second.** The record's docstring says once a second and that
is the scenario's real rate. A day of per-second telemetry across four substations is 345,600
messages, which at the publisher's 287x compression is seven hundred a second of telemetry alone
— a rate that would make the capture a test of IoT Core's ingest limits rather than of this
system's decisions. The period is named here rather than buried, so the difference between what
the scenario describes and what the capture drives is a number somebody can read.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Final

from data import cast
from watermark.core.time import Duration, Instant

#: How often the generator samples. See the module docstring for why this is not one second.
TELEMETRY_PERIOD: Final = Duration.of_seconds(60)

#: The substation driven past its thermal limit, so that a capture contains a `throttle` and not
#: only a day of `release`. Named for the same reason `SILENT_SUBSTATION` is: a case that only
#: exists as a magic number inside a formula is a case nobody can find again.
OVERLOADED_SUBSTATION: Final = "SUB-01"

#: Peak load as a fraction of the declared limit, in basis points, for each substation.
#:
#: `SUB-01` peaks at 106% of its limit — over, and not absurdly over. A profile that reached
#: 300% would make every threshold in the decision path fire identically and would test none of
#: them; the interesting region is the one where a forecast and a measurement can disagree.
PEAK_BASIS_POINTS: Final[dict[str, int]] = {
    "SUB-01": 10_600,
    "SUB-02": 9_200,
    "SUB-03": 7_800,
    "SUB-04": 8_500,
}


@dataclass(frozen=True, slots=True)
class Reading:
    """One telemetry sample, ready to publish.

    Carries the substation as its own field rather than only in the topic, because the payload
    has to survive a route that the topic does not: an S3 object holds the body and not the
    subject it arrived on.
    """

    substation_id: str
    event_time: Instant
    load_w: int
    limit_w: int

    def payload(self) -> str:
        """The wire shape. Integers, and no floats anywhere.

        `load_w` is watts as an exact integer for the same reason `energy_wh` is: a decision
        record, a threshold comparison and a published total must never contain a value two
        engines round differently. A float utilisation would put a tolerance inside a safety
        path, which is the one place ADR-0004 refuses to allow one.
        """
        return json.dumps(
            {
                "substation_id": self.substation_id,
                "event_time": self.event_time.to_iso(),
                "load_w": self.load_w,
                "limit_w": self.limit_w,
            },
            sort_keys=True,
            separators=(",", ":"),
        )


def _shape(minute_of_day: int) -> int:
    """The daily load curve, in basis points of the peak.

    Two humps — morning and evening — because that is what a distribution network does and
    because a flat profile would let a point-in-time limit change pass unnoticed. The limit moves
    at noon (`cast.substation_limits`), so the afternoon hump sits on the *other* side of that
    change: a decision taken at 18:00 against the morning's limit is a wrong decision that a flat
    curve could never reveal.

    Integer arithmetic throughout. A cosine would be the natural way to write this and would put
    a float in the one path that must not contain one.
    """
    # Morning hump centred at 08:00, evening at 19:00. The three constants sum to exactly
    # 10_000 at the evening peak, so `readings` can multiply by `PEAK_BASIS_POINTS` and get the
    # peak that table promises. The first version let them sum to 13_000, and every substation
    # sailed 40% over its limit — a profile in which "over the limit" is the normal state tests
    # the threshold exactly as poorly as one that never reaches it.
    base = 3_000
    morning = max(0, 4_000 - abs(minute_of_day - 8 * 60) * 25)
    evening = max(0, 7_000 - abs(minute_of_day - 19 * 60) * 30)
    return base + max(morning, evening)


def _jitter(substation_id: str, minute_of_day: int) -> int:
    """Deterministic noise, in basis points.

    Seeded from the substation and the minute, so a replay produces the same load — claim 2 is
    that the same day replayed produces identical bytes, and a generator reading a clock or an
    unseeded random would break it in the input rather than in the system under test.
    """
    digest = hashlib.sha256(f"{substation_id}|{minute_of_day}".encode()).hexdigest()
    return int(digest[:4], 16) % 400 - 200


def readings(day_start: Instant | None = None) -> tuple[Reading, ...]:
    """The whole day of telemetry for every substation, in event-time order.

    The limit on each reading is resolved **point-in-time** from the same SCD-2 history the rest
    of the system uses, not looked up once and reused. That is not decoration: the limit changes
    at noon, and a reading that carried the wrong one would make every decision taken against it
    wrong in a way the decision record could not show — the record states the limit it was judged
    against, so the limit has to be the one that was actually in force.
    """
    start = day_start or cast.DAY_START
    limits = cast.substation_limits()
    minutes = 24 * 60 // (TELEMETRY_PERIOD.millis // 60_000)
    out: list[Reading] = []

    for minute in range(minutes):
        at = start.plus(Duration.of_millis(minute * TELEMETRY_PERIOD.millis))
        minute_of_day = (minute * TELEMETRY_PERIOD.millis) // 60_000
        for substation in cast.SUBSTATIONS:
            declared = limits.attribute(substation, at, "limit_w")
            if declared is None:
                # A substation with no limit in force has no basis for a decision, and inventing
                # one here would be inventing the safety envelope. It is skipped and the absence
                # is visible as a gap in the telemetry rather than as a plausible number.
                continue
            limit_w = int(declared)
            peak = PEAK_BASIS_POINTS.get(substation, 8_000)
            shape = _shape(minute_of_day) + _jitter(substation, minute_of_day)
            load_w = limit_w * peak // 10_000 * max(0, shape) // 10_000
            out.append(Reading(substation, at, load_w, limit_w))

    return tuple(out)


def digest(samples: tuple[Reading, ...]) -> str:
    """A hash of the telemetry in event-time order, for the replay comparison.

    Same role as `data.generate.digest`: two runs that disagree here were driven by different
    inputs, and claim 2's comparison would be reporting on the generator rather than on the
    system.
    """
    material = "\n".join(f"{r.event_time.to_iso()}|{r.substation_id}|{r.load_w}" for r in samples)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
