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

import json
import os
from dataclasses import dataclass

from watermark.core.normalise import DEFAULT_POLICY as NORMALISATION
from watermark.core.records import BATCH_GRAIN, METER_INTERVAL, SETTLEMENT_GRAIN
from watermark.core.watermarks import DEFAULT_POLICY as WATERMARK
from watermark.core.windows import DEFAULT_ALLOWED_LATENESS

#: The semantic constants the job is allowed to use, by name. Collected here so that the
#: adapter gate has one place to check against and a reader has one place to look — and so
#: that adding a threshold means adding it to the core first.
SEMANTICS = {
    "window_length": METER_INTERVAL,
    "batch_grain": BATCH_GRAIN,
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
    #: The ceiling for rescaling while retaining state. Set explicitly in the first version of
    #: the application, because changing it later means the job can no longer restart from an
    #: existing snapshot — see docs/AWS-CONSTRAINTS.md.
    max_parallelism: int
    #: Which substations the watermark generator declares. Declared rather than discovered: a
    #: partition it has never heard of cannot hold the watermark back, so a substation that is
    #: down at start-up would be silently excluded and every window would close without it.
    partitions: tuple[str, ...]
    #: Where a fresh application starts reading. `TRIM_HORIZON` for a replay, `LATEST` for a
    #: live start; never defaulted, because the two produce completely different first hours
    #: and the difference is invisible in a dashboard.
    initial_position: str

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
            max_parallelism=int(_required("WATERMARK_MAX_PARALLELISM")),
            partitions=tuple(_required("WATERMARK_PARTITIONS").split(",")),
            initial_position=_required("WATERMARK_INITIAL_POSITION"),
        )


#: Where Managed Flink writes the property groups from `infra/streaming/flink.tf`.
#:
#: **Not environment variables.** Terraform passes placement in a property group and the runtime
#: renders it to this file; nothing exports it to the process environment. Reading only
#: `os.environ` meant the job refused to start on a correctly configured application — and the
#: refusal was this file's own, saying the value had not been set when it had been set
#: perfectly well, one mechanism away.
#:
#: The environment is still read, and second: that is how a local run and `tests_flink` supply
#: placement without inventing a properties file.
APPLICATION_PROPERTIES = "/etc/flink/application_properties.json"

#: The group `infra/streaming/flink.tf` writes placement into.
PROPERTY_GROUP = "watermark"


def _from_properties() -> dict[str, str]:
    """Managed Flink's rendering of the property group, or nothing when running elsewhere."""
    try:
        with open(APPLICATION_PROPERTIES, encoding="utf-8") as handle:
            groups = json.load(handle)
    except (OSError, ValueError):
        return {}
    for group in groups:
        if group.get("PropertyGroupId") == PROPERTY_GROUP:
            return dict(group.get("PropertyMap", {}))
    return {}


def _required(name: str) -> str:
    value = _from_properties().get(name, "") or os.environ.get(name, "")
    if not value:
        raise RuntimeError(
            f"{name} is in neither the `{PROPERTY_GROUP}` property group nor the environment. "
            "Placement comes from Terraform and is never defaulted: a job with a guessed stream "
            "name starts, reads nothing, and reports healthy."
        )
    return value
