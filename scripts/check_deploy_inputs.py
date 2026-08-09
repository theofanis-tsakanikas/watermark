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

    if problems:
        print("deploy-inputs: a required variable is not supplied\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print(f"deploy-inputs: {checked} required variables, all supplied by deploy.yml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
