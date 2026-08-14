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
#: **The energy, and nothing else — a lesson `evals/replay/` had already learned.**
#:
#: This began as four fields: the energy, the reading count, the duplicates suppressed and the
#: corrections absorbed. Against a live estate it reported 3,767 windows in disagreement, and
#: every single one of them agreed on the energy. What differed was `readings` 1 against 2 and
#: `corrections_absorbed` 0 against 1.
#:
#: The reason is that a second delivery into a *running* stream is not an independent run. Its
#: records land in windows the first delivery already closed, and the system absorbs them as
#: corrections — which is at-least-once delivery being handled exactly as claim 2 says it must
#: be. The counts moved because there genuinely were more deliveries.
#:
#: `value_fingerprint` in `evals/replay/` says this in as many words, and has since a failing
#: case forced it: "a harness that hashed the counts too would have reported claim 2 broken by
#: at-least-once delivery working correctly, and the fix would have been to stop counting". The
#: offline harness was right and this file did not carry the lesson across.
#:
#: What must not move is the number a customer is billed.
SETTLED_FIELDS: Final = ("energy_wh",)

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

#: One metering interval, in milliseconds. The grid every window start sits on.
INTERVAL_MILLIS: Final = 15 * 60 * 1000

#: How many interval steps either side of the median difference to try when aligning.
#:
#: Four covers a run gaining or losing an hour's worth of windows at either end, which is far
#: more than a compressed day's tail has ever moved. Widening it costs one set intersection per
#: step and buys nothing if the runs are further apart than that — at which point they are not
#: the same day and the overlap floor should say so rather than a search finding a coincidence.
ALIGNMENT_SEARCH: Final = 4


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
    """The final value of each window, keyed by meter and by the window's absolute instant.

    Absolute here, and aligned later by `align`. Three earlier attempts each tried to make the
    key itself comparable and each failed on a different asymmetry between the two runs:

    *The raw instant.* The publisher moves the day to end at the moment of the run, so two
    deliveries share not one key.

    *An offset from the run's own earliest or median interval.* One window closed by one run and
    not the other moves the anchor by a whole interval and displaces everything. The median over
    rows is weighted by how many meters published at each instant, which moves it for its own
    reason: a live run shared 5% of its windows on that key.

    *The window's rank within its meter's day.* Robust to a missing tail, which is what a
    compressed day usually loses — and wrong the moment a run gains a window at the *head*. It
    did: `M00002` came back with the first run's values shifted one place, `replay[1] == first[0]`
    all the way down.

    Every one of those tried to guess where the two runs differ. `align` stops guessing and
    measures.

    **Revisions collapse to the final value**, the same conclusion `tests_flink/` reached: how
    many times a total was restated is a fact about where batch boundaries fell, and what the
    meter measured is the last word.
    """
    final: dict[tuple[str, int], tuple[int, tuple[str, ...]]] = {}
    for row in rows:
        key = (str(row["meter"]), int(row["interval_start"]))
        revision = int(row["revision"])
        if key not in final or revision > final[key][0]:
            final[key] = (revision, tuple(str(row.get(field)) for field in SETTLED_FIELDS))
    return {key: value for key, (_, value) in final.items()}


def align(
    first: dict[tuple[str, int], tuple[str, ...]],
    second: dict[tuple[str, int], tuple[str, ...]],
) -> tuple[dict[tuple[str, int], tuple[str, ...]], int]:
    """Shift the replay onto the first run's grid, by finding the offset that lines them up.

    **The two runs are the same day at an unknown offset, so the offset is measured rather than
    assumed.** Every previous attempt picked a landmark — the earliest window, the median, the
    rank within a meter — and every one of them was a landmark that moves when the two runs close
    slightly different sets of windows. The offset that maximises agreement cannot move for that
    reason: it is chosen *because* it agrees.

    Candidates come from the difference between the two runs' medians, widened by a few interval
    steps either way, which covers a run gaining or losing windows at either end. The search is a
    handful of set intersections over a few thousand keys.

    Returning the offset as well as the shifted mapping is deliberate: a run whose best alignment
    is several intervals from the median difference is a run worth looking at, and the caller
    prints it.
    """
    if not first or not second:
        return second, 0

    grid = INTERVAL_MILLIS
    firsts = sorted(interval for _, interval in first)
    seconds = sorted(interval for _, interval in second)
    centre = firsts[len(firsts) // 2] - seconds[len(seconds) // 2]

    # **Not rounded to the grid**, and the first version was. `data/publish.py` shifts the day by
    # `now - day_end`, which is whatever the wall clock says — so two runs' windows do not sit on
    # a shared fifteen-minute grid at all, only on grids of their own that are parallel. Rounding
    # the candidate offset to a multiple of the interval therefore moved it *off* the answer by
    # up to seven minutes, and the search found 0% agreement while the two days differed by a
    # single window.
    #
    # The median difference is already the right offset whenever both medians land on the same
    # window of the day. The steps either side are for when they do not.
    best_offset, best_hits = 0, -1
    for step in range(-ALIGNMENT_SEARCH, ALIGNMENT_SEARCH + 1):
        offset = centre + step * grid
        hits = sum(1 for meter, interval in second if (meter, interval + offset) in first)
        if hits > best_hits:
            best_offset, best_hits = offset, hits

    return {
        (meter, interval + best_offset): value for (meter, interval), value in second.items()
    }, best_offset


def compare(
    first: dict[tuple[str, int], tuple[str, ...]],
    second: dict[tuple[str, int], tuple[str, ...]],
) -> list[str]:
    """The serious disagreement: the same events, the same window, two answers.

    **Only the windows both runs closed.** A window one run closed and the other did not is a
    real difference, but it is a difference of *coverage*, and reporting it here would have meant
    four thousand individual lines every time a batch boundary fell a window earlier. It is
    checked in `main` instead, as a proportion against `MINIMUM_OVERLAP`, before this is called —
    which is a stricter test than naming the first few would have been, because it refuses on the
    total rather than on a sample of it.

    That division of labour used to be stated here and not implemented: this function declared
    the two one-sided lists, never appended to either, and looped over them anyway. Twenty lines
    that read as a check and could not fail. The overlap floor was doing the work the whole time.
    """
    problems: list[str] = []

    disagreed = [key for key in set(first) & set(second) if first[key] != second[key]]
    for meter, interval in sorted(disagreed)[:NAMED_DISAGREEMENTS]:
        key = (meter, interval)
        before = dict(zip(SETTLED_FIELDS, first[key], strict=True))
        after = dict(zip(SETTLED_FIELDS, second[key], strict=True))
        problems.append(f"{meter} at {interval}: first run {before}, replay {after}")
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
    second, offset = align(first, second)
    print(f"the replay's day is offset by {offset / 1000:.0f}s from the first run's")

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
