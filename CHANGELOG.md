# Changelog

Dated entries, newest first. Superseded statements are struck through rather than deleted, because
how a thing was wrong is the useful part. Architectural decisions live in
[`docs/adr/`](docs/adr/) and the running ledger in [`docs/DECISIONS.md`](docs/DECISIONS.md).

## 2026-08-22

**`main` had no branch protection, and nothing had ever asked.** The gate before an `apply` was
real — `deploy.yml` re-runs the whole of `ci.yml` against the exact ref and `apply` has
`needs: verify`, so nothing reaches AWS while anything is red. The gate before a *merge* was
not: a red suite showed a red tick and stopped nothing. Whether broken code entered `main`
depended on somebody noticing, which is the mechanism this repository refuses everywhere else.

No document claimed otherwise, so it was an absent control rather than a false statement — and
it was found the way WV-003 was found, by asking the API instead of reading a settings page.
`Branch not protected`, on the default branch, after 265 commits.

The six `ci.yml` jobs are now required status checks, with `enforce_admins` on, no force pushes,
no deletion and linear history required. `enforce_admins` is the half that matters: without it
the sole author bypasses the rule without touching a setting. No required reviewer — one author,
and `contracts/waivers.yaml` already states what that cannot satisfy. Recorded as item 9 in
`docs/DAY-ONE.md`, with the `gh api` call a reader runs to detect that it has been undone.

**The registered payload schema and the normaliser were two descriptions of one fleet, and
nothing held them equal.** `infra/streaming/schemas/meter_reading.json` declares the union of
shapes a meter may publish and is what the Glue Schema Registry evaluates a new firmware
generation against; `src/watermark/core/normalise.py` is what actually reads a payload. The
registry is a *registration-time* gate rather than a validator in the data path — the IoT rule
base64-encodes and forwards, and validates nothing — so the two could drift in either direction
with no symptom.

Registered and not implemented is the expensive one: adding `fw4` to the schema is the documented
step and returns a real verdict, *backward-compatible, approved*, on behalf of a consumer that
cannot read a word of it. The rollout proceeds, every reading from the cohort is quarantined as an
unknown shape, and it surfaces weeks later as a spike in a table nobody was watching — which is
the failure the registry exists to prevent. The other direction leaves the registry describing a
fleet that stopped being real, so the next compatibility check is evaluated against the wrong
union.

[`scripts/check_schemas_agree.py`](scripts/check_schemas_agree.py) now holds the set of
generations, the discriminator each is recognised by, and that the discriminator is `required`
rather than merely permitted, equal across both files. Mutation **42**, *register a firmware
generation the core cannot read*, is what proves it bites. `images/gate_proof.png` shows the
forty-first run and the caption says so rather than being relabelled.

## 2026-08-20

**The five-step sequence ran again, end to end, and every job of both captures was green.**
Run `32311622223`: 3,779 rows merged with **0** published before their interval ended, 3,779
distinct lineage ids, 285 restatements all naming what they replaced, Glue Data Quality 6/6 at
score 1.00, claim 3 agreeing across three features with 0 diverged, `SUB-01` driven to
485,442 W of 450,000 W producing 8 throttles all marked as fallback, the live case matrix at 7/7,
and all six erasure legs confirmed against the estate.

**`held_back` was induced live for the first time.** 3,646 status transitions, one into
`held_back`, and that one naming the substation holding it. Previously reported as a limit of the
capture's compression — it still is, and the occurrence is reported rather than asserted, but it
has now been observed.

**The budget action fired, and the ceiling was the thing that was wrong.** Tagged August spend
reached USD 115.64 against a USD 110 monthly ceiling; the action attached a deny-all policy to the
deploy role and the next deploy died on `ecr:GetAuthorizationToken … explicit deny`. Nothing had
run away — the ceiling had been written as though it bounded a single capture while the budget it
enforces is monthly, across every capture, apply and idle hour. Raised to USD 250 in
`infra/bootstrap/variables.tf`, with the reasoning recorded where the old figure's reasoning was.
~~"The budget action has never fired, so it remains a designed control rather than a demonstrated
one."~~

**The README is now checked by something.** It was the one artefact here that quoted every other
check and was guarded by none of them, and it drifted three times in this day's work — nine eval
harnesses reported as eight, six CI jobs as seven, three dbt tests as two — each found by a person
re-reading rather than by a control. `scripts/check_the_readme.py` re-reads 22 figures against the
repository and reports a figure it can no longer locate as **STALE** rather than passing over it.
It is attacked by a new gate-proof mutation that adds a harness and leaves the sentence counting
them alone, taking the harness to **41**. The repository refused the check twice while it was
being added — once for having no mutation, once for naming a control nothing had declared — which
is the coverage rule working on the person who wrote it.

**README rewritten to the portfolio standard**, with 25 images, each next to the claim it proves.
`SECURITY.md`, `CHANGELOG.md` and `.github/dependabot.yml` added.

## 2026-08-17

**The `offline_store` erasure leg did not exist.** `ErasureScope` declared six legs, the state
machine produced five, and `EveryLegConfirmed` was a five-way `AND` over array positions — a
condition that cannot notice a missing leg, because the missing leg is what changes the count.
Four of a subject's feature rows survived an erasure that certified. The branch exists now,
bounded by the assignment history; the refusal counts against `local.erasure_legs`; and
`check_erasure_legs.py` holds the scope, the machine's branches and that list equal on every push.

**The seed and the publisher were reading two different clocks**, so the generated day and the
reference history drifted apart by however long the training pipeline took. One anchor now, read
once where the day starts, handed to both.

**An erased meter read as a train/serve parity failure.** SageMaker's `DeleteRecord` is a soft
delete and the tombstone's event time outlives any re-materialisation, so claim 3 reported an
erasure that had worked as a divergence. The meter is named and excluded rather than tolerated.

**`capture.yml` takes `-f from_stage`.** A capture that failed at the fifth job continues rather
than starting again. Because the danger is not the resuming but the quoting, every run's summary
opens by saying which it was — a whole capture, or a resumed one that may not be cited as evidence.

**Required reviewers restored on both deploy environments** (WV-003, closed). They had been
removed five days earlier so a session could iterate without an approval prompt, and
`docs/DAY-ONE.md` had gone on describing the intended state.

## 2026-08-11 — `docs/DECISIONS.md` 17

**The estate is deployed.** Decision 15 had argued it never would be, because every claim is
provable offline by construction and a live run would produce a screenshot rather than a proof.
17 retracts it and names the error: proving the logic offline says nothing about whether the
estate that would carry it can exist. Those are two propositions and only one had been checked.
The first runs found four design errors no schema check can reach — among them that PyFlink cannot
emit a custom watermark ([ADR-0007](docs/adr/0007-the-framework-carries-records-not-semantics.md))
and that a green CI run had moved zero records. Decision 15 is kept in full.

## Earlier

The four phases, task by task, with what closed each, are in [`PLAN.md`](PLAN.md).
