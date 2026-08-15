#!/usr/bin/env python3
"""Every feature reads a column that exists, from a table something writes.

A feature contract names a `source_table` and a `source_column`. Nothing checked that either of
them was real, and the consequence was not theoretical: `substation_headroom_15m` declared
`headroom_w` on a table whose columns are `load_w` and `limit_w`, and
`gold.substation_telemetry` was a Glue catalogue entry with no writer anywhere in the
repository. Both loaded. Both validated. Both would have failed the first time anybody asked
for the value, which is why neither did — nobody ever asked.

That is the shape of this failure and it is why the check is here rather than in the contract
model. Pydantic can insist a column name is a string; only the repository as a whole knows
whether the column is in a table, and only the repository knows whether the table has a writer.

Three questions, and the third is the one that catches an empty catalogue:

1. Does the `source_table` appear among the Glue tables `infra/lakehouse/` declares, or among
   the tables the writers create at runtime?
2. Does the `source_column` — along with the entity key, the event-time column and the
   ingest-time column — appear among that table's columns?
3. Does anything actually *write* the table? A declared table nobody populates resolves every
   query to zero rows, and a resolver pointed at an empty table reads zero watt-hours and calls
   it zero watt-hours.

Nothing here reaches AWS. It reads HCL and Python with regular expressions, which is crude and
is the right trade: the alternative is a Glue client and a deployed estate, and a check that
needs an estate is a check that does not run on the pull request that breaks it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from pathlib import Path as pathlib_Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from watermark.contracts import load  # noqa: E402

LAKEHOUSE = ROOT / "infra" / "lakehouse"
#: Where a table can acquire rows. `land_to_silver.py` and the seeding script create tables the
#: Terraform layer deliberately does not declare — see the comment in `infra/lakehouse/glue.tf`
#: about the writer owning the Iceberg schema — so both places are read.
WRITERS = (ROOT / "pipelines" / "jobs", ROOT / "scripts", ROOT / "pipelines" / "dbt")

_TABLE = re.compile(r'resource\s+"aws_glue_catalog_table"\s+"[^"]+"\s*\{(?P<body>.*?)\n\}', re.S)
_NAME = re.compile(r'\n\s*name\s*=\s*"(?P<name>[^"]+)"')
_COLUMN = re.compile(r'columns\s*\{\s*\n\s*name\s*=\s*"(?P<column>[^"]+)"')
_PARTITION = re.compile(r'partition_keys\s*\{\s*\n\s*name\s*=\s*"(?P<column>[^"]+)"')


def _declared_tables() -> dict[str, set[str]]:
    """Table name to its columns, as `infra/lakehouse/` declares them."""
    tables: dict[str, set[str]] = {}
    for path in LAKEHOUSE.glob("*.tf"):
        text = path.read_text(encoding="utf-8")
        for block in _TABLE.finditer(text):
            body = block.group("body")
            named = _NAME.search(body)
            if not named:
                continue
            columns = {match.group("column") for match in _COLUMN.finditer(body)}
            columns |= {match.group("column") for match in _PARTITION.finditer(body)}
            tables[named.group("name")] = columns
    return tables


#: A Glue job block, so a job's script can be tied to the table its arguments name.
_JOB = re.compile(r'resource\s+"aws_glue_job"\s+"[^"]+"\s*\{(?P<body>.*?)\n\}', re.S)
_SCRIPT = re.compile(r"script_location\s*=\s*\"[^\"]*/(?P<script>[\w.]+\.py)\"")
_ARG_TABLE = re.compile(r'"--TABLE"\s*=\s*"(?P<table>[^"]+)"')
_CREATE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>[\w.{}'\[\]]+)\s*\((?P<body>[^)]*)",
    re.I | re.S,
)


def _module_constants(text: str) -> dict[str, str]:
    """Module-level `NAME = "literal"` assignments, so an f-string can be resolved locally."""
    return {
        match.group("name"): match.group("value")
        for match in re.finditer(
            r'^(?P<name>[A-Z_][A-Z0-9_]*)(?::[^=]+)?\s*=\s*"(?P<value>[^"]+)"', text, re.M
        )
    }


def _columns_of(body: str) -> set[str]:
    return {
        line.strip().split()[0].strip('`"')
        for line in body.splitlines()
        if line.strip() and not line.strip().startswith("--")
    }


def _writer_scripts() -> list[pathlib_Path]:
    files = []
    for directory in WRITERS:
        if not directory.exists():
            continue
        files += [p for p in directory.rglob("*.py") if "__pycache__" not in p.parts]
        files += list(directory.rglob("*.sql"))
    return files


def _job_tables() -> dict[str, set[str]]:
    """Script filename to the tables Terraform's `--TABLE` argument points it at.

    `land_to_silver.py` writes `CREATE TABLE IF NOT EXISTS {TARGET}`, and `TARGET` is built from
    a job argument. Reading the script alone therefore finds a table called `{TARGET}`, which is
    how the first version of this check reported `meter_interval` as having no writer while the
    job that creates it sat two directories away. The name lives in the HCL; the columns live in
    the Python; neither half is enough on its own.
    """
    mapping: dict[str, set[str]] = {}
    for path in LAKEHOUSE.glob("*.tf"):
        for block in _JOB.finditer(path.read_text(encoding="utf-8")):
            body = block.group("body")
            script = _SCRIPT.search(body)
            table = _ARG_TABLE.search(body)
            if script and table:
                mapping.setdefault(script.group("script"), set()).add(table.group("table"))
    return mapping


def _tables_a_writer_creates() -> dict[str, set[str]]:
    """Tables created by a `CREATE TABLE` in a job or a script, with the columns it declares.

    The writer owns the Iceberg schema in this repository — ADR-0008 — so a table that appears
    only here is correct and not an omission. What would be an omission is a feature reading
    from a table that appears in *neither* place.
    """
    tables: dict[str, set[str]] = {}
    by_script = _job_tables()
    for path in _writer_scripts():
        text = path.read_text(encoding="utf-8")
        for match in _CREATE.finditer(text):
            columns = _columns_of(match.group("body"))
            name = match.group("name").split(".")[-1]
            if "{" not in name and "[" not in name:
                tables.setdefault(name, set()).update(columns)
                continue

            # The name is interpolated, and there are two ways it can be. A Glue job builds it
            # from a job argument, so Terraform is the only place that knows — `land_to_silver`
            # writes `{TARGET}`. A script builds it from a constant beside it, so the file knows
            # — `land_telemetry` writes `{database}.{TABLE}` with `TABLE` two screens up. Reading
            # only the first left a writer that plainly creates a table reported as no writer at
            # all, which is a false accusation and the fastest way to get a check deleted.
            placeholder = name.strip("{}")
            local = _module_constants(text).get(placeholder)
            if local:
                tables.setdefault(local, set()).update(columns)
                continue
            for resolved in by_script.get(path.name, ()):
                tables.setdefault(resolved, set()).update(columns)
    return tables


def _has_a_writer(table: str) -> bool:
    """Anything at all that puts rows in it: an INSERT, a dbt model, a Spark writer, a job."""
    if any(table in tables for tables in _job_tables().values()):
        return True
    needles = (f"INTO {table}", f"into {table}", f"{table}(", f'"{table}"', f"'{table}'")
    return any(
        any(needle in path.read_text(encoding="utf-8") for needle in needles)
        for path in _writer_scripts()
    )


def main() -> int:
    contracts = load()
    declared = _declared_tables()
    written = _tables_a_writer_creates()
    known = {
        name: declared.get(name, set()) | written.get(name, set()) for name in declared | written
    }

    problems: list[str] = []
    for name, feature in sorted(contracts.features.items()):
        table = feature.source_table
        if table not in known:
            problems.append(
                f"`{name}` reads from `{table}`, which no Terraform declaration and no writer "
                f"in this repository creates. The feature would resolve to nothing, which is "
                f"not the same as resolving to zero."
            )
            continue

        columns = known[table]
        if not columns:
            continue  # the writer creates it with a schema this check cannot read; not a finding
        wanted = {
            "source_column": feature.source_column,
            "entity_key": feature.entity_key,
            "event_time_column": feature.event_time_column,
            "ingest_time_column": feature.ingest_time_column,
        }
        for role, column in wanted.items():
            if column and column not in columns:
                problems.append(
                    f"`{name}` names `{column}` as its {role}, and `{table}` has no such "
                    f"column. Its columns are {sorted(columns)}."
                )

        if not _has_a_writer(table):
            problems.append(
                f"`{name}` reads from `{table}`, which is declared and which nothing writes. An "
                f"empty table answers every query with zero rows and reports no error at all."
            )

    if problems:
        print("feature-sources: a feature reads something that is not there\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print(
        f"feature-sources: {len(contracts.features)} features, every column present in a table "
        f"something writes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
