#!/usr/bin/env python3
"""Break every gate on purpose, and require the real gate to refuse it.

A test suite tells you the code does what it does. It cannot tell you the *gates* still bite,
because a gate that has quietly stopped checking anything passes every test it has. This
script copies the repository, plants a genuine violation in the copy, and demands a refusal.

Three rules keep it a proof rather than a ritual.

**Green first.** Every mutation runs against a repository that currently passes. A refusal
from an already-broken baseline proves nothing at all.

**A non-zero exit is not evidence.** The *named* check must be the thing that failed, with a
message that names the violation. A mutation that happens to cause an unrelated crash is
reported as a failure of the proof, not as a pass — otherwise the day a gate is deleted, its
mutation still "passes", because now the import fails instead.

**A mutation whose target has moved is STALE.** If the code a mutation edits no longer looks
the way it expects, the mutation is not silently skipped and is not counted. It is reported,
and the run is red, because a proof that quietly stopped running is worse than one that never
existed — it is the same red line, with a green tick over it.

Every mutation is a mistake somebody could plausibly make. An absurd one proves the gate
handles absurdity, which nobody was worried about.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Mutation:
    name: str
    gate: str
    #: What this mutation attacks: a gate module under `src/watermark/gates/`, or `claim-N` for
    #: one of the seven claims. Declared rather than inferred, so
    #: `tests/test_gates_are_attacked.py` can insist that every gate in the package is named
    #: here — a gate nobody attacks is a gate nobody has seen refuse.
    module: str
    #: The command that must fail, run inside the mutated copy.
    command: list[str]
    #: A phrase the failure must contain. Its presence is what makes the refusal *the*
    #: refusal rather than any old error.
    expect: str
    apply: Callable[[Path], bool]
    #: Why this mutation is worth planting.
    rationale: str


def _replace(path: Path, old: str, new: str) -> bool:
    """Edit a file, returning False if the target text is not there any more."""
    text = path.read_text(encoding="utf-8")
    if old not in text:
        return False
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return True


# ── The mutations ────────────────────────────────────────────────────────────


def _import_a_cloud_sdk_into_the_core(root: Path) -> bool:
    """Reach for boto3 from inside the stream core.

    The plausible version is not this line; it is the third week of phase 2, when a windowing
    function needs one lookup and the store is in DynamoDB. Nothing fails: the suite still
    passes on the machine that wrote it, and every claim in the repository quietly becomes a
    claim about a machine with credentials.
    """
    return _replace(
        root / "src/watermark/core/time.py",
        "import re\nfrom dataclasses import dataclass",
        "import re\n\nimport boto3\nfrom dataclasses import dataclass",
    )


def _let_the_core_read_the_wall_clock(root: Path) -> bool:
    """Add `Instant.now()`.

    The most natural API in the world, and the one thing that must not exist here. It does not
    raise, does not log and does not fail a test. It makes a replay differ from the run it is
    replaying — three months later, on a machine nobody has, in a number somebody has already
    been invoiced for.
    """
    return _replace(
        root / "src/watermark/core/time.py",
        "    @classmethod\n    def from_epoch_millis(cls, millis: int) -> Instant:",
        "    @classmethod\n"
        "    def now(cls) -> Instant:\n"
        "        return cls(int(datetime.now(UTC).timestamp() * 1000))\n"
        "\n"
        "    @classmethod\n"
        "    def from_epoch_millis(cls, millis: int) -> Instant:",
    )


def _let_the_core_reach_back_into_the_package(root: Path) -> bool:
    """Import a sibling package from inside the core.

    Subtler than the cloud SDK and more likely: `watermark.lineage` is our own code, it is
    pure today, and importing it looks like reuse rather than a boundary violation. It is how
    the boundary stops being a boundary — one honest import at a time, until the core's
    dependency set is the whole application and nobody can say what claim 1 is a claim about.
    """
    return _replace(
        root / "src/watermark/core/time.py",
        "from dataclasses import dataclass",
        "from dataclasses import dataclass\n\nfrom ..lineage import LineageId",
    )


def _publish_before_the_window_closes(root: Path) -> bool:
    """Let a window publish whether or not the watermark has passed it.

    One condition. It is the line claim 1 is made of, and removing it does not raise, does not
    log, and produces totals that look entirely normal — computed from whatever had arrived.
    """
    return _replace(
        root / "src/watermark/core/windows.py",
        "                view.status.may_close_windows",
        "                True or view.status.may_close_windows",
    )


def _keep_whichever_copy_arrived_first(root: Path) -> bool:
    """Deduplicate by arrival instead of by content.

    The natural implementation, and the one that makes a replay a different run: two copies of
    a reading differ in ingestion time, firmware and source, so whichever arrives first is an
    accident of partitioning and retry timing. Every total stays correct.
    """
    return _replace(
        root / "src/watermark/core/dedup.py",
        "    representatives = [min(copies, key=_retry_order) for copies in by_content.values()]",
        "    representatives = [copies[0] for copies in by_content.values()]",
    )


def _let_a_redelivery_change_the_lineage(root: Path) -> bool:
    """Stop de-duplicating a derived id's parents.

    Found by claim 2's harness rather than by review. Under at-least-once delivery the same
    record arrives twice, and hashing `[id, id]` differently from `[id]` gives every published
    total a new lineage id in a replay while every number stays identical.
    """
    return _replace(
        root / "src/watermark/lineage/identity.py",
        "    return _digest(kind, [key, *sorted(set(parents))])",
        "    return _digest(kind, [key, *sorted(parents)])",
    )


def _accept_a_device_reporting_from_the_future(root: Path) -> bool:
    """Stop quarantining clock skew.

    Plausible as a fix for "too many quarantines from one substation". What it actually does
    is let a meter three hours fast advance the watermark, which closes every window in the
    grid three hours early, on incomplete data, with nothing anywhere reporting an error. The
    most damaging single change available in this system, and it is one condition.
    """
    return _replace(
        root / "src/watermark/core/normalise.py",
        "    if skew.millis > policy.skew_tolerance.millis:",
        "    if False:",
    )


def _let_a_contract_hold_personal_data_with_no_purpose(root: Path) -> bool:
    """Strip the declared purpose from an entity that holds personal data.

    GDPR Art. 5(1)(b) requires a specified purpose, and a purpose that is not written down is
    not specified. The plausible version is not deletion — it is a new contract added in a
    hurry without one.
    """
    path = root / "contracts/entities/meter_assignment.yaml"
    text = path.read_text(encoding="utf-8")
    marker = "purpose: >"
    if marker not in text:
        return False
    start = text.index(marker)
    end = text.index("\nscd2:", start)
    path.write_text(text[:start] + text[end + 1 :], encoding="utf-8")
    return True


def _point_a_contract_at_an_entity_that_does_not_exist(root: Path) -> bool:
    """Rename an entity in one place. The join compiles, returns nothing, and reads as a
    customer with no consumption."""
    return _replace(
        root / "contracts/entities/meter_assignment.yaml",
        "  - entity: customer\n",
        "  - entity: customers\n",
    )


def _bake_a_window_into_the_framework(root: Path) -> bool:
    """Put the metering interval in the Flink call, as a literal.

    The line somebody adds on a Tuesday because a reading was being dropped. After it, the
    core and the deployed job disagree about what a window is, the offline suite keeps passing
    because it exercises the core, and only the deployed one is right.
    """
    return _replace(
        root / "streaming/job.py",
        "    environment.enable_checkpointing(placement.checkpoint_interval_millis)",
        "    environment.enable_checkpointing(placement.checkpoint_interval_millis)\n"
        "    _window = Time.minutes(15)",
    )


def _merge_the_two_parity_mechanisms(root: Path) -> bool:
    """Have the offline resolver import the online fold.

    The tidy refactor. `offline._aggregate` and `online._fold` do look alike, and merging them
    is the obvious cleanup — after which claim 3 compares a function with itself and reports
    green in eleven milliseconds, forever.
    """
    return _replace(
        root / "src/watermark/features/offline.py",
        "from watermark.core.time import Instant",
        "from watermark.core.time import Instant"
        + chr(10)
        + "from watermark.features.online import _fold",
    )


def _let_a_feature_be_a_double(root: Path) -> bool:
    """Allow a Fractional feature.

    Plausible as a simplification — the value *is* a mean, after all. It replaces exact integer
    equality with a comparison that only ever passes with a tolerance, and doctrine 7 says the
    parity door has no key.
    """
    return _replace(
        root / "src/watermark/contracts/features.py",
        '        if self.value_type == "Fractional":',
        "        if False:",
    )


def _drop_a_freshness_budget(root: Path) -> bool:
    """Give a feature no freshness budget.

    Not deletion — a feature added in a hurry by copying the one above it. Claim 4 then holds
    vacuously for that feature, and the decision path reading it never falls back.
    """
    return _replace(
        root / "contracts/features/substation_load_15m.yaml",
        "freshness_budget_seconds: 60",
        "freshness_budget_seconds: 0",
    )


def _let_the_fallback_read_the_feature_store(root: Path) -> bool:
    """Have the curtailment fallback declare that it reads served features.

    Not hypothetical: it is what the first draft of `proportional_throttle` actually did, and
    claim 4's harness is what caught it. A fallback that reads the feature store is unavailable
    in exactly the conditions the primary path is.
    """
    return _replace(
        root / "contracts/decisions/curtailment.yaml",
        "  uses_features: false",
        "  uses_features: true",
    )


def _automate_a_decision_about_a_person(root: Path) -> bool:
    """Set the anomaly path to actuate automatically.

    The one-word change claim 7 exists to make impossible, and the change somebody makes when
    the inspection queue is three weeks deep.
    """
    return _replace(
        root / "contracts/decisions/meter_anomaly.yaml",
        "actuation: human_gated",
        "actuation: automatic",
    )


def _accept_an_unnamed_reviewer(root: Path) -> bool:
    """Stop requiring a review to name a human.

    Plausible as a fix for an integration test that has no real inspectors in it, and it is the
    single change that makes claim 7 false while every other test keeps passing: an entry with
    a blank signature actuates, and the record looks exactly like a reviewed one.

    (An earlier version of this mutation gave `reviewer` a *default* instead. It broke the
    dataclass — a defaulted field before two undefaulted ones — so the import failed and the
    harness correctly reported "something failed, but not claim 7". That is the second rule
    working: a non-zero exit is not evidence.)
    """
    return _replace(
        root / "src/watermark/decisions/oversight.py",
        "        if not self.reviewer.strip():",
        "        if False:",
    )


def _let_a_policy_grant_be_a_disjunction(root: Path) -> bool:
    """Match *any* tag instead of all of them.

    `all` to `any` — one word, and it reads identically in the YAML. A purpose-limited grant
    becomes a sensitivity-limited one, so the settlement role can read everything collected for
    a fraud investigation and nothing anywhere looks different.
    """
    return _replace(
        root / "src/watermark/policy/evaluator.py",
        "        return all(tags.get(key) in values for key, values in self.match.items())",
        "        return any(tags.get(key) in values for key, values in self.match.items())",
    )


def _certify_an_incomplete_erasure(root: Path) -> bool:
    """Issue a certificate even when a leg was never attempted.

    Plausible as a fix for a flaky orchestration step. It is the one change that makes claim 6
    a lie rather than a limitation: the subject is told they are gone, and the residual becomes
    invisible.
    """
    return _replace(
        root / "src/watermark/erasure/certificate.py",
        "    if missing or unfinished:",
        "    if False:",
    )


def _let_a_second_leg_declare_a_boundary(root: Path) -> bool:
    """Allow any leg to be BOUNDED, not just the model artefacts.

    How "we could not finish that one either" becomes a second declared boundary, and then a
    third, until the certificate declares a boundary around everything it did not do.
    """
    # The check, not the constant. Widening BOUNDABLE to "*" inverts it — the legitimate
    # bounded leg is then the one refused — which would be a mutation that fails for a reason
    # unrelated to the rule. Removing the check is the change somebody would actually make.
    return _replace(
        root / "src/watermark/erasure/certificate.py",
        "    if misbounded:",
        "    if False:",
    )


def _flatter_the_bias_gate(root: Path) -> bool:
    """Make the precision-gap check one-sided again.

    This is the bug the analysis actually found, restored. Under the finding the expression is
    negative, so a five-fold difference in label coverage reads as a pass — and the model that
    should be refused promotes. See docs/BIAS-FINDING.md.
    """
    return _replace(
        root / "src/watermark/models/promotion.py",
        "        gap = abs(bias.precision_least_deprived - bias.precision_most_deprived)",
        "        gap = bias.precision_least_deprived - bias.precision_most_deprived",
    )


def _transcribe_the_state_bucket_into_a_repository_variable(root: Path) -> bool:
    """Read the backend bucket from a settings page instead of from the layer that named it.

    The plausible version of this is a hurry: the SSM lookup fails once, somebody pastes the
    bucket name into a repository variable to get the deploy moving, and it works. Nothing goes
    red — the deploy is green that day and every day after, right up until the bucket is
    renamed and the failure is a backend nobody can find, with the fix in a settings page
    rather than in a diff.
    """
    return _replace(
        root / ".github/workflows/deploy.yml",
        '-backend-config="bucket=$TF_STATE_BUCKET"',
        '-backend-config="bucket=${{ vars.TF_STATE_BUCKET }}"',
    )


def _let_the_transport_send_the_meter_as_the_partition(root: Path) -> bool:
    """Read the meter id, not the substation, as the record's partition.

    This is the mutation that is not hypothetical: it is what the repository shipped, and it was
    found only by reading a landing file from a live capture. The rule is valid SQL, the core is
    correct, and every claim harness stays green because they drive the core directly with the
    partitions it declares. The two halves meet nowhere offline except the topic.

    What it costs when it is wrong: every declared substation lags infinitely, is excluded as
    idle, and every published total carries a hole that is not there — while claim 1's sharpest
    case, a substation going quiet, cannot fire at all, because it never spoke.
    """
    return _replace(
        root / "infra/streaming/iot.tf",
        "topic(3) AS partition, 'stream' AS source",
        "topic(4) AS partition, 'stream' AS source",
    )


def _stop_generating_a_declared_defect(root: Path) -> bool:
    return _replace(root / "data/cast.py", "_GAP_POSITION: Final = 5", "_GAP_POSITION: Final = -1")


def _let_a_live_case_pass_whatever_it_is_given(root: Path) -> bool:
    return _replace(
        root / "scripts/cases_live.py",
        'return "" if condition else message',
        'return ""  # noqa: ARG001',
    )


def _round_the_replay_offset_onto_the_interval_grid(root: Path) -> bool:
    return _replace(
        root / "scripts/replay_live.py",
        "        offset = centre + step * grid",
        "        offset = round((centre + step * grid) / grid) * grid",
    )


def _list_the_whole_telemetry_prefix_at_once(root: Path) -> bool:
    return _replace(
        root / "scripts/decide_live.py",
        'Bucket=bucket, Prefix=f"telemetry/{substation}/"',
        'Bucket=bucket, Prefix="telemetry/"',
    )


def _open_the_reference_history_on_the_day_of_the_run(root: Path) -> bool:
    return _replace(
        root / "scripts/seed_reference.py",
        'HISTORY_BEGINS: Final = "2000-01-01 00:00:00"',
        'HISTORY_BEGINS: Final = "2026-03-14 00:00:00"',
    )


def _use_a_python_311_builtin_in_a_glue_job(root: Path) -> bool:
    return _replace(
        root / "pipelines/jobs/delete_orphan_files.py",
        "from datetime import datetime, timedelta, timezone",
        "from datetime import UTC, datetime, timedelta, timezone",
    )


def _point_a_feature_at_a_column_no_table_has(root: Path) -> bool:
    return _replace(
        root / "contracts/features/substation_load_15m.yaml",
        "source_column: load_w",
        "source_column: load_kw",
    )


def _let_the_settlement_fallback_publish_a_provisional_number(root: Path) -> bool:
    return _replace(
        root / "contracts/decisions/settlement_publication.yaml",
        "  permitted_actions:\n    - restate\n",
        "  permitted_actions:\n    - restate\n    - publish\n",
    )


def _stop_naming_the_contract_the_harness_holds_the_code_to(root: Path) -> bool:
    return _replace(
        root / "evals/settlement/__init__.py",
        'CONTRACT = "settlement_publication"',
        "CONTRACT = load().decisions and next(iter(sorted(load().decisions)))",
    )


def _read_an_unaskable_question_as_an_empty_answer(root: Path) -> bool:
    return _replace(
        root / "src/watermark/erasure/verify.py",
        "    if observation.rows is None:",
        "    if False:",
    )


def _let_an_exception_outlive_its_grant(root: Path) -> bool:
    return _replace(
        root / "contracts/waivers.yaml", "    expires_on: 2026-09-13", "    expires_on: 2026-06-30"
    )


def _trust_every_branch_in_the_repository(root: Path) -> bool:
    return _replace(
        root / "infra/bootstrap/oidc.tf",
        '    "repo:${var.github_owner}/${var.github_repo}:environment:deploy",',
        '    "repo:${var.github_owner}/${var.github_repo}:*",',
    )


def _drop_an_endpoint_the_estate_reaches_through(root: Path) -> bool:
    # Kinesis, deliberately: it is granted by the streaming layer's IAM policy, so removing its
    # endpoint is the shape the check exists for. `secretsmanager` was the first choice and it
    # proved nothing — no policy grants it, so the check was right to stay quiet and the
    # mutation reported a gate that had accepted a violation nobody had committed.
    return _replace(root / "infra/foundation/network.tf", '    "kinesis-streams",\n', "")


def _rename_a_lakehouse_table_in_one_of_its_three_descriptions(root: Path) -> bool:
    return _replace(
        root / "pipelines/dbt/models/silver/sources.yml",
        "      - name: meter_interval",
        "      - name: meter_intervals",
    )


def _change_the_seed_on_one_side_of_the_experiment(root: Path) -> bool:
    return _replace(
        root / "infra/ml/pipeline.tf", 'seed        = "20260810"', 'seed        = "20260811"'
    )


def _run_the_equivalence_tier_against_a_different_flink(root: Path) -> bool:
    return _replace(root / "pyproject.toml", '"apache-flink>=1.20"', '"apache-flink>=1.19"')


def _leave_the_expensive_things_standing_overnight(root: Path) -> bool:
    return _replace(
        root / "scripts/check_cost_envelope.py",
        "CAPTURE_HOURS = 1",
        "CAPTURE_HOURS = 168",
    )


MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        "point a feature at a column no table has",
        "feature sources",
        "feature-sources",
        ["scripts/check_feature_sources.py"],
        "has no such column",
        _point_a_feature_at_a_column_no_table_has,
        "A unit rename is the most ordinary edit in this domain — watts to kilowatts, and "
        "somebody updates the contract. Nothing refuses it: the contract validates, the "
        "resolver builds a query naming a column that is not there, and the failure arrives at "
        "the first serve rather than at the commit. Two features in this repository spent the "
        "whole life of the lakehouse in exactly that state and nothing ever said so.",
    ),
    Mutation(
        "let the settlement fallback publish a provisional number",
        "the settlement path",
        "settlement",
        ["python", "-m", "evals.settlement", "-q"],
        "A fallback that publishes",
        _let_the_settlement_fallback_publish_a_provisional_number,
        "It reads as the helpful thing to do — the head-end is late, publish what we have and "
        "correct it later. On this path publishing *is* invoicing, so the fallback would create "
        "the correction it exists to avoid, and it would do it under the marker that tells "
        "everyone downstream the number was not model-derived. Curtailment's fallback acts for "
        "exactly the opposite reason, which is why neither engine may decide this at runtime.",
    ),
    Mutation(
        "stop naming the contract the settlement harness holds the code to",
        "every decision contract is exercised",
        "contract-coverage",
        ["python", "-m", "pytest", "tests/test_every_contract_is_exercised.py", "-q"],
        "decision contracts nothing checks",
        _stop_naming_the_contract_the_harness_holds_the_code_to,
        "Deriving the id instead of writing it is the kind of tidy-up that looks like removing "
        "a hard-coded string. The harness keeps passing, so nothing anywhere reports a problem "
        "— and the coverage guard, which can only see literal names, goes quiet about the one "
        "contract that spent months as a document nobody read.",
    ),
    Mutation(
        "read a question nobody could ask as an answer of zero",
        "claim 6 — erasure is complete to a declared boundary",
        "claim-6",
        ["python", "-m", "pytest", "tests/test_erasure_verify.py", "-q"],
        "count_that_was_never_taken",
        _read_an_unaskable_question_as_an_empty_answer,
        "`None` and `0` are one keystroke apart and mean opposite things, and every path that "
        "produces `None` here is a path where the estate could not be asked: a table that does "
        "not exist, a query that failed, a feature group nobody created. Collapsing them turns "
        "the legs nobody could check into the legs that passed — on a certificate whose entire "
        "purpose is to refuse to say 'erased' until every leg confirms.",
    ),
    Mutation(
        "let an exception outlive the grant that made it one",
        "waivers",
        "waivers",
        ["scripts/check_waivers.py"],
        "EXPIRED on",
        _let_an_exception_outlive_its_grant,
        "Nothing is edited to make this happen — that is what makes it the one control in the "
        "doctrine a clock has to enforce. The date passes on an ordinary morning, with no commit "
        "behind it, and the reason the exception was granted stopped being true some weeks "
        "earlier without anybody being present for the moment. Moving the date back is how the "
        "mutation reproduces a Tuesday.",
    ),
    Mutation(
        "trust every branch and pull request in the repository",
        "oidc subjects",
        "oidc-subjects",
        ["scripts/check_oidc_subjects.py"],
        "trusts every branch and every pull request",
        _trust_every_branch_in_the_repository,
        "The single most consequential line in the bootstrap layer, and the mutation is what "
        "somebody writes on the afternoon a deploy will not run from a branch. checkov's "
        "CKV_AWS_358 reads only the first value of the condition list, so a scanner-green "
        "estate can hand its deploy role to a pull request a stranger opens against a fork.",
    ),
    Mutation(
        "remove an endpoint the estate reaches a service through",
        "vpc endpoints",
        "vpc-endpoints",
        ["scripts/check_vpc_endpoints.py"],
        "with no way out of the VPC",
        _drop_an_endpoint_the_estate_reaches_through,
        "There is no NAT gateway, on purpose — so a service with no endpoint does not fail, it "
        "waits. The SDK retries, the socket times out in its own time, and the control plane "
        "reports the application healthy throughout. Trimming an endpoint that looks unused is "
        "an ordinary cost saving, and it has cost hours in the sibling project twice.",
    ),
    Mutation(
        "rename a lakehouse table in one of its three descriptions",
        "lakehouse wiring",
        "lakehouse-wiring",
        ["scripts/check_lakehouse_wiring.py"],
        "the descriptions disagree",
        _rename_a_lakehouse_table_in_one_of_its_three_descriptions,
        "Terraform, dbt and the queries describe one lakehouse three times, and only one of them "
        "is checked against a catalogue. `dbt parse` resolves a source against `sources.yml`, so "
        "a table that exists in no warehouse compiles perfectly — and a resolver pointed at an "
        "empty table reads zero rows and calls it zero watt-hours, which is a settlement.",
    ),
    Mutation(
        "change the seed on one side of the experiment",
        "model pins agree",
        "model-pins-agree",
        ["scripts/check_model_pins_agree.py"],
        "gradient.py says",
        _change_the_seed_on_one_side_of_the_experiment,
        "ADR-0005 promises the same snapshot, image and seed yield the same metrics, and the "
        "promise is void the moment the two sides disagree about what the seed is. Both runs "
        "still succeed and both still report metrics; the metrics differ for a reason nobody "
        "looks for, because the obvious explanation is always the data.",
    ),
    Mutation(
        "run the equivalence tier against a different Flink than the deployment",
        "flink versions agree",
        "flink-versions-agree",
        ["scripts/check_flink_versions_agree.py"],
        "Equivalence with a Flink nobody is running",
        _run_the_equivalence_tier_against_a_different_flink,
        "Lowering a floor to make an install resolve is a five-second edit with no visible "
        "consequence: the tier still runs, still passes, and still prints that the adapter "
        "matches the core. It now says so about a Flink nobody has deployed, which is worse "
        "than no tier at all because it occupies the place where the evidence should be.",
    ),
    Mutation(
        "leave the three expensive things standing for a week",
        "cost envelope",
        "cost-envelope",
        ["scripts/check_cost_envelope.py"],
        "The design is wrong before the budget is",
        _leave_the_expensive_things_standing_overnight,
        "The estimate is dominated by how long the estate stands, not by its shape — €1.17 for "
        "the hour a capture takes, €196 for a week — so the plausible way past the ceiling is "
        "not a bigger cluster, it is an unattended soak. Somebody wants a week of drift data, "
        "or leaves the endpoint up between sessions. CLAUDE.md says the three expensive things "
        "are never left standing, and this is the arithmetic that refuses to let them be.",
    ),
    Mutation(
        "stop generating one of the defects the cast declares",
        "every declared cohort is checked",
        "evals-cases",
        ["python", "-m", "evals.cases"],
        "contains no meter",
        _stop_generating_a_declared_defect,
        "A fixed list of cases cannot catch its own omission — it looks complete by being the "
        "list. Two of the cast's declared defects were exercised by nothing at all until an "
        "audit found them, and this is the shape of that: the generator quietly stops emitting "
        "a cohort, every case still passes, and the matrix reports green over a hole.",
    ),
    Mutation(
        "let a live case pass whatever the estate hands it",
        "the live case matrix",
        "cases-live",
        ["python", "-m", "pytest", "tests/scripts/test_cases_live.py", "-q"],
        "an_empty_run_fails_every_case",
        _let_a_live_case_pass_whatever_it_is_given,
        "The plausible version of this is not sabotage, it is a refactor: `_require` reads like a "
        "formatting helper, and a run that produced no evidence at all satisfies every assertion "
        "written as 'nothing in the landing prefix contradicts us'. The matrix that watches the "
        "deployed estate is the one that would report seven of seven over an empty bucket.",
    ),
    Mutation(
        "round the replay's measured offset onto the interval grid",
        "claim 2 — replay is identical",
        "claim-2",
        ["python", "-m", "pytest", "tests/scripts/test_replay_live.py", "-q"],
        "different_offset_is_identical",
        _round_the_replay_offset_onto_the_interval_grid,
        "This was the code, and rounding to the grid is the obvious tidy-up: the windows are "
        "fifteen minutes, so the offset between two runs of them ought to be too. It is not — "
        "`data/publish.py` shifts by `now - day_end`, so the two days sit on parallel grids of "
        "their own — and the rounding moved the offset off the answer by up to seven minutes. "
        "Claim 2 then reported thousands of disagreements on a run where nothing was wrong, "
        "which is the failure that makes a green harness worth less than no harness.",
    ),
    Mutation(
        "bound the telemetry read over the whole prefix instead of per substation",
        "the curtailment decision reads every substation",
        "decide-live",
        ["python", "-m", "pytest", "tests/scripts/test_decide_live.py", "-q"],
        "when_one_of_them_dominates_the_prefix",
        _list_the_whole_telemetry_prefix_at_once,
        "The bug this repository shipped, and it printed nothing while doing it. One listing, "
        "newest two hundred keys — and because the prefix holds every capture the estate has "
        "driven, all two hundred came from a single substation. Three of the four were reported "
        "as having no telemetry on an estate that was emitting for all of them, so the one "
        "decision with a physical consequence was taken over a quarter of the network.",
    ),
    Mutation(
        "open the reference history on the day of the run",
        "point-in-time joins resolve for historical rows",
        "seed-reference",
        ["python", "-m", "pytest", "tests/scripts/test_seed_reference.py", "-q"],
        "reaches_back_before_the_stream_day",
        _open_the_reference_history_on_the_day_of_the_run,
        "Dating the opening SCD-2 version to the day being seeded is the reading of 'this is the "
        "day the scenario is on', and it is wrong in the direction that hides: `valid_from <= t` "
        "matches nothing earlier, so every row from an earlier capture resolves to no tariff and "
        "no customer. Those rows do not appear as wrong in the settlement. They do not appear.",
    ),
    Mutation(
        "import a 3.11 builtin into a job that runs on Glue 4.0",
        "glue runtime",
        "glue-runtime",
        ["scripts/check_glue_runtime.py"],
        "3.11+",
        _use_a_python_311_builtin_in_a_glue_job,
        "`datetime.UTC` is what every modern editor and every local test run accepts, because the "
        "laptop is on 3.12. Glue 4.0 is on 3.10. Three maintenance jobs were written this way, "
        "and the failure arrives as an ImportError in a cloud log nobody reads on the day the "
        "reaper is supposed to run.",
    ),
    Mutation(
        "let the transport send the meter id as the record's partition",
        "partition vocabulary",
        "partition-vocabulary",
        ["scripts/check_partition_vocabulary.py"],
        "the core declares its partitions",
        _let_the_transport_send_the_meter_as_the_partition,
        "The bug this repository actually shipped. Valid HCL, correct core, seven green "
        "claims — and every substation idle for ever in the one place the two meet.",
    ),
    Mutation(
        "import a cloud SDK into the stream core",
        "core purity",
        "core_purity",
        ["scripts/check_core_is_pure.py"],
        "no framework and no cloud SDK",
        _import_a_cloud_sdk_into_the_core,
        "One lookup, three levels down in a windowing function. Nothing fails; the suite "
        "simply stops being runnable by a stranger, and that was the whole of the evidence.",
    ),
    Mutation(
        "let the stream core read the wall clock",
        "core purity",
        "core_purity",
        ["scripts/check_core_is_pure.py"],
        "reads the wall clock",
        _let_the_core_read_the_wall_clock,
        "`Instant.now()` is the most natural API in the world and it silently ends claim 2.",
    ),
    Mutation(
        "let the stream core reach back into the rest of the package",
        "core purity",
        "core_purity",
        ["scripts/check_core_is_pure.py"],
        "outside watermark.core",
        _let_the_core_reach_back_into_the_package,
        "Looks like reuse of our own pure code. It is the boundary dissolving one honest "
        "import at a time.",
    ),
    Mutation(
        "publish a window the watermark has not passed",
        "claim 1",
        "claim-1",
        ["-m", "evals.watermark"],
        "interval end",
        _publish_before_the_window_closes,
        "One condition, removed. It does not raise, does not log, and produces totals that "
        "look entirely normal — computed from whatever had arrived by then.",
    ),
    Mutation(
        "accept a device reporting from the future",
        "claim 1",
        "claim-1",
        ["-m", "evals.watermark"],
        "quarantined for clock skew",
        _accept_a_device_reporting_from_the_future,
        "One meter three hours fast then closes every window in the grid early, on incomplete "
        "data, silently. The most damaging ordering mistake available here.",
    ),
    Mutation(
        "deduplicate by arrival instead of by content",
        "claim 2",
        "claim-2",
        ["-m", "evals.replay"],
        "shuffling the input changed the output",
        _keep_whichever_copy_arrived_first,
        "The natural implementation. Two copies differ in ingestion time and firmware, so "
        "which one is kept is an accident of partitioning — and every total stays correct.",
    ),
    Mutation(
        "let a redelivery change a lineage id",
        "claim 2",
        "claim-2",
        ["-m", "evals.replay"],
        "lineage id",
        _let_a_redelivery_change_the_lineage,
        "Found by the harness rather than by review: at-least-once delivery then gives every "
        "published total a new lineage id while every number stays identical.",
    ),
    Mutation(
        "bake a window length into the Flink call",
        "adapter thinness",
        "adapter_thinness",
        ["scripts/check_adapter_is_thin.py"],
        "semantic literal",
        _bake_a_window_into_the_framework,
        "One line, added because a reading was being dropped. The core keeps passing because "
        "the offline suite exercises the core, and the deployed job means something else.",
    ),
    Mutation(
        "hold personal data with no declared purpose",
        "entity contracts",
        "claim-6",
        ["scripts/check_contracts.py"],
        "declares no purpose",
        _let_a_contract_hold_personal_data_with_no_purpose,
        "Not deletion — a contract added in a hurry without one. GDPR Art. 5(1)(b) requires a "
        "specified purpose, and one that is not written down is not specified.",
    ),
    Mutation(
        "reference an entity that does not exist",
        "entity contracts",
        "claim-6",
        ["scripts/check_contracts.py"],
        "does not exist",
        _point_a_contract_at_an_entity_that_does_not_exist,
        "A rename in one place. The join compiles, returns nothing, and reads as a customer "
        "with no consumption.",
    ),
    Mutation(
        "merge the two parity mechanisms",
        "parity independence",
        "adapter_thinness",
        ["scripts/check_parity_paths_are_independent.py"],
        "compare it with itself",
        _merge_the_two_parity_mechanisms,
        "The tidy refactor. They do look alike, and after the merge claim 3 reports green in "
        "eleven milliseconds forever.",
    ),
    Mutation(
        "let a feature be a double",
        "claim 3",
        "claim-3",
        ["-m", "evals.parity"],
        "tolerance",
        _let_a_feature_be_a_double,
        "Plausible as a simplification — the value is a mean. It replaces integer equality "
        "with a comparison that only passes with a tolerance.",
    ),
    Mutation(
        "drop a freshness budget",
        "claim 4",
        "claim-4",
        ["scripts/check_contracts.py"],
        "greater than 0",
        _drop_a_freshness_budget,
        "A feature added in a hurry by copying the one above it. Claim 4 then holds vacuously "
        "for it, and the decision path reading it never falls back.",
    ),
    Mutation(
        "let the fallback read the feature store",
        "claim 4",
        "claim-4",
        ["scripts/check_contracts.py"],
        "reads the feature store",
        _let_the_fallback_read_the_feature_store,
        "What the first draft of proportional_throttle actually did, caught by the harness. A "
        "fallback that needs the feature store is unavailable exactly when the primary is.",
    ),
    Mutation(
        "automate a decision about a person",
        "claim 7",
        "claim-7",
        ["scripts/check_contracts.py"],
        "Art. 22",
        _automate_a_decision_about_a_person,
        "One word, and the change somebody makes when the inspection queue is three weeks deep.",
    ),
    Mutation(
        "accept an unnamed reviewer",
        "claim 7",
        "claim-7",
        ["-m", "evals.oversight"],
        "blank reviewer",
        _accept_an_unnamed_reviewer,
        "Plausible as a fix for an integration test with no real inspectors in it. An entry "
        "with a blank signature then actuates, and the record looks exactly like a reviewed one.",
    ),
    Mutation(
        "let a policy grant match any tag instead of all",
        "policy evaluation",
        "core_purity",
        ["scripts/check_policy_access.py"],
        "and must not",
        _let_a_policy_grant_be_a_disjunction,
        "One word, and it reads identically in the YAML. A purpose-limited grant becomes a "
        "sensitivity-limited one and nothing anywhere looks different.",
    ),
    Mutation(
        "certify an incomplete erasure",
        "claim 6",
        "claim-6",
        ["-m", "evals.erasure"],
        "certificate was issued",
        _certify_an_incomplete_erasure,
        "Plausible as a fix for a flaky orchestration step. It is the change that makes claim "
        "6 a lie rather than a limitation.",
    ),
    Mutation(
        "let a second leg declare a boundary",
        "claim 6",
        "claim-6",
        ["-m", "evals.erasure"],
        "second leg declared a boundary",
        _let_a_second_leg_declare_a_boundary,
        "How 'we could not finish that one either' becomes a second boundary, and then a "
        "third, until the certificate declares one around everything it did not do.",
    ),
    Mutation(
        "make the bias gate one-sided again",
        "claim 5",
        "claim-5",
        ["-m", "evals.promotion"],
        "precision gap",
        _flatter_the_bias_gate,
        "The bug the analysis actually found, restored. A five-fold difference in label "
        "coverage then reads as a pass and the model that should be refused promotes.",
    ),
    Mutation(
        "transcribe the state bucket into a repository variable",
        "deploy inputs",
        "deploy-inputs",
        ["scripts/check_deploy_inputs.py"],
        "vars.TF_STATE_BUCKET",
        _transcribe_the_state_bucket_into_a_repository_variable,
        "A value copied by hand looks like an independent setting. It is green the day it is "
        "pasted and every day after, and the failure arrives when the layer that owns the name "
        "changes it — in a settings page nothing in this repository can see.",
    ),
)


# ── Running them ─────────────────────────────────────────────────────────────


def _argv(command: list[str]) -> list[str]:
    """The full argv for a mutation's check, run out of the mutated copy.

    `python -m` for anything installed as a module, so the copy is the code under test rather
    than whatever is on PATH; an explicit interpreter for a script path, because handing a
    bare path to the `-m` branch produces a command that fails for a reason having nothing to
    do with the gate — and the harness then correctly reports "something failed, but not this
    gate", which is an hour spent reading a true statement about the wrong thing.
    """
    if command[0] == "pytest":
        return [sys.executable, "-m", "pytest", *command[1:]]
    if command[0] == "-m":
        return [sys.executable, *command]
    if command[0].endswith(".py"):
        return [sys.executable, *command]
    return list(command)


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    # PYTHONPATH points at the *copy*, not at the editable install. Without it the mutation is
    # planted in a directory nothing imports from, every gate passes, and this script reports
    # a perfect score while proving nothing whatsoever.
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(cwd / "src")
    return subprocess.run(  # noqa: S603 — fixed command lists, no shell
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


def main() -> int:
    print("gate-proof: establishing the baseline")
    baseline = _run([sys.executable, "-m", "pytest", "-q"], ROOT)
    if baseline.returncode != 0:
        print("the suite is not green; every mutation below would be meaningless", file=sys.stderr)
        print(baseline.stdout[-4000:], file=sys.stderr)
        return 1
    print("  baseline green\n")

    passes: list[str] = []
    failures: list[str] = []
    stale: list[str] = []

    for mutation in MUTATIONS:
        with tempfile.TemporaryDirectory() as raw:
            copy = Path(raw) / "watermark"
            shutil.copytree(
                ROOT,
                copy,
                ignore=shutil.ignore_patterns(
                    ".venv",
                    ".venv-checkov",
                    ".git",
                    "__pycache__",
                    ".pytest_cache",
                    ".ruff_cache",
                    ".terraform",
                    "out",
                ),
            )
            try:
                applied = mutation.apply(copy)
            except (ValueError, OSError) as exc:
                applied = False
                print(f"         ({type(exc).__name__}: {exc})")
            if not applied:
                stale.append(mutation.name)
                print(f"  STALE  {mutation.name} — its target has moved; the proof is not running")
                continue

            result = _run(_argv(mutation.command), copy)
            output = (result.stdout + result.stderr).lower()

            if result.returncode == 0:
                failures.append(mutation.name)
                print(f"  FAIL   {mutation.name} — {mutation.gate} accepted the violation")
            elif mutation.expect.lower() not in output:
                failures.append(mutation.name)
                print(
                    f"  FAIL   {mutation.name} — something failed, but not {mutation.gate}; "
                    f"{mutation.expect!r} is absent from the output"
                )
            else:
                passes.append(mutation.name)
                print(f"  ok     {mutation.name} — refused by {mutation.gate}")

    print()
    print(f"gate-proof: {len(passes)} refused, {len(failures)} accepted, {len(stale)} stale")
    if stale:
        print("\nstale mutations point at code that has moved. Update them:", file=sys.stderr)
        for name in stale:
            print(f"  {name}", file=sys.stderr)
    return 1 if failures or stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
