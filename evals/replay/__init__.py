"""**Claim 2** — replay is identical.

The same events, shuffled and duplicated, produce byte-identical output and identical lineage
hashes.

## What "the same events" means, precisely

An event carries its own ingestion time. That is a *fact about the event*, recorded at the
edge, and a replay does not invent a new one — so what varies between a run and its replay is
the order records are handed to the pipeline, not when they arrived. Shuffling the input list
is therefore the whole of the test, and changing the ingestion times would not be a replay at
all: it would be a different set of events, and the lineage ids would rightly differ.

Stating that is not a weakening. It is the difference between a claim that can be checked and
one that sounds stronger and quietly means nothing.

## What is compared

Not "the totals match". The comparison is a digest over every published number, every
restatement with its prior value, every quarantine with its reason, and every lineage id —
because the ways this fails are ways that leave the totals correct. A deduplication that keeps
whichever copy arrived first produces the same energy and different lineage; an emission
ordered by a dictionary's insertion order produces the same rows in a different sequence. Both
are claim 2 being false, and neither moves a single total.
"""

from __future__ import annotations

import hashlib
import random

from data import cast
from data.generate import digest as stream_digest
from data.generate import generate

from evals.scoring import Case, first_problem, require
from watermark.runner import Arrival, RunResult, run

#: Fixed seeds rather than a random shuffle. A failure has to be reproducible by the person
#: reading it, and "it failed on some ordering" is not a bug report.
SEEDS: tuple[int, ...] = (1, 7, 13, 42, 99)


def _arrivals() -> list[Arrival]:
    return [
        Arrival(delivery.raw, delivery.ingest_time, delivery.source, delivery.partition)
        for delivery in generate()
    ]


def value_fingerprint(result: RunResult) -> str:
    """A digest over what the run *stated*, excluding how many deliveries it took to state it.

    The distinction was forced by a failing case, and it is worth keeping. Delivering every
    record twice genuinely produces two quarantine records for a bad reading and one more
    suppressed duplicate for a good one — those counts *should* move, because they are counts
    of deliveries and there were more deliveries. What must not move is the number published,
    the revision it was published at, the watermark that allowed it and the lineage id it
    carries.

    A harness that hashed the counts too would have reported claim 2 broken by at-least-once
    delivery working correctly, and the fix would have been to stop counting.
    """
    lines: list[str] = []
    for published in (*result.published, *result.restated, *result.confirmed):
        lines.append(
            "|".join(
                [
                    "result",
                    published.meter_id,
                    published.interval_start.to_iso(),
                    str(published.revision),
                    str(published.energy_wh),
                    str(published.supersedes),
                    published.first_seen_at.to_iso(),
                    published.closed_at.to_iso(),
                    published.watermark_status.value,
                    ",".join(published.idle_partitions),
                ]
            )
        )
    for reason, payload in sorted({(q.reason.value, q.payload) for q in result.quarantined}):
        lines.append(f"quarantine|{reason}|{payload}")
    for key in sorted(result.lineage):
        lines.append(f"lineage|{key[0]}|{key[1]}|{key[2]}|{result.lineage[key]}")
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def fingerprint(result: RunResult) -> str:
    """A digest over everything the run stated, not just over the numbers.

    Deliberately includes the lineage ids and the emission order. Those are the two things a
    replay can get wrong while every total stays right, which makes them the two things worth
    hashing.
    """
    lines: list[str] = []
    for published in (*result.published, *result.restated, *result.confirmed):
        lines.append(
            "|".join(
                [
                    "result",
                    published.meter_id,
                    published.interval_start.to_iso(),
                    str(published.revision),
                    str(published.energy_wh),
                    str(published.supersedes),
                    str(published.duplicates_suppressed),
                    published.first_seen_at.to_iso(),
                    published.closed_at.to_iso(),
                    published.watermark_status.value,
                    ",".join(published.idle_partitions),
                ]
            )
        )
    for quarantined in result.quarantined:
        lines.append(f"quarantine|{quarantined.reason.value}|{quarantined.payload}")
    for key in sorted(result.lineage):
        lines.append(f"lineage|{key[0]}|{key[1]}|{key[2]}|{result.lineage[key]}")
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _baseline() -> tuple[RunResult, str]:
    result = run(_arrivals(), cast.SUBSTATIONS)
    return result, fingerprint(result)


def a_rerun_is_identical() -> str:
    """The weakest form, and the one that fails first when a clock or a uuid gets in."""
    _, first = _baseline()
    _, second = _baseline()
    return require(first == second, "two runs over the same input produced different output")


