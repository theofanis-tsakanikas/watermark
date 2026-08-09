"""Evaluating the tag policy without an account."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_POLICY = Path(__file__).resolve().parents[3] / "policy" / "tags.yaml"


@dataclass(frozen=True, slots=True)
class Grant:
    """One principal's access, expressed as a tag match rather than a list of tables."""

    principal: str
    description: str
    permissions: tuple[str, ...]
    #: Tag key → the values that satisfy it. **All keys must match** — a grant is a conjunction.
    #: Treating it as a disjunction is the mistake that turns a purpose-limited grant into a
    #: sensitivity-limited one, and it reads identically in YAML.
    match: Mapping[str, tuple[str, ...]]

    def covers(self, tags: Mapping[str, str]) -> bool:
        return all(tags.get(key) in values for key, values in self.match.items())


@dataclass(frozen=True, slots=True)
class Reachable:
    """What a principal can and cannot read. Both halves, because only one is checkable."""

    principal: str
    allowed: tuple[str, ...]
    denied: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Policy:
    """The whole policy: tag definitions, tagged resources, and grants."""

    tag_values: Mapping[str, tuple[str, ...]]
    resources: Mapping[str, Mapping[str, str]]
    grants: tuple[Grant, ...]

    def reachable(self, principal: str) -> Reachable:
        """Everything this principal may read, and everything it may not.

        The denied list is returned rather than implied. An access suite that only asserts what
        a principal *can* reach passes just as happily on a policy that grants everything, and
        that is the failure mode a tag policy is most likely to have.
        """
        grants = [grant for grant in self.grants if grant.principal == principal]
        allowed, denied = [], []
        for resource, tags in sorted(self.resources.items()):
            (allowed if any(grant.covers(tags) for grant in grants) else denied).append(resource)
        return Reachable(principal, tuple(allowed), tuple(denied))

    def principals(self) -> tuple[str, ...]:
        return tuple(sorted({grant.principal for grant in self.grants}))

    def problems(self) -> tuple[str, ...]:
        """Ways the policy cannot be trusted, in a stable order.

        Every one of these resolves *silently* to a plausible answer, which is why they are
        checked rather than assumed.
        """
        found: list[str] = []

        for resource, tags in sorted(self.resources.items()):
            for key, value in sorted(tags.items()):
                if key not in self.tag_values:
                    found.append(
                        f"{resource} carries tag '{key}', which is not defined. Lake Formation "
                        "ignores an unknown tag rather than refusing it, so the resource is "
                        "governed by whatever remains — which is usually less."
                    )
                elif value not in self.tag_values[key]:
                    found.append(
                        f"{resource} has {key}={value!r}, which is not one of "
                        f"{list(self.tag_values[key])}. No grant matches it, so it becomes "
                        "unreadable by everybody — which looks like a permissions bug and is a "
                        "typo."
                    )
            missing = sorted(set(self.tag_values) - set(tags))
            if missing:
                found.append(
                    f"{resource} carries no {missing} tag. A grant is a conjunction over every "
                    "key it names, so an untagged resource is unreachable — and an *untagged "
                    "personal* resource is also outside the erasure scope in claim 6."
                )

        for grant in self.grants:
            for key, values in sorted(grant.match.items()):
                if key not in self.tag_values:
                    found.append(f"{grant.principal} matches on undefined tag '{key}'")
                    continue
                unknown = sorted(set(values) - set(self.tag_values[key]))
                if unknown:
                    found.append(
                        f"{grant.principal} matches {key} against {unknown}, which are not "
                        "defined values. The grant silently selects nothing."
                    )
        return tuple(found)


def load_policy(path: Path = DEFAULT_POLICY) -> Policy:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))

    tag_values = {key: tuple(definition["values"]) for key, definition in document["tags"].items()}
    resources = {entry["resource"]: dict(entry["tags"]) for entry in document["resources"]}
    grants = tuple(
        Grant(
            principal=entry["principal"],
            description=entry.get("description", ""),
            permissions=tuple(entry["permissions"]),
            match={key: tuple(values) for key, values in entry["match"].items()},
        )
        for entry in document["grants"]
    )
    return Policy(tag_values, resources, grants)
