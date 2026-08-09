"""Comparing the two mechanisms. Bitemporally, and with no tolerance.

The comparison rule, stated once so that neither side can be adjusted to fit it:

> the value **served** at instant `T_serve`, for event time `T_event`, equals the value the
> offline query computes for `T_event` **using only rows ingested at or before `T_serve`**.

Two axes. Event time decides what the feature is about; ingestion time decides what was
knowable when it was served. A harness that binds only event time reports a divergence on every
late arrival — correctly, and about the wrong thing.

**No tolerance.** Doctrine 7: the parity door has no key. A floating-point tolerance is a key;
it starts at 1e-9 to make one test pass and it is 0.01 by the time anybody looks again. The
contracts refuse a `Fractional` feature at load time so that this comparison can be integer
equality, which is a comparison nobody can widen.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from watermark.contracts.features import FeatureContract
from watermark.core.time import Duration, Instant
from watermark.features.offline import OfflineResolver
from watermark.features.online import OnlineMaterialiser


@dataclass(frozen=True, slots=True)
class Divergence:
    """One entity at one instant where the two mechanisms disagreed."""

    feature_id: str
    entity_id: str
    at_event: Instant
    at_serve: Instant
    online: int | None
    offline: int | None

    @property
    def kind(self) -> str:
        """What sort of disagreement this is. The three fail for different reasons.

        A value difference is an aggregation that drifted. A *missing* value on one side only
        is worse: it means one mechanism thinks the entity has no feature at all, and a
        decision path reading that side falls back while the other serves a number.
        """
        if self.online is None and self.offline is not None:
            return "online_missing"
        if self.offline is None and self.online is not None:
            return "offline_missing"
        return "value"

    def __str__(self) -> str:
        return (
            f"{self.feature_id}/{self.entity_id} at {self.at_event}: "
            f"online={self.online} offline={self.offline} [{self.kind}]"
        )


@dataclass(frozen=True, slots=True)
class ParityReport:
    """What a comparison found, in a shape a scoreboard row can be read off."""

    feature_id: str
    compared: int
    divergences: tuple[Divergence, ...]

    @property
    def agreed(self) -> int:
        return self.compared - len(self.divergences)

    @property
    def ok(self) -> bool:
        return not self.divergences


def compare_populations(
    contract: FeatureContract,
    online: OnlineMaterialiser,
    offline: OfflineResolver,
    entities: Sequence[str],
    instants: Iterable[Instant],
    serve_lag: Duration,
) -> ParityReport:
    """Compare every entity at every instant, bitemporally.

    `serve_lag` is how far behind the event time the serving instant sits — the pipeline's own
    latency. It is a parameter rather than zero because with zero the two axes coincide and the
    comparison silently becomes the naive one this function exists not to be.
    """
    divergences: list[Divergence] = []
    compared = 0

    for at_event in instants:
        at_serve = at_event.plus(serve_lag)
        for entity_id in entities:
            compared += 1
            served = online.serve(entity_id)
            online_value = served.value if served is not None else None
            offline_value = offline.resolve(entity_id, at_event, at_serve)
            if online_value != offline_value:
                divergences.append(
                    Divergence(
                        feature_id=contract.id,
                        entity_id=entity_id,
                        at_event=at_event,
                        at_serve=at_serve,
                        online=online_value,
                        offline=offline_value,
                    )
                )

    return ParityReport(contract.id, compared, tuple(divergences))
