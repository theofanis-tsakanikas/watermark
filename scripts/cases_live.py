"""The cast's declared cases, asserted against the estate rather than against the core.

**What this adds to `evals/cases/`, which asks the same questions offline.** That harness drives
the generated day through `watermark.runner.run` — the pure core, on a laptop, in a second. It
proves the core sees every defect the cast declares. It cannot prove that the *deployed* system
does, and the difference is not theoretical: every defect this project has found in the cloud was
invisible offline, because the offline path and the deployed path meet only in a running estate.

A meter's duplicate is suppressed by `watermark.core.dedup` in both. Whether it survives IoT
Core's at-least-once delivery, a Kinesis shard, a Flink checkpoint and a Glue `MERGE` is a
different question, and this file is where it is asked.

**Read from the landing evidence, not from the lakehouse.** The landing files are what the stream
itself emitted, one JSON object per closed window, before anything merged or aggregated them.
Asking Athena would fold in the merge's own behaviour and put a second system between the claim
and the thing that made it — and when they disagree, the point is to know which one moved.

**Scoped to this capture.** The prefix accumulates across every run the estate has ever driven,
so a check over everything in it conflates "this run saw the case" with "some run once did". The
caller passes the part files that existed beforehand and they are subtracted, exactly as
`replay_live.py` does and for the same reason.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data import cast  # noqa: E402
from data.telemetry import OVERLOADED_SUBSTATION  # noqa: E402


@dataclass(frozen=True, slots=True)
class Case:
    """One declared defect and the evidence the estate must show for it."""

    name: str
    #: Why it matters, in the sentence somebody reading a red build needs.
    matters: str
    #: Returns an empty string when the estate showed it, or what was found instead.
    check: Callable[[list[dict[str, Any]]], str]


def _require(condition: bool, message: str) -> str:
    return "" if condition else message


def published(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [line for line in lines if line.get("kind") in {"published", "restated", "confirmed"}]


def the_retrying_cohort_is_deduplicated(lines: list[dict[str, Any]]) -> str:
    """At-least-once delivery survived the transport and was still absorbed.

    The offline harness proves the core suppresses a duplicate. This proves the duplicate
    actually reached the deployed operator and was suppressed *there* — through IoT Core's own
    at-least-once semantics, which add retries of their own on top of the ones the cast declares.
    A count of zero here with a green offline harness means the transport deduplicated for us,
    silently, and the property is no longer ours to guarantee.
    """
    suppressed = {
        str(row["meter"])
        for row in published(lines)
        if int(row.get("duplicates_suppressed", 0)) > 0
    }
    missed = [meter for meter in cast.DUPLICATING_METERS if meter not in suppressed]
    return _require(
        not missed,
        f"{len(missed)} of {len(cast.DUPLICATING_METERS)} retrying meters had no duplicate "
        f"suppressed in the estate: {missed[:3]}",
    )


def a_wrong_clock_is_quarantined(lines: list[dict[str, Any]]) -> str:
    """The two devices past tolerance were refused by the deployed operator.

    Quarantine lines are emitted by `streaming/operators.py` alongside the published ones. They
    used to be built and dropped — the local was assigned and never logged — so a record the
    transport could not read vanished without trace from the very branch whose comment says that
    a record which vanishes silently is the one nobody can account for. This is the check that
    would have caught it.
    """
    quarantined = [line for line in lines if line.get("kind") == "quarantine"]
    if not quarantined:
        return (
            "the estate quarantined nothing at all. Two devices are declared past the skew "
            "tolerance, so either they did not arrive or the tolerance moved"
        )
    reasons = {str(line.get("reason")) for line in quarantined}
    return _require(
        any("clock" in reason or "skew" in reason for reason in reasons),
        f"{len(quarantined)} records were quarantined and none of them for a clock. Reasons "
        f"seen: {sorted(reasons)}",
    )


def the_evidence_gap_leaves_a_hole(lines: list[dict[str, Any]]) -> str:
    """A meter that sent nothing for two intervals has no total for them, in the estate too.

    The gap is checked as a *shortfall against the rest of the fleet* rather than against
    absolute interval numbers, because the publisher shifts the whole day to end at the moment of
    the run: the interval a gap falls on is a different instant every capture, and a check keyed
    on one would be a check on the clock.
    """
    per_meter: dict[str, set[int]] = {}
    for row in published(lines):
        per_meter.setdefault(str(row["meter"]), set()).add(int(row["interval_start"]))
    if not per_meter:
        return "the estate published nothing, so a gap cannot be distinguished from an outage"

    ordinary = max(len(intervals) for intervals in per_meter.values())
    short = {
        meter: len(intervals) for meter, intervals in per_meter.items() if len(intervals) < ordinary
    }
    gapped = [meter for meter in cast.GAP_METERS if meter in short]

    # **The counts, in the message.** This case failed live against a thirty-minute capture and
    # said only that it had failed. Reproducing it offline did not work — the generated day's
    # meters have naturally uneven counts, so the cohort stays visibly short under every shift
    # tried — which means the next live run is the only place the answer lives. A failure that
    # does not carry the numbers it judged spends a whole capture to say "no".
    declared = {meter: len(per_meter.get(meter, ())) for meter in cast.GAP_METERS}
    return _require(
        bool(gapped),
        f"no meter the cast declares with missing intervals published fewer windows than the "
        f"fleet's {ordinary}. A gap that fills itself in is worse than one that stays open: an "
        f"hour built from three intervals is a different statement, not a smaller total. "
        f"The declared cohort published {declared}, against a fleet spread of "
        f"{sorted({len(i) for i in per_meter.values()})} across {len(per_meter)} meters",
    )


def the_late_batch_restates_and_names_what_it_replaced(lines: list[dict[str, Any]]) -> str:
    """Doctrine 4, through the whole transport, on the meters the head-end serves."""
    restated = [row for row in published(lines) if row.get("restatement_cause")]
    if not restated:
        return (
            "nothing restated in the estate. Either the three-day-late file never arrived, or a "
            "correction overwrote a total instead of superseding it"
        )
    unnamed = [row for row in restated if not row.get("supersedes")]
    covered = {str(row["meter"]) for row in restated} & set(cast.LATE_BATCH_METERS)
    if not covered:
        return (
            f"{len(restated)} restatements, none on a meter the legacy head-end serves. The "
            f"corrections came from somewhere else, which is a different case"
        )
    return _require(
        not unnamed,
        f"{len(unnamed)} restatements reached the estate without saying what they replaced",
    )


def the_quiet_substation_held_the_watermark_back(lines: list[dict[str, Any]]) -> str:
    """The grid stopped closing windows and named the substation doing it.

    Held back rather than idle: forty minutes is less than `idle_after`, so the partition is not
    abandoned. Asserting the other state would pass on a system that gave up on a substation after
    forty minutes, which is what `idle_after` exists to postpone.

    **Reported rather than required for a *specific* substation.** The publisher compresses the
    day, so which partition happens to be the slowest at a given batch boundary is alignment
    rather than behaviour. What is required is that the estate reports the condition at all and
    names *a* culprit every time — a held-back watermark that cannot say who is holding it is the
    half that makes the state useless to an operator.

    **And reported rather than required that it happened at all** — which this case used to
    demand, and which is the same decision `capture.yml` had already made in the step above it,
    with its reasoning written out. The two disagreed, and the disagreement was invisible until
    both were scoped to the same delivery: the step reported, the matrix failed the run.

    The arithmetic settles it. SUB-03's silence is forty minutes of *event* time and the
    publisher compresses arrivals nearly 300x, so the gap passes in about eight seconds of wall
    clock. Whether a one-second batch boundary falls inside it is alignment — it has, in three
    captures out of six — and a check that turns on that is a check that fails for a reason
    unrelated to the property it names.

    **The property is not unproved; it is proved where it is deterministic.** `evals/watermark/`
    exercises `held_back` offline in seven cases, and every capture asserts claim 1 in SQL as a
    statement about output rather than about timing: no row may be published carrying a watermark
    earlier than its own interval end. Inducing the transition live and on purpose needs a
    capture whose compression preserves the gap — four days at 20x, a five-hour run — and that
    is a real limit, recorded rather than papered over.

    Do not re-tighten this without reading the paragraph above. It was tightened once, and it
    cost a capture.
    """
    conditions = [line for line in lines if line.get("kind") == "watermark"]
    if not conditions:
        return "the job reported no watermark condition at all, so claim 1 has no evidence"
    held = [line for line in conditions if line.get("status") == "held_back"]
    if not held:
        print(
            "        note: no `held_back` in this capture — eight seconds of wall clock at this "
            "compression. Claim 1 is asserted in SQL on every run and proved in evals/watermark/."
        )
        return ""
    unnamed = [line for line in held if not line.get("holding_back")]
    return _require(
        not unnamed,
        f"the watermark was held back {len(held)} times and {len(unnamed)} of those named no "
        f"partition, which is the half that makes the state actionable",
    )


def every_condition_the_decisions_branch_on_is_reached(lines: list[dict[str, Any]]) -> str:
    """The estate reaches more than one watermark condition.

    A capture that only ever reports `advancing` has exercised the healthy path and nothing else,
    and every branch a decision takes on the status is then proved only against a constructed
    fixture. Two distinct conditions is a low bar deliberately: which ones a compressed day
    reaches is alignment, and a check that demanded a specific set would fail for timing.
    """
    reached = {str(line.get("status")) for line in lines if line.get("kind") == "watermark"}
    return _require(
        len(reached) > 1,
        f"the estate reported only {sorted(reached) or 'no'} watermark condition(s). Every "
        f"decision path branches on this, and one condition means one branch was exercised",
    )


def the_overloaded_substation_was_throttled(lines: list[dict[str, Any]]) -> str:
    """Curtailment, from measured load, marked as a fallback all the way to the record.

    Read from the decisions the live decider wrote rather than from the stream, because a
    throttle is a decision and not a window. The file is written by `scripts/decide_live.py` and
    is passed in alongside the landing evidence.
    """
    decisions = [line for line in lines if line.get("kind") == "decision"]
    if not decisions:
        return "no decision records were supplied, so curtailment cannot be checked here"
    throttles = [row for row in decisions if str(row.get("action", "")).startswith("throttle")]
    if not throttles:
        return (
            f"{len(decisions)} decisions and not one throttle. {OVERLOADED_SUBSTATION} is driven "
            f"past its limit by design, so this means the telemetry never reached the decider "
            f"rather than that nothing needed throttling"
        )
    unmarked = [
        row for row in throttles if row.get("origin") != "fallback" or not row.get("unavailable")
    ]
    return _require(
        not unmarked,
        f"{len(unmarked)} throttles reached the record without a fallback marker and a reason. "
        f"A fallback that looks like a model decision is worse than an outage",
    )


CASES: Final = (
    Case(
        "the retrying cohort is deduplicated",
        "The duplicate has to survive the transport and still be absorbed by us. Zero here with "
        "a green offline harness means the transport deduplicated silently and the property is "
        "no longer ours to guarantee.",
        the_retrying_cohort_is_deduplicated,
    ),
    Case(
        "a wrong clock is quarantined",
        "Quarantine lines were built and dropped once, from the branch whose own comment says a "
        "record that vanishes silently is the one nobody can account for.",
        a_wrong_clock_is_quarantined,
    ),
    Case(
        "the evidence gap leaves a hole",
        "A gap that fills itself in is worse than one that stays open: an hour built from three "
        "intervals is a different kind of statement, not a smaller total.",
        the_evidence_gap_leaves_a_hole,
    ),
    Case(
        "the late batch restates and names what it replaced",
        "Doctrine 4, through the whole transport rather than through the core alone.",
        the_late_batch_restates_and_names_what_it_replaced,
    ),
    Case(
        "the quiet substation held the watermark back, and named a culprit",
        "A held-back watermark that cannot say who is holding it is the half that makes the "
        "state useless to an operator.",
        the_quiet_substation_held_the_watermark_back,
    ),
    Case(
        "more than one watermark condition is reached",
        "A capture that only ever reports `advancing` has exercised the healthy path and left "
        "every other branch proved against a fixture.",
        every_condition_the_decisions_branch_on_is_reached,
    ),
    Case(
        "the overloaded substation was throttled, and marked as a fallback",
        "The decision with a physical consequence, and the marker doctrine 2 requires it to "
        "carry into the record.",
        the_overloaded_substation_was_throttled,
    ),
)


def read_lines(
    directory: Path, exclude: set[str], only: set[str] | None = None
) -> list[dict[str, Any]]:
    """Every JSON object under a directory, bounded at both ends.

    `exclude` drops what existed before the capture. `only`, when given, keeps what existed at
    the end of the *first* delivery — and the second boundary is not a refinement, it is the
    difference between this matrix asking its question and asking a different one.

    **Claim 2 re-drives the whole day into a running stream.** Its records land in windows the
    first delivery already closed, which is at-least-once delivery working; but the two days are
    shifted relative to each other, and on a thirty-minute capture the measured offset was
    -1800 s — exactly two intervals, which is exactly how wide the cast's evidence gap is. The
    second delivery therefore published windows at the very instants the first had left empty,
    and `the evidence gap leaves a hole` reported no hole. Nothing was wrong with the estate: the
    matrix had been handed two days and asked a question about one.
    """
    lines: list[dict[str, Any]] = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.name in exclude:
            continue
        if only is not None and path.name not in only:
            continue
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not raw.strip().startswith("{"):
                continue
            try:
                lines.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--landing", type=Path, required=True, help="This capture's landing files.")
    parser.add_argument(
        "--decisions", type=Path, help="decisions.jsonl from scripts/decide_live.py."
    )
    parser.add_argument(
        "--before",
        type=Path,
        help="A file listing part files that existed before this capture, to be excluded.",
    )
    parser.add_argument(
        "--first-delivery",
        type=Path,
        help="A file listing the part files that existed at the end of the first delivery. "
        "Given, the evidence is bounded to that one delivery — see `read_lines`. Omitted, the "
        "matrix reads everything this capture produced, which for a run with a replay in it "
        "means two shifted copies of the same day.",
    )
    arguments = parser.parse_args(argv)

    def _names(path: Path | None) -> set[str] | None:
        if not path or not path.exists():
            return None
        return {
            name.strip() for name in path.read_text(encoding="utf-8").splitlines() if name.strip()
        }

    exclude = _names(arguments.before) or set()
    only = _names(arguments.first_delivery)

    lines = read_lines(arguments.landing, exclude, only)
    if only is not None:
        print(f"cases-live: bounded to the first delivery's {len(only)} part files")
    if arguments.decisions and arguments.decisions.exists():
        for raw in arguments.decisions.read_text(encoding="utf-8").splitlines():
            if raw.strip().startswith("{"):
                row = json.loads(raw)
                row["kind"] = "decision"
                lines.append(row)

    print(f"cases-live: {len(lines)} evidence lines from this capture")
    if not lines:
        # Louder than a clean exit. Every case below would pass over an empty list by finding
        # nothing to contradict it, which is the shape of a green check that proves nothing.
        print("::error::no evidence at all; every case would pass over an empty list")
        return 1

    failed: list[tuple[str, str]] = []
    for case in CASES:
        problem = case.check(lines)
        print(f"  {'ok  ' if not problem else 'FAIL'}  {case.name}")
        if problem:
            failed.append((case.name, problem))

    for name, problem in failed:
        print(f"::error::{name}: {problem}")
    print(f"cases-live: {len(CASES) - len(failed)}/{len(CASES)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
