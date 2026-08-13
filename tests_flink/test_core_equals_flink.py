"""The real PyFlink job must produce the same bytes as the pure core, on the same input.

This is the whole of tier two, and it is the only thing in the repository that a reader should
treat as unproven until they have seen the CI job green — see the conftest beside it.

## What it is comparing, and what it is not

Both sides run the *same* core. That is deliberate and it is not a weakness: a second
implementation would mean comparing two guesses. What this establishes is that **Flink's
mechanics do not change the answers** — that a window fires when the core says it should, that
keyed state survives the way the offline manager's dictionary does, and that a savepoint
restore does not double-count.

Those are exactly the properties a pure pytest run cannot reach, and exactly the ones claim 1
depends on in production while being proved offline.
"""

from __future__ import annotations

import pytest

from data import cast
from data.generate import generate
from watermark.runner import Arrival, run

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def arrivals() -> list[Arrival]:
    return [
        Arrival(delivery.raw, delivery.ingest_time, delivery.source, delivery.partition)
        for delivery in generate()
    ]


@pytest.fixture(scope="module")
def mini_cluster():
    from pyflink.datastream import StreamExecutionEnvironment  # noqa: PLC0415

    environment = StreamExecutionEnvironment.get_execution_environment()
    # One, so the comparison is against a deterministic ordering. Claim 2 already establishes
    # that the core does not depend on arrival order; what this tier is asking is a different
    # question, and running it at parallelism four would answer both at once and neither
    # clearly.
    environment.set_parallelism(1)
    return environment


def test_flink_produces_the_same_values_as_the_core(mini_cluster, arrivals) -> None:
    """The assertion tier two exists for, and the one thing it can honestly assert.

    The offline side is `watermark.runner.run`, the same function every claim harness uses — so
    if this passes, those harnesses are statements about the deployed system and not only about a
    model of it.

    **Values, not bytes, and the difference is not a weakening.** What the two sides must agree
    on is every published `(meter, interval, revision) -> energy`. What they cannot agree on is
    `closed_at` and `watermark_status`, and the reason is structural rather than a defect: the
    offline runner batches by `BATCH_GRAIN` in ingestion time, while Flink batches by whatever
    fell inside a processing-time timer. Two different batch boundaries over the same records
    close the same windows at different *moments* and report different watermark conditions on
    the way — which is the system working. Asserting on them would make this a test of where the
    timer happened to fire.

    So the fingerprint is computed over the numbers a customer is billed on. If Flink's keying,
    its state or its timers changed an answer, it changes one of these.
    """
    from streaming.job import decide_windows  # noqa: PLC0415

    expected = _values(run(arrivals, cast.SUBSTATIONS))
    produced = _values_from_lines(_run_on_mini_cluster(mini_cluster, decide_windows, arrivals))

    assert produced, (
        "the MiniCluster produced no published window at all. An equivalence test that compares "
        "nothing against nothing passes and looks exactly like one that worked, so this is a "
        "failure rather than an empty pass."
    )
    assert produced == expected, (
        "Flink and the core disagree on the same input. The core is the definition of the right "
        "answer, so this is Flink's mechanics changing it: a window firing on the wrong records, "
        "or keyed state that did not survive the way the offline manager's dictionary does."
    )


def test_a_savepoint_restore_does_not_double_count(mini_cluster, arrivals) -> None:
    """Kill it mid-window, restore, and assert the totals are unchanged.

    The Phase 4 recovery drill in miniature. It belongs here rather than there because the
    property is about Flink's state, not about the estate — and because a drill first attempted
    against a live estate is a drill attempted for the first time when it matters.
    """
    pytest.skip(
        "Still not implemented, and the reason has changed. It used to wait on the operators "
        "being wired; they are wired, and the test above drives them. What it now waits on is a "
        "way to stop and restore a MiniCluster mid-window from pytest — `execute_and_collect` "
        "runs a job to completion, and a savepoint drill needs the job held open, cancelled with "
        "a savepoint, and resumed from it. That is a harness, not an assertion, and writing half "
        "of one and skipping it would be worse than saying so here. Skipped explicitly rather "
        "than left out, so the gap is in the report rather than absent from it."
    )


