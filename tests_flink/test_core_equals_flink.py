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
from evals.replay import value_fingerprint
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


def test_flink_produces_the_same_bytes_as_the_core(mini_cluster, arrivals) -> None:
    """The assertion tier two exists for.

    The offline side is `watermark.runner.run`, which is the same function every claim harness
    uses — so if this passes, the harnesses are statements about the deployed system and not
    only about a model of it.
    """
    from streaming.job import build_pipeline  # noqa: PLC0415

    expected = value_fingerprint(run(arrivals, cast.SUBSTATIONS))

    collected = _run_on_mini_cluster(mini_cluster, build_pipeline, arrivals)
    assert value_fingerprint(collected) == expected, (
        "Flink and the core disagree on the same input. The core is the definition of the "
        "right answer, so this is Flink's mechanics changing it: a window firing at a "
        "different moment, or state that did not survive the way the offline manager's "
        "dictionary does."
    )


def test_a_savepoint_restore_does_not_double_count(mini_cluster, arrivals) -> None:
    """Kill it mid-window, restore, and assert the totals are unchanged.

    The Phase 4 recovery drill in miniature. It belongs here rather than there because the
    property is about Flink's state, not about the estate — and because a drill first attempted
    against a live estate is a drill attempted for the first time when it matters.
    """
    pytest.skip(
        "Written and not yet implemented: the restore needs the operators wired, which lands "
        "with the first green run of the test above. Skipped explicitly rather than left out, "
        "so the gap is visible in the report rather than absent from it."
    )


def _run_on_mini_cluster(environment, build, arrivals):
    """Drive the job over an in-memory source and collect what it emits.

    Not implemented until the operators are. Raising rather than returning an empty result is
    the point: an equivalence test that quietly compares nothing against nothing is the worst
    possible outcome here, because it is indistinguishable from success.
    """
    raise NotImplementedError(
        "The MiniCluster harness lands with the operator bodies in streaming/operators.py. "
        "It raises rather than returning an empty collection: an equivalence test that "
        "compares nothing against nothing passes, and looks exactly like one that worked."
    )
