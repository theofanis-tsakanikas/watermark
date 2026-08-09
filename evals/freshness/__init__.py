"""**Claim 4** — no decision on a stale feature, and the fallback marker survives to the end.

Six cases, and the last two are the ones that make the claim worth anything. It is easy to
refuse a stale feature; what is hard is that the refusal has to be *visible* — doctrine 2 — all
the way into the decision record, because a fallback that looks like a model decision is worse
than an outage. It is silent, and it teaches somebody to trust it.
"""

from __future__ import annotations

from evals.scoring import Case, first_problem, require
from watermark.contracts import load
from watermark.core.time import Duration, Instant
from watermark.core.watermarks import (
    WatermarkState,
    WatermarkStatus,
    WatermarkView,
    observe,
)
from watermark.decisions.engine import DecisionEngine, Origin, Unavailable, fallback_rate
from watermark.features.online import ServedValue

CONTRACTS = load()
CURTAILMENT = CONTRACTS.decisions["curtailment"]
ANOMALY = CONTRACTS.decisions["meter_anomaly"]
LOAD = CONTRACTS.features["substation_load_15m"]
HEADROOM = CONTRACTS.features["substation_headroom_15m"]


def at(minute: int, second: int = 0) -> Instant:
    return Instant.from_iso(f"2026-03-14T09:{minute:02d}:{second:02d}Z")


def _healthy_view() -> WatermarkView:
    state = WatermarkState.declare(["SUB-01"])
    _, view = observe(state, [("SUB-01", at(14))], at(14, 30))
    return view


def _stalled_view() -> WatermarkView:
    state = WatermarkState.declare(["SUB-01"])
    arrived = at(0)
    view = _healthy_view()
    for _ in range(6):
        state, view = observe(state, [("SUB-01", at(1))], arrived)
        arrived = arrived.plus(Duration.of_minutes(10))
    return view


def _served(age_seconds: int, feature_id: str, value: int = 400_000) -> ServedValue:
    return ServedValue(
        entity_id="SUB-01",
        feature_id=feature_id,
        value=value,
        event_time=at(14).minus(Duration.of_seconds(age_seconds)),
        write_time=at(14),
    )


def _engine(contract=CURTAILMENT):
    return DecisionEngine(contract, CONTRACTS.features)


def a_fresh_feature_reaches_the_model() -> str:
    """The calibration case. Refusing everything would satisfy every other case here."""
    decision = _engine().decide(
        "SUB-01",
        at(14),
        {
            "substation_load_15m": _served(10, "substation_load_15m"),
            "substation_headroom_15m": _served(10, "substation_headroom_15m", 50_000),
        },
        _healthy_view(),
        model_action="throttle:20",
        model_version="curtailment-forecast-7",
    )
    return first_problem(
        require(decision.origin is Origin.MODEL, f"a fresh feature fell back: {decision.origin}"),
        require(decision.action == "throttle:20", "the model's action was not used"),
        require(decision.model_version == "curtailment-forecast-7", "the version was not recorded"),
    )


def a_stale_feature_never_reaches_the_model() -> str:
    """Past its budget, the value is not served to the model at all.

    Note *at all*. The gate is in front of the input, not on the output: a model given stale
    features produces a plausible answer, and checking the answer afterwards cannot tell it
    from a good one.
    """
    budget = LOAD.freshness_budget_seconds
    decision = _engine().decide(
        "SUB-01",
        at(14),
        {
            "substation_load_15m": _served(budget + 5, "substation_load_15m"),
            "substation_headroom_15m": _served(10, "substation_headroom_15m", 50_000),
        },
        _healthy_view(),
        model_action="throttle:20",
        model_version="curtailment-forecast-7",
    )
    return first_problem(
        require(
            decision.origin is Origin.FALLBACK,
            f"a feature {budget + 5}s old against a {budget}s budget reached the model",
        ),
        require(
            decision.unavailable is Unavailable.FEATURE_STALE,
            f"the reason recorded was {decision.unavailable}, not FEATURE_STALE",
        ),
        require(decision.model_version is None, "a fallback recorded a model version"),
    )


def the_budget_is_the_contracts_and_not_the_engines() -> str:
    """Two features, two budgets, one engine. The horizon belongs to the decision.

    The anomaly path tolerates a value fifteen minutes old; curtailment tolerates sixty
    seconds. An engine with a threshold of its own could not serve both, and the one it did not
    serve would be wrong in the direction nobody notices.
    """
    return first_problem(
        require(
            LOAD.freshness_budget_seconds
            != CONTRACTS.features["meter_consumption_1h"].freshness_budget_seconds,
            "the two features share a budget, so this case proves nothing",
        ),
        require(
            LOAD.freshness_budget_seconds < CURTAILMENT.horizon_seconds * 20,
            "the load budget is far longer than curtailment's horizon; it could never bite",
        ),
    )


def a_missing_feature_falls_back_with_its_own_reason() -> str:
    """Missing and stale are different facts and are counted separately.

    A feature that has never been materialised is a pipeline that is not running; one that is
    stale is a pipeline that is behind. Reporting both as 'stale' sends somebody to look at
    latency while the job is down.
    """
    decision = _engine().decide(
        "SUB-01",
        at(14),
        {"substation_load_15m": None, "substation_headroom_15m": None},
        _healthy_view(),
        model_action="throttle:20",
    )
    return require(
        decision.unavailable is Unavailable.FEATURE_MISSING,
        f"a missing feature was reported as {decision.unavailable}",
    )


