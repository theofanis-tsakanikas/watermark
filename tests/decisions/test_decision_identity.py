"""A decision's identifier must identify a decision, and the oversight queue is where it bites.

`decision_id` used to be the contract id, so every decision this platform had ever taken about
every person carried the string `meter_anomaly`. It read as an identifier and was a category.
The first live run of the decision layer found it in the worst place available: the oversight
queue is a mapping keyed on this, so twenty decisions about twenty different people collapsed
into one entry. It refused to actuate the survivor — correctly — and the other nineteen were
never presented to anybody at all, which is claim 7 failing silently in the structure built to
guarantee it.
"""

from __future__ import annotations

from watermark.contracts import load
from watermark.core.time import Duration, Instant
from watermark.core.watermarks import WatermarkStatus, WatermarkView
from watermark.decisions.engine import DecisionEngine, identity_of
from watermark.decisions.oversight import OversightQueue
from watermark.features.online import ServedValue

CONTRACTS = load()
ANOMALY = CONTRACTS.decisions["meter_anomaly"]
FEATURE = CONTRACTS.features[ANOMALY.features[0]]


def _view() -> WatermarkView:
    return WatermarkView(
        status=WatermarkStatus.ADVANCING,
        watermark=Instant.from_iso("2026-03-14T10:00:00Z"),
        idle=(),
        holding_back=None,
        lag=Duration.of_millis(0),
        leader=None,
    )


def _decide(entity_id: str, at: Instant):
    served = ServedValue(entity_id, FEATURE.id, 4200, at, at)
    return DecisionEngine(ANOMALY, CONTRACTS.features).decide(
        entity_id,
        at,
        {FEATURE.id: served},
        _view(),
        model_action="queue_for_inspection",
        model_version="v1",
    )


def test_two_people_do_not_share_an_identifier() -> None:
    at = Instant.from_iso("2026-03-14T10:00:00Z")
    assert _decide("M00001", at).decision_id != _decide("M00002", at).decision_id


def test_the_same_meter_at_two_instants_does_not_share_one() -> None:
    early = Instant.from_iso("2026-03-14T10:00:00Z")
    late = Instant.from_iso("2026-03-14T11:00:00Z")
    assert _decide("M00001", early).decision_id != _decide("M00001", late).decision_id


def test_two_contracts_about_one_meter_do_not_collide() -> None:
    at = Instant.from_iso("2026-03-14T10:00:00Z")
    assert identity_of("meter_anomaly", "M00001", at) != identity_of("curtailment", "M00001", at)


def test_it_is_derived_and_not_random() -> None:
    """Claim 2. A uuid would make every replay differ in the field a record is looked up by."""
    at = Instant.from_iso("2026-03-14T10:00:00Z")
    assert _decide("M00001", at).decision_id == _decide("M00001", at).decision_id


def test_the_queue_holds_every_person_it_was_given() -> None:
    """The failure as it actually happened: twenty in, one pending, nineteen gone.

    Asserted against the queue rather than against the identifier, because the identifier is the
    cause and this is the consequence — and it is the consequence that claim 7 is about.
    """
    at = Instant.from_iso("2026-03-14T10:00:00Z")
    queue = OversightQueue()
    meters = [f"M{index:05d}" for index in range(20)]
    for meter in meters:
        decision = _decide(meter, at)
        queue.enqueue(decision.decision_id, decision)

    assert len(queue.pending) == len(meters)
    assert {queue.entries[entry].entity_id for entry in queue.pending} == set(meters)
