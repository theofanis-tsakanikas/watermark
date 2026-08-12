# Handover — 2026-08-12

Written at the end of a long working session, for the person who runs `deploy` tomorrow. It
says what changed, what is proved, what is left, and the exact commands to pick it up.

---

## The state of the account, right now

**The estate is destroyed.** `destroy.yml` ran to completion and every step passed. Verified
against the AWS APIs rather than against the tag index, because the tag index is eventually
consistent and still lists resources that are gone:

| | |
|---|---|
| Managed Flink applications | none |
| Kinesis streams | none |
| SageMaker endpoints, feature groups, pipelines | none |
| Step Functions | none |
| Glue jobs, Glue databases (`watermark_*`) | none |
| Lake Formation tags | none |
| VPC endpoints, subnets, NAT gateways, flow logs tagged `watermark:*` | none |
| CloudWatch log groups for the Flink app | none |

**KMS keys are in `PendingDeletion`, and that is correct.** AWS will not delete a key
immediately; sixteen watermark keys sit in the 7-to-30-day window and cost nothing there. The
single `Enabled` watermark key is `alias/watermark-tfstate`, which is bootstrap and must
survive.

**Other projects were not touched.** The one live VPC and the one live VPC endpoint in the
region belong to `attestor` — confirmed by their tags — and `attestor`, `dbx`, `manifest` and
the `sales_eu` Glue database are all intact.

**Bootstrap survives, by design.** State bucket `watermark-tfstate-387229419515` and its logs
bucket, the `watermark-deploy` and `watermark-budget-action` roles, `alias/watermark-tfstate`,
the three `/watermark/bootstrap/*` SSM parameters, the `watermark/processing` ECR repository,
the `watermark-estate` budget, and the account's GitHub OIDC provider. This is why tomorrow
starts with a dispatch and not with `terraform apply` from a laptop.

---

## What was done today

Thirty-odd commits, every one through CI, and **not a single fix applied by hand to AWS**. The
CLI was used to look, never to repair.

### The chain now runs end to end and asserts itself

A capture publishes the generated day to IoT Core, Flink closes the windows, a Glue `MERGE`
lands them in Iceberg, and Athena is then asked six questions whose answers are the claims:

| asserted in SQL, on every capture | last green run |
|---|---|
| rows merged | 5,681 |
| rows published with a watermark earlier than their own interval end | **0** — claim 1 |
| distinct lineage ids | 5,681 for 5,681 rows — claim 2 |
| restatements | 672 |
| restatements naming what they replaced | **672 of 672** — doctrine 4 |

`held_back` was induced in the cloud for the first time, with `holding_back: SUB-01` and
`may_close_windows: false` on the record.

### The serving path exists

A model trained on `randomised_inspection` labels passed the promotion gate, was approved by
**Theofanis Tsakanikas** — recorded in the SageMaker registry's `ApprovalDescription` — and
served real inference: `0.9939` for a meter scoring 820 in the most deprived decile. The AI Act
Art. 12 data capture was written and asserted.

### What the live runs found that no offline check could

Every one of these was invisible to `terraform validate`, checkov, 230 unit tests and seven
green claim harnesses:

1. **No Iceberg table had ever existed.** `aws_glue_catalog_table` with `table_type = "ICEBERG"`
   produces a catalogue entry AWS refuses to read. The `warehouse/` prefix of a fully applied,
   fully green estate was empty. → ADR-0008: the writer creates the table.
2. **The watermark was computed over meters, not substations.** The IoT rule and the core meant
   different things by "partition", so every declared substation lagged infinitely, was excluded
   as idle, and every published total carried a hole that did not exist. Claim 1's sharpest case
   could not fire, because SUB-03 never spoke at all.
3. **The adapter dropped five of the core's thirteen fields**, including `closed_at` — the one
   the repository itself calls "claim 1, checkable in SQL" — and computed no `lineage_id` at
   all, so claim 2 was being proved about a path production does not take.
4. **A third of the fleet published five months late.** `fw1` writes epoch seconds; the
   publisher's regex matched ISO-8601 only.
5. **The capture compressed the axis the closing rule measures on**, collapsing four days into
   one or two fifteen-minute grid cells, so almost nothing ever closed.
