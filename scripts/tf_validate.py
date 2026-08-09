#!/usr/bin/env python3
"""`terraform validate` on every layer, offline.

`-backend=false` so no state is touched and no credentials are needed; the provider registry
is the only thing reached. This is what catches an attribute that does not exist, and it is
the difference between "this should apply" and "this applies".

**What it does not catch.** A value that violates a provider's attribute pattern. Those
validators run in the *plan* phase, and a plan needs a backend and credentials, so an offline
run cannot reach them. Green here means the configuration is well-formed and every attribute
exists — not that every value is acceptable. Where a name has a format AWS enforces, the
offline stand-in is a `check_*.py` script, not this.

Layers are discovered rather than listed. A hardcoded list is a list that goes stale the day
a layer is added, and it goes stale silently in the direction of "validated".
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"


def _layers() -> list[Path]:
    if not INFRA.is_dir():
        return []
    return sorted(path for path in INFRA.iterdir() if path.is_dir() and any(path.glob("*.tf")))


def main() -> int:
    layers = _layers()
    if not layers:
        print("tf-validate: no terraform layers yet")
        return 0

    failures: list[str] = []
    for layer in layers:
        init = subprocess.run(  # noqa: S603 — fixed command list, no shell
            ["terraform", f"-chdir={layer}", "init", "-backend=false", "-input=false"],
            capture_output=True,
            text=True,
            check=False,
        )
        if init.returncode != 0:
            print(f"  {layer.name:<12} FAIL (init)")
            failures.append(f"{layer.name}: init failed\n{init.stderr[-1500:]}")
            continue
        result = subprocess.run(  # noqa: S603 — fixed command list, no shell
            ["terraform", f"-chdir={layer}", "validate"],
            capture_output=True,
            text=True,
            check=False,
        )
        print(f"  {layer.name:<12} {'ok' if result.returncode == 0 else 'FAIL'}")
        if result.returncode != 0:
            failures.append(f"{layer.name}:\n{(result.stdout + result.stderr)[-2500:]}")

    for failure in failures:
        print(failure, file=sys.stderr)
    print(f"tf-validate: {len(layers) - len(failures)}/{len(layers)} layers")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