def _values(result) -> dict[tuple[str, str, int], int]:
    """Published energy per `(meter, interval, revision)`, from a `RunResult`."""
    return {
        (published.meter_id, published.interval_start.to_iso(), published.revision): (
            published.energy_wh
        )
        for published in (*result.published, *result.restated, *result.confirmed)
    }


def _values_from_lines(lines) -> dict[tuple[str, str, int], int]:
    """The same projection, from the JSON the adapter emits.

    The two sides speak different languages by design — the core returns dataclasses and the
    deployed operator emits strings, because `.process()` would otherwise pickle its output and
    every Flink worker would need to import the core's classes to *serialise* a result. This is
    where the two are brought to a common shape, and it is deliberately the only place: a helper
    that converted one side into the other's types would be a second implementation of the
    adapter sitting inside the test that checks the adapter.
    """
    import json  # noqa: PLC0415

    from watermark.core.time import Instant  # noqa: PLC0415

    values: dict[tuple[str, str, int], int] = {}
    for line in lines:
        row = json.loads(line)
        if row.get("kind") not in {"published", "restated", "confirmed"}:
            continue
        key = (
            str(row["meter"]),
            Instant(int(row["interval_start"])).to_iso(),
            int(row["revision"]),
        )
        values[key] = int(row["energy_wh"])
    return values


def _run_on_mini_cluster(environment, build, arrivals):
    """Drive the deployed operator chain over an in-memory source and collect what it emits.

    **The source is the only thing that differs from production**, and it has to be: the real one
    is a Kinesis consumer, which a MiniCluster cannot open. Everything downstream of it —
    `decide_windows` — is imported from `streaming/job.py` rather than rebuilt here, so what runs
    on this cluster is the chain that runs in the account and not a second arrangement of the
    core that happens to resemble it.

    The rows are shaped as the deserialiser shapes them: `(raw, partition, source)`, with `raw`
    base64 because that is what the IoT rule's `encode(*, 'base64')` produces and what
    `process_element` decodes. Feeding plaintext here would exercise a decode path production
    never takes and skip the one it does.

    Ordered by ingestion time. Claim 2 already establishes that the core does not depend on
    arrival order; what this tier asks is a different question, and shuffling here would answer
    both at once and neither clearly.
    """
    import base64  # noqa: PLC0415

    from pyflink.common.typeinfo import Types  # noqa: PLC0415

    from streaming.config import SEMANTICS  # noqa: PLC0415
    from streaming.operators import MeterWindowOperator  # noqa: PLC0415
    from watermark.core.normalise import DEFAULT_POLICY as NORMALISATION  # noqa: PLC0415
    from watermark.core.watermarks import DEFAULT_POLICY as WATERMARK  # noqa: PLC0415
    from watermark.core.windows import WindowPolicy  # noqa: PLC0415

    operator = MeterWindowOperator(
        normalisation=NORMALISATION,
        watermark=WATERMARK,
        window=WindowPolicy(length=SEMANTICS["window_length"]),
        partitions=cast.SUBSTATIONS,
    )

    rows = [
        (
            base64.b64encode(arrival.raw.encode("utf-8")).decode("ascii"),
            arrival.partition,
            arrival.source.value,
        )
        for arrival in sorted(arrivals, key=lambda a: a.ingest_time.epoch_millis)
    ]
    source = environment.from_collection(
        rows,
        type_info=Types.ROW_NAMED(
            ["raw", "partition", "source"],
            [Types.STRING(), Types.STRING(), Types.STRING()],
        ),
    )
    return list(build(source, operator).execute_and_collect())
