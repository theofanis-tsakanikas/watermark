"""The sweep, exercised where it can be — which is every branch that deletes something.

The reaper is the floor under the cost controls and the most dangerous thing in the account: it
deletes resources it did not create, chosen by a tag. For most of this project's life it also
did not delete at all — it classified, logged `would delete`, and returned a list, hourly,
convincingly.

So the tests are about the two failure directions and they are not symmetric. Under-deleting
costs money. Over-deleting costs data, and there is no undo, which is why the cases below spend
more effort on what must *survive* a sweep than on what must not.
"""

from __future__ import annotations

import datetime as dt
import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REAPER = ROOT / "infra" / "foundation" / "reaper"

NOW = dt.datetime.now(dt.timezone.utc)  # noqa: UP017 — the Lambda runtime is 3.12, this file is shared
PAST = (NOW - dt.timedelta(hours=2)).isoformat()
FUTURE = (NOW + dt.timedelta(hours=2)).isoformat()

FLINK = "arn:aws:kinesisanalyticsv2:eu-west-1:1:application/watermark-stream"
ENDPOINT = "arn:aws:sagemaker:eu-west-1:1:endpoint/watermark-anomaly"
STREAM = "arn:aws:kinesis:eu-west-1:1:stream/watermark-telemetry"
BUCKET = "arn:aws:s3:::watermark-lakehouse-1"


class _Recorder:
    """Every client, recording calls instead of making them."""

    def __init__(self, resources: list[dict]) -> None:
        self.resources = resources
        self.calls: list[tuple[str, str, dict]] = []
        self.explode_on: str | None = None

    def __call__(self, service: str):
        return _Client(self, service)

    def deleted(self) -> list[str]:
        return [
            arguments.get("ApplicationName")
            or arguments.get("EndpointName")
            or arguments.get("FeatureGroupName")
            or arguments.get("StreamName")
            for _, method, arguments in self.calls
            if method.startswith("delete_")
        ]


class _Client:
    def __init__(self, recorder: _Recorder, service: str) -> None:
        self._recorder = recorder
        self._service = service

    def get_paginator(self, _name: str):
        resources = self._recorder.resources

        class _Paginator:
            def paginate(self, **_kwargs):
                yield {"ResourceTagMappingList": resources}

        return _Paginator()

    def describe_application(self, **arguments):
        self._recorder.calls.append((self._service, "describe_application", arguments))
        return {"ApplicationDetail": {"ApplicationStatus": "RUNNING", "CreateTimestamp": NOW}}

    def __getattr__(self, method: str):
        def call(**arguments):
            if self._recorder.explode_on == method:
                raise RuntimeError("the API refused")
            self._recorder.calls.append((self._service, method, arguments))
            return {}

        return call


def _resource(arn: str, expires: str | None) -> dict:
    tags = [{"Key": "watermark:project", "Value": "watermark"}]
    if expires is not None:
        tags.append({"Key": "watermark:expires-at", "Value": expires})
    return {"ResourceARN": arn, "Tags": tags}


def _load(mode: str):
    """Import the handler with a mode. Module-level `MODE` means a fresh import per case."""
    sys.path.insert(0, str(REAPER))
    os.environ["WATERMARK_PROJECT"] = "watermark"
    os.environ["WATERMARK_REAPER_MODE"] = mode
    module = importlib.import_module("reap")
    return importlib.reload(module)


@pytest.fixture
def reap_destroy():
    return _load("destroy")


@pytest.fixture
def reap_report():
    return _load("report")


# ── what must be deleted ─────────────────────────────────────────────────────


def test_an_expired_resource_is_actually_deleted(reap_destroy) -> None:
    """The whole finding. Before this, every case below passed against a function that deleted
    nothing at all — it logged `would delete` and returned the ARN, hourly, for months."""
    recorder = _Recorder([_resource(STREAM, PAST)])
    result = reap_destroy.handler({}, None, recorder)

    assert result["deleted"] == [STREAM]
    assert recorder.deleted() == ["watermark-telemetry"]


