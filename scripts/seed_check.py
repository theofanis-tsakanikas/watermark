#!/usr/bin/env python3
"""The generated day must reproduce its recording, exactly.

Two digests, and they fail differently.

**The stream digest** covers the synthetic input: every payload, arrival time, source and
partition. If it moves, the fixture changed — and every claim scored against it was scored
against a different day than the one that was reviewed.

**The run fingerprint** covers the output: every published value, every restatement with its
prior value, every quarantine and every lineage id. If it moves while the stream digest does
not, the *system* changed. That is the interesting case, and it is why the two are recorded
separately rather than as one number: one tells you the question changed, the other tells you
the answer did.

`--record` rewrites the recording. It is a deliberate act, and the diff it produces is the
review.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# `data/` and `evals/` are deliberately outside the installed package — a synthetic generator
# and a set of labelled scenarios have no business shipping in a wheel. The suite reaches them
# through the rootdir conftest; a script run directly has to say so itself.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data import cast
from data.generate import digest as stream_digest
from data.generate import generate
from evals.replay import fingerprint
from watermark.runner import Arrival, run

RECORDING = Path(__file__).resolve().parents[1] / "recordings" / "day.json"


def capture() -> dict[str, object]:
    deliveries = generate()
    arrivals = [Arrival(d.raw, d.ingest_time, d.source, d.partition) for d in deliveries]
    result = run(arrivals, cast.SUBSTATIONS)
    return {
        "day": cast.DAY,
        "meters": len(cast.METERS),
        "substations": list(cast.SUBSTATIONS),
        "deliveries": len(deliveries),
        "stream_digest": stream_digest(deliveries),
        "published": len(result.published),
        "restated": len(result.restated),
        "confirmed": len(result.confirmed),
        "quarantined": len(result.quarantined),
        "net_restatement_wh": sum(r.delta_wh for r in result.restated),
        "run_fingerprint": fingerprint(result),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true", help="Rewrite the recording.")
    arguments = parser.parse_args()

    captured = capture()
    if arguments.record:
        RECORDING.parent.mkdir(exist_ok=True)
        RECORDING.write_text(json.dumps(captured, indent=2, sort_keys=True) + "\n", "utf-8")
        print(f"recorded {RECORDING.name}")
        return 0

    if not RECORDING.exists():
        print(f"no recording at {RECORDING}; run with --record", file=sys.stderr)
        return 1

    recorded = json.loads(RECORDING.read_text(encoding="utf-8"))
    drifted = {
        key: (recorded.get(key), value)
        for key, value in captured.items()
        if recorded.get(key) != value
    }
    if drifted:
        print("the generated day no longer reproduces its recording:", file=sys.stderr)
        for key, (was, now) in sorted(drifted.items()):
            print(f"  {key}: recorded {was!r}, generated {now!r}", file=sys.stderr)
        if "stream_digest" in drifted:
            print(
                "\nthe input changed, so every claim scored against it was scored against a "
                "different day than the one that was reviewed",
                file=sys.stderr,
            )
        elif "run_fingerprint" in drifted:
            print(
                "\nthe input is unchanged and the output moved: the system changed",
                file=sys.stderr,
            )
        return 1

    print(
        f"seed-check: {captured['deliveries']} deliveries reproduce {RECORDING.name} exactly "
        f"({captured['published']} published, {captured['restated']} restated, "
        f"{captured['quarantined']} quarantined)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