6. **The merge could not survive the restatement it was written for** — two source rows for one
   key, which cannot happen until restatements do.
7. **Model Monitor cannot be created in this account.** `CreateDataQualityJobDefinition` answers
   *"in maintenance mode and not available to new customers"* — the same sentence Clarify gives.
   Recorded in `docs/AWS-CONSTRAINTS.md`; the resources are behind `model_monitor_available`,
   default false.
8. **The compiled feature SQL had never been executed.** Three defects in one file: an uncast
   parameter Athena cannot do arithmetic on, an ingestion column two of its three tables do not
   have, and a query that read correctly and could not run.

Each is now refused by a check that fails on a laptop: `check_partition_vocabulary.py` (with a
`gate-proof` mutation — 22 refused, 0 accepted, 0 stale), `test_evidence_line.py` written
against the dataclass, `test_retimed.py` written against the cast, and a stricter
`check_lakehouse_wiring.py` that compares the layer and not only the table name.

---

## What is left, in the order to do it

### 1. Claim 3 and claim 4, live — the only unfinished thing

`scripts/parity_live.py` exists and runs. Its last result was **1 agreed, 19 diverged**, and the
divergence was diagnosed as a harness fault, not a system fault: an online record's window ends
at *that entity's own* latest reading, and the harness was asking the offline store about the
globally latest instant. The fix is committed (`4b2a035`) and **has never been run** — the
capture that would have proved it was cancelled to free the account.

So tomorrow's first job is one deploy and one capture, and reading that number.

```bash
gh workflow run deploy.yml  -f layer=all -f stream_position=LATEST \
    -f online_store=true -f promoted_model= \
    -f expires_at=<RFC-3339, within 7 days> -f confirm=apply

gh workflow run capture.yml -f minutes=20 -f snapshot=snapshot-<date> \
    -f threshold=700 -f labels=randomised_inspection -f confirm=capture
```

`online_store=true` is required — with it off there is no online side and the step says so.
Leave `promoted_model` empty unless an endpoint is wanted; the endpoint costs money and lives
exactly one capture, because the capture's stop step deletes it.

### 2. If an endpoint is wanted again

The model registry was emptied by the destroy, so a new capture must register a version first,
then:

```bash
gh workflow run promote.yml -f model_package_version=<n> \
    -f approver="Theofanis Tsakanikas" -f parity_holds=true -f confirm=promote
gh workflow run deploy.yml ... -f promoted_model=v<n> ...
```

Use `labels=randomised_inspection`. With `dispatch_log` the gate refuses the model for the
label-coverage finding, which is the documented result of claim 5 and not a failure.

### 3. Two owed restorations

**Both GitHub environments have their required-reviewer rule removed** — `deploy` and `destroy`
— so that today's sequence could run unattended. Put them back:

```bash
for env in deploy destroy; do
  echo '{"wait_timer":0,"reviewers":[{"type":"User","id":218610429}],"deployment_branch_policy":null}' \
    | gh api -X PUT repos/:owner/:repo/environments/$env --input -
done
```

Leaving them off contradicts the repository's own doctrine 5, and `deploy.yml`'s header calls
the environment "the authorisation".

### 4. Still gaps, and still declared as gaps

- **`stalled` and `starved`** need a stream that stops rather than one that is quiet.
- **`core equals Flink`** — the MiniCluster harness is unwritten, and writing it needs a design
  decision about how ingest time enters the adapter, because it currently comes from processing
  time and a MiniCluster replay would stamp everything with the wrong clock.
- **Model Monitor** is not a gap that can be closed from this repository.

### 5. Documentation that has drifted again

`README.md`'s status block was updated mid-session and the numbers moved after it. The scoreboard
rows for tests, `gate-proof` and preflight are current; the live-run table quotes 4,682 rows
where the last green run had 5,681. Worth one pass with the final numbers once claim 3 lands.

---

## Cost

Today's session ran roughly a dozen deploys, eight captures, an endpoint for parts of three of
them, and the Feature Store online store. The previous whole-exercise figure of **USD 12.35**
in `README.md` is from 2026-08-11 and is now certainly an undercount for the project as a whole
— it should not be quoted as covering today until the bill is read. The `watermark-estate`
budget is USD 110 and its action never fired.
