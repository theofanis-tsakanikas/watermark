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


MUTATIONS: tuple[Mutation, ...] = (
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
