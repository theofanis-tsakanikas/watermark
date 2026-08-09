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
    #: The gate module under `src/watermark/gates/` this mutation attacks. Declared rather
    #: than inferred, so `tests/test_gates_are_attacked.py` can insist that every gate in the
    #: package is named here — a gate nobody attacks is a gate nobody has seen refuse.
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
