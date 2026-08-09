"""**Claim 7** — a consequential decision about a person cannot be actuated automatically.

Not "is not", and not "is configured not to". *Cannot*. Seven cases, and they attack the claim
from every direction the system offers: through the contract layer, through the engine, through
the queue, and through the type that represents an actuation.

The distinction the harness is built around: a control that *refuses* can be turned off, and a
control that *does not exist* cannot. Wherever both were available, this system chose the
second — so most of these cases assert that something fails to construct rather than that
something returns an error.
"""

from __future__ import annotations

import contextlib

from pydantic import ValidationError

from evals.scoring import Case, first_problem, require
from watermark.contracts import load
from watermark.contracts.decisions import DecisionContract
from watermark.core.time import Instant
from watermark.core.watermarks import WatermarkState, observe
from watermark.decisions.engine import DecisionEngine, Origin
from watermark.decisions.oversight import Actuation, OversightQueue, Review, Verdict
from watermark.features.online import ServedValue

CONTRACTS = load()
ANOMALY = CONTRACTS.decisions["meter_anomaly"]


def at(minute: int) -> Instant:
    return Instant.from_iso(f"2026-03-14T09:{minute:02d}:00Z")


def _view():
    _, view = observe(WatermarkState.declare(["M"]), [("M", at(14))], at(14))
    return view


def _queued_decision():
    served = ServedValue("M00001", "meter_consumption_1h", 4200, at(10), at(10))
    return DecisionEngine(ANOMALY, CONTRACTS.features).decide(
        "M00001",
        at(14),
        {"meter_consumption_1h": served},
        _view(),
        model_action="queue_for_inspection",
        model_version="meter-anomaly-3",
    )


def the_contract_cannot_declare_it() -> str:
    """The first door, and the one that closes before any code runs.

    A contract with a significant effect on a person and automatic actuation does not load, so
    there is no program in which the combination exists. GDPR Art. 22(1) is not engaged, rather
    than engaged and satisfied by a safeguard.
    """
    definition = ANOMALY.model_dump()
    definition["actuation"] = "automatic"

    try:
        DecisionContract.model_validate(definition)
    except (ValidationError, ValueError) as exc:
        return require(
            "Art. 22" in str(exc) or "significant" in str(exc),
            f"the contract was refused, but not for the stated reason: {exc}",
        )
    return (
        "a contract with effect=significant_on_person and actuation=automatic loaded. Claim 7 "
        "is that the automated path is structurally incapable of this, and the structure is "
        "this validator."
    )


def no_shipped_contract_declares_it() -> str:
    """The same check against the real set, not a synthetic one.

    A validator nobody's contracts exercise is a validator that could have been deleted.
    """
    offenders = [
        decision.id
        for decision in CONTRACTS.decisions.values()
        if decision.effect == "significant_on_person" and decision.actuation == "automatic"
    ]
    return require(not offenders, f"contracts that actuate on a person automatically: {offenders}")


def the_engine_produces_a_queue_entry_not_an_action() -> str:
    """Even with a model, fresh features and a healthy watermark, nothing is actuated."""
    decision = _queued_decision()
    return first_problem(
        require(
            decision.action in ANOMALY.permitted_actions,
            f"the engine produced {decision.action!r}, which the contract does not permit",
        ),
        require(
            decision.origin is Origin.MODEL,
            "the fixture did not reach the model, so this case tests nothing",
        ),
        require(
            decision.action == "queue_for_inspection",
            f"the engine produced {decision.action!r} rather than a queue entry",
        ),
    )


def an_unreviewed_entry_cannot_be_actuated() -> str:
    """There is no path from a queue entry to an actuation that does not pass through a review.

    The failure is a `KeyError`, not a permission denial, and the difference is the claim: there
    is nothing to permit, because the review *is* the input.
    """
    queue = OversightQueue()
    queue.enqueue("entry-1", _queued_decision())

    try:
        queue.actuate("entry-1")
    except KeyError:
        return ""
    return "an entry with no recorded human decision was actuated"