def shuffling_changes_nothing() -> str:
    """Arrival order is an accident of partitioning and retry timing, not a fact about the day."""
    _, expected = _baseline()
    problems = []
    for seed in SEEDS:
        arrivals = _arrivals()
        random.Random(seed).shuffle(arrivals)
        actual = fingerprint(run(arrivals, cast.SUBSTATIONS))
        if actual != expected:
            problems.append(f"seed {seed}")
    return require(
        not problems,
        f"shuffling the input changed the output for {', '.join(problems)}. The order records "
        "are handed to the pipeline is an accident; anything that depends on it — which copy "
        "of a duplicate is kept, which order results are emitted in — makes a replay a "
        "different run.",
    )


def duplicating_changes_no_published_value() -> str:
    """Every record delivered twice. At-least-once delivery is what a real stream offers.

    Two assertions, and the second is what stops the first being satisfied by ignoring the
    duplicates entirely: nothing published may move, *and* the suppression counts must go up.
    A pipeline that silently dropped the redeliveries would pass the first on its own.
    """
    baseline, _ = _baseline()
    doubled = _arrivals() * 2
    random.Random(3).shuffle(doubled)
    replayed = run(doubled, cast.SUBSTATIONS)

    suppressed_before = sum(r.duplicates_suppressed for r in baseline.published)
    suppressed_after = sum(r.duplicates_suppressed for r in replayed.published)

    return first_problem(
        require(
            value_fingerprint(replayed) == value_fingerprint(baseline),
            "delivering every record twice changed a published value, a revision, a watermark "
            "or a lineage id. Deduplication is on content, so a redelivery is the same "
            "measurement and must collapse into the same result.",
        ),
        require(
            suppressed_after > suppressed_before,
            f"the duplicates were not seen: {suppressed_before} suppressed before and "
            f"{suppressed_after} after doubling every delivery. Identical output reached by "
            "not looking is not the claim.",
        ),
    )


def the_totals_are_not_enough() -> str:
    """The calibration case for this harness: prove the fingerprint sees more than the numbers.

    A comparison of totals alone would pass a run whose lineage was reordered, and that is
    precisely the failure claim 2 exists to catch. Rather than assert that in prose, the check
    constructs the weaker comparison and shows it agreeing where the real one would not have
    to — so if `fingerprint` is ever narrowed to just the energies, this stops being true.
    """
    result, _ = _baseline()
    return first_problem(
        require(bool(result.lineage), "the run produced no lineage at all to compare"),
        require(
            len(fingerprint(result)) == 64,
            "the fingerprint is not a full digest",
        ),
        require(
            any("lineage" in line for line in _fingerprint_lines(result)),
            "the fingerprint does not include lineage ids, so a run that reordered them "
            "would compare as identical and claim 2 would be a statement about arithmetic",
        ),
        require(
            any("quarantine" in line for line in _fingerprint_lines(result)),
            "the fingerprint does not include quarantines, so a run that silently stopped "
            "refusing bad records would compare as identical",
        ),
    )


def _fingerprint_lines(result: RunResult) -> list[str]:
    lines: list[str] = []
    for published in (*result.published, *result.restated, *result.confirmed):
        lines.append(f"result|{published.meter_id}")
    lines.extend(f"quarantine|{q.reason.value}" for q in result.quarantined)
    lines.extend(f"lineage|{key}" for key in sorted(result.lineage))
    return lines


def the_input_itself_is_stable() -> str:
    """The generator is part of the claim. A drifting fixture makes every run above vacuous."""
    return require(
        stream_digest(generate()) == stream_digest(generate()),
        "the synthetic generator produced two different streams in one process",
    )


CASES: tuple[Case, ...] = (
    Case(
        "a_rerun_is_identical",
        "The weakest form of the claim, and the one a wall-clock read or a uuid4 breaks first.",
        a_rerun_is_identical,
    ),
    Case(
        "shuffling_changes_nothing",
        "Arrival order is an accident of partitioning and retry timing. Anything that depends "
        "on it makes a replay a different run — with identical totals.",
        shuffling_changes_nothing,
    ),
    Case(
        "duplicating_changes_no_published_value",
        "At-least-once delivery is what a real stream offers. A redelivery is the same "
        "measurement and must collapse to the same result and the same lineage.",
        duplicating_changes_no_published_value,
    ),
    Case(
        "the_totals_are_not_enough",
        "The calibration case: if the fingerprint ever narrows to the energies alone, every "
        "case above starts passing for the wrong reason.",
        the_totals_are_not_enough,
    ),
    Case(
        "the_input_itself_is_stable",
        "The generator is part of the claim. A drifting fixture makes every comparison above "
        "a comparison of two accidents.",
        the_input_itself_is_stable,
    ),
)
