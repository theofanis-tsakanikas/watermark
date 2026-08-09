# What this account is, recorded where the next apply will read it.
#
# Bootstrap is the one layer applied by hand, so its inputs are the one place a value can be
# retyped differently each time and nobody finds out. Everything here is a durable fact about
# the deployment target rather than a preference, which is why it is committed instead of
# living in somebody's shell history.
#
# `budget_alert_email` is deliberately absent. It is the address an alarm rings at, it belongs
# to a person rather than to the account, and this repository is a portfolio piece that may not
# stay private. Pass it on the command line:
#
#   terraform -chdir=infra/bootstrap apply -var="budget_alert_email=you@example.com"

github_owner = "theofanis-tsakanikas"
github_repo  = "watermark"

# The ids behind those names. GitHub's subject claim can carry either form, and a name can be
# released and re-registered by somebody else while an id cannot — so the trust accepts both and
# these are what make the second form possible:
#
#   gh api users/theofanis-tsakanikas --jq .id
#   gh api repos/theofanis-tsakanikas/watermark --jq .id
github_owner_id      = "218610429"
github_repository_id = "1328461417"

# An account holds one OIDC provider per issuer URL, and one for GitHub already federated into
# this account on 2026-07-04 for a sibling project. Adopt it rather than create a second: the
# apply would fail with `EntityAlreadyExists` *after* creating the buckets and the key, leaving
# a half-built bootstrap. Recorded here so the next apply does not rediscover it the hard way.
create_oidc_provider = false
