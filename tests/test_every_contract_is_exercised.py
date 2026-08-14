"""A contract nothing reads is a document, and this is how one gets there.

`settlement_publication` was declared before the settlement code existed. The totals were
computed, the restatements were written, and for the whole of that time nothing in the
repository named the contract they were supposed to satisfy. Nothing failed — that is the
point. The contract layer is designed to refuse a decision that violates its declaration, and
it has no opinion at all about a declaration nobody consults.

So the coverage is checked the same way `test_gates_are_attacked.py` checks its own: by
enumerating what exists rather than by keeping a list, because a list is exactly the artefact
that looks complete by being the list.
"""

from __future__ import annotations

import ast
from pathlib import Path

import yaml

REPOSITORY = Path(__file__).resolve().parents[1]
DECISIONS = REPOSITORY / "contracts" / "decisions"
#: Where a contract is *held to*, as opposed to merely used or mentioned. Not `src/` — the
#: engine consuming a contract is not the engine being checked against it — and not `scripts/`,
#: which contains `gate_proof.py`, whose whole job is to name things in order to break them.
SEARCHED = ("evals", "tests")


def _declared() -> set[str]:
    ids = set()
    for path in DECISIONS.glob("*.yaml"):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        ids.add(str(document["id"]))
    return ids


def _docstrings(tree: ast.Module) -> set[int]:
    """The id() of every node that is a docstring, so prose can be told from code.

    The first version of this guard read the files as text, and passed while the contract was
    named *only* in the paragraph of a docstring explaining that nothing named it. A comment
    about coverage is not coverage.
    """
    marked = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                marked.add(id(first.value))
    return marked


def _literals() -> set[str]:
    """Every string literal in a harness that is not a docstring."""
    found: set[str] = set()
    for directory in SEARCHED:
        for path in (REPOSITORY / directory).rglob("*.py"):
            if "__pycache__" in path.parts or path.name == Path(__file__).name:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            prose = _docstrings(tree)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and id(node) not in prose
                ):
                    found.add(node.value)
    return found


def _mentioned() -> set[str]:
    literals = _literals()
    return {name for name in _declared() if name in literals}


def test_every_decision_contract_is_exercised_somewhere() -> None:
    unexercised = _declared() - _mentioned()
    assert not unexercised, (
        f"decision contracts nothing checks: {sorted(unexercised)}. A contract that declares a "
        f"fallback, an actuation policy and a legal posture, and that no harness ever reads, is "
        f"a document. Add a case that holds the code to it."
    )


def test_there_are_three_decisions_and_the_count_is_deliberate() -> None:
    """Not a coverage number — a statement about the domain.

    The scenario has exactly three decisions and they were chosen because they sit at different
    points on every axis that matters: seconds against days, high-risk against commercial,
    automatic against human-gated. A fourth appearing without that argument being made again is
    worth a moment's thought, and a third disappearing is worth more than a moment's.
    """
    assert _declared() == {"curtailment", "meter_anomaly", "settlement_publication"}
