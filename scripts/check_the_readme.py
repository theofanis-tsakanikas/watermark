#!/usr/bin/env python3
"""The README's figures are re-read and compared against what the repository says.

**Why this exists, and why it was written late.** Every claim in this repository is checked by
something. The README that *quotes* those checks was not, and it drifted three times in a single
day: nine eval harnesses reported as eight, six CI jobs reported as seven, three dbt tests
reported as two. None of them was a lie anybody told; each was a number that was true when it was
written and stopped being true when a directory gained a member. That is the direction scoreboards
always drift — towards looking more finished than they are — and it is silent, because adding a
harness is a good day's work and nobody re-reads the prose afterwards.

**What it does not do.** It does not re-run the expensive gates to check their totals. `gate-proof`
takes eight and a half minutes, and a check that costs that much runs rarely, which is the opposite
of what a drift detector is for. Where a figure has a cheap authoritative source in the repository
— the number of mutations *declared* in `gate_proof.py`, the number of directories under `evals/`,
the number of jobs in `ci.yml` — that source is read directly. Where it does not, the harness is
run, and every one of those runs in under three seconds.

**The failure mode this is built against.** A figure reader that matches nothing must not report
success. It has three outcomes, not two: the figure agrees, the figure disagrees, or *the figure
could not be found at all* — which means the prose moved and the reader is now checking nothing.
The third is reported **STALE** and fails the run, because a reader that silently matches nothing
is exactly how a scoreboard check goes quiet while the scoreboard it guards goes wrong.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
README = REPOSITORY / "README.md"

GREEN, RED, AMBER, DIM, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


@dataclass(frozen=True, slots=True)
class Figure:
    """One number the README states, and where the truth about it lives."""

    #: What this figure is, for the report.
    name: str
    #: A regex over the README with exactly one capturing group: the figure as written.
    pattern: str
    #: The value the repository actually has.
    actual: str
    #: Why the README is allowed to say this at all.
    source: str


def _text() -> str:
    return README.read_text(encoding="utf-8")


def _run(*command: str) -> str:
    """Run something in the repository and return its output, stdout and stderr together."""
    done = subprocess.run(  # noqa: S603 — fixed command lists, no shell
        command, cwd=REPOSITORY, capture_output=True, text=True, check=False
    )
    return done.stdout + done.stderr


def _python() -> str:
    venv = REPOSITORY / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def _scored(module: str) -> str:
    """The `n/m` a claim harness prints on its last line."""
    tail = _run(_python(), "-m", module).strip().splitlines()
    if not tail:
        return "the harness printed nothing"
    match = re.search(r"(\d+/\d+)\s*$", tail[-1])
    return match.group(1) if match else f"unreadable: {tail[-1][:60]}"


def _declared_mutations() -> str:
    """Counted from the source rather than from a run. `gate-proof` takes eight minutes; the
    number of mutations it *would* run is a fact in the file, and the harness's own report of
    `0 accepted, 0 stale` is what `make gate-proof` is for."""
    source = (REPOSITORY / "scripts" / "gate_proof.py").read_text(encoding="utf-8")
    body = source.split("MUTATIONS", 1)[-1]
    return str(len(re.findall(r"^    Mutation\(", body, re.M)))


def _ci_jobs() -> str:
    workflow = (REPOSITORY / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    jobs = workflow.split("\njobs:", 1)[-1]
    return str(len(re.findall(r"^  [a-z0-9][a-z0-9-]*:$", jobs, re.M)))


def _figures() -> list[Figure]:
    evals = REPOSITORY / "evals"
    harnesses = sorted(d.name for d in evals.iterdir() if d.is_dir() and not d.name.startswith("_"))
    words = {
        1: "one",
        2: "two",
        3: "three",
        4: "four",
        5: "five",
        6: "six",
        7: "seven",
        8: "eight",
        9: "nine",
        10: "ten",
        11: "eleven",
        12: "twelve",
    }

    def word(count: int) -> str:
        """A count the README spells out. Beyond the table the figure is written as digits,
        and a `KeyError` here would be this check failing on its own arithmetic rather than
        on a drift — which is the one failure it must never produce."""
        return words.get(count, str(count))

    tests = _run(_python(), "-m", "pytest")
    passing = re.search(r"(\d+) passed", tests)

    seed = _run(_python(), "scripts/seed_check.py")
    deliveries = re.search(r"([\d,]+) deliveries", seed)

    policy = _run(_python(), "scripts/check_policy_access.py")
    pairs = re.search(r"(\d+) principal-resource pairs", policy)

    from_cast = _run(
        _python(),
        "-c",
        "from data.cast import meter_assignments, SUBSTATIONS, METERS;"
        "print(len({v.attributes['customer_id'] for v in meter_assignments().versions}),"
        "len(METERS), len(SUBSTATIONS))",
    ).split()

    return [
        Figure(
            "tests passing",
            r"tests-(\d+)%20passing",
            passing.group(1) if passing else "the suite printed no summary",
            "`pytest`",
        ),
        Figure(
            "tests passing, in prose",
            r"\*\*(\d+) tests\*\* — offline",
            passing.group(1) if passing else "the suite printed no summary",
            "`pytest`",
        ),
        Figure(
            "planted gate violations",
            r"gate--proof-(\d+)%20refused",
            _declared_mutations(),
            "`Mutation(` entries in scripts/gate_proof.py",
        ),
        Figure(
            "planted gate violations, in prose",
            r"(\d+) planted gate violations",
            _declared_mutations(),
            "`Mutation(` entries in scripts/gate_proof.py",
        ),
        Figure(
            "claim harnesses",
            # Anchored on the *phrase* beside it, not on the figure in it. Anchoring on the
            # neighbouring number would go STALE the day that number legitimately moves; not
            # anchoring at all is worse, and was tried: `(\w+) claim harnesses` alone matched a
            # second sentence further down the file the moment this one was reworded, so the
            # reader silently moved house and reported a drift in a figure nobody had touched.
            r"(\w+) claim harnesses and \d+ planted gate violations",
            word(len(harnesses)),
            f"directories under evals/ ({', '.join(harnesses)})",
        ),
        Figure(
            "CI jobs",
            r"runs (\w+) jobs on every push",
            word(int(_ci_jobs())),
            "job keys in .github/workflows/ci.yml",
        ),
        Figure(
            "decision records",
            r"(\w+) decision records in",
            word(len(list((REPOSITORY / "docs" / "adr").glob("*.md")))),
            "files under docs/adr/",
        ),
        Figure(
            "Terraform layers",
            r"\| (\w+) Terraform layers, state isolated per layer",
            word(len([d for d in (REPOSITORY / "infra").iterdir() if d.is_dir()])),
            "directories under infra/",
        ),
        Figure(
            "dbt tests",
            r"the gold models with the (\w+) tests that matter",
            word(len(list((REPOSITORY / "pipelines" / "dbt" / "tests").glob("*.sql")))),
            "files under pipelines/dbt/tests/",
        ),
        Figure(
            "checkov exceptions",
            r"with \*\*(\d+)\*\* deliberate exceptions",
            str(
                sum(
                    path.read_text(encoding="utf-8").count("checkov:skip")
                    for path in (REPOSITORY / "infra").rglob("*.tf")
                )
            ),
            "`checkov:skip` comments under infra/",
        ),
        Figure(
            "seeded deliveries",
            r"\*\*([\d,]+) deliveries\*\* reproduce",
            deliveries.group(1) if deliveries else "seed-check printed no count",
            "`scripts/seed_check.py`",
        ),
        Figure(
            "principal-resource pairs",
            r"\*\*4 principals, (\d+) principal-resource pairs\*\*",
            pairs.group(1) if pairs else "check_policy_access printed no count",
            "`scripts/check_policy_access.py`",
        ),
        Figure(
            "data subjects",
            r"(\w+)-one subjects here",
            "forty" if from_cast and from_cast[0] == "41" else f"not 41: {from_cast[:1]}",
            "distinct customer ids in data.cast.meter_assignments()",
        ),
        Figure(
            "meters in the generated day",
            r"while the generated day is (\d+)",
            from_cast[1] if len(from_cast) > 1 else "the cast could not be read",
            "data.cast.METERS",
        ),
        Figure(
            "claim 1",
            r"`make claim-1` \| \*\*(\d+/\d+)\*\*",
            _scored("evals.watermark"),
            "`make claim-1`",
        ),
        Figure(
            "claim 2",
            r"`make claim-2` \| \*\*(\d+/\d+)\*\*",
            _scored("evals.replay"),
            "`make claim-2`",
        ),
        Figure(
            "claim 3",
            r"`make claim-3` \| \*\*(\d+/\d+)\*\*",
            _scored("evals.parity"),
            "`make claim-3`",
        ),
        Figure(
            "claim 4",
            r"`make claim-4` \| \*\*(\d+/\d+)\*\*",
            _scored("evals.freshness"),
            "`make claim-4`",
        ),
        Figure(
            "claim 5",
            r"`make claim-5` \| \*\*(\d+/\d+)\*\*",
            _scored("evals.promotion"),
            "`make claim-5`",
        ),
        Figure(
            "claim 6",
            r"`make claim-6` \| \*\*(\d+/\d+)\*\*",
            _scored("evals.erasure"),
            "`make claim-6`",
        ),
        Figure(
            "claim 7",
            r"`make claim-7` \| \*\*(\d+/\d+)\*\*",
            _scored("evals.oversight"),
            "`make claim-7`",
        ),
        Figure(
            "the declared cases",
            r"\*\*(\d+/\d+)\*\* offline · \*\*7/7\*\* against the deployed estate",
            _scored("evals.cases"),
            "`make cases`",
        ),
    ]


def _same(stated: str, actual: str) -> bool:
    """The README is prose: a count can open a sentence in title case, and a four-figure number
    carries a thousands separator. Neither is a drift, and treating them as one would train
    somebody to ignore this check — which is the only way it can actually fail."""
    return stated.replace(",", "").casefold() == actual.replace(",", "").casefold()


def main() -> int:
    if not README.exists():
        print(f"{RED}there is no README.md to check{OFF}")
        return 1

    text = _text()
    agreed = disagreed = stale = 0

    for figure in _figures():
        found = re.search(figure.pattern, text, re.M)
        if not found:
            print(
                f"  {AMBER}STALE{OFF}  {figure.name:32} "
                f"the README no longer states this figure where this check reads it"
            )
            print(f"         {DIM}pattern: {figure.pattern}{OFF}")
            stale += 1
            continue

        stated = found.group(1)
        if _same(stated, figure.actual):
            print(f"  {GREEN}ok{OFF}     {figure.name:32} {stated}  {DIM}{figure.source}{OFF}")
            agreed += 1
        else:
            print(
                f"  {RED}drift{OFF}  {figure.name:32} "
                f"README says {stated}, {figure.source} says {figure.actual}"
            )
            disagreed += 1

    print()
    verdict = f"the-readme: {agreed} agree, {disagreed} drifted, {stale} stale"
    if disagreed or stale:
        print(f"{RED}{verdict}{OFF}")
        if stale:
            print(
                f"{DIM}A stale figure is not a pass. It means the prose moved and this check is "
                f"reading nothing — repoint the pattern, or remove the figure.{OFF}"
            )
        return 1
    print(f"{GREEN}{verdict}{OFF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
