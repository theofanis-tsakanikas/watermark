# Day one — the manual work, written down

**IaC only** is the rule, and this file is the exception's exception: the small set of actions
that have no API, or that must happen before the API exists. Everything here is recorded
*before* it is done. Manual work that is done and not written down is the drift nobody can
account for six months later, and it is always discovered during the incident rather than
before it.

**Items 1–4 have been done.** The account exists, `infra/bootstrap/` was applied from a laptop
on 2026-08-10, both GitHub environments carry a required reviewer, and the two repository
variables are set. Item 4b — activating the cost allocation tag — behaved exactly as written
below: the first apply failed on it, and the second, a day later, succeeded. Item 9 — branch
protection on `main` — was done on 2026-08-22, after the API was asked and answered *"Branch not
protected"*. Items 6 and 8 have not been needed.

The estate itself is **not** standing. It was deployed through the gated workflow, driven, and
destroyed again; see `docs/DECISIONS.md` 17. What survives a teardown is what this file's
items create, which is the point of them being here.

---

## 1 · An AWS account, and no root access keys

Create the account. Enable MFA on the root user, create **no** access keys for it, and then do
not use it again. Every later action in this repository is taken by a role.

*No API, because there is no identity to call it with yet.*

## 2 · Bootstrap, applied once from a laptop

`infra/bootstrap/` creates the state backend and the role CI assumes. It cannot be applied by
CI, because CI cannot create the role it needs in order to run. See
[infra/bootstrap/README.md](../infra/bootstrap/README.md) for the exact commands.

Everything else is applied **only** from a gated workflow. A layer that can be applied from a
laptop is a layer that will drift.

*Has an API. It is here because of the ordering, not because it is manual.*

## 3 · GitHub environments `deploy` and `destroy`

The deploy role trusts exactly four subjects — two environments, in each of the two forms
GitHub's claim can take:

```
repo:<owner>/watermark:environment:deploy
repo:<owner>/watermark:environment:destroy
repo:<owner>@<owner-id>/watermark@<repo-id>:environment:deploy
repo:<owner>@<owner-id>/watermark@<repo-id>:environment:destroy
```

Both environments must exist. The environment is the whole of the authorisation: without it the
OIDC subject never matches and the workflow cannot assume the role — which is the intended
failure, not a misconfiguration.

*Has an API (`gh api`), and is recorded here because forgetting it produces an
`AssumeRoleWithWebIdentity` denial that reads like a broken trust policy.*

### The required reviewer, and how it came to exist

Both environments carry one. It was not always so, and the history is worth keeping because it
is a lesson about where controls actually live.

While the repository was **private under a free plan**, GitHub refused the protection rule:

```
422  Failed to create the environment protection rule.
     Please ensure the billing plan supports the required reviewers protection rule.
```

— and then **created the environment anyway, with no rules on it**. That is the failure worth
remembering: the request fails, the environment appears, the OIDC subject starts matching, and
the human gate is gone while every page still looks configured. It was found by trying it. For
as long as it lasted, this file said plainly that doctrine 5 did not hold for a deploy.

Publishing the repository fixed it, because environment protection rules are free on public
repositories, and for a time **doctrine 5 — nothing approves itself — held in the estate as well
as in `promotion.py`**: a dispatch of `deploy` or `destroy` waited for a named human before the
job that assumes the role would start.

> **They were off for five days, and that is worth keeping.** They were removed so a working
> session could iterate on a broken capture without an approval prompt on each of six runs in a
> day — a real reason, and one that expired with the iteration. It did not expire on its own: the
> gap was recorded as **WV-003** in [`contracts/waivers.yaml`](../contracts/waivers.yaml) with a
> date, and it was found again by reading this file against `gh api`, which said *no protection
> rules* while the paragraph above described the intended state.
>
> Restored 2026-08-17, on both environments, verified through the API rather than through a
> settings page. A control that is described and switched off is worse than one nobody claimed,
> because the description is what people read.

What still stands in the path besides the reviewer: the confirmation word, the seven-day bound
on `expires_at`, a full CI run against that exact ref, and the printed plan.

## 4 · Two repository variables — and only ever two

| Variable | Value |
|---|---|
| `AWS_ACCOUNT_ID` | the account bootstrap was applied in |
| `AWS_REGION` | the region bootstrap was applied in |

These two are irreducible. **CI has to know which account before it can ask that account
anything**, and reading a parameter is already asking — so the account id cannot come from the
account. Everything else can, and does: `infra/bootstrap` publishes the state bucket, the state
key and the role ARN to `/watermark/bootstrap/*`, and the workflows resolve them after the role
is assumed. The role ARN itself is composed rather than stored, from the account id and the
name bootstrap chose.

**Nothing else may be added here.** A transcribed value is indistinguishable from an
independent setting: rename the state bucket and a pasted `TF_STATE_BUCKET` becomes a deploy
that fails on a backend nobody can find, with the fix in a settings page rather than in a diff
— and nothing in the repository would have gone red first.
`scripts/check_deploy_inputs.py` refuses any other `vars.` reference, and `make gate-proof`
plants exactly that paste to prove the refusal happens.

**Variables, not secrets.** Neither is a credential, and hiding an account id from the workflow
log removes the one thing that makes a denial diagnosable.

## 4b · Activate the cost allocation tag — the one step that must wait

`infra/bootstrap` declares `aws_ce_cost_allocation_tag.project`, and on a fresh account the
apply **fails on it**:

