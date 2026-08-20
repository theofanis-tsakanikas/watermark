#!/usr/bin/env python3
"""Everything that must be true before the estate is stood up, in one command.

Collecting these is not about convenience. It is that *"is it ready?"* should have one answer,
produced the same way every time, rather than a person remembering nine commands and
forgetting the tenth — which will be the one that mattered.

Three groups, and they fail differently.

**Correctness** — the suite, the gates, the claim harnesses. These are the statements the
README makes. A failure here means a claim is false right now.

**Consistency** — two things that must agree have stopped agreeing. Nothing is broken; the
drift is invisible until something reads both, and by then one of them has been believed.

**Deployability** — Terraform validates against real provider schemas, checkov is clean. None
of it affects an offline run, and each one is otherwise a deploy that fails at minute forty.

`--fast` skips the slow members of each group; CI runs the whole thing.

The list grows one line per phase. A check for a claim that is not yet provable would be a
green tick over work that has not happened, so there are fewer lines here than there will be.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _tool(name: str, fallback: str | None = None) -> str:
    """The venv's copy when there is a venv, whatever is on PATH when there is not.

    Hard-coding `.venv/bin/…` makes preflight a check that only runs where it was written, and
    the place it then fails is inside a deploy.
    """
    candidate = ROOT / ".venv" / "bin" / name
    return str(candidate) if candidate.exists() else (fallback or name)


PYTHON = _tool("python", sys.executable)
RUFF = _tool("ruff")
#: checkov lives in its own environment because it pins boto3 exactly. `make iac-scan` creates
#: `.venv-checkov` on demand; this finds it, and falls back to PATH so a runner that installed
#: it another way still runs the scan.
CHECKOV = str(_CV) if (_CV := ROOT / ".venv-checkov" / "bin" / "checkov").exists() else "checkov"

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


@dataclass
class Check:
    group: str
    name: str
    command: list[str]
    #: Why a reader should care that this passed. Printed on failure, because a red line with
    #: no reason is a red line somebody reruns hoping it goes away.
    matters: str
    slow: bool = False
    #: Skipped with a note when the tool is absent, rather than failing the run. A preflight
    #: that cannot start without Terraform installed is a preflight nobody runs. Note the
    #: asymmetry with `make test-flink`, which must *fail* in CI when its runtime is missing:
    #: there, the missing tool hides a claim; here it hides a deployability check that CI runs
    #: anyway on a runner that has the tool.
    needs: str | None = None


CHECKS: list[Check] = [
    # ── Correctness ─────────────────────────────────────────────────────────
    Check(
        "correctness",
        "test suite",
        [PYTHON, "-m", "pytest", "-q"],
        "Every claim this repository makes is asserted by one of these.",
    ),
    Check(
        "correctness",
        "core purity",
        [PYTHON, "scripts/check_core_is_pure.py"],
        "The stream core imports no framework and reads no clock. It is the reason claims 1 "
        "to 4 can be checked at all without a cluster and an AWS account.",
    ),
    Check(
        "correctness",
        "adapter thinness",
        [PYTHON, "scripts/check_adapter_is_thin.py"],
        "Flink decides when a function is called; the core decides what the answer is. A "
        "duration written into a PyFlink call moves an answer where no offline test can see it.",
    ),
    Check(
        "correctness",
        "claim 1 · watermark",
        [PYTHON, "-m", "evals.watermark"],
        "No decision comes out of a window that has not closed — including when a substation "
        "goes silent, which is the case that fails without raising anything.",
    ),
    Check(
        "correctness",
        "claim 2 · replay",
        [PYTHON, "-m", "evals.replay"],
        "The same events, shuffled and duplicated, produce the same bytes and the same lineage.",
    ),
    Check(
        "correctness",
        "claim 3 · parity",
        [PYTHON, "-m", "evals.parity"],
        "One contract compiled two ways, agreeing — including on the planted case where a "
        "naive resolver reads a value from after the instant it is resolving.",
    ),
    Check(
        "correctness",
        "claim 4 · freshness",
        [PYTHON, "-m", "evals.freshness"],
        "A stale feature never reaches the model, and the fallback marker survives into the "
        "record. A fallback that looks like a model decision is worse than an outage.",
    ),
    Check(
        "correctness",
        "claim 5 · promotion",
        [PYTHON, "-m", "evals.promotion"],
        "Performance, bias, a model card and a named approver — and the shipped model is "
        "refused, for the label-coverage finding in docs/BIAS-FINDING.md.",
    ),
    Check(
        "correctness",
        "claim 6 · erasure",
        [PYTHON, "-m", "evals.erasure"],
        "The system refuses to certify unless every leg confirms, and the certificate states "
        "the one leg deletion cannot reach.",
    ),
    Check(
        "correctness",
        "policy access",
        [PYTHON, "scripts/check_policy_access.py"],
        "Every reachable set exact and every closed path closed. A suite asserting only the "
        "first passes on a policy that grants everything.",
    ),
    Check(
        "correctness",
        "claim 7 · oversight",
        [PYTHON, "-m", "evals.oversight"],
        "The automated path is structurally incapable of a consequential decision about a "
        "person — the contract does not load and the actuation type cannot be built.",
    ),
    Check(
        "correctness",
        "parity independence",
        [PYTHON, "scripts/check_parity_paths_are_independent.py"],
        "The two mechanisms share the contract and nothing else. Merged, claim 3 compares a "
        "function with itself and reports green forever.",
    ),
    Check(
        "claims",
        "the declared cases",
        [PYTHON, "-m", "evals.cases"],
        "Every defect the cast declares must be observable in the generated day, and no cohort "
        "may go unchecked. Two were exercised by nothing at all until an audit found them: a "
        "case nothing reads cannot fail, which is worse than one that does.",
    ),
    Check(
        "claims",
        "the settlement path",
        [PYTHON, "-m", "evals.settlement"],
        "The third decision contract, and doctrine 4 — a correction never erases what was "
        "previously stated. Its safe state is the inverse of curtailment's, and the contract "
        "is what says which is which.",
    ),
    Check(
        "deployability",
        "glue runtime",
        [PYTHON, "scripts/check_glue_runtime.py"],
        "Glue 4.0 runs Python 3.10 and this repository targets 3.12. A job using a name from "
        "in between dies on import, thirty seconds into a Spark cluster nobody can refund, "
        "inside whatever was waiting on it.",
    ),
    Check(
        "deployability",
        "partition vocabulary",
        [PYTHON, "scripts/check_partition_vocabulary.py"],
        "The IoT rule labels each record with the partition the core declares. Different "
        "vocabularies mean every substation lags for ever and every total carries a hole that "
        "is not there.",
    ),
    Check(
        "correctness",
        "gate-proof",
        [PYTHON, "scripts/gate_proof.py"],
        "Each gate refuses a real violation, for the right reason. Slow, and the most "
        "informative line here: a gate that has never been shown to fail is a comment.",
        slow=True,
    ),
    # ── Consistency ─────────────────────────────────────────────────────────
    Check(
        "consistency",
        "erasure legs",
        [PYTHON, "scripts/check_erasure_legs.py"],
        "The scope, the state machine and the count the refusal compares against must name the "
        "same legs. `offline_store` was declared, never implemented, absent from the "
        "certificate and absent from the condition that decides whether to write one.",
    ),
    Check(
        "consistency",
        "feature sources",
        [PYTHON, "scripts/check_feature_sources.py"],
        "Every feature reads a column that exists, from a table something writes. A declared "
        "table nobody populates answers every query with zero rows and no error, which is how "
        "two features sat unservable for the whole life of the lakehouse.",
    ),
    Check(
        "consistency",
        "the readme",
        [PYTHON, "scripts/check_the_readme.py"],
        "Every figure the README states, re-read against what the repository says. It is the "
        "one artefact here that quotes every other check and was checked by none of them, and "
        "it drifted three times in a single day before this existed — always in the direction "
        "of looking more finished. A figure this check can no longer find is reported STALE "
        "rather than passed.",
    ),
    Check(
        "consistency",
        "waivers",
        [PYTHON, "scripts/check_waivers.py"],
        "Doctrine 6 — every exception carries a name and an end date, and an expired one "
        "brings its finding back. This check goes red on its own schedule, with no commit "
        "behind it, which is the whole point of it.",
    ),
    Check(
        "consistency",
        "entity contracts",
        [PYTHON, "scripts/check_contracts.py"],
        "Every contract loads, every reference resolves, and nothing holds personal data "
        "without a declared purpose.",
    ),
    Check(
        "consistency",
        "seed reproduces the recording",
        [PYTHON, "scripts/seed_check.py"],
        "If the generated day drifts, every claim above was scored against a different day "
        "than the one that was reviewed — which is the same as not having scored them.",
    ),
    Check(
        "consistency",
        "flink versions agree",
        [PYTHON, "scripts/check_flink_versions_agree.py"],
        "The equivalence tier runs the Flink that is deployed. Against any other, it "
        "establishes equivalence with something nobody is running.",
    ),
    Check(
        "deployability",
        "the application package builds",
        ["make", "package"],
        "terraform's archive_file needs something to zip. Without it the apply fails on a "
        "missing directory, after the expensive layers are already up.",
        slow=True,
    ),
    Check(
        "consistency",
        "lint",
        [RUFF, "check", "src", "tests", "scripts", "data", "evals", "streaming"],
        "The same command CI runs.",
    ),
    Check(
        "consistency",
        "format",
        [RUFF, "format", "--check", "src", "tests", "scripts", "data", "evals", "streaming"],
        "The same command CI runs.",
    ),
    Check(
        "consistency",
        "Annex IV documentation",
        [PYTHON, "scripts/generate_annex_iv.py", "--check"],
        "Generated from the contracts. A hand-edited generated document is one that describes "
        "the system somebody meant to build.",
    ),
    Check(
        "consistency",
        "cost envelope",
        [PYTHON, "scripts/check_cost_envelope.py"],
        "A full capture stays inside the €100 the design is constrained by. An estimate from a "
        "rate card, never a bill — nothing here has been applied.",
    ),
    # ── Deployability ───────────────────────────────────────────────────────
    Check(
        "deployability",
        "lakehouse wiring",
        [PYTHON, "scripts/check_lakehouse_wiring.py"],
        "Terraform, dbt and the queries describe one lakehouse three times. `dbt parse` "
        "resolves a source against sources.yml rather than a catalogue, so a table that exists "
        "nowhere compiles cleanly and the first real build fails — or a resolver reads an empty "
        "table and calls it zero.",
    ),
    Check(
        "deployability",
        "VPC endpoints",
        [PYTHON, "scripts/check_vpc_endpoints.py"],
        "Egress is the endpoint list and nothing else. A service with no endpoint does not "
        "refuse the connection — it waits, while the control plane reports healthy.",
    ),
    Check(
        "deployability",
        "OIDC subjects",
        [PYTHON, "scripts/check_oidc_subjects.py"],
        "Every trusted subject names this repository and one environment. CKV_AWS_358 reads "
        "only the first value of the condition list, so this is the check that covers the trust.",
    ),
    Check(
        "deployability",
        "deploy inputs",
        [PYTHON, "scripts/check_deploy_inputs.py"],
        "Every variable with no default is supplied by the workflow. A missing TF_VAR fails "
        "inside the apply loop, with an earlier layer already created.",
    ),
    Check(
        "deployability",
        "ml package",
        [
            PYTHON,
            "-c",
            (
                "import pathlib,sys;"
                "w=list(pathlib.Path('infra/ml/.package').glob('*.whl')) "
                "if pathlib.Path('infra/ml/.package').exists() else [];"
                "print(f'ml-package: {w[0].name}') if w else "
                "sys.exit('no wheel in infra/ml/.package — run `make package-ml`. "
                "Without it the pipeline\\'s code channel is empty and every step that runs our "
                "code fails after the cluster is paid for.')"
            ),
        ],
        "The wheel the SageMaker processing steps install. An empty code channel fails inside "
        "a running cluster; this fails on a laptop.",
    ),
    Check(
        "deployability",
        "model pins",
        [PYTHON, "scripts/check_model_pins_agree.py"],
        "The local fit and the pipeline fit pin the same seed, threads and tree method. "
        "ADR-0005's practical tier is void the moment the two disagree about what the seed is.",
    ),
    Check(
        "deployability",
        "terraform fmt",
        ["terraform", "fmt", "-check", "-recursive", "infra"],
        "Formatting drift makes a real diff unreadable.",
        needs="terraform",
    ),
    Check(
        "deployability",
        "terraform validate",
        [sys.executable, "scripts/tf_validate.py"],
        "Against real provider schemas. This catches an attribute that does not exist, and it "
        "is the difference between 'this should apply' and 'this applies'.",
        slow=True,
        needs="terraform",
    ),
    Check(
        "deployability",
        "checkov",
        [CHECKOV, "-d", "infra", "--compact", "--quiet"],
        "Zero findings, with every deliberate exception carrying a written reason beside the "
        "resource it applies to.",
        slow=True,
        needs=CHECKOV,
    ),
]


@dataclass
class Result:
    check: Check
    status: str
    seconds: float
    output: str = ""


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)

    @property
    def failed(self) -> list[Result]:
        return [result for result in self.results if result.status == "fail"]

    @property
    def skipped(self) -> list[Result]:
        return [result for result in self.results if result.status == "skip"]

    @property
    def ok(self) -> bool:
        return not self.failed


def run(check: Check) -> Result:
    if check.needs and not (shutil.which(check.needs) or Path(check.needs).exists()):
        return Result(check, "skip", 0.0, f"{check.needs} is not installed")
    started = time.monotonic()
    completed = subprocess.run(  # noqa: S603 — fixed command lists, no shell
        check.command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONWARNINGS": "ignore"},
    )
    elapsed = time.monotonic() - started
    status = "pass" if completed.returncode == 0 else "fail"
    return Result(check, status, elapsed, (completed.stdout + completed.stderr)[-3000:])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fast", action="store_true", help="Skip the slow checks.")
    parser.add_argument("--group", help="Run one group only.")
    arguments = parser.parse_args()

    selected = [
        check
        for check in CHECKS
        if not (arguments.fast and check.slow)
        and (not arguments.group or check.group == arguments.group)
    ]

    report = Report()
    current_group = ""
    for check in selected:
        if check.group != current_group:
            current_group = check.group
            print(f"\n{DIM}── {current_group}{RESET}")
        print(f"   {check.name:<32}", end="", flush=True)
        result = run(check)
        report.results.append(result)
        mark = {
            "pass": f"{GREEN}ok{RESET}",
            "fail": f"{RED}FAIL{RESET}",
            "skip": f"{YELLOW}skip{RESET}",
        }
        print(f"{mark[result.status]}  {DIM}{result.seconds:5.1f}s{RESET}")

    print()
    for result in report.skipped:
        print(f"{YELLOW}skipped{RESET} {result.check.name}: {result.output}")

    for result in report.failed:
        print(f"\n{RED}FAILED{RESET} {result.check.name}")
        print(f"  why it matters: {result.check.matters}")
        print(f"{DIM}{result.output.rstrip()}{RESET}")

    passed = sum(1 for result in report.results if result.status == "pass")
    print(
        f"\npreflight: {passed} passed, {len(report.failed)} failed, {len(report.skipped)} skipped"
    )
    if report.ok:
        print("the repository is ready to deploy; nothing is standing right now")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
