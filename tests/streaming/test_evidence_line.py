"""The evidence line carries the whole of what the core computed.

The adapter serialised nine of `WindowResult`'s thirteen fields and dropped the rest on the way
to the sink — including `closed_at`, which is the watermark that permitted publication and the
only thing that makes a published row auditable without re-running the stream.

Nothing caught it, and the reason is worth stating: every claim harness reads `WindowResult`
objects directly, so all seven stayed green while the bytes that reached the lakehouse were
missing a third of the record. The sink is the one boundary the offline suite does not cross.

So this test crosses it, and it is deliberately written against the *dataclass* rather than
against a list of names: add a field to `WindowResult` and this fails until the adapter carries
it. A test enumerating the fields it expects would pass for ever after the next field is added,
which is the same failure one layer up.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from data import cast
from streaming.config import SEMANTICS
from streaming.operators import MeterWindowOperator, _line
from watermark.core.normalise import DEFAULT_POLICY as NORMALISATION_POLICY
from watermark.core.time import Duration, Instant
from watermark.core.watermarks import DEFAULT_POLICY as WATERMARK_POLICY
from watermark.core.watermarks import WatermarkStatus, WatermarkView
from watermark.core.windows import WindowPolicy, WindowResult

#: The one field whose name changes on the way out, and why it is allowed to.
#:
#: `meter` rather than `meter_id`, because the landing file is read by a Spark job that aliases
#: it back. The rename is old, harmless and load-bearing for `land_to_silver.py`; it is declared
#: here so that the check below stays exact rather than becoming a substring match.
RENAMED = {"meter_id": "meter"}


@pytest.fixture
def result() -> WindowResult:
    return WindowResult(
        meter_id="MTR-000001",
        interval_start=Instant.from_iso("2026-08-12T10:00:00Z"),
        energy_wh=1234,
        readings=4,
        duplicates_suppressed=1,
        corrections_absorbed=0,
        closed_at=Instant.from_iso("2026-08-12T10:17:00Z"),
        watermark_status=WatermarkStatus.ADVANCING,
        idle_partitions=("SUB-03",),
        first_seen_at=Instant.from_iso("2026-08-12T10:01:00Z"),
        revision=1,
        supersedes=1200,
        restatement_cause="late_batch",
    )


@pytest.fixture
def view() -> WatermarkView:
    return WatermarkView(
        status=WatermarkStatus.HELD_BACK,
        watermark=Instant.from_iso("2026-08-12T10:15:00Z"),
        idle=(),
        holding_back="SUB-03",
        lag=Duration.of_minutes(2),
    )


def test_every_field_the_core_computed_reaches_the_sink(result, view) -> None:
    line = json.loads(_line("published", result, view, "wm-abc123"))

    missing = [
        field.name
        for field in dataclasses.fields(WindowResult)
        if RENAMED.get(field.name, field.name) not in line
    ]
    assert not missing, (
        f"the adapter drops {missing} between the core and the sink. Every one of these is "
        "computed, and a field that is computed and not written is a field nobody downstream "
        "can ever ask for."
    )


def test_the_published_row_carries_its_own_closure_not_the_views(result, view) -> None:
    """`closed_at` and `idle_partitions` come off the result, not off the current watermark.

    They agree at the moment of publication and they do not agree afterwards: a restatement is
    emitted days later, and a row is a statement about *its* window. Taking either from the view
    would make a correction inherit the grid's condition at the time it was corrected, which is
    a different fact wearing the same name.
    """
    line = json.loads(_line("restated", result, view, "wm-abc123"))

    assert line["closed_at"] == result.closed_at.epoch_millis
    assert line["idle_partitions"] == list(result.idle_partitions)
    assert line["watermark_status"] == result.watermark_status.value
    # The view is still reported, under its own name, so a reader can see both.
    assert line["observed_status"] == view.status.value


def test_energy_is_a_string_so_no_reader_rounds_it(result, view) -> None:
    """ADR-0004 removed the parity tolerance in favour of an exact integer count of watt-hours.

    JSON numbers are doubles in most readers, so a large enough total would come back rounded —
    and a tolerance reintroduced by the transport is a tolerance nobody would look for.
    """
    line = json.loads(_line("published", result, view, None))
    assert line["energy_wh"] == "1234"
    assert isinstance(line["energy_wh"], str)


def test_a_line_without_a_lineage_id_says_so_rather_than_omitting_it(result, view) -> None:
    line = json.loads(_line("published", result, view, None))
    assert "lineage_id" in line
    assert line["lineage_id"] is None


def test_an_empty_batch_still_produces_a_watermark_view() -> None:
    """Starvation is the empty batch, and it must still be reportable.

    The adapter used to `return` before reporting anything when nothing had arrived — so the one
    state claim 1 names by name ("never starves silently") was the one state it could not
    report. The core has always handled this; what follows is the evidence that it does, so the
    adapter's job is only to ask.
    """
    operator = MeterWindowOperator(
        normalisation=NORMALISATION_POLICY,
        watermark=WATERMARK_POLICY,
        window=WindowPolicy(length=SEMANTICS["window_length"]),
        partitions=cast.SUBSTATIONS,
    )
    _, refused, view, lineage = operator.process([], Instant(0))
    assert refused == ()
    assert lineage == {}
    assert view.status in {WatermarkStatus.UNSTARTED, WatermarkStatus.STARVED}
    assert not view.status.may_close_windows


def test_the_adapter_no_longer_returns_before_reporting() -> None:
    """A structural check, because the callback cannot be built without a JVM.

    `build_process_function` imports PyFlink, which does not install on every machine this
    repository is developed on, so the branch above cannot be exercised directly here. What can
    be checked is that the early return is gone and the timer is re-armed independently of
    arrivals — the two halves that made a starved stream indistinguishable from a healthy one.
    """
    source = Path(__file__).resolve().parents[2].joinpath("streaming/operators.py").read_text()
    body = source[source.index("def on_timer") :]
    assert "if not batch:\n                return" not in body, (
        "the empty batch returns early again, and starvation is the empty batch"
    )
    assert "register_processing_time_timer" in body, (
        "on_timer no longer re-arms the timer; with no arrivals the operator goes silent and "
        "the condition it exists to report is the condition that stops it reporting"
    )