def a_stalled_watermark_is_reported_as_the_cause() -> str:
    """The watermark is checked before the features, and the reason says so.

    A stalled stream makes every feature stale as a consequence. Reporting the consequence
    sends somebody to the feature store while the thing that is stuck is the stream.
    """
    view = _stalled_view()
    decision = _engine().decide(
        "SUB-01",
        at(14),
        {
            "substation_load_15m": _served(10, "substation_load_15m"),
            "substation_headroom_15m": _served(10, "substation_headroom_15m", 50_000),
        },
        view,
        model_action="throttle:20",
    )
    return first_problem(
        require(view.status is WatermarkStatus.STALLED, "the fixture did not produce a stall"),
        require(
            decision.unavailable is Unavailable.WATERMARK_STALLED,
            f"the cause recorded was {decision.unavailable}, not the stall itself",
        ),
    )


def the_fallback_marker_survives_to_the_record() -> str:
    """Doctrine 2, asserted on the artefact rather than on the object.

    The record is what an operator reads and what a dashboard aggregates. A marker that exists
    on the in-memory object and not in the row is a marker nobody sees.
    """
    budget = LOAD.freshness_budget_seconds
    decision = _engine().decide(
        "SUB-01",
        at(14),
        {
            "substation_load_15m": _served(budget + 5, "substation_load_15m"),
            "substation_headroom_15m": _served(10, "substation_headroom_15m", -20_000),
        },
        _healthy_view(),
        model_action="throttle:20",
    )
    row = decision.as_row()
    return first_problem(
        require(row["origin"] == "fallback", f"the record says origin={row['origin']!r}"),
        require(row["unavailable"] == "feature_stale", "the record does not say why"),
        require(row["model_version"] is None, "the record names a model that took no decision"),
        require(
            bool(row["input_ages_ms"]),
            "the record does not carry how stale the inputs were, so the judgement cannot be "
            "reviewed against a budget that has since changed",
        ),
        require(
            fallback_rate([decision]) == 1.0,
            "the fallback rate does not count it. A path silently running on fallback for a "
            "week is an outage nothing reports; the aggregate is the only place it is visible.",
        ),
    )


def silence_is_the_safe_state_only_where_the_contract_says_so() -> str:
    """The distinction ADR-0001 exists for, asserted across two paths.

    Curtailment falls back to an action; the anomaly path falls back to nothing. Same engine,
    same staleness, opposite outcomes — decided by the contract, never at runtime.
    """
    stale = {
        "substation_load_15m": _served(999, "substation_load_15m"),
        "substation_headroom_15m": _served(999, "substation_headroom_15m", -20_000),
    }
    curtailment = _engine().decide(
        "SUB-01", at(14), stale, _healthy_view(), model_action="throttle:20"
    )
    anomaly = _engine(ANOMALY).decide(
        "M00001",
        at(14),
        {"meter_consumption_1h": _served(999_999, "meter_consumption_1h")},
        _healthy_view(),
        model_action="queue_for_inspection",
    )
    return first_problem(
        require(
            curtailment.origin is Origin.FALLBACK,
            "curtailment withheld. On a grid, silence is not the safe state: the substation "
            "keeps heating while nobody decides.",
        ),
        require(
            anomaly.origin is Origin.WITHHELD,
            "the anomaly path took an action on a stale feature. Nothing physical moves when "
            "it withholds, which is why silence is safe there and only there.",
        ),
    )


CASES: tuple[Case, ...] = (
    Case(
        "a_fresh_feature_reaches_the_model",
        "The calibration case. Refusing everything would satisfy every other case in this file.",
        a_fresh_feature_reaches_the_model,
    ),
    Case(
        "a_stale_feature_never_reaches_the_model",
        "The gate is in front of the input. A model given stale features produces a plausible "
        "answer, and checking the answer afterwards cannot tell it from a good one.",
        a_stale_feature_never_reaches_the_model,
    ),
    Case(
        "the_budget_is_the_contracts_and_not_the_engines",
        "Sixty seconds for curtailment, fifteen minutes for the anomaly queue. An engine with "
        "one threshold would be wrong for one of them, in the direction nobody notices.",
        the_budget_is_the_contracts_and_not_the_engines,
    ),
    Case(
        "a_missing_feature_falls_back_with_its_own_reason",
        "Missing is a pipeline that is not running; stale is one that is behind. Reporting "
        "both as stale sends somebody to look at latency while the job is down.",
        a_missing_feature_falls_back_with_its_own_reason,
    ),
    Case(
        "a_stalled_watermark_is_reported_as_the_cause",
        "A stall makes every feature stale as a consequence. Reporting the consequence sends "
        "somebody to the feature store while the stream is the thing that is stuck.",
        a_stalled_watermark_is_reported_as_the_cause,
    ),
    Case(
        "the_fallback_marker_survives_to_the_record",
        "Doctrine 2. A fallback that looks like a model decision is worse than an outage — it "
        "is silent, and it teaches somebody to trust it.",
        the_fallback_marker_survives_to_the_record,
    ),
    Case(
        "silence_is_the_safe_state_only_where_the_contract_says_so",
        "ADR-0001's whole argument, across two paths: same engine, same staleness, opposite "
        "outcomes, decided by the contract and never at runtime.",
        silence_is_the_safe_state_only_where_the_contract_says_so,
    ),
)
