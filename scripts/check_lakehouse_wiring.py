#!/usr/bin/env python3
"""Terraform, dbt and the queries describe one lakehouse three times. They must agree.

`dbt parse` resolves a source against `sources.yml`, not against a catalogue, so a table name
that exists in no warehouse compiles perfectly and fails on the first real build — after the
infrastructure is up, which is the expensive place to find out. Worse, a resolver pointed at an
empty table reads zero rows and calls it zero watt-hours.

Three statements are compared:

  * the Glue tables `infra/lakehouse/` creates,
  * the sources `pipelines/dbt/models/**/sources.yml` declares,
  * the tables `queries/*.sql` read.

The check is on the *table* name rather than the fully qualified name, because the database
prefix is an environment variable in dbt and an interpolated string in Terraform, and comparing
those would be comparing two different renderings of one fact.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

#: Tables written by something other than this repository's Terraform. The CDC pipeline lands
#: the SCD-2 reference tables; they are declared here rather than silently tolerated, so that a
#: typo in a source name is still a failure.
EXTERNAL = {"meter_assignment_scd2", "customer_scd2", "training_snapshot"}


def _terraform_tables() -> set[str]:
    tables: set[str] = set()
    for path in (ROOT / "infra" / "lakehouse").glob("*.tf"):
        text = path.read_text(encoding="utf-8")
        tables.update(
            re.findall(
                r'resource\s+"aws_glue_catalog_table"\s+"[^"]+"\s*\{[^}]*?name\s*=\s*"([^"]+)"',
                text,
                re.S,
            )
        )
    return tables


def _dbt_sources() -> set[str]:
    """Parsed, not pattern-matched.

    The first version matched `- name:` with a regex and picked up every column as well as
    every table, then subtracted the source-group names by hand. It reported six columns as
    missing tables on its first run — and a check that cries wolf immediately is a check
    somebody deletes, so it reads the YAML properly.
    """
    tables: set[str] = set()
    for path in (ROOT / "pipelines" / "dbt" / "models").rglob("sources.yml"):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for source in document.get("sources", []):
            tables.update(table["name"] for table in source.get("tables", []))
    return tables


def _query_tables() -> set[str]:
    tables: set[str] = set()
    for path in (ROOT / "queries").glob("*.sql"):
        text = path.read_text(encoding="utf-8")
        tables.update(name for _, name in re.findall(r"\b(?:FROM|JOIN)\s+(\w+)\.(\w+)", text, re.I))
    return tables


def main() -> int:
    terraform, dbt, queries = _terraform_tables(), _dbt_sources(), _query_tables()
    known = terraform | EXTERNAL

    problems = []
    for name in sorted(dbt - known):
        problems.append(
            f"dbt declares source `{name}`, which no Glue table creates and which is not "
            "listed as external. `dbt parse` will resolve it happily and the first build will "
            "fail on a table that never existed."
        )
    for name in sorted(queries - known):
        problems.append(
            f"a query reads `{name}`, which no Glue table creates. A resolver pointed at a "
            "table that is not there reads zero rows and calls it zero watt-hours."
        )

    if problems:
        print("lakehouse-wiring: the three descriptions disagree\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print(
        f"lakehouse-wiring: {len(terraform)} Glue tables, {len(dbt)} dbt sources and "
        f"{len(queries)} tables read by queries all agree"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
