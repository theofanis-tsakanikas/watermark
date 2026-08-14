"""The live decider's pure halves, checked with no account.

`scripts/decide_live.py` is the first thing in this repository to run `src/watermark/decisions/`
against AWS. Two parts of it decide what the run *concludes*, and neither needs a cloud: how the
watermark view is reconstructed from the stream's evidence, and which properties are asserted
about the decisions that come out. Both are here.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streaming.operators import _line  # noqa: E402
from watermark.core.time import Duration, Instant  # noqa: E402
from watermark.core.watermarks import WatermarkStatus, WatermarkView  # noqa: E402
from watermark.core.windows import WindowResult  # noqa: E402
from watermark.decisions.engine import Origin  # noqa: E402


@pytest.fixture(scope="module")
def decider():
    spec = importlib.util.spec_from_file_location(
        "decide_live", ROOT / "scripts" / "decide_live.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["decide_live"] = module
    spec.loader.exec_module(module)
    return module


def _view(status: WatermarkStatus) -> WatermarkView:
    return WatermarkView(
        status=status,
        watermark=Instant.from_iso("2026-03-14T10:00:00Z"),
        idle=(),
        holding_back=None,
        lag=Duration.of_millis(0),
        leader=None,
    )


def _row(origin: Origin, *, unavailable: str | None = None, age_ms: int = 0) -> dict:
    return {
        "decision_id": "d1",
        "origin": origin.value,
        "unavailable": unavailable,
        "input_ages_ms": {"meter_consumption_1h": age_ms},
    }


class _Feature:
    freshness_budget_seconds = 3600


def test_no_evidence_is_unstarted_and_not_a_healthy_silence(decider) -> None:
    """A stream that has never reported a condition is indistinguishable from one that never ran.

    Resolving that to a healthy view is how a decision gets taken against a job that is not
    running, and it is the exact shape of claim 1's failure.
    """
    view = decider.watermark_view([])
    assert view.status is WatermarkStatus.UNSTARTED
    assert not view.status.may_close_windows


def test_the_last_transition_wins(decider) -> None:
    """And it is read with the field names the operator actually emits.

    `at`, not `observed_at`; `idle_partitions`, not `idle`; epoch milliseconds, not ISO-8601.
    Every mismatch would read as a missing key, resolve to a default, and present a healthy view
    of a grid that was holding back — claim 1 failing in the reader rather than in the stream.
    """
    view = decider.watermark_view(
        [
            {
                "kind": "watermark",
                "status": "advancing",
                "at": 1_000,
                "watermark": 1_000,
                "lag_millis": 0,
            },
            {
                "kind": "watermark",
                "status": "held_back",
                "at": 2_000,
                "watermark": 1_500,
                "lag_millis": 1800000,
                "holding_back": "SUB-01",
                "idle_partitions": ["SUB-02"],
            },
            {"kind": "published", "meter_id": "M00001"},
        ]
    )
    assert view.status is WatermarkStatus.HELD_BACK
    assert view.holding_back == "SUB-01"
    assert view.idle == ("SUB-02",)
    assert view.lag.millis == 1800000


def test_a_model_decision_under_a_held_back_watermark_is_a_failure(decider) -> None:
    """Claim 1, on the decision path. The window check is not the whole of it.

    A watermark that has not advanced permits no close, so a decision that came out of the model
    anyway was taken on data the system does not claim to have seen.
    """
    problems = decider.properties_that_must_hold(
        [_row(Origin.MODEL)], _view(WatermarkStatus.HELD_BACK), _Feature()
    )
    assert any("claim 1" in problem for problem in problems)


def test_a_model_decision_under_an_advanced_watermark_is_fine(decider) -> None:
    assert not decider.properties_that_must_hold(
        [_row(Origin.MODEL)], _view(WatermarkStatus.ADVANCING), _Feature()
    )


def test_a_fallback_with_no_reason_recorded_is_a_failure(decider) -> None:
    """Doctrine 2. The marker is not the point — the marker surviving into the record is.

    A fallback that reaches the record looking like a model decision is worse than an outage,
    because it is silent and it trains somebody to trust it.
    """
    problems = decider.properties_that_must_hold(
        [_row(Origin.FALLBACK)], _view(WatermarkStatus.ADVANCING), _Feature()
    )
    assert any("no reason recorded" in problem for problem in problems)


def test_a_model_decision_carrying_a_reason_is_also_a_failure(decider) -> None:
    """The other direction, and it matters as much.

    `unavailable` on a model decision means something judged an input missing and the model ran
    regardless. The fallback rate is computed off `origin`, so this row would be counted as
    healthy for ever.
    """
    problems = decider.properties_that_must_hold(
        [_row(Origin.MODEL, unavailable="stale_feature")],
        _view(WatermarkStatus.ADVANCING),
        _Feature(),
    )
    assert any("carries a reason" in problem for problem in problems)


def test_a_model_decision_on_a_stale_feature_is_a_failure(decider) -> None:
    """Claim 4. The gate is in front of the input, so nothing past the budget reaches the model."""
    problems = decider.properties_that_must_hold(
        [_row(Origin.MODEL, age_ms=3_600_001)], _view(WatermarkStatus.ADVANCING), _Feature()
    )
    assert any("claim 4" in problem for problem in problems)


def test_a_fallback_on_a_stale_feature_is_the_correct_answer(decider) -> None:
    assert not decider.properties_that_must_hold(
        [_row(Origin.FALLBACK, unavailable="stale_feature", age_ms=3_600_001)],
        _view(WatermarkStatus.ADVANCING),
        _Feature(),
    )


def test_it_reads_the_field_names_the_emitter_actually_writes(decider) -> None:
    """Against real `_line` output, not against a fixture.

    Two field-name mismatches have already been found in this one file: the watermark line's
    (`observed_at` for `at`, `idle` for `idle_partitions`, ISO-8601 for epoch milliseconds) and
    the published line's (`meter_id` for `meter`). Neither raised. Both read as a missing key,
    resolved to a default, and produced a confident wrong answer — an empty entity list read as
    "the estate published nothing", and a healthy view of a grid that was holding back.

    A hand-written fixture cannot catch that: it is the same mistake written twice, agreeing
    with itself. So this builds the line with the emitter and reads it with the reader.
    """
    result = WindowResult(
        meter_id="M00042",
        interval_start=Instant.from_iso("2026-03-14T10:00:00Z"),
        energy_wh=4200,
        readings=4,
        duplicates_suppressed=0,
        corrections_absorbed=0,
        closed_at=Instant.from_iso("2026-03-14T10:15:00Z"),
        first_seen_at=Instant.from_iso("2026-03-14T10:00:00Z"),
        watermark_status=WatermarkStatus.ADVANCING,
        idle_partitions=(),
        revision=0,
        supersedes=None,
        restatement_cause=None,
    )
    view = WatermarkView(
        status=WatermarkStatus.ADVANCING,
        watermark=Instant.from_iso("2026-03-14T10:15:00Z"),
        idle=(),
        holding_back=None,
        lag=Duration.of_millis(0),
        leader=None,
    )
    emitted = json.loads(_line("published", result, view, "abc123"))
    assert decider.meters_in([emitted]) == ["M00042"]


class _FakeS3:
    """Enough of the S3 client for `telemetry_stream`, and no more.

    The defect this stands in for is not a parsing error — it is a *listing* error, so the fake
    has to model the one thing that matters: the prefix holds every capture the estate ever drove,
    and the keys of one substation can outnumber the bound on their own.
    """

    def __init__(self, keys: list[tuple[str, int]]) -> None:
        #: (key, last-modified rank). Written newest-last, as S3 would.
        self._keys = keys

    def get_paginator(self, _name: str):
        client = self

        class _Paginator:
            def paginate(self, *, Bucket: str, Prefix: str):
                del Bucket
                yield {
                    "Contents": [
                        {"Key": key, "LastModified": rank}
                        for key, rank in client._keys
                        if key.startswith(Prefix)
                    ]
                }

        return _Paginator()

    def get_object(self, *, Bucket: str, Key: str):
        del Bucket
        substation = Key.split("/")[1]
        index = int(Key.rsplit("-", 1)[1])
        record = {
            "substation_id": substation,
            "event_time": Instant.from_epoch_millis(1_700_000_000_000 + index * 300_000).to_iso(),
            "load_w": 100,
            "limit_w": 1_000,
        }
        return {"Body": _Body(json.dumps(record).encode("utf-8"))}


class _Body:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


def test_every_substation_is_read_even_when_one_of_them_dominates_the_prefix(decider) -> None:
    """The bug that made three substations invisible, and it printed nothing while doing it.

    The first version listed the whole `telemetry/` prefix, sorted the keys in reverse and took
    the newest two hundred. `SUB-04` sorts last, so all two hundred were `SUB-04` — and the run
    reported three substations as having no telemetry at all, on an estate that was emitting for
    every one of them. Listing under each substation's own prefix asks the question meant.
    """
    keys = [
        (f"telemetry/SUB-04/x-{index}", index) for index in range(decider.TELEMETRY_TAIL * 2)
    ] + [(f"telemetry/SUB-0{n}/x-{index}", index) for n in (1, 2, 3) for index in range(5)]
    stream = decider.telemetry_stream(_FakeS3(keys), "bucket")

    assert set(stream) == {"SUB-01", "SUB-02", "SUB-03", "SUB-04"}
    # On the *contents*, not on the keys. `stream` is keyed by the substation being asked about,
    # so a listing that returned another substation's telemetry would still fill all four slots
    # and still be a decision taken over the wrong network.
    for substation, samples in stream.items():
        reported = {sample.substation_id for sample in samples}
        assert reported == {substation}, f"{substation} was decided on {sorted(reported)}"
    assert len(stream["SUB-04"]) == decider.TELEMETRY_TAIL, "the bound is per substation"


def test_the_tail_is_the_newest_samples_put_back_into_event_order(decider) -> None:
    """Bounded by write time, decided in event order. Reversing one without the other silently
    curtails on the oldest samples in the prefix."""
    keys = [(f"telemetry/SUB-01/x-{index}", index) for index in range(decider.TELEMETRY_TAIL + 30)]
    samples = decider.telemetry_stream(_FakeS3(keys), "bucket")["SUB-01"]

    times = [sample.event_time.epoch_millis for sample in samples]
    assert times == sorted(times)
    assert len(times) == decider.TELEMETRY_TAIL
    assert times[-1] == 1_700_000_000_000 + (decider.TELEMETRY_TAIL + 29) * 300_000
