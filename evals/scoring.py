"""The shape every harness reports in, so the scoreboard rows are comparable."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


@dataclass(frozen=True, slots=True)
class Case:
    """One labelled situation and the outcome it must produce."""

    name: str
    #: What the case is about, in a sentence somebody reading a failure needs.
    matters: str
    #: Returns an empty string when the expectation held, or the reason it did not.
    check: Callable[[], str]


@dataclass(frozen=True, slots=True)
class Score:
    passed: int
    failed: tuple[tuple[str, str], ...]

    @property
    def total(self) -> int:
        return self.passed + len(self.failed)

    @property
    def ok(self) -> bool:
        return not self.failed


def score(title: str, cases: Iterable[Case]) -> int:
    """Run every case, print the score, and return a process exit code."""
    listed = list(cases)
    failures: list[tuple[str, str]] = []

    print(f"{title}")
    for case in listed:
        problem = case.check()
        if problem:
            failures.append((case.name, problem))
            print(f"  {RED}FAIL{RESET}  {case.name}")
        else:
            print(f"  {GREEN}ok{RESET}    {case.name}")

    result = Score(len(listed) - len(failures), tuple(failures))
    print()
    for name, problem in result.failed:
        matters = next(case.matters for case in listed if case.name == name)
        print(f"{RED}FAILED{RESET} {name}\n  why it matters: {matters}\n  {DIM}{problem}{RESET}")
    print(f"{title}: {result.passed}/{result.total}")
    return 0 if result.ok else 1


def require(condition: bool, message: str) -> str:
    """`""` when the condition held, the message when it did not.

    A helper rather than a bare `assert`, because a harness that stops at the first failure
    reports one problem where there were four, and the fourth is usually the informative one.
    """
    return "" if condition else message


def first_problem(*problems: str) -> str:
    return next((problem for problem in problems if problem), "")


def exit_with(code: int) -> None:  # pragma: no cover — the CLI edge
    sys.exit(code)