```
ValidationException: Failed to update Cost Allocation Tag: Tag keys not found: watermark:project
```

That is not a misconfiguration. AWS only lists a tag key as activatable once it has seen it on a
**billed** resource, which lags the resource's creation by up to 24 hours. Apply bootstrap
again the next day and the resource is created.

**Until it is, the budget's cost filter matches nothing and the ceiling cannot fire.** A budget
reporting zero looks exactly like a project that is not spending, which is why this is written
here rather than left to be noticed. Check it with:

```
aws ce list-cost-allocation-tags --tag-keys "watermark:project"
```

*Has an API, and Terraform owns it. It is here because it is the one step that cannot succeed
on the first apply.*

## 5 · Nothing. The budget needs no confirmation.

This item used to say the budget's email subscription had to be confirmed from an inbox. It
does not, and the item described two things that were not true.

**There is no SNS topic in the budget path.** AWS Budgets delivers to
`subscriber_email_addresses` directly, and a direct budget subscriber is not confirmed the way
an SNS subscription is. The SNS topic in `infra/foundation` is the reaper's dead-letter queue,
which is a different mechanism with no email subscriber at all.

**And the sentence assumed a control that did not exist.** It said "the budget action still
disables the deploy role at its threshold" — there was no budget action anywhere in the
repository. There is one now, in `infra/bootstrap/cost.tf`, applied before any layer can spend
anything. Passing `budget_alert_email` on the bootstrap command line is the whole of the work.

## 6 · Service quota increases, if the capture needs them

Managed Service for Apache Flink defaults to **64 KPUs** per application and Kinesis Data
Streams has account-level shard limits. The design in `docs/AWS-CONSTRAINTS.md` sits well
inside both, so no increase is expected. If one turns out to be needed it is a support case
with a lead time, and finding that out on capture day is how a capture becomes two capture
days.

*Has an API (`service-quotas`), with a human approval and a lead time behind it.*


## 7 · Nothing. There is no second round of variables.

This item used to add three more — `TF_STATE_BUCKET`, `BUDGET_ALERT_EMAIL` and
`WATERMARK_SUBSTATIONS`. All three are now resolved rather than transcribed, and the reasoning
is worth keeping because each was wrong in a different way.

**`TF_STATE_BUCKET`** is a name `infra/bootstrap` chose. It is published to
`/watermark/bootstrap/state_bucket` and read back after the role is assumed.

**`BUDGET_ALERT_EMAIL`** is an address belonging to a person. It is an input to bootstrap, held
as a **SecureString** in `/watermark/bootstrap/budget_alert_email`, and never in a settings page
— which is where personal data survives every later decision about who may read this repository.

**`WATERMARK_SUBSTATIONS`** was the worst of the three, because the list already exists in
`data/cast.py`. A second copy in a settings page is the copy that drifts, and the drift is
invisible: a substation added to the generator and not to the variable is a partition that
cannot hold the watermark back, so every window closes without it. The workflows now read
`data.cast.SUBSTATIONS` directly, which makes that failure impossible rather than documented.

## 8 · A `capture` environment, if the estate is ever driven

`capture.yml` starts the three resources that bill while idle. It uses the `deploy` environment
today, which means its reviewers are the deploy reviewers. If the two audiences ever differ —
somebody who may drive a scenario but not create infrastructure — that is a third environment
and a third OIDC subject in `infra/bootstrap/oidc.tf`, not a widened trust on the existing one.

*Recorded now because the temptation at that moment is to add a wildcard.*

## 9 · Branch protection on `main`

*Done 2026-08-22.*

**Why it is here rather than in Terraform.** It has an API — `PUT
/repos/{owner}/{repo}/branches/main/protection` — and no Terraform layer in this repository
manages GitHub. Putting it in one would mean a provider with a personal access token, which is
the long-lived credential `CLAUDE.md` refuses; the OIDC role this project uses reaches AWS and
nothing else. So it is a repository setting, applied once, recorded here.

**What is set.** The six `ci.yml` jobs as required status checks — `secret scan`, `lint and
tests`, `the claim gates`, `attack our own gates`, `core equals Flink`, `terraform and
checkov` — with `enforce_admins`, no force pushes, no deletion, and linear history required.

**`enforce_admins` is the whole of it.** Without it the sole author bypasses the rule without
touching a setting, and the protection is a reminder rather than a control — the shape this
repository refuses everywhere else. It costs what it should: a red suite blocks the author too,
including at an inconvenient moment.

**No required reviewer.** One author. `contracts/waivers.yaml` already states what a
single-author repository can and cannot satisfy of doctrine 5, and manufacturing a second person
is not among them. Required *status checks* need no second person, which is why they are the
half that can be enforced.

**Why it was not there for the first 265 commits.** Nothing claimed it was — no document
asserted branch protection, so this is an absent control rather than a false statement. It was
found by asking the API rather than by reading a settings page, which is the same method that
found WV-003.

**How a reader detects it has not been done:**

```bash
gh api repos/<owner>/watermark/branches/main/protection \
  --jq '{checks: .required_status_checks.contexts, admins: .enforce_admins.enabled}'
```

`Branch not protected` is the failure. A response listing fewer than six contexts, or
`admins: false`, is the same failure wearing a green tick.

---

## The rule for adding to this list

A new entry needs three things: **what** was done, **why it has no API**, and **how a reader
detects that it has not been done**. The third is the one that matters. An undone manual step
that fails loudly is an inconvenience; one that fails silently is an estate that does not
behave the way this repository says it does.

