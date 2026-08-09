#!/usr/bin/env python3
"""Every Terraform variable with no default is supplied by the deploy workflow.

A missing `TF_VAR_` does not fail at plan time in automation — `TF_INPUT=false` makes Terraform
error, but it errors *inside the apply loop*, four minutes in, with an earlier layer already
created. That is the expensive place, and it is exactly the failure this repository keeps
saying it moves earlier.

The two lists compared are the variables the layers declare and the environment the workflow
sets. Nothing here reaches AWS.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"

#: Layers a deploy never applies. Bootstrap is applied once from a laptop by a person who is
#: given the chance to answer a prompt.
NOT_DEPLOYED = {"bootstrap"}

#: The only two repository variables any workflow may read.
#:
#: Everything else a deploy needs is a consequence of a name `infra/bootstrap` already chose, so
#: it is published to SSM and resolved after the role is assumed. These two cannot be: CI has to
#: know *which* account and *which* region before it can ask that account anything, and reading
#: a parameter is already asking.
#:
#: The rule exists because a transcribed value is indistinguishable from an independent setting.
#: Rename the state bucket and a copied `TF_STATE_BUCKET` becomes a deploy that fails on a
#: backend nobody can find, with the fix in a settings page rather than in a diff — and nothing
#: in the repository would have gone red first.
RESOLVABLE_FROM_AWS = {"AWS_ACCOUNT_ID", "AWS_REGION"}


def _variables_without_defaults(layer: Path) -> set[str]:
    """Brace-counted rather than pattern-matched.

    A regex ending at the first `\\n}` stops at the close of a nested `validation` block, so a
    variable with validation *and* no default reads as though it had one — which is a false
    negative in a check whose whole job is to catch a missing input.
    """
    found: set[str] = set()
    for path in sorted(layer.glob("*.tf")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r'variable "([a-z_]+)"\s*\{', text):
            name, depth, index = match.group(1), 0, match.end() - 1
            while index < len(text):
                if text[index] == "{":
                    depth += 1
                elif text[index] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                index += 1
            body = text[match.end() : index]
            # `default` at the top level of the block only. A `default` inside a nested block
            # would not be one.
            if not re.search(r"^\s{2}default\s*=", body, re.M):
                found.add(name)
    return found


def main() -> int:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    problems: list[str] = []
    checked = 0

    for layer in sorted(path for path in (ROOT / "infra").iterdir() if path.is_dir()):
        if layer.name in NOT_DEPLOYED:
            continue
        for name in sorted(_variables_without_defaults(layer)):
            checked += 1
            if f"TF_VAR_{name}" not in workflow:
                problems.append(
                    f"{layer.name}: `{name}` has no default and deploy.yml does not set "
                    f"TF_VAR_{name}. The apply fails inside the loop, with an earlier layer "
                    "already created."
                )

    # The other half: nothing may be transcribed that the account can be asked for.
    transcribed: set[str] = set()
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        used = set(re.findall(r"vars\.([A-Z_]+)", path.read_text(encoding="utf-8")))
        transcribed |= used
        for name in sorted(used - RESOLVABLE_FROM_AWS):
            problems.append(
                f"{path.name}: reads `vars.{name}`. Only {sorted(RESOLVABLE_FROM_AWS)} may be "
                "repository variables — everything else is published by infra/bootstrap and "
                "resolved after the role is assumed, so it cannot drift from the layer that "
                "chose it."
            )

    if problems:
        print("deploy-inputs: the deploy wiring is wrong\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print(
        f"deploy-inputs: {checked} required variables all supplied; "
        f"{len(transcribed)} repository variables set by hand "
        f"({', '.join(sorted(transcribed)) or 'none'}), none of them resolvable from AWS"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
