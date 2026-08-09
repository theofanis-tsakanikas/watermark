"""Every value the job runs with, resolved from the core and from the environment.

Two kinds, kept apart on purpose.

**Semantics** come from `watermark.core`. They are the same objects the offline runner uses, so
the two cannot disagree about what a window is — which is the whole of what the equivalence
tier in ADR-0003 asserts.

**Placement** comes from the environment: which stream, which region, which parallelism. Those
are facts about a deployment and Terraform owns them; a job with a hardcoded stream name is a
job that can only ever run in one account.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from watermark.core.normalise import DEFAULT_POLICY as NORMALISATION
from watermark.core.records import METER_INTERVAL, SETTLEMENT_GRAIN
from watermark.core.watermarks import DEFAULT_POLICY as WATERMARK
from watermark.core.windows import DEFAULT_ALLOWED_LATENESS

#: The semantic constants the job is allowed to use, by name. Collected here so that the
#: adapter gate has one place to check against and a reader has one place to look — and so
#: that adding a threshold means adding it to the core first.
SEMANTICS = {
    "window_length": METER_INTERVAL,
    "settlement_grain": SETTLEMENT_GRAIN,
    "out_of_orderness": WATERMARK.out_of_orderness,
    "idle_after": WATERMARK.idle_after,
    "stall_after": WATERMARK.stall_after,
    "allowed_lateness": DEFAULT_ALLOWED_LATENESS,
    "skew_tolerance": NORMALISATION.skew_tolerance,
}


@dataclass(frozen=True, slots=True)
class Placement:
    """Where this job runs and what it reads. Supplied by Terraform, never assumed."""

    region: str
    meter_stream: str
    telemetry_stream: str
    output_bucket: str
    checkpoint_interval_millis: int

    @staticmethod
    def from_environment() -> Placement:
        """Read the placement, refusing to invent any of it.

        A default stream name is the most expensive kind of convenience: the job starts, reads
        nothing, reports healthy, and the first anybody knows is a settlement run with no rows.
        """
        return Placement(
            region=_required("WATERMARK_REGION"),
            meter_stream=_required("WATERMARK_METER_STREAM"),
            telemetry_stream=_required("WATERMARK_TELEMETRY_STREAM"),
            output_bucket=_required("WATERMARK_OUTPUT_BUCKET"),
            checkpoint_interval_millis=int(_required("WATERMARK_CHECKPOINT_INTERVAL_MILLIS")),
        )


def _required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(
            f"{name} is not set. Placement comes from Terraform and is never defaulted: a job "
            "with a guessed stream name starts, reads nothing, and reports healthy."
        )
    return value
