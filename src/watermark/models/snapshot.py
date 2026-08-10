"""Pin the offline store at an instant and write the training set the rest of the run reads.

The first step of `infra/ml/pipeline.tf`, and the reason the four after it can be trusted. It
does exactly two things: read rows *as of* one snapshot, and record a digest of what it read.

Neither is bureaucracy. A training run that reads "the current table" is a run nobody can
repeat — the lakehouse moves, a three-day-late batch restates a total, and last month's metrics
cannot be argued with. The digest is what separates a model that got better from a model that
read different rows.

**Runs inside a SageMaker Processing job**, where the container is a stock AWS image that has
never heard of this package. `make package-ml` builds a wheel, Terraform uploads it, and the
step installs it before calling this module — see the `code` channel in `pipeline.tf`. A
processing step whose entrypoint names a module the image does not contain is a step that fails
after the cluster has been paid for.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

#: Where SageMaker mounts a processing job's outputs. Fixed by the service, not by us.
OUTPUT = Path("/opt/ml/processing/output")


def rows_as_of(source: Path, snapshot: str, as_of: str) -> list[dict[str, object]]:
    """Read the training set from the source the caller was given, and stamp it.

    **It reads a path. It does not know where the rows came from**, and that is deliberate: an
    earlier version imported the synthetic generator from `data/`, which is not in the wheel the
    processing job installs. The step would have failed with `ImportError` inside a running
    cluster — the exact failure the code channel exists to prevent, reintroduced one import
    lower down.

    The shipped package must not depend on the repository's test fixtures. Here the pipeline
    supplies a `population` channel; a real implementation replaces the caller with
    `SELECT ... FOR VERSION AS OF <snapshot>` against Iceberg, binding parameters rather than
    interpolating them, and this function is unchanged because it never knew the difference.
    """
    with source.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return []

    required = {"entity_id", "deprivation_decile", "score", "confirmed"}
    missing = required - set(rows[0])
    if missing:
        raise ValueError(f"{source} is missing columns: {sorted(missing)}")

    return [
        {
            "entity_id": row["entity_id"],
            "deprivation_decile": int(row["deprivation_decile"]),
            "score": int(row["score"]),
            "confirmed": int(row["confirmed"]),
            "as_of": as_of,
            "snapshot": snapshot,
        }
        for row in rows
    ]


def digest_of(rows: list[dict[str, object]]) -> str:
    """A hash of the rows in content order.

    Sorted by entity, so the digest describes *what was read* and not the order a query happened
    to return it in. Two runs over one snapshot that disagreed here would be two runs over
    different data wearing the same name.
    """
    material = "\n".join(
        "|".join(str(row[key]) for key in sorted(row))
        for row in sorted(rows, key=lambda item: str(item["entity_id"]))
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("/opt/ml/processing/input/population/population.csv"),
        help="The rows to pin. Supplied as a channel; never imported.",
    )
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    arguments = parser.parse_args(argv)

    rows = rows_as_of(arguments.source, arguments.snapshot, arguments.as_of)
    if not rows:
        # Louder than an empty file. A downstream trainer given nothing fits nothing and scores
        # perfectly on it, and the run would register a model with flawless metrics.
        print("snapshot: no rows at that snapshot", file=sys.stderr)
        return 1

    arguments.output.mkdir(parents=True, exist_ok=True)

    # Headers, and in a fixed order. Clarify reads this file by column name and the header list
    # in `clarify.analysis_configuration` has to match it — two places agreeing by convention is
    # two places that drift, so the order here is the one that module names.
    columns = ["entity_id", "deprivation_decile", "score", "confirmed"]
    with (arguments.output / "dataset.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda item: str(item["entity_id"])))

    # Clarify's configuration, written here because this step is what decides the columns.
    # It used to be read from S3 by the Clarify step and written by nothing at all — the job
    # would have started, found no config, and failed after paying for the cluster.
    from watermark.models.clarify import analysis_configuration  # noqa: PLC0415

    (arguments.output / "analysis_config.json").write_text(
        json.dumps(
            analysis_configuration(
                dataset_uri=str(arguments.output / "dataset.csv"),
                output_uri=str(arguments.output / "clarify"),
            ),
            indent=2,
        ),
        "utf-8",
    )

    manifest = {
        "snapshot": arguments.snapshot,
        "as_of": arguments.as_of,
        "rows": len(rows),
        "data_digest": digest_of(rows),
        "columns": columns,
    }
    (arguments.output / "manifest.json").write_text(json.dumps(manifest, indent=2), "utf-8")
    print(f"snapshot: {len(rows)} rows, digest {manifest['data_digest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
