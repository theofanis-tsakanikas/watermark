"""The writer `gold.substation_telemetry` never had, exercised where it can be.

Two feature contracts read that table. It was declared, catalogued, granted and empty for the
whole life of the lakehouse, and nothing ever noticed because an empty Iceberg table answers
every query with zero rows and no error. So the two things worth testing are the two that would
have kept it empty: what gets read out of S3, and what SQL that turns into.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def lander():
    spec = importlib.util.spec_from_file_location(
        "land_telemetry", ROOT / "scripts" / "land_telemetry.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["land_telemetry"] = module
    # No skip on ImportError. boto3 is imported inside `main()` precisely so that the reading
    # and the SQL — the two halves that can be wrong — are testable with no cloud extra, and a
    # guard that skips on the machine that runs it is not a guard.
    spec.loader.exec_module(module)
    return module


class _Body:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


class _FakeS3:
    def __init__(self, objects: dict[str, tuple[dt.datetime, bytes]]) -> None:
        self._objects = objects

    def get_paginator(self, _name: str):
        client = self

        class _Paginator:
            def paginate(self, *, Bucket: str, Prefix: str):
                del Bucket
                yield {
                    "Contents": [
                        {"Key": key, "LastModified": written}
                        for key, (written, _) in client._objects.items()
                        if key.startswith(Prefix)
                    ]
                }

        return _Paginator()

    def get_object(self, *, Bucket: str, Key: str):
        del Bucket
        return {"Body": _Body(self._objects[Key][1])}


def _written(minute: int) -> dt.datetime:
    return dt.datetime(2026, 3, 14, 9, minute, tzinfo=dt.timezone.utc)  # noqa: UP017


def _object(substation: str, minute: int, load: int, limit: int = 450_000) -> bytes:
    return json.dumps(
        {
            "substation_id": substation,
            "event_time": f"2026-03-14T09:{minute:02d}:00Z",
            "load_w": load,
            "limit_w": limit,
        }
    ).encode("utf-8")


def test_the_headroom_is_computed_once_by_the_writer(lander) -> None:
    """Stored, not derived. Claim 3 compares two mechanisms with no tolerance, and a minimum
    over `limit_w - load_w` equals a minimum over `headroom_w` only while both paths stay
    identical — which is exactly what nobody notices changing."""
    client = _FakeS3({"telemetry/SUB-01/a": (_written(0), _object("SUB-01", 0, 400_000))})
    rows = lander.read_telemetry(client, "bucket", 0)
    assert len(rows) == 1
    assert rows[0]["headroom_w"] == 50_000


def test_an_overloaded_substation_keeps_a_negative_headroom(lander) -> None:
    """The case the curtailment decision exists for. Clamping it at zero would erase how far
    over the limit the substation actually was, which is the magnitude somebody acts on."""
    client = _FakeS3({"telemetry/SUB-01/a": (_written(0), _object("SUB-01", 0, 485_442))})
    rows = lander.read_telemetry(client, "bucket", 0)
    assert rows[0]["headroom_w"] == 450_000 - 485_442


def test_only_objects_written_since_the_bound_are_landed(lander) -> None:
    """The prefix holds every capture this estate has ever driven. Landing all of it every run
    would grow the table without bound and re-land what the last run already wrote."""
    client = _FakeS3(
        {
            "telemetry/SUB-01/old": (_written(0), _object("SUB-01", 0, 100)),
            "telemetry/SUB-01/new": (_written(30), _object("SUB-01", 30, 200)),
        }
    )
    since = int(_written(15).timestamp() * 1000)
    rows = lander.read_telemetry(client, "bucket", since)
    assert [row["load_w"] for row in rows] == [200]


def test_an_undecodable_object_is_skipped_rather_than_fatal(lander) -> None:
    """The landing prefix carries quarantined records too. A run that refuses to land anything
    because one object is malformed reports nothing at all, which is the worse outcome."""
    client = _FakeS3(
        {
            "telemetry/SUB-01/bad": (_written(0), b"{not json"),
            "telemetry/SUB-01/short": (
                _written(1),
                json.dumps({"substation_id": "SUB-01"}).encode(),
            ),
            "telemetry/SUB-01/good": (_written(2), _object("SUB-01", 2, 300)),
        }
    )
    rows = lander.read_telemetry(client, "bucket", 0)
    assert [row["load_w"] for row in rows] == [300]


def test_the_insert_names_every_column_the_table_declares(lander) -> None:
    """A positional INSERT against a table whose columns were reordered writes the load into the
    limit and reports success."""
    rows = [
        {
            "substation_id": "SUB-01",
            "event_time": "2026-03-14 09:00:00",
            "ingest_time": "2026-03-14 09:00:05",
            "load_w": 400_000,
            "limit_w": 450_000,
            "headroom_w": 50_000,
            "event_day": "2026-03-14",
        }
    ]
    statement = lander.insert_statements("watermark_gold", rows)[0]
    for column in (
        "event_time",
        "ingest_time",
        "substation_id",
        "load_w",
        "limit_w",
        "headroom_w",
        "event_day",
    ):
        assert column in statement
    assert "watermark_gold.substation_telemetry" in statement


def test_the_rows_are_batched_rather_than_sent_as_one_statement(lander) -> None:
    """Athena's statement ceiling is 262,144 bytes, and a long capture makes many short rows.
    One oversized INSERT fails the whole landing rather than a batch of it."""
    rows = [
        {
            "substation_id": f"SUB-{index % 4:02d}",
            "event_time": "2026-03-14 09:00:00",
            "ingest_time": "2026-03-14 09:00:05",
            "load_w": index,
            "limit_w": 450_000,
            "headroom_w": 450_000 - index,
            "event_day": "2026-03-14",
        }
        for index in range(lander.ROWS_PER_INSERT * 2 + 1)
    ]
    statements = lander.insert_statements("watermark_gold", rows)
    assert len(statements) == 3
    assert all(len(statement) < 262_144 for statement in statements)