def a_rejected_review_does_not_actuate() -> str:
    """The verdict is not advisory."""
    queue = OversightQueue()
    queue.enqueue("entry-1", _queued_decision())
    queue.record(
        Review("entry-1", "inspector.aliki", Verdict.REJECTED, at(15), reason="meter replaced")
    )

    try:
        queue.actuate("entry-1")
    except ValueError:
        return ""
    return "a rejected entry actuated. A verdict that does not bind is a verdict for show."


def an_actuation_cannot_be_built_without_a_review() -> str:
    """The type itself. This is where 'cannot' stops being a policy.

    `Actuation` has two required fields and no default for either. There is no signature that
    accepts an entry alone, so the unreviewed actuation is not refused — it is unwritable.
    """
    with contextlib.suppress(TypeError):
        Actuation(_queued_decision())  # type: ignore[call-arg]
        return (
            "an Actuation was constructed from an entry alone. The review must be a required "
            "field with no default; a default reviewer of 'system' is how an unreviewed entry "
            "becomes an actuated one with a plausible audit trail."
        )
    return ""


def a_review_needs_a_named_human() -> str:
    """AI Act Art. 14. Oversight by an unnamed principal is not oversight.

    And a rejection needs a reason, because a rejection is a training signal — `docs/SCENARIO.md`
    names the feedback loop as the source of the proxy-discrimination risk, and a signal with no
    reason teaches the next model that the inspector was arbitrary.
    """
    problems = []
    try:
        Review("entry-1", "  ", Verdict.ACCEPTED, at(15))
        problems.append("a review with a blank reviewer was accepted")
    except ValueError:
        pass
    try:
        Review("entry-1", "inspector.aliki", Verdict.REJECTED, at(15))
        problems.append("a rejection with no reason was accepted")
    except ValueError:
        pass
    return require(not problems, "; ".join(problems))


def an_accepted_review_does_actuate() -> str:
    """The calibration case. A queue nothing can ever leave is not oversight either.

    Claim 7 is that the *automatic* path cannot actuate. If the human-gated one could not
    either, every case above would pass on a system that simply does nothing, and the anomaly
    path would be theatre.
    """
    queue = OversightQueue()
    queue.enqueue("entry-1", _queued_decision())
    queue.record(Review("entry-1", "inspector.aliki", Verdict.ACCEPTED, at(15)))
    actuation = queue.actuate("entry-1")
    return first_problem(
        require(actuation.review.reviewer == "inspector.aliki", "the reviewer was not recorded"),
        require(queue.pending == (), "the entry is still pending after being actuated"),
    )


CASES: tuple[Case, ...] = (
    Case(
        "the_contract_cannot_declare_it",
        "The door that closes before any code runs. There is no program in which the "
        "combination exists, so Art. 22(1) is not engaged rather than satisfied.",
        the_contract_cannot_declare_it,
    ),
    Case(
        "no_shipped_contract_declares_it",
        "A validator that none of the real contracts exercise is one that could be deleted "
        "without anything failing.",
        no_shipped_contract_declares_it,
    ),
    Case(
        "the_engine_produces_a_queue_entry_not_an_action",
        "With a model, fresh features and a healthy watermark, the path still produces a "
        "position in a queue.",
        the_engine_produces_a_queue_entry_not_an_action,
    ),
    Case(
        "an_unreviewed_entry_cannot_be_actuated",
        "The failure is a KeyError, not a permission denial. There is nothing to permit: the "
        "review is the input.",
        an_unreviewed_entry_cannot_be_actuated,
    ),
    Case(
        "a_rejected_review_does_not_actuate",
        "A verdict that does not bind is a verdict for show.",
        a_rejected_review_does_not_actuate,
    ),
    Case(
        "an_actuation_cannot_be_built_without_a_review",
        "Where 'cannot' stops being a policy. A default reviewer of 'system' is how an "
        "unreviewed entry becomes an actuated one with a plausible audit trail.",
        an_actuation_cannot_be_built_without_a_review,
    ),
    Case(
        "a_review_needs_a_named_human",
        "Art. 14: oversight by an unnamed principal is not oversight. And a rejection with no "
        "reason teaches the next model that the inspector was arbitrary.",
        a_review_needs_a_named_human,
    ),
    Case(
        "an_accepted_review_does_actuate",
        "The calibration case. A queue nothing can ever leave would satisfy every case above "
        "and would be theatre.",
        an_accepted_review_does_actuate,
    ),
)
