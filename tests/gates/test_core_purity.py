"""The gate that keeps claims 1 to 4 checkable without a cluster."""

from __future__ import annotations

from pathlib import Path

import pytest

from watermark.gates.core_purity import Finding, report, scan

REPOSITORY = Path(__file__).resolve().parents[2]


def _core(tmp_path: Path, source: str, name: str = "windows.py") -> list[Finding]:
    """A one-module core, in a repository shaped like this one."""
    core = tmp_path / "src" / "watermark" / "core"
    core.mkdir(parents=True)
    (core / name).write_text(source, encoding="utf-8")
    return scan(core, tmp_path)


class TestTheRealCore:
    def test_the_core_in_this_repository_is_clean(self) -> None:
        """The gate's own subject. If this fails, the repository has stopped being able to
        make its central claim, and the message says which line did it."""
        findings = scan(REPOSITORY / "src" / "watermark" / "core", REPOSITORY)
        assert findings == [], report(findings)


class TestImports:
    def test_the_standard_library_is_fine(self, tmp_path: Path) -> None:
        assert _core(tmp_path, "import bisect\nfrom decimal import Decimal\n") == []

    def test_the_core_may_import_itself(self, tmp_path: Path) -> None:
        source = "from watermark.core.time import Instant\nfrom .time import Duration\n"
        assert _core(tmp_path, source) == []

    def test_a_cloud_sdk_is_refused(self, tmp_path: Path) -> None:
        findings = _core(tmp_path, "import boto3\n")
        assert len(findings) == 1
        assert "boto3" in findings[0].detail
        assert "no cloud SDK" in findings[0].reason

    def test_flink_is_refused(self, tmp_path: Path) -> None:
        findings = _core(tmp_path, "from pyflink.datastream import StreamExecutionEnvironment\n")
        assert [f.rule for f in findings] == ["import"]

    def test_a_sibling_package_is_refused(self, tmp_path: Path) -> None:
        """Our own pure code, and still refused. Importing it looks like reuse; it is the
        boundary dissolving one honest import at a time."""
        findings = _core(tmp_path, "from watermark.lineage import LineageId\n")
        assert "outside watermark.core" in findings[0].reason

    def test_a_relative_import_that_escapes_the_core_is_refused(self, tmp_path: Path) -> None:
        findings = _core(tmp_path, "from ..lineage import LineageId\n")
        assert "outside watermark.core" in findings[0].reason

    def test_a_relative_import_climbing_past_the_root_is_refused(self, tmp_path: Path) -> None:
        findings = _core(tmp_path, "from .... import anything\n")
        assert "escaping" in findings[0].detail

    def test_a_dynamic_import_is_refused(self, tmp_path: Path) -> None:
        """A rule that one indirection defeats will be defeated by one indirection."""
        findings = _core(tmp_path, "import importlib\n\nboto3 = importlib.import_module('boto3')\n")
        assert {f.rule for f in findings} == {"import", "ambient state"}


class TestAmbientState:
    @pytest.mark.parametrize(
        "source",
        [
            "from datetime import datetime\n\nx = datetime.now()\n",
            "import time\n\nx = time.time()\n",
            "from datetime import date\n\nx = date.today()\n",
        ],
    )
    def test_the_wall_clock_is_refused(self, tmp_path: Path, source: str) -> None:
        findings = _core(tmp_path, source)
        assert any("wall clock" in f.reason for f in findings)

    def test_a_method_of_our_own_called_now_is_refused_too(self, tmp_path: Path) -> None:
        """Deliberately. `Instant.now()` is the most natural API in the world, and it is the
        one that ends claim 2 without failing anything."""
        findings = _core(tmp_path, "def f(clock):\n    return clock.now()\n")
        assert any("wall clock" in f.reason for f in findings)

    def test_the_environment_is_refused_as_an_attribute_read(self, tmp_path: Path) -> None:
        """`os.environ["REGION"]` is not a call, so the call check never sees it."""
        findings = _core(tmp_path, "import os\n\nREGION = os.environ['AWS_REGION']\n")
        assert any("reads the environment" in f.reason for f in findings)

    def test_randomness_is_refused(self, tmp_path: Path) -> None:
        findings = _core(tmp_path, "import random\n")
        assert "same bytes" in findings[0].reason

    def test_reading_a_file_is_refused(self, tmp_path: Path) -> None:
        """The core is given its data. It does not go and find it."""
        findings = _core(tmp_path, "def f(path):\n    return open(path).read()\n")
        assert any("filesystem" in f.reason for f in findings)


class TestReporting:
    def test_a_module_the_gate_cannot_parse_is_a_violation(self, tmp_path: Path) -> None:
        """Not a skip. A module the gate cannot read is a module the gate is not checking, and
        a silently unchecked module is the failure this whole gate exists to prevent."""
        findings = _core(tmp_path, "def broken(:\n")
        assert findings[0].rule == "unparseable"

    def test_every_violation_is_reported_not_just_the_first(self, tmp_path: Path) -> None:
        """A caller who fixes one import and reruns to find the next learns the gate one
        refusal at a time, and gives up before the end."""
        findings = _core(tmp_path, "import boto3\nimport pyflink\nimport random\n")
        assert len(findings) == 3

    def test_findings_carry_a_repository_relative_path(self, tmp_path: Path) -> None:
        """Identical output on a laptop and on a runner. A path that differs between the two
        is a finding somebody has to translate before they can act on it."""
        findings = _core(tmp_path, "import boto3\n")
        assert findings[0].path == "src/watermark/core/windows.py"

    def test_the_clean_report_says_what_was_checked(self) -> None:
        assert "no framework" in report([])
