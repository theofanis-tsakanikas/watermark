#!/usr/bin/env python3
"""Doctrine 6, made mechanical: an exception that has expired brings its finding back.

Every other rule in the doctrine is enforced by something that refuses at the moment of the
mistake. This one cannot be — it refuses at a moment *nobody is present for*, ninety days after
the decision, when the person who made it has moved on and the reason has stopped being true
without anyone noticing. That is precisely why it has to be a clock rather than a habit.

**The check goes red on its own schedule, and that is the design.** A CI failure with no commit
behind it reads as a broken pipeline the first time it happens, so the message says plainly what
expired, who granted it and what would close it. The correct responses are to fix the finding or
to renew the grant; there is no third one, and in particular there is no flag here.

Nothing here reaches AWS. It reads one YAML file and today's date.
"""

from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "contracts" / "waivers.yaml"

#: Fields every waiver must carry. A waiver missing any of them is not a decision anybody made,
#: it is a note — and the whole point is that somebody's name is on it.
REQUIRED = (
    "id",
    "finding",
    "where",
    "reason",
    "closes_when",
    "requested_by",
    "granted_by",
    "granted_on",
    "expires_on",
)


@dataclass(frozen=True, slots=True)
class Problem:
    waiver: str
    detail: str


def _date(value: object, field: str, waiver: str, problems: list[Problem]) -> dt.date | None:
    """YAML parses an unquoted ISO date into a `date` already; anything else is a mistake."""
    if isinstance(value, dt.date):
        return value
    problems.append(
        Problem(waiver, f"`{field}` is {value!r}, which is not a date. Write it as 2026-11-12.")
    )
    return None


def _check(waiver: dict, today: dt.date, horizon: int, problems: list[Problem]) -> None:
    name = str(waiver.get("id", "<no id>"))

    for field in REQUIRED:
        if not waiver.get(field):
            problems.append(Problem(name, f"has no `{field}`"))
    if [field for field in REQUIRED if not waiver.get(field)]:
        return

    granted = _date(waiver["granted_on"], "granted_on", name, problems)
    expires = _date(waiver["expires_on"], "expires_on", name, problems)
    if granted is None or expires is None:
        return

    # Expiry first, and it returns. A waiver that has run out is expired whatever else is wrong
    # with how it was written, and reporting the malformed dates instead would be technically
    # true and completely useless to the person reading CI at nine in the morning.
    if expires < today:
        problems.append(
            Problem(
                name,
                f"EXPIRED on {expires} ({(today - expires).days} days ago). The finding is back:\n"
                f"      {str(waiver['finding']).strip()}\n"
                f"    It closes when: {str(waiver['closes_when']).strip()}\n"
                f"    Fix it, or renew the grant with a new date and a name against it.",
            )
        )
        return

    if expires <= granted:
        problems.append(Problem(name, "expires on or before the day it was granted"))
    elif (expires - granted).days > horizon:
        problems.append(
            Problem(
                name,
                f"runs for {(expires - granted).days} days, past the {horizon}-day horizon. "
                f"Grant it for less and renew it — a renewal is a second decision by a named "
                f"person, which is the thing a long waiver never gets.",
            )
        )

    # Doctrine 5. A single-author repository cannot satisfy this by finding a second person, so
    # what it can do is refuse to let the gap be silent.
    if waiver["requested_by"] == waiver["granted_by"] and not waiver.get(
        "self_approval_acknowledged"
    ):
        problems.append(
            Problem(
                name,
                "was requested and granted by the same person, with nothing said about it. "
                "Doctrine 5 — nothing approves itself. Either name a different approver, or "
                "add `self_approval_acknowledged` stating why this project cannot.",
            )
        )


def main() -> int:
    if not REGISTER.exists():
        print(f"no waiver register at {REGISTER.relative_to(ROOT)}", file=sys.stderr)
        return 1

    document = yaml.safe_load(REGISTER.read_text(encoding="utf-8")) or {}
    horizon = int(document.get("horizon_days", 0))
    if horizon <= 0:
        print("the register declares no `horizon_days`, so no grant is bounded", file=sys.stderr)
        return 1

    waivers = document.get("waivers") or []
    today = dt.datetime.now(dt.timezone.utc).date()  # noqa: UP017 — Glue 4.0 runs Python 3.10

    problems: list[Problem] = []
    identifiers = [str(waiver.get("id")) for waiver in waivers]
    if len(identifiers) != len(set(identifiers)):
        problems.append(Problem("<register>", "two waivers share an id"))
    for waiver in waivers:
        _check(waiver, today, horizon, problems)

    if problems:
        print("waivers: an exception has expired or was never properly granted\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem.waiver}: {problem.detail}", file=sys.stderr)
        print(
            "\nDoctrine 6: exceptions expire. There is no flag here — the finding is either "
            "fixed or the grant is renewed by a named person, on a date.",
            file=sys.stderr,
        )
        return 1

    if not waivers:
        print("waivers: the register is empty — nothing is being lived with")
        return 0

    soonest = min(waiver["expires_on"] for waiver in waivers)
    print(
        f"waivers: {len(waivers)} live, none expired; the next returns on {soonest} "
        f"({(soonest - today).days} days)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
