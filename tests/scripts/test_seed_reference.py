"""The reference seed lands the cast, and lands the version history rather than flattening it.

`scripts/seed_reference.py` exists because two `gold` tables were catalogue entries describing
tables that had never existed, and an erasure request was the first thing to find out. These
tests are what stops that being true again — offline, with no account.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


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


def test_the_meter_that_changes_customer_gets_two_versions(seeder) -> None:
    """SCD-2, not a mapping.

    `M00007` belongs to `C00007` until 10:00 and to `C00007-NEW` after. A seed that flattened
    that to one row would make an erasure for either subject reach the other's readings, and the
    certificate would say the boundary held.
    """
    rows = [row for row in seeder.meter_assignment_rows() if "'M00007'" in row]
    assert len(rows) == 2
    assert any("'C00007'," in row and "10:00:00'" in row for row in rows)
    assert any("'C00007-NEW'" in row and "CAST(NULL AS timestamp)" in row for row in rows)


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
