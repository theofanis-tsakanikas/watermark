"""The adapter carries no numbers.

ADR-0003's second part, enforced. The failure this exists for is not somebody rewriting the
windowing logic in `streaming/` — it is one line:

    .window(TumblingEventTimeWindows.of(Time.minutes(15)))
    .allowed_lateness(Time.minutes(5))
    WatermarkStrategy.for_bounded_out_of_orderness(Duration.of_seconds(5))

Each of those is a decision `watermark.core` is supposed to own, expressed where no offline
test can see it. The core's tests keep passing, because they exercise the core; the deployed
job quietly means something else, and only the deployed one is right.

Two rules, read off the syntax tree.

**No numeric literal.** Every duration, grain, threshold and bound in the adapter is a name
resolved from `watermark.core`. A literal is refused wherever it appears — not only inside a
PyFlink call — because "is this argument semantic?" is a judgement, and a gate that makes
judgements is a gate somebody argues with.

**No convenience constructor that bakes in a policy.** Flink offers watermark strategies with
the bound as an argument. They are refused by name; the generator-backed form delegates to the
core instead.

The rule is mechanical, and being mechanical is the point. "Keep the adapter thin" is advice,
and advice loses to a deadline.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

#: Watermark strategies whose whole purpose is to hold the policy Flink-side.
_FORBIDDEN_CALLS: Final[dict[str, str]] = {
    "for_bounded_out_of_orderness": (
        "this holds the out-of-orderness bound inside Flink, where no offline test can read "
        "it; use a generator backed by watermark.core.watermarks instead"
    ),
    "for_monotonous_timestamps": (
        "this asserts the stream is ordered, which is the one thing this domain guarantees it "
        "is not"
    ),
}

#: Literals that carry no policy: a package index, an exit code, an enumeration start.
_HARMLESS: Final[frozenset[int]] = frozenset({0, 1})


@dataclass(frozen=True, slots=True)
class Finding:
    path: str
    line: int
    rule: str
    detail: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}  [{self.rule}] {self.detail} — {self.reason}"


def scan(adapter_root: Path, repository_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted(adapter_root.rglob("*.py")):
        findings.extend(_scan_file(path, repository_root))
    return findings


def _scan_file(path: Path, repository_root: Path) -> Iterator[Finding]:
    relative = path.relative_to(repository_root).as_posix()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        yield Finding(
            relative,
            exc.lineno or 0,
            "unparseable",
            exc.msg,
            "a module the gate cannot read is a module the gate is not checking",
        )
        return

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            if isinstance(node.value, bool) or node.value in _HARMLESS:
                continue
            yield Finding(
                relative,
                node.lineno,
                "semantic literal",
                f"`{node.value}`",
                "every duration, grain and threshold in the adapter is a name resolved from "
                "watermark.core. A number here is a policy decision written where no offline "
                "test can see it",
            )
        elif isinstance(node, ast.Call):
            name = _dotted(node.func).rsplit(".", 1)[-1]
            if name in _FORBIDDEN_CALLS:
                yield Finding(
                    relative,
                    node.lineno,
                    "framework policy",
                    f"`{name}(...)`",
                    _FORBIDDEN_CALLS[name],
                )


def _dotted(node: ast.expr) -> str:
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return ""


def report(findings: Iterable[Finding]) -> str:
    listed = list(findings)
    if not listed:
        return "adapter-thin: the streaming adapter carries no semantic literal"
    lines = [f"adapter-thin: {len(listed)} decision(s) taken inside the adapter", ""]
    lines.extend(f"  {finding}" for finding in listed)
    lines.append("")
    lines.append(
        "Flink decides when a function is called; the core decides what the answer is. Each "
        "line above moves an answer into the framework, where the offline suite cannot reach "
        "it — so the core keeps passing and the deployed job means something else."
    )
    return "\n".join(lines)


def main(adapter_root: Path, repository_root: Path) -> int:
    findings = scan(adapter_root, repository_root)
    print(report(findings), file=sys.stderr if findings else sys.stdout)
    return 1 if findings else 0
