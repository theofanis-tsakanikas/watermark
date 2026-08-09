"""The adapter carries no numbers — ADR-0003's second part, enforced."""

from __future__ import annotations

from pathlib import Path

from watermark.gates.adapter_thinness import report, scan

REPOSITORY = Path(__file__).resolve().parents[2]


def _adapter(tmp_path: Path, source: str) -> list:
    directory = tmp_path / "streaming"
    directory.mkdir()
    (directory / "job.py").write_text(source, encoding="utf-8")
    return scan(directory, tmp_path)


def test_the_adapter_in_this_repository_is_thin() -> None:
    findings = scan(REPOSITORY / "streaming", REPOSITORY)
    assert findings == [], report(findings)


def test_a_window_length_written_into_the_call_is_refused(tmp_path: Path) -> None:
    """The Tuesday line. The core keeps passing; the deployed job means something else."""
    findings = _adapter(tmp_path, "w = Time.minutes(15)\n")
    assert [f.rule for f in findings] == ["semantic literal"]


def test_a_bounded_out_of_orderness_strategy_is_refused(tmp_path: Path) -> None:
    """It holds the bound inside Flink, where no offline test can read it."""
    findings = _adapter(tmp_path, "s = WatermarkStrategy.for_bounded_out_of_orderness(bound)\n")
    assert any("out-of-orderness bound" in f.reason for f in findings)


def test_a_monotonous_timestamps_strategy_is_refused(tmp_path: Path) -> None:
    findings = _adapter(tmp_path, "s = WatermarkStrategy.for_monotonous_timestamps()\n")
    assert any("is not" in f.reason for f in findings)


def test_a_name_from_the_core_is_fine(tmp_path: Path) -> None:
    source = "from watermark.core.records import METER_INTERVAL\n\nw = window(METER_INTERVAL)\n"
    assert _adapter(tmp_path, source) == []


def test_an_index_is_not_a_policy(tmp_path: Path) -> None:
    """0 and 1 carry no decision. Refusing them would make the gate something people work
    around rather than something they satisfy."""
    assert _adapter(tmp_path, "first = items[0]\nnext_one = items[1]\n") == []


def test_an_unparseable_module_is_a_violation_not_a_skip(tmp_path: Path) -> None:
    assert _adapter(tmp_path, "def broken(:\n")[0].rule == "unparseable"


def test_the_clean_report_says_what_was_checked() -> None:
    assert "no semantic literal" in report([])
