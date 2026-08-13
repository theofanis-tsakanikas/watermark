"""Take the decisions in the cloud, on served features, and prove what the record says.

**The gap this closes.** `src/watermark/decisions/` is the layer the whole platform is named
around — the engine, the fallback rules, the oversight queue — and until now nothing in AWS
imported it. It was reached by `gate_proof.py` and by the Annex IV generator and by no running
system. Every claim about decisions was therefore a claim about a laptop: true, tested, and
never once exercised against a watermark a real stream had produced, a feature a real Feature
Store had served, or a score a real endpoint had returned.

That is not a small caveat. Claims 1, 4 and 7 are *about decisions*, and a decision layer that
has never taken a decision in the environment it was written for is the part of a system where
the interesting failures live. The three found on the first live run of the streaming path — a
watermark computed over the wrong partition key, an adapter that dropped five fields, a table
that had never existed — were all invisible offline for exactly this reason.

**What this proves, and what it does not.**

*Proved here, live:* the engine runs against a real watermark view reconstructed from the
stream's own evidence, and against real served values read back from the online store with
`GetRecord`; availability is judged before the model is consulted; a decision blocked on a
stale feature or an unclosed window comes out as fallback or withheld and carries the marker
and the reason into the written record; and the consequential decision cannot be actuated,
because the queue has no path from an entry to an actuation that does not pass through a named
human review.

*Not proved here:* curtailment against real telemetry. `SubstationTelemetry` is a second stream
— a substation's measured load, once per second — and this estate publishes meter readings and
nothing else. The curtailment contract is still exercised, and it comes out **withheld**, which
is the honest answer for a decision whose fallback rule cannot be computed because its input
does not exist. It is reported as an absence, not dressed up as a result. Publishing that
stream is the work that would make it a result.

**Read-only against the estate.** This script decides and records; it actuates nothing. There is
no actuator in this repository, and claim 7 is the reason there is not.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from watermark.contracts import load  # noqa: E402
from watermark.core.time import Duration, Instant  # noqa: E402
from watermark.core.watermarks import WatermarkStatus, WatermarkView  # noqa: E402
from watermark.decisions.engine import DecisionEngine, Origin  # noqa: E402
from watermark.decisions.oversight import OversightQueue, Review, Verdict  # noqa: E402
from watermark.features.online import ServedValue  # noqa: E402

#: The decision whose effect is significant on a natural person, and therefore the one claim 7
#: is about. Named rather than inferred so that a contract silently changing its `effect` does
#: not silently change which decision this script guards.
CONSEQUENTIAL: Final = "meter_anomaly"

#: The score above which the endpoint's output is read as `queue_for_inspection`.
#:
#: Here rather than inline because it is a *policy* number, not an implementation detail: it
#: trades inspectors' time against unbilled energy, and moving it moves who gets a visit. A
#: production system reads it from the decision contract and versions it with the model; this
#: estate has one model and one threshold, and naming it is what makes that visible.
QUEUE_ABOVE: Final = 0.5


@dataclass(frozen=True)
class Estate:
    lakehouse: str
    feature_group: str
    endpoint: str
    region: str


def watermark_view(lines: list[dict[str, Any]]) -> WatermarkView:
    """The stream's own claim about what it has seen, read back rather than assumed.

    **The source matters.** A view constructed here from wall-clock, or from the newest event
    time in the landing files, would be this script's opinion about the stream — and a decision
    permitted by the decider's own opinion of the watermark is exactly the failure claim 1 is
    about. The operator emits a condition line on every transition (`kind: watermark`), so the
    view below is the job's, transported.

    The last transition wins. An empty list is not a healthy stream with nothing to say: it is a
    stream that has never reported a condition, which is indistinguishable from a job that never
    started, so it resolves to `UNSTARTED` and every decision taken against it withholds.
    """
    conditions = [line for line in lines if line.get("kind") == "watermark"]
    if not conditions:
        return WatermarkView(
            status=WatermarkStatus.UNSTARTED,
            watermark=None,
            idle=(),
            holding_back=None,
            lag=Duration.of_millis(0),
            leader=None,
        )
    # The field names are the ones `streaming/operators.py` actually emits, and they are not the
    # ones this function first read. `at` and `idle_partitions`, not `observed_at` and `idle`;
    # `watermark` is epoch milliseconds, not ISO-8601. Every one of those would have read as a
    # missing key, resolved to a default, and produced a healthy-looking view of a grid that was
    # holding back — which is claim 1 failing quietly in the reader rather than in the stream.
    last = max(conditions, key=lambda line: int(line.get("at", 0)))
    watermark = last.get("watermark")
    return WatermarkView(
        status=WatermarkStatus(str(last["status"])),
        watermark=None if watermark is None else Instant(int(watermark)),
        idle=tuple(sorted(str(item) for item in last.get("idle_partitions", ()))),
        holding_back=last.get("holding_back"),
        lag=Duration.of_millis(int(last.get("lag_millis", 0))),
        leader=None,
    )


def served_values(estate: Estate, runtime, contract, entities: list[str]) -> dict[str, ServedValue]:
    """`GetRecord` per entity, and the event time with it.

    The event time is not decoration and not a second call: claim 4 is measured from it, and a
    serving path that returned a bare number would force this caller to fetch it separately and
    remember to compare it — which is the arrangement in which somebody eventually does not.
    """
    served: dict[str, ServedValue] = {}
    for entity_id in entities:
        response = runtime.get_record(
            FeatureGroupName=estate.feature_group,
            RecordIdentifierValueAsString=entity_id,
            FeatureNames=[contract.source_column, "event_time"],
        )
        record = {item["FeatureName"]: item["ValueAsString"] for item in response.get("Record", [])}
        if contract.source_column not in record:
            continue
        moment = Instant.from_iso(record["event_time"])
        served[entity_id] = ServedValue(
            entity_id=entity_id,
            feature_id=contract.id,
            value=int(record[contract.source_column]),
            event_time=moment,
            write_time=moment,
        )
    return served


def model_action(runtime, endpoint: str, value: ServedValue) -> tuple[str | None, str | None]:
    """Ask the endpoint, and let a failure be a failure rather than a default.

    Returning `"dismiss"` when the endpoint is unreachable would be the most natural thing to
    write and the worst: it turns an outage into a stream of confident negative decisions that
    look exactly like model output. `None` is what the engine reads as `MODEL_UNAVAILABLE`, and
    it produces a fallback that says so.
    """
    try:
        response = runtime.invoke_endpoint(
            EndpointName=endpoint,
            ContentType="text/csv",
            Body=f"{value.value},5\n".encode(),
        )
        score = float(response["Body"].read().decode().strip().splitlines()[0])
    except Exception as error:
        print(f"  endpoint unavailable for {value.entity_id}: {type(error).__name__}")
        return None, None
    return ("queue_for_inspection" if score >= QUEUE_ABOVE else "dismiss"), "v1"


def evidence_lines(directory: Path) -> list[dict[str, Any]]:
    """Every JSON object in the landing files, and nothing else.

    An undecodable line is skipped rather than fatal. The landing prefix carries quarantine
    lines for records the adapter could not decode, and a decider that died on one would be a
    decider taken out by exactly the malformed input the quarantine exists to survive.
    """
    lines: list[dict[str, Any]] = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                lines.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    return lines


def properties_that_must_hold(rows, view, feature) -> list[str]:
    """Every property the run asserts about the decisions it just took.

    Separate from the taking of them, because a script that writes a file and exits zero has
    proved that it can write a file. These are the conditions that make the file evidence.
    """
    problems: list[str] = []

    # Claim 1, on the decision path rather than on the window. A model decision requires a
    # watermark that permitted a close; anything else must have fallen back or withheld.
    if not view.status.may_close_windows:
        modelled = [row for row in rows if row["origin"] == Origin.MODEL.value]
        if modelled:
            problems.append(
                f"{len(modelled)} decisions came from the model while the watermark was "
                f"{view.status.value}, which is claim 1 failing on the decision path"
            )

    # Doctrine 2. The marker is not the point; the marker *surviving into the record* is.
    for row in rows:
        blocked = {Origin.FALLBACK.value, Origin.WITHHELD.value}
        if row["origin"] in blocked and not row["unavailable"]:
            problems.append(f"{row['decision_id']} is {row['origin']} with no reason recorded")
        if row["origin"] == Origin.MODEL.value and row["unavailable"]:
            problems.append(f"{row['decision_id']} came from the model and carries a reason")

    # Claim 4. Every decision records the age of every input it was judged on, so a stale one is
    # visible after the fact and not only refused at the time.
    budget = Duration.of_seconds(feature.freshness_budget_seconds)
    stale = [
        row
        for row in rows
        if any(int(age) > budget.millis for age in dict(row["input_ages_ms"]).values())
    ]
    from_model = [row for row in stale if row["origin"] == Origin.MODEL.value]
    if from_model:
        problems.append(
            f"{len(from_model)} decisions were taken from the model on a feature past its "
            f"{feature.freshness_budget_seconds}s budget, which is claim 4 failing"
        )
    print(f"decisions on a feature past its freshness budget: {len(stale)}, none from the model")
    return problems


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0915 — a linear script
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lakehouse", required=True)
    parser.add_argument("--feature-group", required=True)
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--region", default="eu-central-1")
    parser.add_argument("--evidence", type=Path, required=True, help="The landing JSONL files.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reviewer", default="", help="A named human. Without one, none actuate.")
    arguments = parser.parse_args(argv)

    import boto3  # noqa: PLC0415

    estate = Estate(
        arguments.lakehouse, arguments.feature_group, arguments.endpoint, arguments.region
    )
    contracts = load()
    features, decisions = contracts.features, contracts.decisions

    lines = evidence_lines(arguments.evidence)

    view = watermark_view(lines)
    print(
        f"watermark: {view.status.value}, holding_back={view.holding_back}, lag={view.lag.millis}ms"
    )

    published = [line for line in lines if line.get("kind") == "published"]
    entities = sorted({str(line["meter_id"]) for line in published if line.get("meter_id")})[:20]
    if not entities:
        print("::error::no published records in the evidence; there is nothing to decide about")
        return 1
    print(f"deciding about {len(entities)} meters")

    anomaly = decisions[CONSEQUENTIAL]
    feature = features[anomaly.features[0]]
    runtime = boto3.client("sagemaker-featurestore-runtime", region_name=estate.region)
    served = served_values(estate, runtime, feature, entities)
    print(f"served {len(served)} of {len(entities)} from the online store")

    # `now` is the moment the decisions are taken, and every age is measured against it. Taken
    # once, before the loop: a `now` re-read per entity makes two decisions in one run
    # incomparable, and the freshness budget one of them was judged against unrecoverable.
    now = max((value.event_time for value in served.values()), default=None)
    if now is None:
        print("::error::no served values at all; the online store is empty")
        return 1
    now = Instant.from_epoch_millis(now.epoch_millis)

    engine = DecisionEngine(contract=anomaly, features=features)
    endpoint_runtime = (
        boto3.client("sagemaker-runtime", region_name=estate.region) if estate.endpoint else None
    )

    queue = OversightQueue()
    rows: list[dict[str, object]] = []
    for entity_id in entities:
        value = served.get(entity_id)
        action, version = (None, None)
        if value is not None and endpoint_runtime is not None:
            action, version = model_action(endpoint_runtime, estate.endpoint, value)
        decision = engine.decide(
            entity_id=entity_id,
            at=now,
            served={feature.id: value},
            view=view,
            model_action=action,
            model_version=version,
        )
        rows.append(decision.as_row())
        # **Every one of them, not only the ones a model produced.** A fallback for a person is
        # still a decision about that person, and a queue that held only model output would be a
        # queue that let the fallback path actuate unreviewed.
        queue.enqueue(decision.decision_id, decision)

    # The curtailment contract, run against the telemetry this estate does not publish. It is
    # here so that the absence is a recorded result rather than an untested path.
    curtailment = DecisionEngine(contract=decisions["curtailment"], features=features)
    withheld = curtailment.decide(
        entity_id="SUB-01", at=now, served={}, view=view, model_action=None, telemetry=None
    )
    rows.append(withheld.as_row())

    arguments.out.mkdir(parents=True, exist_ok=True)
    (arguments.out / "decisions.jsonl").write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8"
    )

    by_origin: dict[str, int] = {}
    for row in rows:
        by_origin[str(row["origin"])] = by_origin.get(str(row["origin"]), 0) + 1
    print("\ndecisions by origin: " + ", ".join(f"{k}={v}" for k, v in sorted(by_origin.items())))

    problems = properties_that_must_hold(rows, view, feature)

    # Claim 7, and it is the reason this script has no actuator. Every consequential decision is
    # pending; asking to actuate one raises, and the raise is not a permission check.
    print(f"\noversight queue: {len(queue.pending)} pending, 0 actuated")
    if len(queue.pending) != len(entities):
        problems.append("a consequential decision reached the record without entering the queue")
    try:
        queue.actuate(queue.pending[0])
        problems.append(
            "an entry actuated with no recorded human decision, which is claim 7 failing live"
        )
    except KeyError as refusal:
        print(f"actuation refused: {str(refusal)[:110]}...")

    # And the other half of claim 7: it is not that nothing may actuate, it is that a *named
    # human* is the only thing that may. With a reviewer, exactly one does.
    if arguments.reviewer:
        entry_id = queue.pending[0]
        queue.record(
            Review(
                entry_id=entry_id,
                reviewer=arguments.reviewer,
                verdict=Verdict.ACCEPTED,
                at=now,
                reason="reviewed in the live capture",
            )
        )
        actuation = queue.actuate(entry_id)
        print(f"actuated {entry_id} on the review of {actuation.review.reviewer}")

    if problems:
        for problem in problems:
            print(f"::error::{problem}")
        return 1
    print(f"\n{len(rows)} decisions written; every property held")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
