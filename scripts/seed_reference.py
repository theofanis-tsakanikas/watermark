"""Create and land the gold reference tables, because nothing else ever did.

**The finding this exists for.** The first erasure run against a live estate refused to certify.
Three of its five legs were fixed and it still refused, on two:

    DELETE FROM watermark_gold.training_snapshot WHERE customer_id = 'C00007'
      -> Detected Iceberg type table without metadata location

    DELETE FROM watermark_silver.meter_interval WHERE meter_id IN (
        SELECT meter_id FROM watermark_gold.meter_assignment_scd2 WHERE customer_id = 'C00007')
      -> ... and the same, once the S3 grant beneath it was fixed

`infra/lakehouse/glue.tf` declared five tables in `gold`. Every one of them was a catalogue
entry with no metadata location — a name and a column list describing a table that has never
existed. They were tolerated by `check_lakehouse_wiring.py` as `EXTERNAL`, "landed by a CDC
pipeline that is outside this repository", and no such pipeline is in this account or in the
plan. So the declaration was not an interface. It was a table that would fail on first contact
and had never been contacted.

**Erasure is the first thing that ever touched them, and it is the worst place to find out.**
`meter_assignment_scd2` is how a subject id becomes a set of meter ids: without it the system
cannot answer *which rows belong to this person*, which is the first question an erasure asks.
`training_snapshot` is the register of who was in which training set, which is the only way the
quarantine leg can ever name the models a subject contributed to. Neither is a nicety on the
side of claim 6. They are how it knows what it is deleting.

**Why a seeder and not a CDC pipeline.** The reference data is a distribution operator's CRM and
asset registry — genuinely another system, and modelling one would be inventing a fiction to
land a fiction. What is real here is `data/cast.py` and `data/labels.py`: the committed,
deterministic cast this whole repository is proved against. This script lands *that* into the
catalogue, so the tables the queries name are the tables the tests mean. The interface stays an
interface; this is the seed behind it, and it is labelled as one.

**Why Athena and not Glue.** ADR-0008 requires an Iceberg-capable engine to create the table, not
that it be Spark. Six hundred rows do not need a cluster, and a Glue job costs two minutes of
start-up per run to write less data than fits in one query. The engine here is Athena, the table
it produces is the same Iceberg table, and `land_to_silver.py` remains the writer for the volume
path where Spark earns its keep.

**On interpolation.** `queries/` binds parameters and never interpolates, because those queries
take input from outside. Nothing here does: every value is computed from the committed cast, in
this process, three lines above the string it lands in. Binding six hundred rows would need six
hundred placeholders against Athena's limit of twenty-five, so the seed is rendered — and the
subject id used to *check* the result is bound, because that one is an argument.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: The tables this script creates, and the layer each lands in.
#:
#: Read by `check_lakehouse_wiring.py`, which after ADR-0008 asks every table a query names for
#: the thing that creates it. A Glue job declares that in its Terraform arguments and a dbt model
#: in its filename; a script has to say so itself, and this is where.
CREATES: Final[dict[str, str]] = {
    "meter_assignment_scd2": "gold",
    "customer_scd2": "gold",
    "tariff_scd2": "gold",
    "training_snapshot": "gold",
}


def _sql_timestamp(epoch_millis: int) -> str:
    """An Athena timestamp literal, or the typed null that closes an open SCD-2 interval.

    `valid_to` is nullable and means "still in force". A literal `NULL` in a `VALUES` list has no
    type, and Athena refuses the insert rather than guessing, so the cast is not decoration.
    """
    moment = datetime.fromtimestamp(epoch_millis / 1000, UTC)
    return f"TIMESTAMP '{moment.strftime('%Y-%m-%d %H:%M:%S')}'"


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def day_shift() -> int:
    """The milliseconds `data/publish.py` adds to every event time, computed the same way.

    **The reference data has to live on the same day as the stream, and it did not.** The cast
    fixes the scenario to 2026-03-14 — a fixed date rather than "today", because a generator that
    reads a clock produces a different dataset every day and a golden recording of it is a
    recording of the afternoon it was captured. The *publisher* then shifts the whole day forward
    so it ends at the moment of the run, which is what makes a capture a replay rather than an
    archive.

    Seeding the assignment history unshifted put every live reading after the 10:00 changeover,
    so the meter that changes customer attributed all of its day to the second customer and none
    to the first. The bounded `DELETE` was correct and the data it was bounded against was not,
    which is the harder half of that pair to notice.

    **The two shifts are computed minutes apart** — this at seeding, the publisher's when it
    starts — so the changeover instant differs by that gap. It is stated rather than hidden: at
    most one fifteen-minute interval can land on the wrong side of it, and nothing in this
    repository asserts against the raw instant because of it. `capture.yml` counts through the
    SCD-2 join instead, so the assertion and the erasure resolve the same way whatever the gap.
    """
    from data.cast import DAY_END  # noqa: PLC0415

    return int(time.time() * 1000) - DAY_END.epoch_millis


#: Where an SCD-2 history begins, for the version that is in force at the start of the cast's day.
#:
#: **Not the shifted `DAY_START`, and the first live build of the gold layer showed why.** The
#: cast's day is a window into a longer history: a customer had a tariff before the day being
#: settled, and a CRM export would say so. Seeding the earliest version at the day's own start
#: makes every reading from *before* that instant unpriced — and the silver table accumulates
#: across captures, so it holds readings from days the current seed has never heard of.
#:
#: `settlement_priced` inner-joins the tariff deliberately rather than defaulting to zero, so
#: those rows were absent rather than free: 1,357 settled hours against 961 priced, and the test
#: written to catch exactly that reported 396. The test was right; the history was too short.
#:
#: Only the *opening* version moves. The changeovers — `M00007`'s customer at 10:00, `M00019`'s
#: tariff at 14:00 — stay on the stream's day, because those are the point-in-time cases and
#: moving them would erase what they exist to prove.
HISTORY_BEGINS: Final = "2000-01-01 00:00:00"


def _valid_from(epoch_millis: int, shift: int, day_start_millis: int) -> str:
    """The opening version reaches back; every later one sits on the stream's day."""
    if epoch_millis == day_start_millis:
        return f"TIMESTAMP '{HISTORY_BEGINS}'"
    return _sql_timestamp(epoch_millis + shift)


