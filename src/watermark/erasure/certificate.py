"""The certificate, and the refusal to issue one."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from watermark.core.time import Duration, Instant
from watermark.erasure.scope import ErasureScope


class LegOutcome(Enum):
    """What happened to one leg of an erasure."""

    #: Done and verified. The subject is unreachable through this store.
    CONFIRMED = "confirmed"
    #: Attempted and failed. No certificate is issued.
    FAILED = "failed"
    #: Not attempted. Distinct from failed on purpose: an unattempted leg is an orchestration
    #: bug and a failed one is a store that would not cooperate, and they are fixed by
    #: different people.
    NOT_ATTEMPTED = "not_attempted"
    #: Done as far as deletion can reach, with a stated residual. Available to exactly one leg.
    BOUNDED = "bounded"


@dataclass(frozen=True, slots=True)
class Leg:
    """One store, and what became of the subject in it."""

    name: str
    outcome: LegOutcome
    detail: str
    #: Only for `BOUNDED`: how long until the residual is gone. Printed on the certificate.
    residual: Duration | None = None

    def __post_init__(self) -> None:
        if self.outcome is LegOutcome.BOUNDED and self.residual is None:
            raise ValueError(
                f"leg '{self.name}' is bounded and states no residual window. A boundary "
                "without a duration is a boundary nobody can hold anybody to — it is the "
                "overclaim with an extra word in front of it."
            )
        if self.outcome is not LegOutcome.BOUNDED and self.residual is not None:
            raise ValueError(
                f"leg '{self.name}' states a residual window and is not bounded. Only the "
                "model-artefact leg has a residual; a residual on a completed leg suggests the "
                "deletion was partial when it was not."
            )


class ErasureIncomplete(Exception):
    """Raised instead of issuing a certificate. Carries which legs and why."""

    def __init__(self, outstanding: Sequence[Leg]) -> None:
        listed = ", ".join(f"{leg.name} ({leg.outcome.value})" for leg in outstanding)
        super().__init__(
            f"no certificate: {listed}. A partial erasure reported as complete is worse than "
            "none — the subject is told they are gone and the residual becomes invisible."
        )
        self.outstanding = tuple(outstanding)


#: The one leg that may be BOUNDED. Naming it here rather than allowing any leg to be bounded
#: is what stops "we could not finish that one either" from becoming a second boundary.
BOUNDABLE = "model_artefacts"


@dataclass(frozen=True, slots=True)
class Certificate:
    """Issued only when every leg confirms. Says what it does not cover, in words."""

    subject_id: str
    requested_at: Instant
    completed_at: Instant
    legs: tuple[Leg, ...]
    scope: ErasureScope

    @property
    def residual(self) -> Duration | None:
        for leg in self.legs:
            if leg.outcome is LegOutcome.BOUNDED:
                return leg.residual
        return None

    def statement(self) -> str:
        """What the subject is actually told. The wording is the deliverable.

        It says "erased to a declared boundary" rather than "erased", and it names the boundary
        in the same breath. Saying the second would be exactly the overclaim this repository
        exists to argue against — and it would be an overclaim made to somebody exercising a
        right, which is the worst audience for one.
        """
        lines = [
            f"Subject {self.subject_id} was erased to a declared boundary on "
            f"{self.completed_at.to_iso()}, following a request at {self.requested_at.to_iso()}.",
            "",
            "Confirmed unreachable in:",
        ]
        lines += [
            f"  - {leg.name}: {leg.detail}"
            for leg in self.legs
            if leg.outcome is LegOutcome.CONFIRMED
        ]

        bounded = [leg for leg in self.legs if leg.outcome is LegOutcome.BOUNDED]
        if bounded:
            leg = bounded[0]
            assert leg.residual is not None
            lines += [
                "",
                "Not reached by deletion:",
                f"  - {leg.name}: {leg.detail}",
                "",
                f"    A model trained before this request retains the subject's contribution "
                f"in its weights. Crypto-shredding does not reach it and destroying a key does "
                f"not remove it. The affected models are quarantined and retrained from the "
                f"shredded corpus within {leg.residual}. **Machine unlearning is not claimed "
                f"and has not been attempted.**",
            ]
        return "\n".join(lines)


def certify(
    subject_id: str,
    requested_at: Instant,
    completed_at: Instant,
    legs: Sequence[Leg],
    scope: ErasureScope,
) -> Certificate:
    """Issue a certificate, or refuse.

    Three ways to refuse, and the third is the one an orchestration bug produces:

    1. a leg failed;
    2. a leg was never attempted;
    3. a leg in the scope has no entry at all — the silent one, because a run that simply never
       reached a store looks identical to one that had nothing to do there.
    """
    by_name = {leg.name: leg for leg in legs}

    missing = [
        Leg(name, LegOutcome.NOT_ATTEMPTED, "no entry in this run")
        for name in scope.legs
        if name not in by_name
    ]
    unfinished = [
        leg for leg in legs if leg.outcome in (LegOutcome.FAILED, LegOutcome.NOT_ATTEMPTED)
    ]
    misbounded = [
        leg for leg in legs if leg.outcome is LegOutcome.BOUNDED and leg.name != BOUNDABLE
    ]

    if missing or unfinished:
        raise ErasureIncomplete([*missing, *unfinished])
    if misbounded:
        raise ErasureIncomplete(misbounded)

    return Certificate(subject_id, requested_at, completed_at, tuple(legs), scope)