def test_a_running_flink_application_is_stopped_before_it_is_deleted(reap_destroy) -> None:
    """`DeleteApplication` refuses while it runs, and a reaper that reports success against an
    application still billing KPUs is worse than one that never ran."""
    recorder = _Recorder([_resource(FLINK, PAST)])
    reap_destroy.handler({}, None, recorder)

    methods = [method for _, method, _ in recorder.calls]
    assert "stop_application" in methods
    assert methods.index("stop_application") < methods.index("delete_application")


def test_every_expired_resource_is_swept_even_when_one_of_them_will_not_delete(
    reap_destroy,
) -> None:
    """One stuck resource must not abandon the rest of the page — they are all still billing."""
    recorder = _Recorder([_resource(ENDPOINT, PAST), _resource(STREAM, PAST)])
    recorder.explode_on = "delete_endpoint"

    with pytest.raises(RuntimeError, match="could not be deleted"):
        reap_destroy.handler({}, None, recorder)
    assert recorder.deleted() == ["watermark-telemetry"]


# ── what must survive ────────────────────────────────────────────────────────


def test_a_resource_that_has_not_expired_is_left_alone(reap_destroy) -> None:
    recorder = _Recorder([_resource(STREAM, FUTURE)])
    result = reap_destroy.handler({}, None, recorder)

    assert result["deleted"] == []
    assert recorder.deleted() == []


def test_a_resource_with_no_expiry_is_reported_rather_than_deleted(reap_destroy) -> None:
    """An untagged resource is a mistake in the Terraform or something made by hand. Both are
    worth a line; neither is a reason to delete."""
    recorder = _Recorder([_resource(STREAM, None)])
    result = reap_destroy.handler({}, None, recorder)

    assert result["without_expiry"] == [STREAM]
    assert recorder.deleted() == []


def test_an_unparseable_expiry_is_not_an_excuse_to_delete(reap_destroy) -> None:
    """Otherwise a typo in a tag is destructive."""
    recorder = _Recorder([_resource(STREAM, "yesterday-ish")])
    result = reap_destroy.handler({}, None, recorder)

    assert result["without_expiry"] == [STREAM]
    assert recorder.deleted() == []


def test_never_means_never(reap_destroy) -> None:
    """The bootstrap layer is permanent and carries `expires-at: never`. A sweeper that read
    that as an unparseable date and deleted on it would take the state backend with it."""
    recorder = _Recorder([_resource(STREAM, "never")])
    assert reap_destroy.handler({}, None, recorder)["deleted"] == []


def test_a_type_the_reaper_cannot_delete_is_reported_not_guessed(reap_destroy) -> None:
    """Guessing an API from an ARN is how a sweeper deletes a table when it meant to stop an
    application. An expired bucket is reported and left."""
    recorder = _Recorder([_resource(BUCKET, PAST)])
    result = reap_destroy.handler({}, None, recorder)

    assert result["unknown_type"] == [BUCKET]
    assert recorder.deleted() == []


# ── the mode ─────────────────────────────────────────────────────────────────


def test_report_mode_classifies_and_deletes_nothing(reap_report) -> None:
    """The behaviour the reaper had for its whole life, now reachable only by asking for it."""
    recorder = _Recorder([_resource(STREAM, PAST)])
    result = reap_report.handler({}, None, recorder)

    assert result["expired"] == [STREAM]
    assert result["deleted"] == []
    assert recorder.deleted() == []


def test_the_default_mode_under_deletes_rather_than_over_deletes() -> None:
    """A deployment that forgets the variable costs money. The other default costs data."""
    sys.path.insert(0, str(REAPER))
    os.environ["WATERMARK_PROJECT"] = "watermark"
    os.environ.pop("WATERMARK_REAPER_MODE", None)
    module = importlib.reload(importlib.import_module("reap"))
    assert module.MODE != "destroy"
