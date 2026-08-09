"""**Claim 6** — erasure is complete to a declared boundary, and proved.

Nine cases. The hardest claim in the project, and the one where the interesting work is not
deleting anything — it is refusing to say a thing that is not true.

Three of the cases are refusals of *incomplete* runs, one is a refusal of a run that tried to
claim a second boundary, and one asserts what the certificate says in words. That last one is
not decoration: the wording is read by somebody exercising Art. 17, and "erased" where "erased
to a declared boundary" is true would be an overclaim made to the worst possible audience.
"""

from __future__ import annotations

import contextlib

from evals.scoring import Case, first_problem, require
from watermark.contracts import load
from watermark.core.time import Duration, Instant
from watermark.erasure import certify, scope_from_contracts
from watermark.erasure.certificate import ErasureIncomplete, Leg, LegOutcome
from watermark.features.online import OnlineMaterialiser
from watermark.policy import load_policy

CONTRACTS = load()
POLICY = load_policy()
REQUESTED = Instant.from_iso("2026-03-14T09:00:00Z")
COMPLETED = Instant.from_iso("2026-03-14T09:20:00Z")
RESIDUAL = Duration.of_days(30)


def _scope():
    personal = tuple(
        resource
        for resource, tags in POLICY.resources.items()
        if tags.get("watermark:sensitivity") == "personal"
    )
    return scope_from_contracts(CONTRACTS, personal_resources=personal, models=("meter_anomaly",))


def _legs(**overrides) -> list[Leg]:
    """Every leg confirmed, unless a case says otherwise."""
    default = {
        "crypto_shred": Leg(
            "crypto_shred", LegOutcome.CONFIRMED, "subject key scheduled for deletion"
        ),
        "lakehouse_rows": Leg(
            "lakehouse_rows",
            LegOutcome.CONFIRMED,
            "rows deleted, files rewritten by compaction run 41",
        ),
        "offline_store": Leg(
            "offline_store", LegOutcome.CONFIRMED, "feature-store offline rows deleted"
        ),
        "online_store": Leg(
            "online_store", LegOutcome.CONFIRMED, "DeleteRecord confirmed for 1 feature group"
        ),
        "training_sets": Leg(
            "training_sets", LegOutcome.CONFIRMED, "snapshot re-derived without the subject"
        ),
        "model_artefacts": Leg(
            "model_artefacts",
            LegOutcome.BOUNDED,
            "1 model quarantined and scheduled for retraining",
            residual=RESIDUAL,
        ),
    }
    default.update(overrides)
    return list(default.values())


def the_scope_is_derived_not_maintained() -> str:
    """A hand-kept list of tables is right on the day it is written.

    This is right on the day it is read, which is the only day that matters: an erasure arrives
    against whatever the platform looks like then, and the table added last month is exactly
    the one nobody remembers.
    """
    scope = _scope()
    return first_problem(
        require(bool(scope.entities), "no personal entities in scope"),
        require(
            bool(scope.features),
            "no personal features in scope — the offline and online "
            "store records are the leg easiest to forget, because a feature does not look "
            "like a table",
        ),
        require(bool(scope.resources), "no personal Lake Formation resources in scope"),
        require(
            set(scope.entities) == set(CONTRACTS.personal_data_entities),
            "the scope and the contracts disagree about which entities hold personal data",
        ),
    )


def a_complete_run_certifies() -> str:
    """The calibration case. A system that never certifies satisfies every refusal below."""
    certificate = certify("C00007", REQUESTED, COMPLETED, _legs(), _scope())
    return require(certificate.subject_id == "C00007", "the certificate names the wrong subject")


def a_failed_leg_refuses() -> str:
    failed = Leg("online_store", LegOutcome.FAILED, "GetRecord still returns the subject")
    with contextlib.suppress(ErasureIncomplete):
        certify("C00007", REQUESTED, COMPLETED, _legs(online_store=failed), _scope())
        return "a certificate was issued with a failed leg"
    return ""


def an_unattempted_leg_refuses() -> str:
    """Distinct from failed, and fixed by different people.

    Unattempted is an orchestration bug; failed is a store that would not cooperate. Reporting
    both the same way sends somebody to debug DynamoDB when the state machine never called it.
    """
    skipped = Leg("training_sets", LegOutcome.NOT_ATTEMPTED, "step skipped")
    with contextlib.suppress(ErasureIncomplete):
        certify("C00007", REQUESTED, COMPLETED, _legs(training_sets=skipped), _scope())
        return "a certificate was issued with an unattempted leg"
    return ""


