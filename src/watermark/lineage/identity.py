"""Deriving an identity from content, so that a replay produces the same one."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Final, NewType

from watermark.core.records import MeterReading
from watermark.core.windows import WindowResult

#: A `str` at runtime with a distinct static type. Lineage ids and payload hashes are both
#: 32 hex characters, they are not the same thing, and a function that takes one and is handed
#: the other would work perfectly until somebody tried to trace a number back.
LineageId = NewType("LineageId", str)

_LENGTH: Final = 32


def _digest(kind: str, parts: Iterable[str]) -> LineageId:
    """Hash a kind and its parts into an id.

    The kind is in the material so that a reading and a result derived from exactly one reading
    cannot collide.

    **The parts are concatenated with no separator, and this docstring used to claim otherwise.**
    It said they were "joined with a separator that cannot appear in an id or an ISO instant, so
    `("ab", "cd")` and `("abc", "d")` do not hash alike" — a defence the line below has never
    implemented. Both of those pairs produce `abcd`. The sentence is recorded rather than
    quietly deleted: a docstring describing a control that is not there is the same defect this
    repository keeps finding in its gates, and finding it in the file that mints identities is
    worth stating plainly.

    **Why the ids are distinct anyway, and why that is weaker than it sounds.** No caller can
    produce two different part lists whose concatenations agree, because every part with a
    neighbour after it is fixed-width or carries its own delimiters:

      * `Instant.to_iso()` is always 24 characters — `2026-03-14T00:00:00.000Z`;
      * a `payload_hash` and a `LineageId` are always 32 hex characters;
      * `derive`'s key carries `|` internally and is followed only by 32-character parents;
      * `Source.value` is variable — `stream` against `batch` — and is last, so nothing follows
        it to absorb the difference.

    That is a property of **the callers**, not of this function. A part added tomorrow between
    two variable-width neighbours would collide silently and nothing here would refuse it.

    The honest fix is a separator. It is not applied because every existing id would change —
    `recordings/day.json`, every figure asserted about lineage, and the two-run comparison claim
    2 makes live. That is a restatement, and it is not being smuggled in under a docstring edit.
    """
    material = "".join([kind, *parts])
    return LineageId(hashlib.sha256(material.encode("utf-8")).hexdigest()[:_LENGTH])


def of_reading(reading: MeterReading) -> LineageId:
    """The id minted for a raw reading at ingestion.

    Includes the ingestion time and the source, so the two *copies* of a retried reading have
    different lineage ids even though they share a payload hash. That is deliberate and it is
    the difference between the two identifiers: the payload hash answers "is this the same
    measurement?", the lineage id answers "is this the same record?". Deduplication needs the
    first; tracing a published number back to the delivery it came from needs the second.
    """
    return _digest(
        "reading",
        [
            reading.meter_id,
            reading.interval_start.to_iso(),
            reading.payload_hash,
            reading.ingest_time.to_iso(),
            reading.source.value,
        ],
    )


def derive(kind: str, key: str, parents: Iterable[LineageId]) -> LineageId:
    """An id for something computed from other things.

    Parents are **sorted and de-duplicated**, and both halves were earned.

    Sorting, because otherwise the id of a total would depend on the order its readings
    happened to arrive in — the whole failure claim 2 exists to catch, reappearing in the one
    place designed to prove it did not happen.

    De-duplicating, because claim 2's harness found the other half. Under at-least-once
    delivery the same record arrives twice, and a record delivered twice is *one* parent seen
    twice: `[id, id]` and `[id]` describe the same provenance and must hash alike. Without
    this, every published total in a replay with redeliveries carried a different lineage id
    while every number was identical — which is precisely the shape of failure that no
    reconciliation of totals would ever surface.
    """
    return _digest(kind, [key, *sorted(set(parents))])


def of_result(result: WindowResult, contributing: Iterable[LineageId]) -> LineageId:
    """The id of a published window.

    The revision is in the key. A restatement is a *new* statement about the same interval, so
    it has its own identity — otherwise revision 1 and revision 0 would be indistinguishable to
    anything holding a reference, and "which version of this total was I told?" would have no
    answer.
    """
    key = f"{result.meter_id}|{result.interval_start.to_iso()}|r{result.revision}"
    return derive("window", key, contributing)
