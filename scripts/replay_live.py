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

**Three sets, not two — and the live run that showed why.** The landing prefix accumulates across
every capture the estate has ever driven, so a copy taken "before the replay" contains months of
history as well as this capture's first delivery. The first live run reported
`first run: 9664 published values; replay: 4062` and named four thousand rows the replay had
supposedly lost. It had lost nothing: 9,664 was every capture ever run, and the offset
normalisation below was computed against the oldest interval in the bucket rather than against
this run's own first. The caller must therefore pass what existed *before the capture started* as
well, so history can be subtracted from both sides. `capture.yml` records it at the top of the
run.

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

#: How much of the two deliveries must be the same windows before comparing them means anything.
#:
#: A compressed day loses a window or two at the tail — whether the last batch arrives before the
#: publisher stops is timing, not behaviour — so the sets are never exactly equal. The floor is
#: what stops that allowance from covering a regression: a change that published almost nothing
#: would otherwise compare its two remaining windows and report claim 2 proved.
MINIMUM_OVERLAP: Final = 0.95


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


def published_values(rows: list[dict[str, Any]]) -> dict[tuple[str, int], tuple[str, ...]]:
    """The final value of each window, keyed by meter and by the window's position in that
    meter's own day.

    **Three anchors were tried and the first two were wrong in instructive ways.**

    *The absolute instant.* `data/publish.py` moves the whole generated day so that it ends at the
    moment of the run — which is what makes a capture a replay rather than an archive — so two
    deliveries land on two different sets of fifteen-minute boundaries and share not one key. The
    comparison reported every row missing from both sides while nothing was wrong.

    *The run's earliest interval.* Subtracting a per-run anchor recovers a comparable position,
    and the minimum is the fragile choice: one window closed at the edge by one run and not the
    other moves that run's minimum by a whole interval and displaces every key. Two runs two
    windows apart reported four thousand missing. The median over rows is no better — it is
    weighted by how many meters published at each instant, so it moves for a different reason.

    *The window's position within its own meter's day*, which is what this uses. Windows close in
    order and what a compressed day loses is the tail, so ranks align from the front and a missing
    last window costs one key rather than shifting all of them. It assumes losses are at the end;
    the overlap floor in `main` is what catches the case where they are not.

    **Revisions collapse to the final value.** How many times a total was restated is a fact about
    where the batch boundaries fell — the same conclusion `tests_flink/` reached comparing the
    core against Flink. What the meter measured is the last word, and it is what settlement reads.
    """
    if not rows:
        return {}

    by_meter: dict[str, dict[int, tuple[int, tuple[str, ...]]]] = {}
    for row in rows:
        meter = str(row["meter"])
        interval = int(row["interval_start"])
        revision = int(row["revision"])
        values = tuple(str(row.get(field)) for field in SETTLED_FIELDS)
        seen = by_meter.setdefault(meter, {})
        if interval not in seen or revision > seen[interval][0]:
            seen[interval] = (revision, values)

    final: dict[tuple[str, int], tuple[str, ...]] = {}
    for meter, intervals in by_meter.items():
        for rank, interval in enumerate(sorted(intervals)):
            final[(meter, rank)] = intervals[interval][1]
    return final


def compare(
    first: dict[tuple[str, int], tuple[str, ...]],
    second: dict[tuple[str, int], tuple[str, ...]],
) -> list[str]:
    """Every way two runs can disagree, reported as sentences rather than as a diff.

    Three kinds, and they mean different things. A key only the first run produced is a window
    the replay failed to close. A key only the second produced is a window the *first* failed to
    close, which is the same defect seen from the other side and is worth naming separately so
    nobody reads a one-sided report as "the replay lost data". A key both produced with different
    values is the serious one: the same events, the same window, two answers.
    """
    problems: list[str] = []

    # Only the windows both runs closed. What each closed and the other did not is reported by
    # the overlap floor in `main`, as a proportion rather than as four thousand individual lines.
    missing: list[tuple[str, int]] = []
    extra: list[tuple[str, int]] = []
    for meter, rank in missing[:NAMED_DISAGREEMENTS]:
        problems.append(f"{meter} window {rank} was published by the first run and not the replay")
    if len(missing) > NAMED_DISAGREEMENTS:
        problems.append(
            f"...and {len(missing) - NAMED_DISAGREEMENTS} more the replay did not publish"
        )
    for meter, rank in extra[:NAMED_DISAGREEMENTS]:
        problems.append(f"{meter} window {rank} was published by the replay and not the first run")
    if len(extra) > NAMED_DISAGREEMENTS:
        problems.append(
            f"...and {len(extra) - NAMED_DISAGREEMENTS} more the first run did not publish"
        )

    disagreed = [key for key in set(first) & set(second) if first[key] != second[key]]
    for meter, rank in sorted(disagreed)[:NAMED_DISAGREEMENTS]:
        key = (meter, rank)
        before = dict(zip(SETTLED_FIELDS, first[key], strict=True))
        after = dict(zip(SETTLED_FIELDS, second[key], strict=True))
        problems.append(f"{meter} window {rank}: first run {before}, replay {after}")
    if len(disagreed) > NAMED_DISAGREEMENTS:
        problems.append(f"...and {len(disagreed) - NAMED_DISAGREEMENTS} more values that disagree")

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--before",
        type=Path,
        required=True,
        help="A file listing the part files that existed before this capture started.",
    )
    parser.add_argument(
        "--first", type=Path, required=True, help="Landing files before the replay."
    )
    parser.add_argument("--after", type=Path, required=True, help="Landing files after it.")
    arguments = parser.parse_args(argv)

    # Three sets, and each subtraction removes a different thing. `history` is every capture this
    # estate has ever driven; taking it out of `--first` leaves this run's first delivery. Taking
    # everything in `--first` out of `--after` leaves the replay.
    history = {
        name.strip()
        for name in arguments.before.read_text(encoding="utf-8").splitlines()
        if name.strip()
    }
    seen = history | {path.name for path in arguments.first.rglob("*") if path.is_file()}
    first = published_values(_rows(arguments.first, exclude=history))
    second = published_values(_rows(arguments.after, exclude=seen))
    print(f"first run: {len(first)} published values; replay: {len(second)}")

    # Louder than a clean exit. Two empty sets agree perfectly, and a harness that reports green
    # over nothing is the exact failure this repository exists to argue against.
    if not first or not second:
        print("::error::one of the runs published nothing; there is no replay to compare")
        return 1

    # **The overlap floor, and why the comparison is over the intersection.**
    #
    # Two runs of a compressed day do not close exactly the same set of windows: the last batch
    # before the publisher stops may or may not arrive in time, so each run has a window or two
    # the other does not. That is the boundary moving, not the arithmetic changing.
    #
    # A floor is what stops that allowance from covering a regression. Without it, a change that
    # published almost nothing would compare its two remaining windows, find them equal, and
    # report claim 2 proved.
    shared = set(first) & set(second)
    overlap = len(shared) / max(len(first), len(second))
    if overlap < MINIMUM_OVERLAP:
        print(
            f"::error::the two deliveries share only {overlap:.0%} of their published windows. "
            f"A handful at the edges is the batch boundary; this many means they are not the "
            f"same day"
        )
        return 1

    problems = compare(first, second)
    revisions = Counter(int(row["revision"]) for row in _rows(arguments.first, exclude=history))
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
