"""The label source decides what the model is fitted against, and it must actually decide.

`docs/BIAS-FINDING.md` measured a model that is perfectly precise where the dispatch log is
densest and almost useless where it is thin — because inspectors went where they had always
gone, so `confirmed` is complete in one tercile and full of holes in the other. The promotion
gate refuses that model. It promotes the same model class, over the same population and the
same thresholds, fitted against ground truth.

That difference is the whole finding, and it is only demonstrable end to end if the pipeline
can be *told* which labels to use. A parameter that is threaded through four files and quietly
ignored at the end would leave the run describing one thing and doing another — which is the
provenance failure the finding is itself about.
"""

from __future__ import annotations

import csv

import pytest

from data.labels import labels as population_labels
from watermark.models.snapshot import LABEL_COLUMNS, digest_of, rows_as_of


@pytest.fixture
def population(tmp_path):
    """The rows `make package-ml` ships, written the same way."""
    path = tmp_path / "population.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["entity_id", "deprivation_decile", "score", "confirmed", "truly"])
        for label in population_labels():
            writer.writerow(
                [
                    label.meter_id,
                    label.deprivation_decile,
                    label.score,
                    int(label.confirmed),
                    int(label.truly_tampering),
                ]
            )
    return path


def test_both_sources_are_readable(population) -> None:
    for source in LABEL_COLUMNS:
        rows = rows_as_of(population, "snap-1", "2026-08-12T00:00:00Z", source)
        assert rows, f"{source} produced no rows"
        assert {row["label_source"] for row in rows} == {source}


def test_the_two_sources_disagree(population) -> None:
    """The parameter is inert unless the two answers differ, and they must differ *this* way.

    Ground truth has strictly more positives than the dispatch log: an inspector only ever
    confirmed a meter that was genuinely tampering, and did not visit most of them. If these
    ever came out equal the fixture would have lost the defect and the finding with it.
    """
    from_log = rows_as_of(population, "snap-1", "2026-08-12T00:00:00Z", "dispatch_log")
    from_truth = rows_as_of(population, "snap-1", "2026-08-12T00:00:00Z", "randomised_inspection")

    confirmed = sum(int(row["confirmed"]) for row in from_log)
    truly = sum(int(row["confirmed"]) for row in from_truth)

    assert truly > confirmed, (
        f"ground truth has {truly} positives and the dispatch log {confirmed}. The dispatch log "
        "cannot have more — an inspector only confirms what is true — and equal means the "
        "fixture no longer carries the under-inspection that docs/BIAS-FINDING.md measured."
    )


def test_an_unknown_source_is_refused(population) -> None:
    """Not defaulted to the dispatch log. A typo that silently trains on the biased labels and
    registers a model anyway is exactly the run nobody would think to question."""
    with pytest.raises(KeyError):
        rows_as_of(population, "snap-1", "2026-08-12T00:00:00Z", "whatever_was_lying_around")


def test_the_digest_separates_them(population) -> None:
    """Two runs over one snapshot with different labels are two different datasets.

    `digest_of` is what the model card carries and what makes a training run arguable months
    later. If it collapsed these two the registry would hold two models, trained on different
    data, claiming the same provenance.
    """
    a = digest_of(rows_as_of(population, "snap-1", "2026-08-12T00:00:00Z", "dispatch_log"))
    b = digest_of(rows_as_of(population, "snap-1", "2026-08-12T00:00:00Z", "randomised_inspection"))
    assert a != b
