"""The feature contract's three refusals, and the two mechanisms behaving unalike."""

from __future__ import annotations

import pytest

from watermark.contracts import load
from watermark.contracts.features import FeatureContract
from watermark.core.time import Instant
from watermark.features.offline import OfflineResolver, Row, as_of_sql
from watermark.features.online import OnlineMaterialiser

VALID = {
    "id": "substation_load_15m",
    "title": "Mean load",
    "owner": "network-operations",
    "entity": "substation",
    "entity_key": "substation_id",
    "window": {"length_seconds": 900, "grain_seconds": 60},
    "freshness_budget_seconds": 60,
    "personal_data": False,
    "value_type": "Integral",
    "scale": 1,
    "aggregation": "mean",
    "source_table": "substation_telemetry",
    "source_column": "load_w",
    "event_time_column": "event_time",
    "ingest_time_column": "ingest_time",
}


def at(minute: int) -> Instant:
    return Instant.from_iso(f"2026-03-14T09:{minute:02d}:00Z")


class TestTheThreeRefusals:
    def test_personal_data_needs_a_purpose(self) -> None:
        with pytest.raises(ValueError, match="declares no purpose"):
            FeatureContract.model_validate({**VALID, "personal_data": True})

    def test_a_fractional_feature_does_not_load(self) -> None:
        """There is no decimal type. A double compared against Iceberg's decimal differs in the
        last bits, and the only alternatives are an exact representation and a tolerance —
        which is a key to the one door doctrine 7 says has none."""
        with pytest.raises(ValueError, match="tolerance"):
            FeatureContract.model_validate({**VALID, "value_type": "Fractional"})

    def test_a_budget_longer_than_the_window_is_refused(self) -> None:
        """It looks generous and it is a disabled control: the feature is recomputed every
        grain, so a value older than the window is one the pipeline stopped producing."""
        with pytest.raises(ValueError, match="vacuously"):
            FeatureContract.model_validate({**VALID, "freshness_budget_seconds": 901})

    def test_a_window_that_is_not_a_whole_number_of_grains_is_refused(self) -> None:
        window = {"length_seconds": 900, "grain_seconds": 400}
        with pytest.raises(ValueError, match="partial grain"):
            FeatureContract.model_validate({**VALID, "window": window})


class TestTheRealSet:
    def test_every_shipped_feature_loads(self) -> None:
        assert len(load().features) == 3

    def test_the_two_budgets_differ_because_the_decisions_do(self) -> None:
        features = load().features
        assert features["substation_load_15m"].freshness_budget_seconds == 60
        assert features["meter_consumption_1h"].freshness_budget_seconds == 900

    def test_a_feature_of_a_personal_entity_is_personal(self) -> None:
        """An aggregate over personal data is personal data. Declaring otherwise is what
        happens when a feature is added by copying the one above it, and the consequence is a
        column outside the erasure scope."""
        assert load().features["meter_consumption_1h"].personal_data


class TestTheOfflineMechanism:
    def test_the_compiled_sql_binds_its_parameters(self) -> None:
        """Never interpolated. A feature query built by concatenation is one somebody
        eventually builds from a customer-supplied meter id."""
        sql = as_of_sql(load().features["meter_consumption_1h"])
        assert sql.count("?") == 4
        assert "M0000" not in sql

    def test_the_compiled_sql_binds_both_time_axes(self) -> None:
        sql = as_of_sql(load().features["substation_load_15m"])
        assert "ingest_time <= CAST(? AS TIMESTAMP)" in sql

    def test_every_bound_instant_is_cast(self) -> None:
        """Athena binds `ExecutionParameters` as `varchar`.

        An uncast placeholder in arithmetic answers `TYPE_MISMATCH: Cannot apply operator:
        varchar(19) - interval day to second`, and one in a comparison against a `timestamp`
        column compares different types. The SQL read correctly and could not run, because
        nothing executed it: the resolver beside it is Python over rows, and the deployed
        executor arrived with the live parity harness.
        """
        for feature in ("meter_consumption_1h", "substation_load_15m"):
            sql = as_of_sql(load().features[feature])
            # Three of the four placeholders are instants; the fourth is the entity key, which
            # is a string on both sides and must *not* be cast to a timestamp.
            assert sql.count("CAST(? AS TIMESTAMP)") == 3, feature
            assert sql.count("?") == 4, feature

    def test_it_returns_nothing_rather_than_zero_when_there_is_nothing(self) -> None:
        """A substation with no telemetry has no load figure. Inventing a zero tells the
        curtailment path the substation is idle when what is true is that nobody knows."""
        resolver = OfflineResolver(load().features["substation_load_15m"], [])
        assert resolver.resolve("SUB-01", at(14), at(14)) is None

    def test_a_row_ingested_after_the_serving_instant_is_excluded(self) -> None:
        rows = [Row("SUB-01", at(10), at(10), 100), Row("SUB-01", at(11), at(20), 900)]
        resolver = OfflineResolver(load().features["substation_load_15m"], rows)
        assert resolver.resolve("SUB-01", at(14), at(12)) == 100
        assert resolver.resolve("SUB-01", at(14), at(21)) == 500


class TestTheOnlineMechanism:
    def test_it_evicts_records_that_fall_out_of_the_window(self) -> None:
        """A running sum with no eviction would be cheaper and would drift — slowly, which is
        the worst available failure because it is right at first."""
        materialiser = OnlineMaterialiser(load().features["substation_load_15m"])
        materialiser.observe("SUB-01", at(0), 100, at(0))
        materialiser.observe("SUB-01", at(20), 300, at(20))
        served = materialiser.serve("SUB-01")
        assert served is not None and served.value == 300

    def test_it_serves_nothing_for_an_entity_it_has_not_seen(self) -> None:
        assert OnlineMaterialiser(load().features["substation_load_15m"]).serve("SUB-99") is None

    def test_forgetting_reports_whether_anything_was_there(self) -> None:
        """The online-store leg of claim 6. A silent no-op reported as success is exactly the
        kind of leg that makes an erasure certificate untrue."""
        materialiser = OnlineMaterialiser(load().features["substation_load_15m"])
        materialiser.observe("SUB-01", at(10), 100, at(10))
        assert materialiser.forget("SUB-01") is True
        assert materialiser.forget("SUB-01") is False

    def test_the_event_time_is_widened_for_the_feature_store(self) -> None:
        """Nine fractional digits. This repository renders three, which matches neither shape
        SageMaker accepts — the widening happens in the adapter and the core stays as it is."""
        materialiser = OnlineMaterialiser(load().features["substation_load_15m"])
        materialiser.observe("SUB-01", at(10), 100, at(10))
        served = materialiser.serve("SUB-01")
        assert served is not None
        assert served.to_feature_store_event_time() == "2026-03-14T09:10:00.000000000Z"
