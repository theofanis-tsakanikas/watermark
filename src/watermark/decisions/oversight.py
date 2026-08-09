"""The oversight queue. Claim 7's runtime half.

The contract layer already makes a consequential automatic decision unrepresentable. This is
what exists instead: a queue a named human works, where **the actuation path does not exist
without a recorded decision**.

The shape is the argument. There is no `actuate()` that takes an optional reviewer; there is a
`Review` that must be constructed with a reviewer's name and a verdict, and an actuation that
takes a `Review` and nothing else. An unreviewed entry cannot be actuated because there is no
function signature that accepts one — which is the difference between a control and a check.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum

from watermark.core.time import Instant
from watermark.decisions.engine import Decision


class Verdict(Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class Review:
    """A named human's decision about one queue entry.

    `reviewer` is required and has no default. A default of `"system"` — the obvious
    convenience — is how an unreviewed entry becomes an actuated one with a plausible audit
    trail, and it is the single change that would make claim 7 false while every test passed.
    """

    entry_id: str
    reviewer: str
    verdict: Verdict
    at: Instant
    #: Why. Required on a rejection: a rejection is a training signal, and a signal with no
    #: reason teaches the next model that the inspector was arbitrary.
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.reviewer.strip():
            raise ValueError(
                "a review needs a named reviewer. Art. 14 oversight by an unnamed principal is "
                "not oversight, and claim 7 rests on the name being in the record."
            )
        if self.verdict is Verdict.REJECTED and not self.reason.strip():
            raise ValueError(
                "a rejection needs a reason. It is a training signal, and one with no reason "
                "teaches the next model that the inspector was arbitrary."
            )


@dataclass(frozen=True, slots=True)
class Actuation:
    """An actuated consequential decision. Constructible only from a review.

    There is no other constructor and no default for `review`. That is claim 7: not a check
    that refuses, but a type that cannot be built.
    """

    entry: Decision
    review: Review

    def __post_init__(self) -> None:
        if self.review.verdict is not Verdict.ACCEPTED:
            raise ValueError(
                f"entry {self.review.entry_id} was {self.review.verdict.value}; a rejected "
                "review does not actuate. The verdict is not advisory."
            )


@dataclass
class OversightQueue:
    """Entries awaiting a human, and the reviews that resolved them."""

    entries: dict[str, Decision] = field(default_factory=dict)
    reviews: dict[str, Review] = field(default_factory=dict)

    def enqueue(self, entry_id: str, decision: Decision) -> None:
        self.entries[entry_id] = decision

    def record(self, review: Review) -> None:
        if review.entry_id not in self.entries:
            raise KeyError(
                f"no queue entry {review.entry_id}. A review of nothing is a signature on a "
                "decision that was never presented."
            )
        self.reviews[review.entry_id] = review

    def actuate(self, entry_id: str) -> Actuation:
        """Actuate an accepted entry. Raises for anything else.

        Reaching this without a review is a `KeyError`, not a permission denial, because there
        is nothing to permit: the review *is* the input.
        """
        review = self.reviews.get(entry_id)
        if review is None:
            raise KeyError(
                f"entry {entry_id} has no recorded human decision. There is no path from a "
                "queue entry to an actuation that does not pass through one — see claim 7."
            )
        return Actuation(self.entries[entry_id], review)

    @property
    def pending(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.entries) - set(self.reviews)))

    def training_signal(self) -> Iterable[tuple[Decision, Review]]:
        """Rejections, as the feedback the next model trains on.

        `docs/SCENARIO.md` names this loop as the source of the proxy-discrimination risk: a
        model trained on confirmed cases learns where inspectors went. Exposing rejections
        deliberately is what lets the Phase 3 bias analysis measure the loop rather than
        inherit it.
        """
        for entry_id, review in sorted(self.reviews.items()):
            if review.verdict is Verdict.REJECTED:
                yield self.entries[entry_id], review
