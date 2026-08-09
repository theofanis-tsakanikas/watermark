"""Point-in-time resolution against slowly changing reference data.

A meter changes customer. A tariff changes mid-period. A substation's thermal limit changes
with the season. Every one of those is reference data with a history, and every join against it
has to ask *what was true at the time of the event*, not what is true now.

Getting this wrong has two distinct costs, and the second is the expensive one.

**The obvious cost** is a wrong number: last month's reading priced at this month's tariff.
Somebody notices, eventually, because a customer complains.

**The costly one is label leakage.** A model trained on features joined against *current*
reference data has been shown the future — the meter's customer as it is today, after the
tampering investigation reassigned it. The model scores beautifully in evaluation and is
useless in production, and nothing about the evaluation reveals why. `docs/SCENARIO.md` names
this as the classic route into it, and ADR-0004 plants a case for the parity harness to catch.

So there is exactly one definition of the join, here, and every query and every feature uses
it. Two definitions is the arrangement in which one of them is wrong and nobody can say which.

**Intervals are half-open: `[valid_from, valid_to)`.** An event at exactly the instant a tariff
changes resolves to the *new* tariff. The choice matters less than the fact that it is made
once — a closed interval on both sides makes two versions valid at a boundary, and whichever
one a query returns is then a property of its `ORDER BY`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

from watermark.core.time import Instant


@dataclass(frozen=True, slots=True)
class Version:
    """One version of one entity, valid over a half-open interval of event time."""

    entity_id: str
    valid_from: Instant
    #: `None` means still current. Exactly one open version per entity, which `problems` checks.
    valid_to: Instant | None
    attributes: Mapping[str, str]

    def covers(self, moment: Instant) -> bool:
        if moment.epoch_millis < self.valid_from.epoch_millis:
            return False
        return self.valid_to is None or moment.epoch_millis < self.valid_to.epoch_millis

    def sort_key(self) -> tuple[str, int]:
        return (self.entity_id, self.valid_from.epoch_millis)


@dataclass(frozen=True, slots=True)
class History:
    """Every version of every entity of one kind — meters, tariffs, substation limits.

    Built through `of`, which sorts, so that resolution never depends on the order rows came
    back from a catalogue.
    """

    kind: str
    versions: tuple[Version, ...]

    @staticmethod
    def of(kind: str, versions: Iterable[Version]) -> History:
        return History(kind, tuple(sorted(versions, key=Version.sort_key)))

    def resolve(self, entity_id: str, as_of: Instant) -> Version | None:
        """The version in force for this entity at this instant, or `None`.

        `None` is a real answer, not a failure: a meter has no customer before it is installed,
        and a reading from that period genuinely has nobody to bill. Returning the nearest
        version instead — the usual "helpful" behaviour — invents a commercial relationship
        that did not exist, and it does so most often at exactly the boundary a dispute is
        about.
        """
        for version in self.versions:
            if version.entity_id == entity_id and version.covers(as_of):
                return version
        return None

    def attribute(self, entity_id: str, as_of: Instant, name: str) -> str | None:
        version = self.resolve(entity_id, as_of)
        return None if version is None else version.attributes.get(name)


@dataclass(frozen=True, slots=True)
class Problem:
    """A defect in a history that makes point-in-time resolution ambiguous or impossible."""

    entity_id: str
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"{self.entity_id} [{self.kind}] {self.detail}"


def problems(history: History) -> tuple[Problem, ...]:
    """Every way this history cannot be resolved against, in a stable order.

    Validated rather than trusted, because SCD-2 data arrives from a CDC pipeline against an
    operational database that was not built to produce it. The three defects below all resolve
    *silently* to a plausible wrong answer:

    - **Overlapping versions** — two tariffs valid at once. `resolve` returns whichever sorts
      first, so the answer is stable and arbitrary, which is worse than an error.
    - **A backwards interval** — `valid_to` before `valid_from`. Covers nothing, so every
      reading in the period resolves to `None` and quietly loses its customer.
    - **Two open versions** — the entity has two presents. Every future reading resolves to one
      of them by sort order.

    Gaps are deliberately *not* a problem. A meter uninstalled for a month has a real gap, and
    flagging it would train whoever reads this to ignore the output.
    """
    found: list[Problem] = []
    by_entity: dict[str, list[Version]] = {}
    for version in history.versions:
        by_entity.setdefault(version.entity_id, []).append(version)

    for entity_id in sorted(by_entity):
        versions = sorted(by_entity[entity_id], key=Version.sort_key)
        found.extend(_backwards(versions))
        found.extend(_overlaps(versions))
        found.extend(_multiple_open(entity_id, versions))
    return tuple(found)


def _backwards(versions: Sequence[Version]) -> Iterable[Problem]:
    for version in versions:
        if version.valid_to is not None and (
            version.valid_to.epoch_millis < version.valid_from.epoch_millis
        ):
            yield Problem(
                version.entity_id,
                "backwards_interval",
                f"valid_to {version.valid_to} precedes valid_from {version.valid_from}, so this "
                "version covers nothing and every reading in the period silently resolves to "
                "no version at all",
            )


def _overlaps(versions: Sequence[Version]) -> Iterable[Problem]:
    for earlier, later in pairwise(versions):
        if earlier.valid_to is None or (
            earlier.valid_to.epoch_millis > later.valid_from.epoch_millis
        ):
            yield Problem(
                earlier.entity_id,
                "overlapping_versions",
                f"the version from {earlier.valid_from} runs to "
                f"{'no end' if earlier.valid_to is None else earlier.valid_to} and the next "
                f"begins at {later.valid_from}; two versions are in force at once, and "
                "resolution then returns whichever sorts first — stable, arbitrary, and worse "
                "than an error",
            )


def _multiple_open(entity_id: str, versions: Sequence[Version]) -> Iterable[Problem]:
    open_versions = [version for version in versions if version.valid_to is None]
    if len(open_versions) > 1:
        starts = ", ".join(str(version.valid_from) for version in open_versions)
        yield Problem(
            entity_id,
            "multiple_open_versions",
            f"{len(open_versions)} versions have no end ({starts}); the entity has two "
            "presents, and every future reading resolves to one of them by sort order",
        )
