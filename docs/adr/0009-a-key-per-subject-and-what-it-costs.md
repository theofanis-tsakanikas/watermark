# ADR-0009 — A KMS key per data subject, and the ceiling it has

**Status:** accepted · **Date:** 2026-08-13 · **Serves:** claim 6 · **Refines** `docs/DECISIONS.md` 11

## Context

Decision 11 settled that erasure is crypto-shredding with "a key per subject". `infra/foundation/`
created the *root* of that hierarchy and described it as the key "every subject key is derived
under". The erasure state machine resolves `alias/watermark-subject-<subject_id>` and schedules
that key for deletion.

Between the root and the state machine there was nothing. No per-subject key was ever created,
by anything. The first erasure ever run against a live estate answered:

```
Kms.NotFoundException: Alias alias/watermark-subject-C00007 is not found
```

and the orchestration refused to certify — which is the designed behaviour and, in this case,
the wrong reason for it. **A claim that has only ever been observed failing is not a claim that
has been demonstrated.** The refusal proves the guard; it does not prove the mechanism.

## Decision

**One customer master key per data subject, created by `infra/governance/`, one alias each,
seven-day deletion window.**

The subjects come from `data/cast.py` through `deploy.yml`, the same route the substation list
takes and for the same reason: a second copy in a settings page is a copy that drifts, and a
subject with no key is one whose erasure request cannot be honoured — discovered at the moment
somebody has asked to be forgotten.

**Why a customer master key rather than a data key under the root.** Crypto-shredding needs the
key material to become unavailable, and *provably* so. A data key wrapped under the root and
held in a table can be deleted, but proving no plaintext copy survives is a statement about
every place it was ever unwrapped — every cache, every worker, every log. `ScheduleKeyDeletion`
on a CMK is a statement AWS makes and can be asked to confirm, and a completeness proof needs
something it can ask.

**Seven days, not thirty.** The root uses thirty because destroying it ends every subject at
once, and the window is the only thing between the largest erasure in the system's history and
the worst accident available in it. A *subject* key is the opposite case: GDPR Art. 12(3) gives
one month to answer a request, and a key that lingers thirty days spends most of that month in a
state where the data is still readable. Seven is the shortest AWS allows.

## The ceiling, stated rather than discovered

**This does not scale to the scenario's fleet, and the repository says so here rather than
finding out in production.**

`docs/SCENARIO.md` describes 250,000 meters. One CMK per subject at that size is:

- **past the default quota.** AWS allows 100,000 customer master keys per account per region by
  default. 250,000 subjects needs three accounts or a quota conversation.
- **roughly USD 250,000 a month** in key storage alone, at one dollar per key per month, before
  a single request.

Forty-one subjects is what this estate holds and what the claim is demonstrated against. A
production system at fleet scale needs envelope encryption: one root, a data key per subject
wrapped under it, the wrapped key stored beside the subject, and shredding is the deletion of
that stored ciphertext. That design trades the provable statement above for a scalable one, and
the trade is real — it moves "AWS confirms the key is gone" to "we confirm we deleted our only
copy", which is a weaker claim and an honest one.

**What is claimed here:** crypto-shredding is demonstrated end to end, at the scale of this
estate, with a mechanism whose completion AWS confirms. **What is not claimed:** that this
mechanism is what a 250,000-subject deployment would use.

## The apply that un-erased somebody

Discovered on the deploy after the first successful shred, and it is the half of this design
that nothing in the literature warns about.

An erasure calls `ScheduleKeyDeletion`. The next `terraform apply` found the key in
`PendingDeletion`, planned to replace it, and asked to re-point the alias:

```
AccessDeniedException: not authorized to perform: kms:UpdateAlias on resource:
arn:aws:kms:eu-central-1:...:alias/watermark-subject-C00007
```

The obvious reading is a missing permission and the obvious fix is to grant it. **That fix would
have made every deploy silently un-erase whoever had asked to be forgotten** — a fresh key under
the same alias, the declaration satisfied, the apply reporting success, and a subject whose
erasure certificate is sitting in the bucket restored by routine maintenance nobody would think
to audit.

**Declarative infrastructure and crypto-shredding want opposite things.** Terraform's whole
purpose is to make reality match the declaration. An erasure's whole purpose is to destroy
something the declaration says should exist. They cannot both win, and the interesting question
is which one gives way.

It is the declaration. `deploy.yml` computes the subject list as the cast *minus* every subject
whose key is already in `PendingDeletion`, so Terraform removes those resources rather than
resurrecting them and the erasure survives every subsequent apply. The reason this direction is
right and not merely convenient: an erasure is a legal obligation with a subject on the other end
of it, and a declaration is a statement of intent by an operator. When they conflict, the party
who can be harmed is not the operator.

The permission is deliberately **not** granted. A control that depends on remembering not to use
a permission is not a control; the role cannot re-point a subject alias at all.

## Alternatives rejected

**Create the subject key lazily, during erasure.** A key created in order to be destroyed shreds
nothing — the data was never encrypted under it. It would turn the leg green and mean nothing,
which is worse than the failure it replaces.

**Drop the leg and declare crypto-shredding out of scope.** Claim 6's whole difficulty is that
deletion has a boundary and the boundary must be provable. Removing the mechanism leaves
physical row deletion, which Iceberg does well and which says nothing about the copies in
snapshots, in the offline store's Parquet, or in an old training set.

**Key per substation, or per cohort.** Cheaper by four orders of magnitude and it erases the
wrong number of people. A cohort key destroyed on one subject's request takes the others with
it; kept, it erases nobody.
