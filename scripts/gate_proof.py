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
