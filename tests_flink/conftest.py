"""Tier two of ADR-0003. It needs a JVM, and a skip here is not the same as a pass.

**Locally** the tier skips with a printed reason when `apache-flink` is not importable, because
a check nobody can run is a check nobody runs — and on this author's machine it genuinely
cannot: `apache-flink` pulls `apache-beam`, which has no wheel for Python 3.12 on arm64 macOS
and fails to build. That is recorded, with its date, in `docs/AWS-CONSTRAINTS.md`.

**In CI** `WATERMARK_REQUIRE_FLINK=1` turns that skip into a failure. Silent skipping is how a
suite reports green for a year while proving one thing less than it says, and this is the one
thing in the repository that cannot be proved on the machine that wrote it.
"""

from __future__ import annotations

import os

import pytest

REQUIRED = bool(os.environ.get("WATERMARK_REQUIRE_FLINK"))


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    try:
        import pyflink  # noqa: F401, PLC0415
    except ImportError as exc:
        if REQUIRED:
            pytest.exit(
                "WATERMARK_REQUIRE_FLINK is set and apache-flink is not importable. The tier "
                "that would prove Flink agrees with the core is not running, so nothing here "
                f"proves it. ({exc})",
                returncode=1,
            )
        skip = pytest.mark.skip(
            reason="apache-flink is not installed; the equivalence tier needs a JVM. "
            "See docs/AWS-CONSTRAINTS.md — it will not install on macOS/arm64 under 3.12."
        )
        for item in items:
            item.add_marker(skip)
