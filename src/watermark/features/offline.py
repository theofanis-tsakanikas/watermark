"""The offline mechanism: one as-of query over the raw lakehouse, recomputed from scratch.

**It does not read the Feature Store's offline store**, and that is the second tautology
ADR-0004 names. A `PutRecord` populates the online store and the offline store from the same
call, so comparing them compares a value with AWS's copy of it — it exercises the Feature
Store's plumbing, which is not broken and is not what claim 3 is about.

**The query is bitemporal.** Event time decides what the feature is *about*; ingestion time
decides what was *knowable* when it was served. A resolver that binds only event time reports a
divergence on every late arrival — correctly, and about the wrong thing, because the online
value was computed from what had arrived and the offline one from what has arrived since.

Collapsing the two axes is the same error as taking a decision on a window that has not closed,
one layer up.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from watermark.contracts.features import FeatureContract
from watermark.core.time import Instant

#: How each aggregation is written in SQL. A mapping rather than an expression language: a
#: second small language would be implemented differently by the two compilers, which is the
#: failure claim 3 exists to catch, occurring inside the mechanism built to catch it.
_SQL_AGGREGATIONS = {
    "sum": "SUM({column})",
    "mean": "CAST(ROUND(AVG(CAST({column} AS DOUBLE))) AS BIGINT)",
    "max": "MAX({column})",
    "min": "MIN({column})",
    "count": "COUNT(*)",
    "last": "MAX_BY({column}, {event_time})",
}


def as_of_sql(contract: FeatureContract) -> str:
    """Compile the contract into a bitemporal as-of query, with parameters bound.

    Three placeholders, in order: the entity key, the event-time upper bound, and the ingestion
    upper bound. Bound, never interpolated — a feature query built by string concatenation is
    one somebody eventually builds from a customer-supplied meter id.

    `mean` rounds to an integer inside the query rather than returning a double. The contract
    declares `Integral` because there is no decimal type in the Feature Store (ADR-0004), so
    the offline side has to reach the same integer the online side does; returning a double
    here and rounding in Python would put the rounding in a different place on each side, and
    the last bit would differ for exactly the values nobody tests.
    """
    # S608 is right to look here and wrong about this one. Every interpolated value comes from
    # a contract that has already been validated — the aggregation is a key into a closed
    # mapping, the identifiers match `^[a-z][a-z0-9_]*$`, and the window is an integer. What is
    # never interpolated is the *data*: the entity id and both time bounds are `?` placeholders,
    # bound by the caller. A feature query built by concatenating those is one somebody
    # eventually builds from a customer-supplied meter id.
    aggregation = _SQL_AGGREGATIONS[contract.aggregation].format(
        column=contract.source_column, event_time=contract.event_time_column
    )
    # **Every bound instant is cast.** Athena binds `ExecutionParameters` as `varchar`, so a
    # placeholder used in arithmetic answers
    #
    #     TYPE_MISMATCH: Cannot apply operator: varchar(19) - interval day to second
    #
    # and one used in a comparison against a `timestamp` column is a comparison between
    # different types. The SQL had never been executed by Athena — the offline resolver beside
    # it is Python over rows — so it compiled, it read correctly, and it could not run.
    #
    # The cast is in the compiled text rather than in the caller, because the caller binding a
    # timestamp differently from the caller next to it is how two executors of one contract
    # start disagreeing.
    query = f"""
        SELECT {aggregation} AS value
        FROM {contract.source_table}
        WHERE {contract.entity_key} = ?
          AND {contract.event_time_column} >  CAST(? AS TIMESTAMP)
                                              - INTERVAL '{contract.window.length_seconds}' SECOND
          AND {contract.event_time_column} <= CAST(? AS TIMESTAMP)
          -- The second time axis. Without it a late arrival changes what this returns for an
          -- instant that has already been served, and the parity harness reports a divergence
          -- about a reading nobody had when the decision was taken.
          AND ingest_time <= CAST(? AS TIMESTAMP)
    """
    return query.strip()


@dataclass(frozen=True, slots=True)
class Row:
    """One source row, as the offline store holds it. Both time axes, always."""

    entity_id: str
    event_time: Instant
    ingest_time: Instant
    value: int


@dataclass(frozen=True, slots=True)
class OfflineResolver:
    """Executes the contract's definition over a set of rows.

    In the deployed system the executor is Athena. Here it is Python over the same rows, which
    is what makes claim 3 checkable with no account — and it is a genuinely different mechanism
    from the incremental aggregator in `online.py`: set-oriented, stateless, recomputed whole.
    """

    contract: FeatureContract
    rows: Sequence[Row]

    def resolve(self, entity_id: str, as_of_event: Instant, as_of_ingest: Instant) -> int | None:
        """The feature's value for one entity, as of an event time and an ingestion time.

        `None` when no row qualifies — a real answer, not a failure. A substation with no
        telemetry in the window has no load figure, and inventing a zero would tell the
        curtailment path the substation is idle when what is true is that nobody knows.
        """
        window_start = as_of_event.minus(self.contract.window.length)
        qualifying = [
            row.value
            for row in self.rows
            if row.entity_id == entity_id
            and window_start.epoch_millis < row.event_time.epoch_millis <= as_of_event.epoch_millis
            and row.ingest_time.epoch_millis <= as_of_ingest.epoch_millis
        ]
        return _aggregate(self.contract.aggregation, qualifying)


def _aggregate(kind: str, values: Iterable[int]) -> int | None:  # noqa: PLR0911
    """One branch per aggregation, each returning.

    Folding them together would hide which aggregations exist, and that list is the first thing
    a reader needs — it is the closed vocabulary the two compilers agree on.
    """
    listed = list(values)
    if not listed:
        return None
    if kind == "sum":
        return sum(listed)
    if kind == "mean":
        # Round half away from zero, the same rule the SQL uses. Python's `round` is
        # banker's rounding and would disagree with Athena's ROUND on every .5 — one value in
        # a thousand, which is exactly often enough to look like a flaky test.
        total, count = sum(listed), len(listed)
        return (
            (total * 2 + count) // (count * 2)
            if total >= 0
            else -((-total * 2 + count) // (count * 2))
        )
    if kind == "max":
        return max(listed)
    if kind == "min":
        return min(listed)
    if kind == "count":
        return len(listed)
    if kind == "last":
        return listed[-1]
    raise ValueError(f"no offline implementation for aggregation {kind!r}")
