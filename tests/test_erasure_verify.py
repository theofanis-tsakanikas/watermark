"""The decision table for an independently-verified erasure, exercised where it can be.

Every case here is one a live run reaches only by being broken, which is exactly why they are
here: the shapes this module exists to catch cannot be produced on demand against a real estate
without breaking a real estate.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from watermark.contracts import load  # noqa: E402
from watermark.erasure.certificate import BOUNDABLE as CERTIFICATE_BOUNDABLE  # noqa: E402
from watermark.erasure.scope import scope_from_contracts  # noqa: E402
from watermark.erasure.verify import (  # noqa: E402
    BOUNDABLE,
    Finding,
    Observation,
    report,
    residual_from_certificate,
    verdict,
)


def _expected_legs() -> tuple[str, ...]:
    """Read `EXPECTED_LEGS` out of the script's source rather than importing it.

    Importing would need boto3, which is a cloud extra — so the guard would skip on a laptop and
    in any CI job that installs only the base set. A guard that skips is a guard that is not
    there, and this one exists precisely because the list it watches is a hand copy.
    """
    tree = ast.parse((ROOT / "scripts" / "erasure_legs_live.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "EXPECTED_LEGS" for target in node.targets
        ):
            return tuple(ast.literal_eval(node.value))
    raise AssertionError("scripts/erasure_legs_live.py declares no EXPECTED_LEGS")


# ── the deletion legs ────────────────────────────────────────────────────────


def test_a_surviving_row_contradicts_the_certificate() -> None:
    result = verdict(Observation(leg="lakehouse_rows", rows=3))
    assert result.finding is Finding.CONTRADICTED
    assert "3 rows" in result.detail


def test_no_rows_confirms_the_leg() -> None:
    assert verdict(Observation(leg="lakehouse_rows", rows=0)).finding is Finding.CONFIRMED


def test_a_count_that_was_never_taken_is_not_a_count_of_zero() -> None:
    """The failure mode this module was written for.

    A table that does not exist, a query that failed, a feature group nobody created — each
    returns nothing, and nothing read as zero is a green tick over an erasure that did not run.
    """
    result = verdict(Observation(leg="training_sets", rows=None))
    assert result.finding is Finding.UNOBSERVABLE
    assert not result.ok


def test_an_explicit_reason_survives_into_the_finding() -> None:
    result = verdict(Observation(leg="offline_store", unobservable_because="Athena refused"))
    assert result.finding is Finding.UNOBSERVABLE
    assert "Athena refused" in result.detail


# ── crypto_shred, the leg where absence is ambiguous ─────────────────────────


@pytest.mark.parametrize("state", ["PendingDeletion", "PendingReplicaDeletion", "Disabled"])
def test_a_key_that_cannot_decrypt_confirms_the_shred(state: str) -> None:
    assert verdict(Observation(leg="crypto_shred", key_state=state)).ok


def test_a_live_key_contradicts_a_certified_shred() -> None:
    result = verdict(Observation(leg="crypto_shred", key_state="Enabled"))
    assert result.finding is Finding.CONTRADICTED
    assert "can still decrypt" in result.detail


def test_a_missing_key_with_a_marker_is_a_shred() -> None:
    assert verdict(Observation(leg="crypto_shred", key_state=None, shred_marker=True)).ok


def test_a_missing_key_with_no_marker_is_not_a_shred() -> None:
    """Terraform stops declaring a shredded subject's key, so the alias goes. A subject who never
    had one looks identical, and the two have opposite right answers."""
    result = verdict(Observation(leg="crypto_shred", key_state=None, shred_marker=False))
    assert result.finding is Finding.UNOBSERVABLE
    assert "never have had a key" in result.detail


# ── model_artefacts, the leg that cannot be completed ────────────────────────


def test_the_bounded_leg_must_declare_its_residual() -> None:
    result = verdict(Observation(leg=BOUNDABLE, residual=""))
    assert result.finding is Finding.CONTRADICTED
    assert "keeps the subject's contribution in its weights" in result.detail


def test_a_declared_residual_confirms_the_bounded_leg() -> None:
    result = verdict(Observation(leg=BOUNDABLE, residual="until the next scheduled retrain"))
    assert result.ok
    assert "until the next scheduled retrain" in result.detail


def test_the_boundable_leg_is_the_same_one_the_certificate_names() -> None:
    """Two modules name it, so something has to insist they name the same one."""
    assert BOUNDABLE == CERTIFICATE_BOUNDABLE


# ── the report, and the omission a list cannot catch ─────────────────────────


def test_a_declared_leg_nobody_looked_at_fails_the_run() -> None:
    verdicts = report(
        [Observation(leg="lakehouse_rows", rows=0)], ("lakehouse_rows", "online_store")
    )
    missing = next(item for item in verdicts if item.leg == "online_store")
    assert missing.finding is Finding.UNOBSERVABLE
    assert "nothing went and looked at it" in missing.detail


def test_an_observation_for_a_leg_the_scope_does_not_declare_is_reported() -> None:
    """The other direction: a leg renamed in the scope leaves this checking something nobody
    asked about, which would otherwise read as extra rigour."""
    verdicts = report([Observation(leg="ghost_leg", rows=0)], ("lakehouse_rows",))
    ghost = next(item for item in verdicts if item.leg == "ghost_leg")
    assert ghost.finding is Finding.CONTRADICTED


def test_the_order_is_the_scope_s_order() -> None:
    expected = ("crypto_shred", "lakehouse_rows", "online_store")
    verdicts = report([Observation(leg="lakehouse_rows", rows=0)], expected)
    assert tuple(item.leg for item in verdicts) == expected


# ── the copy that must not drift ─────────────────────────────────────────────


def test_the_collector_covers_exactly_the_legs_the_scope_declares() -> None:
    """`EXPECTED_LEGS` is a hand copy of `ErasureScope.legs`, kept because the script must run
    without a resolvable contract root. A copy nobody compares is a copy that drifts."""
    scope = scope_from_contracts(load())
    assert scope.legs == _expected_legs()


# ── reading the certificate the state machine actually writes ────────────────

CERTIFICATE = {
    "subject_id": "C00007-NEW",
    "legs": [
        {"leg": "crypto_shred", "confirmed": True},
        {"leg": "physical_deletion", "confirmed": True},
        {
            "leg": "model_artefacts",
            "confirmed": True,
            "boundary": "declared",
            "residual_days": 30,
            "note": "Models trained before this request retain the subject statistically.",
        },
    ],
}


def test_the_certificate_is_json_inside_a_json_string() -> None:
    """What Step Functions writes, and what crashed the first live run.

    `States.JsonToString` puts a JSON *string* in S3, so one `json.loads` returns `str` — and
    reaching for `.get` on it raises `AttributeError` in a module whose whole purpose is to
    return a verdict rather than to crash.
    """
    body = json.dumps(json.dumps(CERTIFICATE)).encode("utf-8")
    observation = residual_from_certificate(body)
    assert verdict(observation).ok
    assert "retain the subject statistically" in verdict(observation).detail
    assert "30 days" in verdict(observation).detail


def test_a_plain_object_is_read_too() -> None:
    """The encoding is a property of how the state machine is wired, not of the document."""
    assert verdict(residual_from_certificate(json.dumps(CERTIFICATE))).ok


def test_the_leg_key_is_leg_and_not_name() -> None:
    """The scope calls it a leg name; the state machine writes `leg`. Reading the wrong key
    finds nothing, and finding nothing must be unobservable rather than a silent pass."""
    wrong = {"legs": [{"name": "model_artefacts", "note": "x", "residual_days": 30}]}
    result = verdict(residual_from_certificate(json.dumps(wrong)))
    assert result.finding is Finding.UNOBSERVABLE
    assert "names no `model_artefacts` leg" in result.detail


def test_a_certificate_that_is_not_json_is_unobservable_not_a_crash() -> None:
    result = verdict(residual_from_certificate(b"<html>403</html>"))
    assert result.finding is Finding.UNOBSERVABLE


def test_a_bounded_leg_with_no_note_and_no_window_is_contradicted() -> None:
    """The one dishonest outcome available: reporting the unreachable leg as complete."""
    bare = {"legs": [{"leg": "model_artefacts", "confirmed": True}]}
    assert verdict(residual_from_certificate(json.dumps(bare))).finding is Finding.CONTRADICTED


def test_a_contradiction_carries_the_rows_it_counted() -> None:
    """A leg that reports "four rows survived" and stops has spent a whole capture saying that
    something is wrong without saying what.

    It matters most here because two very different findings produce the same count. A DELETE
    whose predicate is wrong leaves rows written *before* the erasure; the offline store being
    eventually consistent leaves rows written *after* it, which the leg could not have deleted
    and which say nothing about the predicate. Only the timestamps tell them apart.
    """
    result = verdict(
        Observation(leg="offline_store", rows=1, note="survivors: M00007 2026-08-16T22:59:00Z")
    )
    assert result.finding is Finding.CONTRADICTED
    assert "M00007" in result.detail
    assert "1 rows" in result.detail


def test_a_confirmed_leg_needs_no_note() -> None:
    """The note is evidence for a refusal, not decoration on a pass."""
    assert verdict(Observation(leg="offline_store", rows=0)).ok
