"""The Managed Service for Apache Flink entry point.

Twelve lines of routing over `watermark.core`, in the same sense Attestor's container is twelve
lines of routing over its resolver: if Managed Flink stops fitting, what moves is this file.
Nothing under `src/watermark/` imports PyFlink, and `scripts/check_core_is_pure.py` makes that
a build failure rather than a habit.

The job is not importable without PyFlink installed, which is why it is here and not in the
package: `make test`, every claim gate and `make preflight --fast` must keep running on a
machine with no JVM (ADR-0003).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from streaming.config import SEMANTICS, Placement
from streaming.operators import (
    MeterWindowOperator,
    build_process_function,
    build_watermark_generator,
)
from watermark.core.normalise import DEFAULT_POLICY as NORMALISATION_POLICY
from watermark.core.watermarks import DEFAULT_POLICY as WATERMARK_POLICY
from watermark.core.windows import WindowPolicy
from watermark.runner import Arrival, run

if TYPE_CHECKING:  # pragma: no cover — import-time only where PyFlink exists
    from pyflink.datastream import StreamExecutionEnvironment

LOG = logging.getLogger("watermark.streaming")


def build_pipeline(environment: StreamExecutionEnvironment, placement: Placement) -> None:
    """Wire the sources, the operators and the sinks. No semantics are decided here.

    Every duration handed to a PyFlink call comes out of `SEMANTICS`, which is resolved from
    `watermark.core`. `scripts/check_adapter_is_thin.py` refuses a numeric literal in this
    package for that reason: `.allowed_lateness(Time.minutes(5))` is one line, it is the sort
    of line somebody adds on a Tuesday because a reading was being dropped, and after it the
    core and the deployed job disagree about what late means with only the job being right.
    """
    from pyflink.common.typeinfo import Types  # noqa: PLC0415 — PyFlink is optional
    from pyflink.datastream.connectors.kinesis import (  # noqa: PLC0415
        FlinkKinesisConsumer,
    )
    from pyflink.datastream.formats.json import JsonRowDeserializationSchema  # noqa: PLC0415

    environment.enable_checkpointing(placement.checkpoint_interval_millis)
    # Set explicitly, in the first version of the application. It is the one setting that
    # cannot be corrected later: changing it means the job can no longer restart from an
    # existing snapshot. See docs/AWS-CONSTRAINTS.md.
    environment.set_max_parallelism(placement.max_parallelism)

    operator = MeterWindowOperator(
        normalisation=NORMALISATION_POLICY,
        watermark=WATERMARK_POLICY,
        window=WindowPolicy(length=SEMANTICS["window_length"]),
        partitions=placement.partitions,
    )

    consumer = FlinkKinesisConsumer(
        placement.meter_stream,
        JsonRowDeserializationSchema.builder()
        .type_info(Types.ROW([Types.STRING(), Types.STRING(), Types.STRING()]))
        .build(),
        {"aws.region": placement.region, "flink.stream.initpos": placement.initial_position},
    )

    (
        environment.add_source(consumer)
        # `for_generator`, never `for_bounded_out_of_orderness`. The convenience constructor
        # holds the bound inside Flink, where no offline test can read it; the generator asks
        # the operator, whose watermark *is* the core's.
        .assign_timestamps_and_watermarks(build_watermark_generator(operator))
        .key_by(lambda record: record[1])
        .process(build_process_function(operator))
        .name("watermark-meter-windows")
    )


def replay(arrivals: list[Arrival], partitions: tuple[str, ...]) -> object:
    """The offline path, exposed so the equivalence tier has something to compare against.

    Both sides of ADR-0003's tier two run the *same* core; what tier two establishes is that
    Flink's mechanics — when a timer fires, what state survives a rescale — do not change the
    answer. A comparison against a second implementation would be comparing two guesses.
    """
    return run(arrivals, partitions)


def main() -> None:  # pragma: no cover — the deployed entry point, never run offline
    from pyflink.datastream import StreamExecutionEnvironment  # noqa: PLC0415

    logging.basicConfig(level=logging.INFO)
    placement = Placement.from_environment()
    environment = StreamExecutionEnvironment.get_execution_environment()
    build_pipeline(environment, placement)
    environment.execute("watermark")


if __name__ == "__main__":  # pragma: no cover
    main()
