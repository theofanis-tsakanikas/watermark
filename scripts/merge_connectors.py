#!/usr/bin/env python3
"""Merge the connector JARs into the one archive Managed Flink will load.

`kinesis.analytics.flink.run.options.jarfile` takes exactly **one** path — a comma-separated
pair is rejected as a file name — and this job needs three: the Kinesis connector, the Iceberg
Flink runtime, and the Iceberg AWS bundle that actually contains `GlueCatalog` and `S3FileIO`.
Adding them from the job with `add_jars` does not help: a catalog factory is resolved by the
planner in the driver, before the job graph exists.

An uber jar is the documented answer for this platform, and merging is not concatenation:

**`META-INF/services/*` must be appended, never overwritten.** Those files are how Java finds
factories — the Iceberg catalog factory and the Kinesis connector factory both register there,
and a naive merge keeps whichever archive was read last and silently loses the other. That
failure looks exactly like a missing dependency.

**Signature files must be dropped.** `META-INF/*.SF`, `*.DSA` and `*.RSA` sign the archive they
came from; carried into a merged jar they make the JVM refuse it as tampered with.

Deterministic: entries are written in sorted order with a fixed timestamp, so the same inputs
produce the same bytes and the content-addressed S3 key does not churn between builds.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

#: Zip entries whose presence would make the JVM reject the merged archive.
SIGNATURES = (".SF", ".DSA", ".RSA", ".EC")

#: A fixed timestamp, so two builds of the same inputs are the same bytes.
EPOCH = (1980, 1, 1, 0, 0, 0)


def merge(sources: list[Path], target: Path) -> tuple[int, int]:
    services: dict[str, bytes] = {}
    entries: dict[str, bytes] = {}

    for source in sources:
        with zipfile.ZipFile(source) as archive:
            for name in archive.namelist():
                if name.endswith("/"):
                    continue
                if name.startswith("META-INF/") and name.endswith(SIGNATURES):
                    continue
                data = archive.read(name)
                if name.startswith("META-INF/services/"):
                    # Appended. Two factories registered under one name are two factories.
                    previous = services.get(name, b"")
                    joined = previous + (
                        b"\n" if previous and not previous.endswith(b"\n") else b""
                    )
                    services[name] = joined + data
                elif name not in entries:
                    # First wins. Shaded connectors carry overlapping third-party classes, and
                    # the alternative — failing on every collision — would refuse every real
                    # pair of connectors ever built.
                    entries[name] = data

    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
        for name in sorted(entries | services):
            info = zipfile.ZipInfo(name, date_time=EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            out.writestr(info, services.get(name) or entries[name])

    return len(entries), len(services)


#: The program name, a target, and at least one source.
MINIMUM_ARGUMENTS = 3


def main() -> int:
    if len(sys.argv) < MINIMUM_ARGUMENTS:
        print("usage: merge_connectors.py <out.jar> <in.jar>...", file=sys.stderr)
        return 1

    target, sources = Path(sys.argv[1]), [Path(a) for a in sys.argv[2:]]
    missing = [s for s in sources if not s.is_file()]
    if missing:
        print(f"missing: {', '.join(str(m) for m in missing)}", file=sys.stderr)
        return 1

    classes, service_files = merge(sources, target)
    size = target.stat().st_size / 1_000_000
    print(
        f"merged {len(sources)} jars → {target} "
        f"({size:.0f} MB, {classes} entries, {service_files} service files)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
