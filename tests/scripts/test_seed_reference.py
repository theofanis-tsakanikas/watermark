"""The reference seed lands the cast, and lands the version history rather than flattening it.

`scripts/seed_reference.py` exists because two `gold` tables were catalogue entries describing
tables that had never existed, and an erasure request was the first thing to find out. These
tests are what stops that being true again — offline, with no account.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from data.cast import DAY_START

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.generate import INTERVALS_PER_DAY, interval_start  # noqa: E402
from watermark.core.records import METER_INTERVAL  # noqa: E402
from watermark.core.time import Instant  # noqa: E402

#: The instant the cast's day ends, which is what every shift is measured from.
DAY_END_MILLIS = interval_start(INTERVALS_PER_DAY - 1).plus(METER_INTERVAL).epoch_millis
HOUR_MILLIS = 60 * 60 * 1000
HALF_AN_HOUR = HOUR_MILLIS // 2


def _seeder():
    spec = importlib.util.spec_from_file_location(
        "seed_reference", ROOT / "scripts" / "seed_reference.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def seeder():
    return _seeder()


def test_the_assignment_history_lands_on_the_day_the_stream_is_on(seeder) -> None:
    """The seed follows the publisher's shift, and the first version of it did not.

    `data/cast.py` fixes the scenario to 2026-03-14 so the dataset is reproducible;
    `data/publish.py` moves the whole day forward to end at the moment of the run so a capture is
    a replay. A seed written at the cast's date describes a day the stream is nowhere near, and
    every live reading falls on one side of the changeover.
    """
    rows = seeder.meter_assignment_rows()
    assert rows
    assert not any(str(DAY_START.to_iso()[:10]) in row for row in rows), (
        "the seed is still on the cast's fixed date rather than the stream's day"
    )


def test_the_meter_that_changes_customer_gets_two_versions(seeder) -> None:
    """SCD-2, not a mapping.

    `M00007` belongs to `C00007` until 10:00 and to `C00007-NEW` after. A seed that flattened
    that to one row would make an erasure for either subject reach the other's readings, and the
    certificate would say the boundary held.
    """
    rows = [row for row in seeder.meter_assignment_rows() if "'M00007'" in row]
    assert len(rows) == 2

    # The *structure*, not the instant. `data/publish.py` shifts the whole scenario day forward
    # so it ends at the moment of the run, and the seed follows it — so asserting `10:00:00`
    # here would pin this test to a wall clock and, worse, would have gone on passing while the
    # seeded table sat months away from the stream it describes. That is the bug this assertion
    # missed the first time.
    closed = [row for row in rows if "CAST(NULL AS timestamp)" not in row]
    open_ended = [row for row in rows if "CAST(NULL AS timestamp)" in row]
    assert len(closed) == 1, "the first customer's version must end"
    assert len(open_ended) == 1, "the second customer's version must still be in force"
    assert "'C00007'," in closed[0]
    assert "'C00007-NEW'" in open_ended[0]


def test_every_other_meter_has_exactly_one_open_version(seeder) -> None:
    rows = [row for row in seeder.meter_assignment_rows() if "'M00007'" not in row]
    assert rows
    assert all(row.endswith("CAST(NULL AS timestamp))") for row in rows)


def test_the_training_register_carries_the_subject_not_only_the_meter(seeder) -> None:
    """The column the erasure DELETE names.

    A register keyed on meters alone leaves the subject reachable through a reassignment: the
    meter is erased, the person who held it in March is not.
    """
    rows = seeder.training_snapshot_rows("2026-03-14T00:00:00Z", "randomised_inspection")
    assert rows
    assert all(row.startswith("('C") for row in rows)
    assert any(row.startswith("('C00007', 'M00007',") for row in rows)


def test_the_label_source_travels_with_every_row(seeder) -> None:
    """Which labels a model saw is the fact that decides whether it may be promoted.

    The same population produces two different training sets — `docs/BIAS-FINDING.md` — and a
    register that could not tell them apart could not say which models a subject reached.
    """
    for source in ("dispatch_log", "randomised_inspection"):
        rows = seeder.training_snapshot_rows("s", source)
        assert all(row.endswith(f"'{source}')") for row in rows)


def test_the_two_label_sources_disagree(seeder) -> None:
    """Otherwise the register records a distinction that is not there."""
    dispatch = seeder.training_snapshot_rows("s", "dispatch_log")
    randomised = seeder.training_snapshot_rows("s", "randomised_inspection")
    assert dispatch != randomised


def test_the_tables_it_declares_are_the_tables_it_creates(seeder) -> None:
    """`CREATES` is read by `check_lakehouse_wiring.py`, which cannot see the SQL below it."""
    source = (ROOT / "scripts" / "seed_reference.py").read_text(encoding="utf-8")
    for table, layer in seeder.CREATES.items():
        assert f"CREATE TABLE IF NOT EXISTS {{gold}}.{table}" in source
        assert layer == "gold"


def test_the_opening_version_reaches_back_before_the_stream_day(seeder) -> None:
    """Every SCD-2 opening version begins at the start of history, not at the start of the day.

    The lakehouse holds rows from earlier captures. A point-in-time join is `valid_from <= t <
    valid_to`, so an opening version dated to *this* run's day matches nothing before it, and
    every historical row resolves to no tariff and no customer — priced at nothing, owned by
    nobody, and silently absent from the settlement rather than wrong in it.
    """
    for rows in (seeder.meter_assignment_rows(), seeder.customer_rows(), seeder.tariff_rows()):
        assert rows
        opening = [row for row in rows if seeder.HISTORY_BEGINS in row]
        assert opening, "no version reaches back before the stream's day"

    assert seeder.HISTORY_BEGINS < "2020", (
        "the opening version must predate anything the lakehouse could already hold"
    )


def test_one_anchor_puts_the_seed_and_the_publisher_on_the_same_day(seeder) -> None:
    """The gap that cost a gold layer, closed rather than bounded.

    `data/publish.py` shifted the day at the start of `drive`; this seeded inside `train`, after
    the training pipeline. The two read the clock independently, so the reference history landed
    a training run away from the stream — minutes, or half an hour, depending on the fit.
    """
    anchor = Instant.from_iso("2026-08-17T03:00:00Z")
    seeder._ANCHOR_MILLIS = anchor.epoch_millis
    try:
        assert seeder.day_shift() == anchor.epoch_millis - DAY_END_MILLIS
    finally:
        seeder._ANCHOR_MILLIS = 0


@pytest.mark.parametrize("minute", [0, 7, 15, 29, 30, 31, 45, 59])
def test_a_settled_hour_always_straddles_the_tariff_change(seeder, minute: int) -> None:
    """The property the gold layer's no-boundary branch asks about, on the right grid.

    `M00019` changes at 14:30 so that a settled hour genuinely straddles it — the on-the-hour
    version exercised nothing, which `data/cast.py` records. The grid that decides this is the
    *stream's*: the day is shifted to end at the anchor, so its hour boundaries fall on the
    anchor's minute, not on the wall clock's.

    Which is why one shared anchor is the whole fix. Share it and the change sits exactly half an
    hour off every hour boundary whatever minute the capture starts at. Compute the two shifts
    separately and the offset becomes the difference between them — zero, and no hour straddles
    anything, when the training run happens to have taken thirty minutes.
    """
    anchor = Instant.from_iso(f"2026-08-17T03:{minute:02d}:00Z")
    seeder._ANCHOR_MILLIS = anchor.epoch_millis
    try:
        change = next(row for row in seeder.tariff_rows() if "TOU-02" in row)
        shift = seeder.day_shift()
    finally:
        seeder._ANCHOR_MILLIS = 0

    rendered = change.split("TIMESTAMP '")[1].split("'")[0]
    at = Instant.from_iso(rendered.replace(" ", "T") + "Z").epoch_millis
    into_the_hour = (at - (DAY_END_MILLIS + shift)) % HOUR_MILLIS

    assert into_the_hour == HALF_AN_HOUR, (
        f"the change sits {into_the_hour / 60000:.0f} minutes into the stream's hour. At zero no "
        f"settled hour straddles it and the point-in-time join resolves everything against one "
        f"version — which is the defect moving it off 14:00 was meant to prevent."
    )