def meter_assignment_rows() -> list[str]:
    """The SCD-2 assignment history, straight off the cast and moved onto the stream's day.

    `M00007` changes customer at 10:00 and therefore has two versions — the scenario's *"a meter
    changes customer"*, and the reason this table is SCD-2 rather than a mapping. An erasure for
    the customer who held the meter before 10:00 must reach the readings from before 10:00 and
    not the ones after, and a flat map cannot express the difference.
    """
    from data.cast import DAY_START, meter_assignments  # noqa: PLC0415

    shift = day_shift()
    day_start = DAY_START.epoch_millis
    rows = []
    for version in meter_assignments().versions:
        valid_to = (
            "CAST(NULL AS timestamp)"
            if version.valid_to is None
            else _sql_timestamp(version.valid_to.epoch_millis + shift)
        )
        rows.append(
            f"({_quote(version.entity_id)}, "
            f"{_quote(str(version.attributes['customer_id']))}, "
            f"{_valid_from(version.valid_from.epoch_millis, shift, day_start)}, {valid_to})"
        )
    return rows


def customer_rows() -> list[str]:
    """The customer reference data, shifted onto the stream's day like everything else here.

    `settlement_balancing_group` needs a balancing group per customer and `unattributed_meters`
    needs to know when there is none. Both models have existed since phase 2 and neither had
    ever run, because the table they read was a Terraform declaration with no writer.
    """
    from data.cast import DAY_START, customers  # noqa: PLC0415

    shift = day_shift()
    day_start = DAY_START.epoch_millis
    rows = []
    for version in customers().versions:
        valid_to = (
            "CAST(NULL AS timestamp)"
            if version.valid_to is None
            else _sql_timestamp(version.valid_to.epoch_millis + shift)
        )
        rows.append(
            f"({_quote(version.entity_id)}, "
            f"{_quote(str(version.attributes['balancing_group']))}, "
            f"{_quote(str(version.attributes['postcode_area']))}, "
            f"{_valid_from(version.valid_from.epoch_millis, shift, day_start)}, {valid_to})"
        )
    return rows


