"""Event time, defined once.

Every window boundary, every watermark, every lateness judgement and every point-in-time join
in this system is an arithmetic statement about instants. If there are two answers to *what is
an instant and how are two of them compared*, those answers diverge on the first busy
afternoon and claims 1, 2 and 3 quietly stop meaning anything: a replay produces a different
number, and nothing in the suite can say why.

So this module is small on purpose, and four decisions are baked into it.

**An instant is an integer count of milliseconds since the Unix epoch, in UTC.** Not a
`datetime`. A `datetime` can be naive, carries a timezone that participates in comparison, and
round-trips through parsing with a resolution nobody declared. An integer has exactly one
representation, one ordering, and one serialisation — which is what claim 2 (byte-identical
replay) is actually asking for.

**Milliseconds, because that is what Flink carries.** Flink timestamps and watermarks are
`long` milliseconds. A core that reasoned in microseconds would be claiming a precision the
runtime cannot carry, and the difference would surface as an off-by-one at a window boundary
in production and nowhere in the tests.

**A timestamp with no offset is refused, never assumed to be UTC.** Event time comes off a
device. Assuming UTC for a device that was actually reporting local time is how clock skew
becomes invisible — the readings land in the wrong window, every window still closes, and
nothing anywhere reports an error. `docs/SCENARIO.md` lists clock skew as a pathology to
quarantine with a reason; it cannot be quarantined if the parser has already guessed.

**Sub-millisecond precision is floored, never rounded.** Flooring can only move an event time
*earlier*. Rounding can move it later, and an event time nudged later is precisely what makes
a late event look on time — the one direction of error this project exists to prevent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

_MILLIS_PER_SECOND: Final = 1_000
_MILLIS_PER_MINUTE: Final = 60 * _MILLIS_PER_SECOND
_MILLIS_PER_HOUR: Final = 60 * _MILLIS_PER_MINUTE
_MILLIS_PER_DAY: Final = 24 * _MILLIS_PER_HOUR

#: An ISO-8601 timestamp is only accepted with an explicit offset — `Z`, `+03:00` or `-0500`.
#: `datetime.fromisoformat` accepts a naive string happily, so the refusal has to happen here,
#: before the value is parsed and its ambiguity forgotten.
_HAS_OFFSET: Final = re.compile(r"(?:Z|z|[+-]\d{2}:?\d{2})$")


class EventTimeError(ValueError):
    """A timestamp that cannot be turned into an unambiguous instant.

    A `ValueError`, because that is what a caller parsing a payload is already prepared for,
    and a distinct type, because the ingestion path quarantines these with a reason rather
    than dropping them.
    """


@dataclass(frozen=True, order=True, slots=True)
class Duration:
    """A signed span of milliseconds.

    Signed, because the quantities this system cares most about are differences that can go
    either way: `event_time - ingest_time` is negative for a late reading and positive for a
    device whose clock runs fast, and collapsing the two into a magnitude would throw away the
    only bit that distinguishes lateness from skew.

    Integer, because a window is exact. A 15-minute window built from a float is a window that
    is occasionally 899,999.9 ms long, and the reading on the boundary lands in whichever of
    two windows the rounding happened to pick.
    """

    millis: int

    def __post_init__(self) -> None:
        if not isinstance(self.millis, int) or isinstance(self.millis, bool):
            raise EventTimeError(f"a duration is an integer count of milliseconds: {self.millis!r}")

    # ── Constructors ─────────────────────────────────────────────────────────
    # `int` in, `int` out, at every step. There is deliberately no `of_millis(float)`.

    @classmethod
    def of_millis(cls, millis: int) -> Duration:
        return cls(millis)

    @classmethod
    def of_seconds(cls, seconds: int) -> Duration:
        return cls(seconds * _MILLIS_PER_SECOND)

    @classmethod
    def of_minutes(cls, minutes: int) -> Duration:
        return cls(minutes * _MILLIS_PER_MINUTE)

    @classmethod
    def of_hours(cls, hours: int) -> Duration:
        return cls(hours * _MILLIS_PER_HOUR)

    @classmethod
    def of_days(cls, days: int) -> Duration:
        """Exactly 24 hours.

        Not a calendar day. Nothing in the stream path may depend on a local calendar: a
        24-hour "day" is 23 or 25 hours twice a year in every timezone this operator runs in,
        and a settlement total that changes length twice a year is a restatement nobody asked
        for. Where a calendar boundary is genuinely required — the hourly and daily settlement
        grains — it is resolved against a declared timezone in the settlement layer, not here.
        """
        return cls(days * _MILLIS_PER_DAY)

    # ── Arithmetic ───────────────────────────────────────────────────────────

    def __add__(self, other: Duration) -> Duration:
        return Duration(self.millis + other.millis)

    def __sub__(self, other: Duration) -> Duration:
        return Duration(self.millis - other.millis)

    def __neg__(self) -> Duration:
        return Duration(-self.millis)

    def __mul__(self, factor: int) -> Duration:
        if not isinstance(factor, int) or isinstance(factor, bool):
            raise EventTimeError(f"a duration scales by an integer, not by {factor!r}")
        return Duration(self.millis * factor)

    @property
    def is_positive(self) -> bool:
        return self.millis > 0

    def __str__(self) -> str:
        return f"{self.millis}ms"


@dataclass(frozen=True, order=True, slots=True)
class Instant:
    """A point in time: milliseconds since the Unix epoch, UTC.

    Frozen and ordered, so it can be a dictionary key, a set member and a sort key without
    anyone having to remember which. Those three uses are the whole of deduplication,
    window assignment and point-in-time resolution.
    """

    epoch_millis: int

    def __post_init__(self) -> None:
        if not isinstance(self.epoch_millis, int) or isinstance(self.epoch_millis, bool):
            raise EventTimeError(
                f"an instant is an integer count of milliseconds: {self.epoch_millis!r}"
            )

    # ── Constructors ─────────────────────────────────────────────────────────

    @classmethod
    def from_epoch_millis(cls, millis: int) -> Instant:
        return cls(millis)

    @classmethod
    def from_iso(cls, text: str) -> Instant:
        """Parse an ISO-8601 timestamp that carries an explicit offset.

        Refuses a naive timestamp rather than assuming UTC — see the module docstring. Also
        refuses a value that is not a string at all, because a payload field that arrived as
        an integer is a schema variant to normalise, not a timestamp to guess at.
        """
        if not isinstance(text, str):
            raise EventTimeError(f"a timestamp is a string, not {type(text).__name__}")
        candidate = text.strip()
        if not _HAS_OFFSET.search(candidate):
            raise EventTimeError(
                f"timestamp {text!r} carries no UTC offset; a device timestamp is never "
                "assumed to be UTC, because a device reporting local time would then land in "
                "the wrong window with nothing reporting an error"
            )
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise EventTimeError(f"timestamp {text!r} is not ISO-8601: {exc}") from exc
        if parsed.tzinfo is None:  # pragma: no cover — _HAS_OFFSET has already refused these
            raise EventTimeError(f"timestamp {text!r} carries no UTC offset")
        return cls._from_datetime(parsed)

    @classmethod
    def _from_datetime(cls, moment: datetime) -> Instant:
        """Floor an aware `datetime` to the millisecond.

        `//` on a negative microsecond count floors toward the past, which is the behaviour
        wanted here and *not* what `int()` would do. Pre-epoch instants do not occur in this
        domain, but a rule that is only correct for positive numbers is a rule that fails the
        first time somebody uses it somewhere else.
        """
        micros = int(moment.astimezone(UTC).timestamp() * 1_000_000)
        return cls(micros // 1_000)

    # ── Rendering ────────────────────────────────────────────────────────────

    def to_iso(self) -> str:
        """The canonical rendering: always UTC, always `Z`, always three decimal places.

        One instant, one string, byte for byte. `datetime.isoformat()` omits the fractional
        part when it is zero, which would make two runs of the same data differ in the
        serialised output while agreeing on every value — and claim 2 is a claim about bytes.
        """
        moment = datetime.fromtimestamp(self.epoch_millis / 1000, tz=UTC)
        return f"{moment.strftime('%Y-%m-%dT%H:%M:%S')}.{self.epoch_millis % 1000:03d}Z"

    def __str__(self) -> str:
        return self.to_iso()

    # ── Arithmetic ───────────────────────────────────────────────────────────

    def plus(self, span: Duration) -> Instant:
        return Instant(self.epoch_millis + span.millis)

    def minus(self, span: Duration) -> Instant:
        return Instant(self.epoch_millis - span.millis)

    def since(self, earlier: Instant) -> Duration:
        """How long after `earlier` this instant is. Negative when it is before it."""
        return Duration(self.epoch_millis - earlier.epoch_millis)

    def floor_to(self, span: Duration) -> Instant:
        """The start of the `span`-aligned interval containing this instant.

        Aligned to the epoch, so every participant computes the same boundary without needing
        to agree on an origin: a 15-minute grain gives :00, :15, :30, :45 for anybody who asks.

        Floor division rather than truncation, for the same reason as `_from_datetime`.
        """
        if not span.is_positive:
            raise EventTimeError(f"cannot align to a non-positive interval: {span}")
        return Instant(self.epoch_millis - self.epoch_millis % span.millis)
