"""Claim 2 against the estate: the same day, delivered twice, must publish the same values.

**What the capture already asserted, and why it was not claim 2.** Every run checks that each
published row carries a lineage id, that the ids are distinct, and that every restatement names
what it replaced. Those are properties of *one* run. Claim 2 is a statement about *two*: the same
events, shuffled, duplicated and delivered late, produce byte-identical outputs. A single run
cannot exhibit that no matter how many invariants it satisfies — and `evals/replay/` proves it
over the pure core, which leaves exactly the gap this file closes. The core is deterministic; a
Kinesis shard, a Flink checkpoint, a Glue `MERGE` and an S3 listing are the parts that might not
be, and they are the parts the offline harness cannot reach.

**What is compared, and what deliberately is not.**

Compared: the published *values*. For every `(meter, interval, revision)` the energy in
watt-hours, the reading count, the duplicates suppressed and the corrections absorbed. Those are
the numbers a customer is billed on, and claim 2 is that a replay bills the same.

Not compared: the lineage ids. They are minted from the *delivery* — the ingestion instant and
the source — precisely so that two copies of one reading are distinguishable, which
`watermark.lineage.identity.of_reading` says in as many words. Two runs of the publisher deliver
at different wall-clock instants by construction, so demanding identical lineage ids would be
demanding that the second run pretend to be the first. What must be identical is the answer; what
must differ is the record of how it arrived.

Not compared either: `closed_at`. It is the watermark that permitted publication, which depends
on when the batches happened to fall relative to each other. Two runs closing the same window on
the same data at different instants is the system working, and asserting on it would make this a
test of the runner's pacing.

**A run that finds no rows fails.** An empty comparison agrees with itself perfectly, and a
harness that reports green over nothing is the failure this whole repository is written against.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Final

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: The fields whose agreement *is* claim 2.
#:
#: Named as a constant rather than inlined, because the interesting half of this harness is the
#: choice of what to compare — and a choice buried in a dict comprehension is a choice nobody
#: revisits. See the module docstring for what is excluded and why.
SETTLED_FIELDS: Final = ("energy_wh", "readings", "duplicates_suppressed", "corrections_absorbed")

#: How many disagreements to name individually before summarising the rest.
#:
#: A diff of four thousand rows is a diff nobody reads, and the first few are almost always the
#: same defect as the rest. The count is still reported in full — what is truncated is the
#: enumeration, not the number.
NAMED_DISAGREEMENTS: Final = 5

#: The number of distinct revisions a day must contain for the replay to have covered a
#: correction: revision 0 and revision 1 at least.
REVISIONS_WITH_A_RESTATEMENT: Final = 2


def _rows(directory: Path, exclude: set[str] | None = None) -> list[dict[str, Any]]:
    """Published rows from a landing directory, optionally skipping files seen before.

    **The exclusion is how the two runs are separated.** Both deliveries land in the same S3
    prefix, so a copy taken after the second publish contains the first run's files as well. The
    file names are unique per part file, so subtracting the ones already seen leaves exactly what
    the replay produced — no timestamps to reason about and nothing to clear from the bucket.
    """
    skip = exclude or set()
    rows: list[dict[str, Any]] = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.name in skip:
            continue
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not raw.strip().startswith("{"):
                continue
            try:
                row: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if row.get("kind") in {"published", "restated", "confirmed"}:
                rows.append(row)
    return rows


def published_values(rows: list[dict[str, Any]]) -> dict[tuple[str, int, int], tuple[str, ...]]:
    """Every published value, keyed by meter, **interval offset** and revision.

    **The offset, not the instant, and that is not a convenience.** `data/publish.py` moves the
    whole generated day so that it *ends at the moment of the run* — which is what makes a
    capture a replay rather than an archive, and what makes two runs land on two different sets
    of fifteen-minute boundaries. Keyed on the absolute instant, the two deliveries of the same
    day would share not one key, and the comparison would report every row missing from both
    sides while nothing at all was wrong.
    """
    if not rows:
        return {}
    origin = min(int(row["interval_start"]) for row in rows)
    values: dict[tuple[str, int, int], tuple[str, ...]] = {}
    for row in rows:
        key = (
            str(row["meter"]),
            int(row["interval_start"]) - origin,
            int(row["revision"]),
        )
        values[key] = tuple(str(row.get(field)) for field in SETTLED_FIELDS)
    return values


def compare(
    first: dict[tuple[str, int, int], tuple[str, ...]],
    second: dict[tuple[str, int, int], tuple[str, ...]],
) -> list[str]:
    """Every way two runs can disagree, reported as sentences rather than as a diff.

    Three kinds, and they mean different things. A key only the first run produced is a window
    the replay failed to close. A key only the second produced is a window the *first* failed to
    close, which is the same defect seen from the other side and is worth naming separately so
    nobody reads a one-sided report as "the replay lost data". A key both produced with different
    values is the serious one: the same events, the same window, two answers.
    """
    problems: list[str] = []

    missing = sorted(set(first) - set(second))
    extra = sorted(set(second) - set(first))
    for meter, interval, revision in missing[:NAMED_DISAGREEMENTS]:
        problems.append(
            f"{meter} interval {interval} revision {revision} was published by the first run "
            f"and not by the replay"
        )
    if len(missing) > NAMED_DISAGREEMENTS:
        problems.append(
            f"...and {len(missing) - NAMED_DISAGREEMENTS} more the replay did not publish"
        )
    for meter, interval, revision in extra[:NAMED_DISAGREEMENTS]:
        problems.append(
            f"{meter} interval {interval} revision {revision} was published by the replay and "
            f"not by the first run"
        )
    if len(extra) > NAMED_DISAGREEMENTS:
        problems.append(
            f"...and {len(extra) - NAMED_DISAGREEMENTS} more the first run did not publish"
        )

    disagreed = [key for key in set(first) & set(second) if first[key] != second[key]]
    for meter, interval, revision in sorted(disagreed)[:NAMED_DISAGREEMENTS]:
        key = (meter, interval, revision)
        before = dict(zip(SETTLED_FIELDS, first[key], strict=True))
        after = dict(zip(SETTLED_FIELDS, second[key], strict=True))
        problems.append(
            f"{meter} interval {interval} revision {revision}: first run {before}, replay {after}"
        )
    if len(disagreed) > NAMED_DISAGREEMENTS:
        problems.append(f"...and {len(disagreed) - NAMED_DISAGREEMENTS} more values that disagree")

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--first", type=Path, required=True, help="Landing files before the replay."
    )
    parser.add_argument("--after", type=Path, required=True, help="Landing files after it.")
    arguments = parser.parse_args(argv)

    seen = {path.name for path in arguments.first.rglob("*") if path.is_file()}
    first = published_values(_rows(arguments.first))
    second = published_values(_rows(arguments.after, exclude=seen))
    print(f"first run: {len(first)} published values; replay: {len(second)}")

    # Louder than a clean exit. Two empty sets agree perfectly, and a harness that reports green
    # over nothing is the exact failure this repository exists to argue against.
    if not first or not second:
        print("::error::one of the runs published nothing; there is no replay to compare")
        return 1

    problems = compare(first, second)
    revisions = Counter(revision for _, _, revision in first)
    print(f"revisions in the first run: {dict(sorted(revisions.items()))}")
    if len(revisions) < REVISIONS_WITH_A_RESTATEMENT:
        # A day with no restatement in it is a day that never exercised the interesting half.
        # Claim 2 is not only "the same totals twice" — it is that a *correction* replays the
        # same way, and a run where nothing was corrected proves the easy case only.
        print("::error::no restatement in the first run, so the replay never covered a correction")
        return 1

    if problems:
        for problem in problems:
            print(f"::error::{problem}")
        print(f"replay: {len(problems)} disagreements. Claim 2 is that there are none")
        return 1

    print(f"replay: {len(first)} published values identical across two deliveries of the same day")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
