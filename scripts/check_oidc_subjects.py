#!/usr/bin/env python3
"""Every OIDC subject the deploy role trusts names this repository and one environment.

`repo:owner/repo:*` trusts every branch and every pull request in the repository, including one
a stranger opens against a public fork. It reads as a small convenience right up until it is
the whole of the breach, and it is the single most consequential line in `infra/bootstrap`.

checkov's CKV_AWS_358 looks at this and reads only the first value of the condition list, so a
wildcard in the second slot passes its scan. This reads all of them.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OIDC = ROOT / "infra" / "bootstrap" / "oidc.tf"


def main() -> int:
    text = OIDC.read_text(encoding="utf-8")

    subjects = re.search(r"trusted_subjects\s*=\s*\[(.*?)\n\s*\]", text, re.S)
    if not subjects:
        print("no `trusted_subjects` local in infra/bootstrap/oidc.tf", file=sys.stderr)
        return 1
    body = subjects.group(1)

    problems = []
    if "*" in body:
        problems.append(
            "a wildcard appears in the trusted subjects. `repo:owner/repo:*` trusts every "
            "branch and every pull request, including one a stranger opens."
        )
    if "environment:" not in body:
        problems.append(
            "no subject names an environment. The environment is the whole of the "
            "authorisation — it is what a required reviewer is attached to."
        )
    if "github_owner" not in body or "github_repo" not in body:
        problems.append("a subject does not name both the owner and the repository.")

    # StringLike with no wildcard is not wrong, but it is one edit away from being wrong.
    if re.search(
        r'test\s*=\s*"StringLike"[^}]*?token\.actions\.githubusercontent\.com:sub', text, re.S
    ):
        problems.append(
            "the `sub` condition uses StringLike. There is no wildcard in the values, so the "
            "weaker operator buys nothing and lets one creep in unnoticed later. StringEquals."
        )

    if problems:
        print(
            "oidc-subjects: the deploy role's trust is wider than it should be\n", file=sys.stderr
        )
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print("oidc-subjects: every trusted subject names this repository and one environment")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
