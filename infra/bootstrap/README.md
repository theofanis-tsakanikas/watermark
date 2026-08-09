# bootstrap — the only layer applied from a laptop

It creates the two things CI cannot create for itself: the S3 backend every other layer keeps
its state in, and the IAM role GitHub Actions assumes. CI cannot create the role it needs in
order to run, so somebody has to, once.

**Every other layer under `infra/` is applied only from a gated workflow.** A layer that can be
applied from a laptop is a layer that will drift, and the drift is discovered by a `plan` that
wants to destroy something nobody remembers creating.

## What it makes

| Resource | Why |
|---|---|
| `<project>-tfstate-<account>` | State. Versioned, KMS-encrypted, access-logged, TLS enforced, `prevent_destroy` |
| `<project>-tfstate-logs-<account>` | Access logs for the above, SSE-S3, expiring |
| `alias/<project>-tfstate` | The state key, rotating, with an explicit key policy |
| GitHub OIDC provider | Optional — set `create_oidc_provider = false` if the account already has one |
| `<project>-deploy` | The role CI assumes. Trusts this repository and named environments only |

No DynamoDB lock table: the S3 backend locks with a lock file (`use_lockfile = true`), which
puts the lock in the same bucket, under the same key, as the thing it protects.

## Applying it

```bash
terraform -chdir=infra/bootstrap init
terraform -chdir=infra/bootstrap apply \
  -var 'github_owner=<your-github-account>'
```

Then read `terraform output backend_configuration` and paste the block into each new layer as
it is written, with its own `key`. Two layers sharing one state key is the mistake this output
exists to prevent.

## Its own state is local, and stays local

This layer creates the backend, so it cannot store its state in it. `terraform.tfstate` stays
on the machine that applied it and is covered by `.gitignore`.

Two honest options once it exists:

1. **Leave it local.** The layer changes about once a year, and a lost state file costs one
   `terraform import` of five resources.
2. **Migrate it into the bucket it created** (`terraform init -migrate-state` after adding a
   backend block). Tidier, and it makes the bucket's `prevent_destroy` protect the record of
   its own existence.

Neither is chosen here, because the choice belongs to whoever runs it and both are defensible.
What is not defensible is committing the state file: it holds resource ids and the KMS key ARN,
and once it is in the history it is in the history.

## Validating it without an account

```bash
make tf-validate   # terraform validate, -backend=false, no credentials
make iac-scan      # checkov, zero findings
```

Both run offline. `terraform validate` reaches the provider registry and nothing else.
