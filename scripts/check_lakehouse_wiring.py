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

**The layer is checked too, and it was not always.** This file used to compare bare table names
and say so: the database prefix was an environment variable in dbt and an interpolated string in
Terraform, and comparing those would have been comparing two renderings of one fact. That was
true and it left a hole exactly the width of the bug that fell through it —
`queries/settlement_hourly.sql` read `gold.meter_interval` for as long as this check existed,
while Terraform and dbt both put `meter_interval` in *silver*. `terraform validate` cannot see
it, checkov cannot see it, and a query registered against a schema that does not exist looks
identical to one that does until somebody runs it. Athena did, and answered `SCHEMA_NOT_FOUND`.

So the prefix is still not compared — but the **layer** is. dbt writes its schema as
`{{ env_var(...) }}_silver` and the queries now write `${silver}`, so both name the layer and
neither names the project. That is one fact in three places, which is what this file is for.
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


def _terraform_tables() -> dict[str, str]:
    """The Glue catalogue entries Terraform declares, mapped to their layer.

    After ADR-0008 these are only the tables no engine in this repository writes — the interface
    a CDC pipeline lands into. The tables that *are* written declare themselves in the writer,
    and `_job_tables` and `_dbt_models` below are where those come from.

    The layer, not the database name: `database_name` is `aws_glue_catalog_database.silver.name`,
    and the resource label is the one part of that which is not a rendering of `var.project`.
    """
    tables: dict[str, str] = {}
    for path in (ROOT / "infra" / "lakehouse").glob("*.tf"):
        text = path.read_text(encoding="utf-8")
        # Split on the resource header rather than matching a balanced block: a table resource
        # contains `storage_descriptor` and a dozen `columns` blocks, so `[^}]*?` stops at the
        # first nested brace and a greedy match runs into the next resource. Both `name` and
        # `database_name` are in the first few lines, which is all this needs to read.
        for chunk in text.split('resource "aws_glue_catalog_table"')[1:]:
            name = re.search(r'\bname\s*=\s*"([^"]+)"', chunk)
            layer = re.search(
                r"\bdatabase_name\s*=\s*aws_glue_catalog_database\.(\w+)\.name", chunk
            )
            if name and layer:
                tables[name.group(1)] = layer.group(1)
    return tables


def _job_tables() -> dict[str, str]:
    """Tables a Glue job creates and writes, read from the arguments the job is given.

    The job's `CREATE TABLE IF NOT EXISTS` names `ARGUMENTS['TABLE']` in
    `ARGUMENTS['DATABASE']`, so the *name* of what it creates is not in the Python at all — it is
    in `default_arguments`. Reading it here is what keeps `--TABLE` and the queries from drifting
    now that there is no Terraform resource in between to reference.
    """
    tables: dict[str, str] = {}
    for path in (ROOT / "infra" / "lakehouse").glob("*.tf"):
        text = path.read_text(encoding="utf-8")
        for chunk in text.split('resource "aws_glue_job"')[1:]:
            name = re.search(r'"--TABLE"\s*=\s*"([^"]+)"', chunk)
            layer = re.search(r'"--DATABASE"\s*=\s*aws_glue_catalog_database\.(\w+)\.name', chunk)
            if name and layer:
                tables[name.group(1)] = layer.group(1)
    return tables


def _dbt_models() -> dict[str, str]:
    """Tables dbt builds. The model file is the table and its directory is the layer.

    dbt creates its own Iceberg tables — `dbt_project.yml` sets `+table_type: iceberg` on the
    gold models — so a model is a table this repository creates just as much as a Glue job is,
    and a query reading one must be checked against it.
    """
    models: dict[str, str] = {}
    for path in (ROOT / "pipelines" / "dbt" / "models").glob("*/*.sql"):
        models[path.stem] = path.parent.name
    return models


def _dbt_sources() -> dict[str, str]:
    """Parsed, not pattern-matched.

    The first version matched `- name:` with a regex and picked up every column as well as
    every table, then subtracted the source-group names by hand. It reported six columns as
    missing tables on its first run — and a check that cries wolf immediately is a check
    somebody deletes, so it reads the YAML properly.

    The layer comes off the end of the declared `schema`, which is
    `{{ env_var('WATERMARK_PROJECT', 'watermark') }}_silver`. The source *group* name is not
    used for it: a group may be called `reference` and read from `gold`, which is exactly the
    case that would make a group-name reading wrong and silent.
    """
    tables: dict[str, str] = {}
    for path in (ROOT / "pipelines" / "dbt" / "models").rglob("sources.yml"):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for source in document.get("sources", []):
            layer = str(source.get("schema", "")).rpartition("_")[2]
            for table in source.get("tables", []):
                tables[table["name"]] = layer
    return tables


def _query_tables() -> dict[str, set[str]]:
    """Every table the queries read, mapped to the layers they read it from.

    A set of layers rather than one, so that a table read from two different schemas is a
    reported disagreement rather than whichever the last file happened to say.

    The pattern matches `${silver}.meter_interval` — the placeholder form
    `infra/lakehouse/athena.tf` renders. A bare `silver.meter_interval` no longer matches at
    all, which is deliberate: it would be a query that hardcodes a database name this repository
    does not create, and going unread here is the closest thing to a false pass available.
    """
    tables: dict[str, set[str]] = {}
    for path in (ROOT / "queries").glob("*.sql"):
        text = path.read_text(encoding="utf-8")
        for layer, name in re.findall(r"\$\{(\w+)\}\.(\w+)", text):
            tables.setdefault(name, set()).add(layer)
    return tables


def main() -> int:
    sources, queries = _dbt_sources(), _query_tables()

    # Everything this repository creates, and where. Three creators now rather than one: the
    # catalogue entries Terraform still declares, the tables a Glue job creates as it writes
    # them, and the tables dbt builds. A table with no creator at all is `EXTERNAL` — landed by
    # a CDC pipeline that is outside this repository — and is listed by name so that a typo is
    # still a failure rather than a shrug.
    created: dict[str, str] = {**_terraform_tables(), **_job_tables(), **_dbt_models()}
    known = set(created) | EXTERNAL

    problems = []
    for name in sorted(set(sources) - known):
        problems.append(
            f"dbt declares source `{name}`, which nothing in this repository creates and which "
            "is not listed as external. `dbt parse` will resolve it happily and the first build "
            "will fail on a table that never existed."
        )
    for name in sorted(set(queries) - known):
        problems.append(
            f"a query reads `{name}`, which nothing in this repository creates. A resolver "
            "pointed at a table that is not there reads zero rows and calls it zero watt-hours."
        )

    # The layer, for everything more than one description names. This is the half that was
    # missing, and the reason a query read `gold.meter_interval` for the whole of phase 4 while
    # Terraform and dbt both put the table in silver.
    for name, layer in sorted(created.items()):
        if name in sources and sources[name] != layer:
            problems.append(
                f"`{name}` is created in `{layer}` and dbt declares it as a source in "
                f"`{sources[name]}`. dbt parses against its own declaration, so this compiles "
                "and fails on the first build."
            )
        for read_from in sorted(queries.get(name, set())):
            if read_from != layer:
                problems.append(
                    f"a query reads `{read_from}.{name}` and `{name}` is created in `{layer}`. "
                    "Athena answers SCHEMA_NOT_FOUND or, worse, finds a table of the same name "
                    "in the other layer and totals the wrong rows."
                )

    if problems:
        print("lakehouse-wiring: the descriptions disagree\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print(
        f"lakehouse-wiring: {len(created)} tables created, {len(sources)} dbt sources and "
        f"{len(queries)} tables read by queries all agree, layer included"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
