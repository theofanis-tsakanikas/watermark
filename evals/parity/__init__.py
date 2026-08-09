"""**Claim 3** — train/serve parity, between two mechanisms rather than one.

Five cases. Two of them are the tautologies ADR-0004 names, planted deliberately so that the
harness is shown catching them rather than trusted not to need to.

The one that matters most is `future_leakage`. The offline store keeps every historical record,
so a resolver written as "the latest row for this entity" reads a value from *after* the
instant it is resolving. Every number it produces is plausible, the model trained on it scores
beautifully, and it is useless in production — and nothing about the evaluation reveals why.
"""

from __future__ import annotations

from evals.scoring import Case, first_problem, require
from watermark.contracts import load
from watermark.core.time import Duration, Instant
from watermark.features.offline import OfflineResolver, Row
from watermark.features.online import OnlineMaterialiser

CONTRACTS = load()
FEATURE = CONTRACTS.features["substation_load_15m"]

#: Where the serving instant sits relative to the event time — the pipeline's own latency.
#: Non-zero on purpose: at zero the two time axes coincide and the comparison silently becomes
#: the naive one this whole harness exists not to be.
SERVE_LAG = Duration.of_seconds(30)


def at(minute: int, second: int = 0) -> Instant:
    return Instant.from_iso(f"2026-03-14T09:{minute:02d}:{second:02d}Z")


def _rows() -> list[Row]:
    """Telemetry for two substations, one of which has a reading that arrives late."""
    rows = [
        Row("SUB-01", at(minute), at(minute, 5), 400_000 + minute * 100) for minute in range(1, 15)
    ]
    rows += [Row("SUB-02", at(minute), at(minute, 5), 300_000) for minute in range(1, 15)]
    return rows


def _materialise(rows: list[Row], up_to: Instant) -> OnlineMaterialiser:
    """Feed the online mechanism everything it would have seen by `up_to`.

    Ingestion order, not event order — the stream sees records as they arrive, and the whole
    reason the online mechanism can disagree with the offline one is that it never gets to see
    the window whole.
    """
    materialiser = OnlineMaterialiser(FEATURE)
    for row in sorted(rows, key=lambda item: item.ingest_time.epoch_millis):
        if row.ingest_time.epoch_millis <= up_to.epoch_millis:
            materialiser.observe(row.entity_id, row.event_time, row.value, row.ingest_time)
    return materialiser


def the_two_mechanisms_agree() -> str:
    """The claim itself, over a population and a set of instants."""
    rows = _rows()
    offline = OfflineResolver(FEATURE, rows)
    problems: list[str] = []

    for minute in (10, 12, 14):
        event_time = at(minute)
        serve_time = event_time.plus(SERVE_LAG)
        online = _materialise(rows, serve_time)

        for entity in ("SUB-01", "SUB-02"):
            served = online.serve(entity)
            expected = offline.resolve(entity, event_time, serve_time)
            actual = served.value if served else None
            if actual != expected:
                problems.append(f"{entity} at {event_time}: online={actual} offline={expected}")

    return require(
        not problems,
        "the two mechanisms disagree: " + "; ".join(problems) + ". They share the contract and "
        "nothing else, so a disagreement is a real difference between an incremental fold and "
        "a set-oriented recomputation — which is what claim 3 is for.",
    )


def future_leakage_is_caught() -> str:
    """A resolver that ignores the as-of bound must be caught by the comparison.

    The planted case. `CLAUDE.md` and `PLAN.md` both name it, and it is planted rather than
    argued because the failure is invisible in every other way: the leaked value is plausible,
    the model scores well, and the evaluation says nothing.
    """
    rows = _rows()
    offline = OfflineResolver(FEATURE, rows)

    honest = offline.resolve("SUB-01", at(8), at(8).plus(SERVE_LAG))
    leaked = offline.resolve("SUB-01", at(14), at(14).plus(SERVE_LAG))

    return first_problem(
        require(
            honest is not None and leaked is not None,
            "the fixture produced no value to compare; the case proves nothing",
        ),
        require(
            honest != leaked,
            "resolving at 09:08 and at 09:14 returned the same value, so the as-of bound is "
            "not being applied and a harness comparing them would agree for the wrong reason",
        ),
    )


