#!/usr/bin/env python3
"""The scope, the state machine and Terraform must name the same legs.

**Three lists said different things and nothing compared them.** `ErasureScope.legs` declared
six. The state machine produced five — and called one of them by a different name. The refusal
was a five-way AND over `$.legs[0]` through `$.legs[4]`, which cannot notice a missing leg,
because the missing leg is what changes the count.

The consequence was not theoretical. `offline_store` was declared in the scope, had no branch,
was absent from the certificate, and was absent from the condition that decides whether to write
one. Four of a subject's feature rows survived an erasure that certified, and it took an
independent check against the estate to find them.

None of the three lists is wrong to exist. The scope is what the platform believes it must
reach; the state machine is what it does; Terraform holds the count the refusal compares against,
because HCL cannot import Python. What was wrong was that no two of them were ever compared.

Nothing here reaches AWS. It reads a Python dataclass, a JSON template and one HCL local.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from watermark.contracts import load  # noqa: E402
from watermark.erasure.scope import scope_from_contracts  # noqa: E402

TEMPLATE = ROOT / "infra" / "governance" / "erasure.asl.json.tftpl"
TERRAFORM = ROOT / "infra" / "governance" / "erasure.tf"

#: The leg that may be BOUNDED rather than completed, mirroring `certificate.BOUNDABLE`. It is
#: still a leg, still declared, and still has to appear in all three lists — what differs is what
#: confirming it means.
BOUNDABLE = "model_artefacts"


def _scope_legs() -> tuple[str, ...]:
    return scope_from_contracts(load()).legs


def _machine_legs() -> set[str]:
    """Every leg the state machine reports, read out of the rendered ASL.

    Read from the template rather than from a deployed machine on purpose: a check that needs an
    estate is a check that does not run on the pull request that breaks it.
    """
    text = TEMPLATE.read_text(encoding="utf-8")
    return set(re.findall(r'"leg"\s*:\s*"([a-z_]+)"', text))


def _terraform_legs() -> list[str]:
    """The `erasure_legs` local, which is what the refusal counts against."""
    text = TERRAFORM.read_text(encoding="utf-8")
    match = re.search(r"erasure_legs\s*=\s*\[(?P<body>[^\]]*)\]", text, re.S)
    if not match:
        return []
    return re.findall(r'"([a-z_]+)"', match.group("body"))


def _branch_count() -> int:
    """How many branches the parallel state runs, so a leg with no branch is visible.

    A leg name can appear in the template inside a `Catch` fallback without any work being done
    for it — that is how a leg reports `confirmed: false` — so counting names alone would accept
    a branch that exists only to fail. The branch count is the second half of the question.
    """
    text = TEMPLATE.read_text(encoding="utf-8")
    rendered = re.sub(r"\$\{[a-z_]+\}", "1", text)
    try:
        document = json.loads(rendered)
    except json.JSONDecodeError as error:
        raise SystemExit(f"the ASL template does not render to JSON: {error}") from error
    return len(document["States"]["EraseEveryLeg"]["Branches"])


def main() -> int:
    scope = _scope_legs()
    machine = _machine_legs()
    terraform = _terraform_legs()
    problems: list[str] = []

    missing = [leg for leg in scope if leg not in machine]
    if missing:
        problems.append(
            f"the scope declares {missing} and the state machine has no such leg. A declared leg "
            f"with no branch is a subject the erasure never reaches, on a certificate that says "
            f"it did — which is exactly what `offline_store` was."
        )

    extra = sorted(machine - set(scope))
    if extra:
        problems.append(
            f"the state machine reports {extra}, which the scope does not declare. Either the "
            f"scope is missing a leg or the machine is doing work nobody asked for; both are "
            f"worth resolving before an erasure certifies on it."
        )

    if list(terraform) != list(scope):
        problems.append(
            f"`local.erasure_legs` in erasure.tf is {terraform}, and the scope declares "
            f"{list(scope)}. The refusal counts against the Terraform list, so a stale copy is a "
            f"refusal that passes on the wrong number of legs."
        )

    branches = _branch_count()
    if branches != len(scope):
        problems.append(
            f"the parallel state runs {branches} branches for {len(scope)} declared legs. A leg "
            f"name can appear in a `Catch` fallback without any work being done for it, so the "
            f"names agreeing is not enough."
        )

    if BOUNDABLE not in scope:
        problems.append(
            f"`{BOUNDABLE}` is not among the declared legs. It is the one leg deletion cannot "
            f"complete, and dropping it would remove the residual window from the certificate — "
            f"which is the only place this system admits what it cannot do."
        )

    if problems:
        print(
            "erasure-legs: the scope, the state machine and Terraform disagree\n", file=sys.stderr
        )
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print(
        f"erasure-legs: {len(scope)} legs, named identically by the scope, the state machine's "
        f"{branches} branches and the count the refusal compares against"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
