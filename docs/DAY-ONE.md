# Day one — the manual work, written down

**IaC only** is the rule, and this file is the exception's exception: the small set of actions
that have no API, or that must happen before the API exists. Everything here is recorded
*before* it is done. Manual work that is done and not written down is the drift nobody can
account for six months later, and it is always discovered during the incident rather than
before it.

Nothing on this list has been done. The estate does not exist.

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

The deploy role trusts exactly two subjects:

```
repo:<owner>/watermark:environment:deploy
repo:<owner>/watermark:environment:destroy
```

Both environments must exist in the repository settings, each with **required reviewers**. The
environment is the whole of the authorisation: without it the OIDC subject never matches and
the workflow cannot assume the role — which is the intended failure, not a misconfiguration.

*Has an API (`gh api`), and is recorded here because forgetting it produces an
`AssumeRoleWithWebIdentity` denial that reads like a broken trust policy.*

## 4 · Two repository variables

| Variable | Value |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | the `deploy_role_arn` output from bootstrap |
| `AWS_REGION` | the region bootstrap was applied in |

**Variables, not secrets.** A role ARN is not a credential: it is useless to anybody the trust
policy does not name. Storing it as a secret hides it from the workflow logs that would
otherwise make a denial diagnosable, and buys nothing.

## 5 · Confirm the budget alarm's email subscription

An AWS Budget action and its SNS topic are Terraform. The **confirmation of an email
subscription is a link in an inbox** and has no API by design.

Until it is confirmed the topic has no confirmed endpoint, the budget action still disables the
deploy role at its threshold, and nobody is told that it did. Confirm it before the first
deploy, not after the first surprise.

*Genuinely no API.*

## 6 · Service quota increases, if the capture needs them

Managed Service for Apache Flink defaults to **64 KPUs** per application and Kinesis Data
Streams has account-level shard limits. The design in `docs/AWS-CONSTRAINTS.md` sits well
inside both, so no increase is expected. If one turns out to be needed it is a support case
with a lead time, and finding that out on capture day is how a capture becomes two capture
days.

*Has an API (`service-quotas`), with a human approval and a lead time behind it.*


## 7 · Two more repository variables, once the layers exist

| Variable | Value |
|---|---|
| `TF_STATE_BUCKET` | the `state_bucket` output from bootstrap |
| `BUDGET_ALERT_EMAIL` | where the budget alarm goes; the subscription confirmation is item 5 |
| `WATERMARK_SUBSTATIONS` | a JSON list, e.g. `["SUB-01","SUB-02","SUB-03","SUB-04"]` |

`WATERMARK_SUBSTATIONS` has no default on purpose. It is what the watermark generator declares,
and a substation missing from it is one that can never hold a window open — so every window
closes without it, silently. Declared and silent is a fact; unknown is an assumption.

Variables rather than secrets, for the same reason as the role ARN: neither is a credential,
and hiding a bucket name from the workflow log removes the one thing that makes a backend
misconfiguration diagnosable.

## 8 · A `capture` environment, if the estate is ever driven

`capture.yml` starts the three resources that bill while idle. It uses the `deploy` environment
today, which means its reviewers are the deploy reviewers. If the two audiences ever differ —
somebody who may drive a scenario but not create infrastructure — that is a third environment
and a third OIDC subject in `infra/bootstrap/oidc.tf`, not a widened trust on the existing one.

*Recorded now because the temptation at that moment is to add a wildcard.*

---

## The rule for adding to this list

A new entry needs three things: **what** was done, **why it has no API**, and **how a reader
detects that it has not been done**. The third is the one that matters. An undone manual step
that fails loudly is an inconvenience; one that fails silently is an estate that does not
behave the way this repository says it does.