def tariff_rows() -> list[str]:
    """Tariffs, SCD-2, with `M00019` moving to a time-of-use price at 14:00.

    **This is the scenario case that had no consumer at all.** `docs/SCENARIO.md` declares "a
    tariff changes mid-period", `data/cast.py` builds the history, and nothing read it — not a
    query, not a dbt model, not a test. The word `tariff` appeared in this repository only in
    the docstrings of `pit.py`. A declared case with no consumer is a case that cannot fail,
    which is worse than one that fails: it looks handled.

    `unit_price_cents_per_kwh` is an integer, and cents rather than euros for the same reason
    energy is watt-hours: a settlement figure that arrives as a float is a settlement figure two
    engines disagree about in the last place, which is exactly where money lives.
    """
    from data.cast import DAY_START, tariffs  # noqa: PLC0415

    shift = day_shift()
    day_start = DAY_START.epoch_millis
    rows = []
    for version in tariffs().versions:
        valid_to = (
            "CAST(NULL AS timestamp)"
            if version.valid_to is None
            else _sql_timestamp(version.valid_to.epoch_millis + shift)
        )
        rows.append(
            f"({_quote(version.entity_id)}, "
            f"{_quote(str(version.attributes['tariff_code']))}, "
            f"{int(version.attributes['unit_price_cents_per_kwh'])}, "
            f"{_valid_from(version.valid_from.epoch_millis, shift, day_start)}, {valid_to})"
        )
    return rows


def training_snapshot_rows(snapshot_id: str, label_source: str) -> list[str]:
    """One row per meter in the training population, carrying the subject it belongs to.

    **Keyed by customer and not only by meter**, which is the column the erasure DELETE names.
    A training set keyed on meters alone leaves the subject reachable through a reassignment: the
    meter is erased, the person who held it in March is not, and the certificate would say
    otherwise.

    `label_source` travels with the row because the same population produces two different
    training sets — the dispatch log the promotion gate refuses and the randomised allocation it
    promotes, per `docs/BIAS-FINDING.md`. A register that could not tell them apart could not say
    which models a subject actually reached.
    """
    from data.labels import labels  # noqa: PLC0415
    from watermark.models.snapshot import LABEL_COLUMNS  # noqa: PLC0415

    column = LABEL_COLUMNS[label_source]
    rows = []
    for label in labels():
        # The cast's convention, and the only place customer identity comes from: meter `M00007`
        # belongs to customer `C00007`. Derived rather than looked up, because the training
        # population is six hundred meters and the streamed cast is forty-one — the register has
        # to cover every meter a model was fitted on, not only the ones that sent telemetry.
        customer_id = f"C{label.meter_id[1:]}"
        features = json.dumps(
            {"score": label.score, "deprivation_decile": label.deprivation_decile},
            sort_keys=True,
            separators=(",", ":"),
        )
        confirmed = int(getattr(label, "truly_tampering" if column == "truly" else "confirmed"))
        rows.append(
            f"({_quote(customer_id)}, {_quote(label.meter_id)}, {_quote(snapshot_id)}, "
            f"{_quote(features)}, {confirmed}, {_quote(label_source)})"
        )
    return rows


