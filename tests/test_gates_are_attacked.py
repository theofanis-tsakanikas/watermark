"""No gate ships without the mutation that breaks it.

`make gate-proof` proves that the gates it knows about still bite. It cannot prove anything
about a gate nobody told it exists, and a gate that has never been shown to fail is a comment
— so the coverage itself has to be checked by something, and this is it.

The mapping is declared on each mutation rather than inferred from what it edits, because
inferring it would mean this test and the harness agreeing on a heuristic, and a heuristic
that goes wrong here goes wrong silently in the direction of "covered".
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPOSITORY = Path(__file__).resolve().parents[1]
GATES = REPOSITORY / "src" / "watermark" / "gates"


def _load_gate_proof() -> ModuleType:
    """`scripts/` is not an importable package, and should not become one for a test."""
    path = REPOSITORY / "scripts" / "gate_proof.py"
    spec = importlib.util.spec_from_file_location("gate_proof", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["gate_proof"] = module
    spec.loader.exec_module(module)
    return module


#: The claims a mutation may name instead of a gate module. Not every control in this system is
#: a module under `gates/`: claim 1 lives in a windowing condition and claim 2 in a sort order,
#: and both are attacked through the harness that scores them.
CLAIMS = {f"claim-{number}" for number in range(1, 8)}

#: Controls that guard how the repository is *wired* rather than what it computes. They have no
#: runtime module because nothing imports them — they read workflows and Terraform and refuse a
#: shape. `deploy-inputs` is the rule that no value the account can be asked for may be
#: transcribed into a settings page, which is a control precisely because its failure mode is
#: silent and lives outside the repository.
WIRING = {"deploy-inputs"}


def _targets() -> set[str]:
    return {mutation.module for mutation in _load_gate_proof().MUTATIONS}


def test_every_gate_module_is_attacked() -> None:
    gates = {path.stem for path in GATES.glob("*.py") if path.stem != "__init__"}
    assert gates <= _targets(), f"gates with no mutation against them: {sorted(gates - _targets())}"


def test_every_mutation_names_a_gate_or_a_claim_that_exists() -> None:
    """The other direction. A mutation pointing at a module that has been renamed or deleted
    would be reported STALE by the harness, but only once somebody runs it; this fails in the
    fast suite instead."""
    gates = {path.stem for path in GATES.glob("*.py") if path.stem != "__init__"}
    unknown = _targets() - gates - CLAIMS - WIRING
    assert not unknown, f"mutations against neither a gate nor a claim: {sorted(unknown)}"


def test_both_claims_proved_in_this_phase_are_attacked() -> None:
    """Claims 1 and 2 are what phase 1 delivers. A claim with a harness and no mutation is a
    harness nobody has seen refuse anything."""
    assert {"claim-1", "claim-2"} <= _targets()


def test_no_two_mutations_share_a_name() -> None:
    """The name is what a failure is reported under. Two of them and the report is ambiguous
    at exactly the moment somebody is reading it in a hurry."""
    names = [mutation.name for mutation in _load_gate_proof().MUTATIONS]
    assert len(names) == len(set(names))


def test_every_mutation_states_why_it_is_plausible() -> None:
    """An absurd mutation proves the gate handles absurdity, which nobody was worried about.
    The rationale is where the author has to argue that somebody could actually do this."""
    for mutation in _load_gate_proof().MUTATIONS:
        assert len(mutation.rationale) > 40, mutation.name