def late_arrival_is_not_a_divergence() -> str:
    """A reading ingested after the serving instant must not enter the comparison.

    Without the second time axis this case fails — correctly, and about the wrong thing. The
    online value was computed from what had arrived; the offline one from what has arrived
    since. Reporting that as a parity failure would make claim 3 fire on late data working.
    """
    rows = _rows()
    # A reading for 09:06 that only arrives at 09:20 — three minutes after the serving instant.
    rows.append(Row("SUB-01", at(6), at(20), 999_000))
    offline = OfflineResolver(FEATURE, rows)

    event_time, serve_time = at(14), at(14).plus(SERVE_LAG)
    online = _materialise(rows, serve_time)
    served = online.serve("SUB-01")

    bitemporal = offline.resolve("SUB-01", event_time, serve_time)
    event_time_only = offline.resolve("SUB-01", event_time, at(21))

    return first_problem(
        require(
            served is not None and served.value == bitemporal,
            f"bitemporal comparison disagreed: online={served.value if served else None} "
            f"offline={bitemporal}",
        ),
        require(
            bitemporal != event_time_only,
            "binding only event time produced the same answer as binding both, so this "
            "fixture does not exercise the late arrival and the case proves nothing",
        ),
    )


def the_contract_refuses_an_inexact_value() -> str:
    """ADR-0004's other half: no feature may be Fractional, so the comparison stays exact.

    A tolerance is a key to the one door doctrine 7 says has none. This is checked at the
    contract layer rather than here, and the case exists so that removing that check shows up
    as a claim 3 failure rather than as a quietly widened comparison.
    """
    from pydantic import ValidationError  # noqa: PLC0415

    from watermark.contracts.features import FeatureContract  # noqa: PLC0415

    definition = FEATURE.model_dump()
    definition["value_type"] = "Fractional"

    try:
        FeatureContract.model_validate(definition)
    except (ValidationError, ValueError):
        return ""
    return (
        "a Fractional feature loaded. There is no decimal type in the Feature Store, so its "
        "value is a double — and a double compared against the same quantity in Iceberg "
        "differs in the last bits. The only alternatives are an exact representation and a "
        "tolerance, and a tolerance is a key to the door that has none."
    )


def every_feature_has_a_budget_and_a_purpose() -> str:
    """The precondition for claim 4, checked from the claim 3 side too.

    A feature with no budget cannot be judged stale, so claim 4 would hold vacuously for it —
    and the place that omission is most likely to appear is a feature added for a model, which
    is this harness's subject.
    """
    missing = [
        feature.id
        for feature in CONTRACTS.features.values()
        if feature.freshness_budget_seconds <= 0
        or (feature.personal_data and not (feature.purpose or "").strip())
    ]
    return require(not missing, f"features without a budget or a purpose: {missing}")


CASES: tuple[Case, ...] = (
    Case(
        "the_two_mechanisms_agree",
        "One contract compiled two ways — a set-oriented recomputation and an incremental "
        "fold. They share nothing else, so agreement is evidence rather than arithmetic.",
        the_two_mechanisms_agree,
    ),
    Case(
        "future_leakage_is_caught",
        "The offline store keeps every historical record. A resolver written as 'the latest "
        "row' reads a value from after the instant it is resolving, and nothing about the "
        "evaluation reveals it.",
        future_leakage_is_caught,
    ),
    Case(
        "late_arrival_is_not_a_divergence",
        "Event time decides what the feature is about; ingestion time decides what was "
        "knowable when it was served. Binding only the first makes claim 3 fire on late data "
        "working correctly.",
        late_arrival_is_not_a_divergence,
    ),
    Case(
        "the_contract_refuses_an_inexact_value",
        "The comparison is integer equality because the contract layer makes it possible to "
        "be. Remove that and this fails, rather than the comparison quietly widening.",
        the_contract_refuses_an_inexact_value,
    ),
    Case(
        "every_feature_has_a_budget_and_a_purpose",
        "Claim 4's precondition, checked from this side too: a feature with no budget cannot "
        "be judged stale, so the claim would hold vacuously for it.",
        every_feature_has_a_budget_and_a_purpose,
    ),
)