class Athena:
    """The smallest client that can run a statement and wait for it.

    Waits rather than polls-and-hopes: a `CREATE TABLE` that has not finished when the `INSERT`
    is submitted fails on a table that does not exist yet, and the failure reads like the bug
    this whole script exists to fix.
    """

    def __init__(self, workgroup: str, region: str) -> None:
        import boto3  # noqa: PLC0415

        self._client = boto3.client("athena", region_name=region)
        self._workgroup = workgroup

    def run(self, statement: str, parameters: list[str] | None = None) -> str:
        request: dict[str, Any] = {"QueryString": statement, "WorkGroup": self._workgroup}
        if parameters:
            request["ExecutionParameters"] = parameters
        query_id = self._client.start_query_execution(**request)["QueryExecutionId"]
        while True:
            status = self._client.get_query_execution(QueryExecutionId=query_id)["QueryExecution"][
                "Status"
            ]
            if status["State"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                break
            time.sleep(2)
        if status["State"] != "SUCCEEDED":
            reason = status.get("StateChangeReason", "no reason given")
            raise RuntimeError(f"{statement.splitlines()[0].strip()} ...: {reason}")
        return query_id

    def scalar(self, statement: str, parameters: list[str] | None = None) -> str:
        query_id = self.run(statement, parameters)
        rows = self._client.get_query_results(QueryExecutionId=query_id)["ResultSet"]["Rows"]
        return rows[1]["Data"][0].get("VarCharValue", "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="watermark")
    parser.add_argument("--warehouse", required=True, help="s3://bucket/warehouse")
    parser.add_argument("--region", default="eu-central-1")
    # Optional, and the split is deliberate. `meter_assignment_scd2` is reference data: it
    # describes the estate and is seeded when the estate comes up. A training-set membership
    # describes a *training run*, so it is registered by the run that pinned the snapshot —
    # `capture.yml` after the pipeline, with the snapshot id the pipeline was given. Seeding a
    # membership at deploy time would register a training set that nobody has trained.
    parser.add_argument("--snapshot", help="Register this training snapshot's membership.")
    parser.add_argument("--labels", default="randomised_inspection")
    arguments = parser.parse_args(argv)

    gold = f"{arguments.project}_gold"
    athena = Athena(arguments.project, arguments.region)
    warehouse = arguments.warehouse.rstrip("/")

    # `IF NOT EXISTS`, so a redeploy is not a restatement. The table survives the estate coming
    # up and down; what would not survive is a script that dropped and rebuilt it, because the
    # erasure of a subject would then be undone by the next deploy — and nothing would say so.
    athena.run(f"""
        CREATE TABLE IF NOT EXISTS {gold}.meter_assignment_scd2 (
            meter_id string, customer_id string, valid_from timestamp, valid_to timestamp)
        LOCATION '{warehouse}/gold/meter_assignment_scd2'
        TBLPROPERTIES ('table_type' = 'ICEBERG', 'format' = 'parquet')
    """)
    athena.run(f"""
        CREATE TABLE IF NOT EXISTS {gold}.customer_scd2 (
            customer_id string, balancing_group string, postcode_area string,
            valid_from timestamp, valid_to timestamp)
        LOCATION '{warehouse}/gold/customer_scd2'
        TBLPROPERTIES ('table_type' = 'ICEBERG', 'format' = 'parquet')
    """)
    athena.run(f"""
        CREATE TABLE IF NOT EXISTS {gold}.tariff_scd2 (
            meter_id string, tariff_code string, unit_price_cents_per_kwh int,
            valid_from timestamp, valid_to timestamp)
        LOCATION '{warehouse}/gold/tariff_scd2'
        TBLPROPERTIES ('table_type' = 'ICEBERG', 'format' = 'parquet')
    """)
    athena.run(f"""
        CREATE TABLE IF NOT EXISTS {gold}.training_snapshot (
            customer_id string, meter_id string, snapshot_id string,
            features string, label int, label_source string)
        LOCATION '{warehouse}/gold/training_snapshot'
        TBLPROPERTIES ('table_type' = 'ICEBERG', 'format' = 'parquet')
    """)

    # **Replaced, not appended, and not skipped-if-present.**
    #
    # A second insert would double every assignment and put two versions of one meter in force at
    # the same instant — the SCD-2 defect `src/watermark/core/pit.py` refuses to resolve against.
    # But skipping when rows exist is wrong too, and that is what the first version did: the
    # publisher moves the whole scenario day forward to end at the moment of the run, so a
    # yesterday's seed describes a day the stream is no longer on. Every live reading then fell
    # after the 10:00 changeover and the meter that changes customer attributed its entire day to
    # the second customer.
    #
    # Safe to replace because this is *reference* data derived from the committed cast — a CRM
    # export, in the scenario — and not subject data. An erasure removes readings, online
    # records and training-set membership; nothing it deletes is rebuilt here.
    for table, builder in (
        ("meter_assignment_scd2", meter_assignment_rows),
        ("customer_scd2", customer_rows),
        ("tariff_scd2", tariff_rows),
    ):
        rows = builder()
        athena.run(f"DELETE FROM {gold}.{table}")
        athena.run(f"INSERT INTO {gold}.{table} VALUES {', '.join(rows)}")
        print(f"seed: {len(rows)} rows into {table}, on the day the stream is on")

    # The table is created either way, empty if no snapshot was named. An erasure that finds no
    # table fails its training-set leg and refuses to certify; an erasure that finds an empty one
    # deletes nothing and says so truthfully. The difference between those two is the difference
    # between "the register is broken" and "this subject was in no training set", and a system
    # that cannot tell them apart cannot certify either.
    if not arguments.snapshot:
        print("seed: no snapshot named; training_snapshot created, membership not registered")
        return 0

    # Per snapshot, not per table: a second training run is a second membership, and the whole
    # point of the register is that it can say which one a subject was in.
    present = athena.scalar(
        f"SELECT count(*) FROM {gold}.training_snapshot WHERE snapshot_id = ?",
        [arguments.snapshot],
    )
    if present == "0":
        rows = training_snapshot_rows(arguments.snapshot, arguments.labels)
        athena.run(f"INSERT INTO {gold}.training_snapshot VALUES {', '.join(rows)}")
        print(f"seed: {len(rows)} training-set members for snapshot {arguments.snapshot}")
    else:
        print(f"seed: snapshot {arguments.snapshot} already registered ({present} rows)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
