"""Every leg of an erasure, checked against the estate rather than against the report.

The state machine in `infra/governance/` erases a subject and writes a certificate saying it
did. Until now the capture verified one leg of that certificate — the lakehouse rows — and took
the other five on the machine's word. **That is the certificate checking itself.** A leg that
silently did nothing produces the same `Succeeded` as a leg that worked: `DeleteFromTheOnlineStore`
against a feature group that no longer exists returns cleanly, `RederiveTrainingSets` over a
table with no matching rows rewrites zero of them and reports success, and `CryptoShred` against
an alias that was never created is the *expected* path for a subject who had no key.

So the check has to be adversarial and it has to be independent: ask the estate the question the
state machine claimed to have answered, through a different call than the one it made.

**The findings are pure.** `verdict()` takes plain observations — booleans, counts, strings that
somebody already fetched — and decides. Nothing in this module imports boto3, so the whole
decision table is exercised on a laptop, including the cases a live run can only reach by being
broken. `scripts/erasure_legs_live.py` is the thin part that goes and looks.

The asymmetry between the legs is the interesting part and it is not incidental:

* Four legs are *deletion*, and for those the question is a count that must be zero.
* `crypto_shred` is not deletion, it is **destruction of the means of reading** — so the check
  is on the key's state, and "the alias is gone" is not by itself an answer, because a subject
  who never had a key looks identical to one whose key has been destroyed.
* `model_artefacts` cannot be completed at all. A model trained before the request keeps the
  subject's contribution in its weights and no amount of deleting reaches it. That leg is
  BOUNDED, and the only thing to verify is that the certificate *says so* — a certificate that
  reported it complete would be the one genuinely dishonest outcome available here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

#: The leg that may be BOUNDED, mirroring `certificate.BOUNDABLE`. Stated again rather than
#: imported so that this module can be read on its own; `test_erasure_verify.py` asserts the two
#: agree, which is the check that keeps the duplication honest.
BOUNDABLE = "model_artefacts"

#: How a subject's key may legitimately look after a shred. `PendingDeletion` is the normal
#: outcome — KMS has no immediate delete — and `Disabled` is accepted because a key that cannot
#: be used is a key that cannot decrypt, which is the property being claimed.
SHREDDED_KEY_STATES = frozenset({"PendingDeletion", "PendingReplicaDeletion", "Disabled"})


class Finding(Enum):
    """What an independent look concluded about one leg."""

    #: The estate agrees with the certificate.
    CONFIRMED = "confirmed"
    #: The estate contradicts it. The certificate is wrong and the erasure is not complete.
    CONTRADICTED = "contradicted"
    #: Nothing could be observed, so nothing is claimed. **Not a pass.** A leg that cannot be
    #: checked is reported as such and fails the run, because "we could not tell" recorded as
    #: green is the exact failure this module exists to prevent.
    UNOBSERVABLE = "unobservable"


@dataclass(frozen=True, slots=True)
class LegVerdict:
    leg: str
    finding: Finding
    detail: str

    @property
    def ok(self) -> bool:
        return self.finding is Finding.CONFIRMED


@dataclass(frozen=True, slots=True)
class Observation:
    """What was seen, by whoever went and looked. Deliberately dumb.

    `rows` is None when the question could not be asked at all — a table that does not exist, a
    query that failed, a feature group that was never created. None and 0 are different answers
    and collapsing them is how an unobservable leg becomes a confirmed one.
    """

    leg: str
    #: Rows belonging to the subject that a deletion leg should have removed.
    rows: int | None = None
    #: For `crypto_shred`: the key's state, or None if the alias resolved to nothing.
    key_state: str | None = None
    #: For `crypto_shred`: whether a durable shred marker exists for this subject.
    shred_marker: bool = False
    #: For `model_artefacts`: the residual window the certificate declares.
    residual: str | None = None
    #: Free text from the collector about why it could not look, if it could not.
    unobservable_because: str | None = None
    #: Free text the collector wants carried into the finding whatever the verdict — the rows
    #: behind a count, the instant behind a state. A verdict that names a number and not the
    #: thing it counted costs a whole run to interpret.
    note: str | None = None


def _deletion_verdict(observation: Observation) -> LegVerdict:
    if observation.rows is None:
        return LegVerdict(
            observation.leg,
            Finding.UNOBSERVABLE,
            "no count was taken, so nothing is claimed. A leg nobody could look at is not a leg "
            "that passed.",
        )
    if observation.rows > 0:
        detail = (
            f"{observation.rows} rows belonging to the subject survived a leg the certificate "
            f"reports as complete"
        )
        if observation.note:
            detail = f"{detail}. {observation.note}"
        return LegVerdict(observation.leg, Finding.CONTRADICTED, detail)
    return LegVerdict(observation.leg, Finding.CONFIRMED, "no rows belonging to the subject remain")


def _shred_verdict(observation: Observation) -> LegVerdict:
    """The one leg where absence is ambiguous, and the reason `erasure-shredded/` exists.

    Once a subject is shredded the deploy stops declaring their key, so Terraform removes the
    alias — which is what stops a routine apply resurrecting somebody who asked to be forgotten.
    The consequence is that a shredded subject and a subject who never had a key both present as
    "alias not found". The durable marker is what separates them, and without it this leg is
    unobservable rather than confirmed.
    """
    if observation.key_state is None:
        if observation.shred_marker:
            return LegVerdict(
                observation.leg,
                Finding.CONFIRMED,
                "the key is gone and a durable shred marker records that it was destroyed for "
                "this subject",
            )
        return LegVerdict(
            observation.leg,
            Finding.UNOBSERVABLE,
            "no key and no shred marker. This subject may have been shredded, or may never have "
            "had a key — the two look identical from here and have opposite right answers.",
        )
    if observation.key_state in SHREDDED_KEY_STATES:
        return LegVerdict(
            observation.leg,
            Finding.CONFIRMED,
            f"the subject's key is {observation.key_state}; the ciphertext is unreadable",
        )
    return LegVerdict(
        observation.leg,
        Finding.CONTRADICTED,
        f"the subject's key is {observation.key_state} and can still decrypt their data. The "
        f"certificate reports a crypto-shred that did not happen.",
    )


def _bounded_verdict(observation: Observation) -> LegVerdict:
    """The leg that cannot be completed, so what is checked is the honesty of the statement."""
    if observation.residual and observation.residual.strip():
        return LegVerdict(
            observation.leg,
            Finding.CONFIRMED,
            f"declared bounded, with the residual on the face of the certificate: "
            f"{observation.residual.strip()}",
        )
    return LegVerdict(
        observation.leg,
        Finding.CONTRADICTED,
        "reported without a residual window. A model trained before the request keeps the "
        "subject's contribution in its weights; a certificate that does not say so is claiming "
        "an erasure that did not occur.",
    )


def verdict(observation: Observation) -> LegVerdict:
    """One observation in, one finding out. The whole decision table, and it is pure.

    **"Could not look" is answered here, before any leg-specific rule.** It was answered inside
    the deletion branch only, so a certificate that could not be read reached the bounded rule,
    found no residual, and was reported CONTRADICTED — an accusation that the certificate lies,
    made on the basis of not having read it. Both outcomes fail the run, which is why it went
    unnoticed and why it matters: the two say completely different things to whoever is holding
    the report.
    """
    if observation.unobservable_because:
        return LegVerdict(observation.leg, Finding.UNOBSERVABLE, observation.unobservable_because)
    if observation.leg == "crypto_shred":
        return _shred_verdict(observation)
    if observation.leg == BOUNDABLE:
        return _bounded_verdict(observation)
    return _deletion_verdict(observation)


def report(observations: list[Observation], expected: tuple[str, ...]) -> list[LegVerdict]:
    """Every declared leg, in the order the scope declares them.

    A leg the scope declares and nobody observed is reported UNOBSERVABLE and fails the run. That
    is the same guard the case matrices carry, and for the same reason: a list of checks cannot
    catch its own omission, so the list of *things to check* comes from somewhere else.
    """
    seen = {observation.leg: observation for observation in observations}
    verdicts = []
    for leg in expected:
        if leg not in seen:
            verdicts.append(
                LegVerdict(
                    leg,
                    Finding.UNOBSERVABLE,
                    "the scope declares this leg and nothing went and looked at it",
                )
            )
        else:
            verdicts.append(verdict(seen[leg]))
    for leg in sorted(set(seen) - set(expected)):
        verdicts.append(
            LegVerdict(leg, Finding.CONTRADICTED, "observed, but the scope declares no such leg")
        )
    return verdicts


#: How many times the certificate may be JSON-encoded before this gives up. Step Functions writes
#: it through `States.JsonToString`, so what lands in S3 is a JSON *string* containing JSON and a
#: single `json.loads` returns `str`. The bound exists so a third encoding is reported rather
#: than looped on.
MAXIMUM_ENCODINGS = 4


def residual_from_certificate(body: str | bytes) -> Observation:
    """What the certificate says about the leg deletion cannot reach.

    Pure, and here rather than in the collector, because every way this can go wrong is a shape
    of document rather than a state of the estate — and a shape can be written down. The first
    version reached for `.get` on the result of one `json.loads` and raised `AttributeError` on
    a live certificate, which is a crash where the whole point of the module is a verdict.
    """
    document: object = body
    for _ in range(MAXIMUM_ENCODINGS):
        if isinstance(document, dict):
            break
        try:
            document = json.loads(document)
        except (json.JSONDecodeError, TypeError, ValueError):
            return Observation(
                leg=BOUNDABLE, unobservable_because="the certificate is not readable JSON"
            )
    if not isinstance(document, dict):
        return Observation(
            leg=BOUNDABLE,
            unobservable_because="the certificate is still not an object after unwrapping",
        )

    # `leg`, not `name` — the state machine's key for it. The residual is prose plus a window,
    # and the prose is what a data subject would be shown, so the prose is what is checked.
    for entry in document.get("legs", []):
        if entry.get("leg") == BOUNDABLE:
            note = str(entry.get("note") or "").strip()
            days = entry.get("residual_days")
            if note and days:
                return Observation(leg=BOUNDABLE, residual=f"{note} Residual window: {days} days.")
            return Observation(leg=BOUNDABLE, residual=note or (f"{days} days" if days else ""))

    return Observation(
        leg=BOUNDABLE,
        unobservable_because=(
            f"the certificate names no `{BOUNDABLE}` leg, so it does not say what it could not "
            f"reach"
        ),
    )
