"""The framework-free core, enforced.

`src/watermark/core/` holds the logic claims 1 to 4 are about: windowing, watermark
generation, deduplication, lateness, point-in-time joins. The rule is that it imports the
standard library and nothing else, and that it is a pure function of its arguments.

The rule is worth a gate rather than a paragraph because breaking it is invisible and
irreversible in practice. The day a `boto3` call appears three levels down inside a windowing
function, the tests still pass on the laptop that wrote it — and every claim in this
repository silently becomes a claim about a machine with credentials. Nobody notices, because
nothing fails; the suite simply stops being runnable by a stranger, which is the only property
that made it evidence.

Two rules, checked by reading the syntax tree rather than by importing anything.

**Nothing but the standard library and `watermark.core` itself.** No Flink, no boto3, no SDK,
no test helper reaching back in. The check refuses dynamic imports too — `importlib` and
`__import__` — because a rule that a single indirection defeats is a rule that will be
defeated by a single indirection.

**No ambient state.** No clock, no randomness, no environment, no filesystem, no network.
This is the half that protects claim 2. A `datetime.now()` inside window assignment does not
raise, does not log and does not fail a test; it makes a replay differ from the run it is
replaying, three months later, on a machine nobody has.

Both rules are attacked in `scripts/gate_proof.py`.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

#: Standard-library modules the core may not use either. Being in the standard library says
#: nothing about whether a module makes a function a pure function of its arguments.
_FORBIDDEN_STDLIB: Final[dict[str, str]] = {
    "socket": "the core never opens a connection",
    "ssl": "the core never opens a connection",
    "urllib": "the core never opens a connection",
    "http": "the core never opens a connection",
    "ftplib": "the core never opens a connection",
    "smtplib": "the core never opens a connection",
    "subprocess": "the core never starts a process",
    "random": "a replay must produce the same bytes; a random source guarantees it will not",
    "secrets": "a replay must produce the same bytes; a random source guarantees it will not",
    "importlib": "a dynamic import is this rule with one indirection in front of it",
}

#: Callables that read something the arguments did not supply. Matched on the dotted
#: expression and on the bare attribute name, so `datetime.now()`, `dt.now()` and a method of
#: our own called `now()` are all refused — the last one deliberately. An `Instant.now()` is
#: the most natural API in the world and it is exactly what must not exist here.
_FORBIDDEN_CALLS: Final[dict[str, str]] = {
    "now": "reads the wall clock; event time comes from the record, never from the machine",
    "utcnow": "reads the wall clock; event time comes from the record, never from the machine",
    "today": "reads the wall clock; event time comes from the record, never from the machine",
    "time": "reads the wall clock; event time comes from the record, never from the machine",
    "time_ns": "reads the wall clock; event time comes from the record, never from the machine",
    "monotonic": "reads the wall clock; processing time is the adapter's business, not the core's",
    "perf_counter": "reads the wall clock; measurement belongs in observability, not in the core",
    "getenv": "reads the environment; a decision must depend on its inputs and nothing else",
    "urandom": "a replay must produce the same bytes",
    "uuid1": "a replay must produce the same bytes; lineage ids are derived, never generated",
    "uuid4": "a replay must produce the same bytes; lineage ids are derived, never generated",
    "open": "reads the filesystem; the core is given its data, it does not go and find it",
    "input": "reads a terminal",
    "eval": "arbitrary evaluation has no place behind a published number",
    "exec": "arbitrary evaluation has no place behind a published number",
    "__import__": "a dynamic import is the import rule with one indirection in front of it",
    "import_module": "a dynamic import is the import rule with one indirection in front of it",
}

#: Attribute reads with the same problem. `os.environ["REGION"]` is not a call.
_FORBIDDEN_ATTRIBUTES: Final[dict[str, str]] = {
    "os.environ": "reads the environment; a decision must depend on its inputs and nothing else",
}

_ALLOWED_PACKAGE: Final = "watermark.core"


@dataclass(frozen=True, slots=True)
class Finding:
    """One violation, with the reason it is one.

    `path` is repository-relative so the output is the same on a laptop and on a runner —
    a finding whose text differs between the two is a finding somebody has to translate.
    """

    path: str
    line: int
    rule: str
    detail: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}  [{self.rule}] {self.detail} — {self.reason}"


def scan(core_root: Path, repository_root: Path) -> list[Finding]:
    """Every violation under `core_root`, in file and line order.

    Every violation, not the first: a caller who fixes one import and reruns to find the next
    learns the gate one refusal at a time, and gives up before the end.
    """
    findings: list[Finding] = []
    for path in sorted(core_root.rglob("*.py")):
        findings.extend(_scan_file(path, repository_root))
    return findings


def _scan_file(path: Path, repository_root: Path) -> Iterator[Finding]:
    source = path.read_text(encoding="utf-8")
    relative = path.relative_to(repository_root).as_posix()
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        yield Finding(
            relative,
            exc.lineno or 0,
            "unparseable",
            f"{exc.msg}",
            "a module the gate cannot read is a module the gate is not checking",
        )
        return
    package = _package_of(path, repository_root)
    yield from _import_findings(tree, relative, package)
    yield from _ambient_findings(tree, relative)


def _package_of(path: Path, repository_root: Path) -> str:
    """The dotted package a module lives in, for resolving relative imports.

    `src/watermark/core/windows.py` is in package `watermark.core`; a `from . import x` in it
    means `watermark.core.x`, and a `from .. import x` means `watermark.x` — which is outside
    the core and is exactly what this needs to be able to say.
    """
    parts = path.relative_to(repository_root / "src").with_suffix("").parts
    return ".".join(parts[:-1])


def _import_findings(tree: ast.AST, relative: str, package: str) -> Iterator[Finding]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield from _judge_module(alias.name, node.lineno, relative)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve(node, package)
            if module is None:
                yield Finding(
                    relative,
                    node.lineno,
                    "import",
                    f"relative import escaping {_ALLOWED_PACKAGE}",
                    "the core does not reach back into the rest of the package",
                )
                continue
            yield from _judge_module(module, node.lineno, relative)


def _resolve(node: ast.ImportFrom, package: str) -> str | None:
    """The absolute module an `ImportFrom` names, or None if it climbs past the package root."""
    if not node.level:
        return node.module or ""
    parts = package.split(".")
    if node.level > len(parts):
        return None
    base = parts[: len(parts) - (node.level - 1)]
    return ".".join([*base, node.module]) if node.module else ".".join(base)


def _judge_module(module: str, line: int, relative: str) -> Iterator[Finding]:
    if not module:
        return
    root = module.split(".", maxsplit=1)[0]
    if module == _ALLOWED_PACKAGE or module.startswith(f"{_ALLOWED_PACKAGE}."):
        return
    if root == "watermark":
        yield Finding(
            relative,
            line,
            "import",
            f"`{module}`",
            f"the core imports only itself and the standard library; {module} is outside "
            f"{_ALLOWED_PACKAGE}",
        )
        return
    if root in _FORBIDDEN_STDLIB:
        yield Finding(relative, line, "import", f"`{module}`", _FORBIDDEN_STDLIB[root])
        return
    if root == "__future__" or root in sys.stdlib_module_names:
        return
    yield Finding(
        relative,
        line,
        "import",
        f"`{module}`",
        "not in the standard library; the core runs with no framework and no cloud SDK, "
        "which is the only reason claims 1 to 4 can be checked without a cluster",
    )


def _ambient_findings(tree: ast.AST, relative: str) -> Iterator[Finding]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            dotted = _dotted(node.func)
            name = dotted.rsplit(".", 1)[-1] if dotted else ""
            if name in _FORBIDDEN_CALLS:
                yield Finding(
                    relative,
                    node.lineno,
                    "ambient state",
                    f"`{dotted}(...)`",
                    _FORBIDDEN_CALLS[name],
                )
        elif isinstance(node, ast.Attribute):
            dotted = _dotted(node)
            if dotted in _FORBIDDEN_ATTRIBUTES:
                yield Finding(
                    relative,
                    node.lineno,
                    "ambient state",
                    f"`{dotted}`",
                    _FORBIDDEN_ATTRIBUTES[dotted],
                )


def _dotted(node: ast.expr) -> str:
    """`a.b.c` for an attribute chain, `f` for a bare name, `""` for anything else."""
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
        return "core-pure: the stream core imports no framework and reads no ambient state"
    lines = [f"core-pure: {len(listed)} violation(s) in the stream core", ""]
    lines.extend(f"  {finding}" for finding in listed)
    lines.append("")
    lines.append(
        "The core is where claims 1 to 4 are proved. Every one of them is a claim that can "
        "be checked on a laptop with no cluster and no AWS account, and each line above is "
        "that property being given up. The logic moves out to an adapter; the tests do not "
        "move in."
    )
    return "\n".join(lines)