def a_leg_missing_entirely_refuses() -> str:
    """The silent one.

    A run that never reached a store produces no entry for it, and that looks identical to a
    run that had nothing to do there. This is the case a deliberately incomplete run exercises,
    and it is the one `PLAN.md` asks for by name.
    """
    partial = [leg for leg in _legs() if leg.name != "offline_store"]
    with contextlib.suppress(ErasureIncomplete):
        certify("C00007", REQUESTED, COMPLETED, partial, _scope())
        return "a certificate was issued with a leg missing from the run entirely"
    return ""


def only_the_model_leg_may_be_bounded() -> str:
    """One boundary, not a pattern.

    Allowing any leg to be bounded is how "we could not finish that one either" becomes a
    second declared boundary, and then a third. There is exactly one thing deletion cannot
    reach, and it is named.
    """
    bounded = Leg(
        "lakehouse_rows", LegOutcome.BOUNDED, "compaction is slow", residual=Duration.of_days(7)
    )
    with contextlib.suppress(ErasureIncomplete):
        certify("C00007", REQUESTED, COMPLETED, _legs(lakehouse_rows=bounded), _scope())
        return "a second leg declared a boundary. There is exactly one, and it is named."
    return ""


def a_boundary_without_a_residual_is_refused() -> str:
    """A boundary with no duration is the overclaim with an extra word in front of it."""
    with contextlib.suppress(ValueError):
        Leg("model_artefacts", LegOutcome.BOUNDED, "quarantined")
        return "a bounded leg with no residual window was constructed"
    return ""


def the_certificate_says_what_it_does_not_cover() -> str:
    """The wording is the deliverable.

    It is read by somebody exercising Art. 17, and "erased" where "erased to a declared
    boundary" is true would be an overclaim made to the worst possible audience.
    """
    statement = certify("C00007", REQUESTED, COMPLETED, _legs(), _scope()).statement()
    return first_problem(
        require("declared boundary" in statement, "the certificate claims plain erasure"),
        require(
            "Machine unlearning is not claimed" in statement,
            "the certificate does not say that machine unlearning is not claimed",
        ),
        require("30d" in statement, "the residual window is not printed on the certificate"),
        require(
            "does not reach" in statement,
            "the certificate does not state that crypto-shredding cannot reach model weights",
        ),
    )


def the_online_store_leg_can_tell_deleted_from_absent() -> str:
    """A silent no-op reported as success is what makes a certificate untrue.

    `forget` returns whether anything was there. Without that, an erasure against a subject the
    online store never held reports the same confirmation as one that actually deleted
    something — and the two are indistinguishable in the record forever after.
    """
    feature = CONTRACTS.features["meter_consumption_1h"]
    materialiser = OnlineMaterialiser(feature)
    materialiser.observe("M00001", REQUESTED, 4200, REQUESTED)
    return first_problem(
        require(materialiser.forget("M00001") is True, "deleting a present subject reported False"),
        require(materialiser.forget("M00001") is False, "deleting an absent subject reported True"),
    )


CASES: tuple[Case, ...] = (
    Case(
        "the_scope_is_derived_not_maintained",
        "A hand-kept list of tables is right on the day it is written. An erasure arrives "
        "against whatever the platform looks like the day it is requested.",
        the_scope_is_derived_not_maintained,
    ),
    Case(
        "a_complete_run_certifies",
        "The calibration case. A system that never certifies satisfies every refusal below.",
        a_complete_run_certifies,
    ),
    Case(
        "a_failed_leg_refuses",
        "A partial erasure reported as complete is worse than none: the subject is told they "
        "are gone and the residual becomes invisible.",
        a_failed_leg_refuses,
    ),
    Case(
        "an_unattempted_leg_refuses",
        "Unattempted is an orchestration bug; failed is a store that would not cooperate. They "
        "are fixed by different people.",
        an_unattempted_leg_refuses,
    ),
    Case(
        "a_leg_missing_entirely_refuses",
        "The silent one. A run that never reached a store looks identical to one that had "
        "nothing to do there.",
        a_leg_missing_entirely_refuses,
    ),
    Case(
        "only_the_model_leg_may_be_bounded",
        "One boundary, not a pattern. Otherwise 'we could not finish that one either' becomes "
        "a second declared boundary, and then a third.",
        only_the_model_leg_may_be_bounded,
    ),
    Case(
        "a_boundary_without_a_residual_is_refused",
        "A boundary with no duration is a boundary nobody can be held to — the overclaim with "
        "an extra word in front of it.",
        a_boundary_without_a_residual_is_refused,
    ),
    Case(
        "the_certificate_says_what_it_does_not_cover",
        "The wording is the deliverable, and its audience is somebody exercising Art. 17.",
        the_certificate_says_what_it_does_not_cover,
    ),
    Case(
        "the_online_store_leg_can_tell_deleted_from_absent",
        "A silent no-op reported as success is what makes a certificate untrue.",
        the_online_store_leg_can_tell_deleted_from_absent,
    ),
)
