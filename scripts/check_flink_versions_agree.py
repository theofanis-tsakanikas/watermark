#!/usr/bin/env python3
"""The Flink the equivalence tier runs against is the Flink that is deployed.

ADR-0003's tier two asserts that the real PyFlink job produces the same bytes as the pure core.
Run against a different Flink than the deployed one, it establishes equivalence with something
nobody is running — which is worse than not running it, because it looks like coverage.

Two places state the version and they are compared here: the `apache-flink` floor in the
`flink` extra of `pyproject.toml`, and `runtime_environment` in `infra/streaming/`. The check
is on the minor version; a patch difference is not a semantic difference and requiring an exact
match would make this a nuisance that gets deleted.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _declared_extra() -> tuple[int, int] | None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    for requirement in metadata["project"]["optional-dependencies"].get("flink", []):
        match = re.match(r"apache-flink\s*>=\s*(\d+)\.(\d+)", requirement)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


def _declared_runtime() -> tuple[int, int] | None:
    variables = (ROOT / "infra" / "streaming" / "variables.tf").read_text(encoding="utf-8")
    match = re.search(r'default\s*=\s*"FLINK-(\d+)_(\d+)"', variables)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _declared_connector() -> tuple[int, int] | None:
    """The Flink minor the Kinesis connector JAR is built for.

    Third place the version is stated, and the one that fails latest: a mismatched connector
    does not fail at package time or at apply time. The application starts, reports READY, and
    reads nothing — which is the most expensive shape a failure can take.
    """
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    match = re.search(r"CONNECTOR_VERSION := [\d.]+-(\d+)\.(\d+)", makefile)
    return (int(match.group(1)), int(match.group(2))) if match else None


def main() -> int:
    extra, runtime = _declared_extra(), _declared_runtime()
    connector = _declared_connector()
    if extra is None:
        print("no apache-flink floor in the `flink` extra of pyproject.toml", file=sys.stderr)
        return 1
    if runtime is None:
        print("no FLINK-x_y default for runtime_environment in infra/streaming", file=sys.stderr)
        return 1
    if connector is None:
        print("no CONNECTOR_VERSION in the Makefile", file=sys.stderr)
        return 1
    if connector != runtime:
        print(
            f"the Kinesis connector is built for Flink {connector[0]}.{connector[1]} and the "
            f"deployment runs Flink {runtime[0]}.{runtime[1]}. This does not fail at package "
            "time or at apply time — the application starts, reports READY, and reads nothing.",
            file=sys.stderr,
        )
        return 1
    if extra != runtime:
        print(
            f"the equivalence tier would run Flink {extra[0]}.{extra[1]} against a deployment "
            f"of Flink {runtime[0]}.{runtime[1]}.\n"
            "Equivalence with a Flink nobody is running is not equivalence. Move one of them: "
            "the `flink` extra in pyproject.toml, or `flink_runtime` in infra/streaming.",
            file=sys.stderr,
        )
        return 1
    print(
        f"flink-versions: the equivalence tier, the connector and the deployment all run "
        f"Flink {extra[0]}.{extra[1]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
