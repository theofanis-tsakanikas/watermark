"""Lake Formation tag policy, authored in the repository and evaluated offline.

The same arrangement Attestor uses for Cedar, and for the same reason: the deployed grants and
the offline evaluator must read the **same bytes**, or the suite is checking a policy that is
not the one in force.

`policy/tags.yaml` is that file. Terraform applies it; `evaluate()` here answers "may this
principal read this resource?" without an AWS account; and
`scripts/check_policy_matches_terraform.py` fails the build when the two drift.

What the evaluator is *not* is a re-implementation of Lake Formation. It models one thing —
tag-expression matching, which is how a grant selects resources — and it models it exactly. A
fuller emulation would be a second system to be wrong in.
"""

from __future__ import annotations

from watermark.policy.evaluator import Grant, Policy, Reachable, load_policy

__all__ = ["Grant", "Policy", "Reachable", "load_policy"]
