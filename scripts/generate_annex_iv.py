#!/usr/bin/env python3
"""Generate the AI Act Annex IV technical documentation from the repository.

AI Act Art. 11 requires technical documentation for a high-risk system, and Annex IV lists what
it must contain. Generated rather than written, and CI-failing on drift, for the reason the
sibling project gives: a document typed beside the code describes the system somebody meant to
build, and it stops being true on the first afternoon nobody updates it.

`--check` regenerates and compares. It is the drift detector, and it is what makes the document
evidence rather than prose.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from watermark.contracts import load
from watermark.policy import load_policy

OUTPUT = Path(__file__).resolve().parents[1] / "docs" / "ANNEX-IV.md"


def generate() -> str:
    contracts = load()
    policy = load_policy()

    curtailment = contracts.decisions["curtailment"]
    features = [contracts.features[name] for name in sorted(curtailment.features)]

    lines = [
        "# Annex IV technical documentation — curtailment",
        "",
        "**Generated from the repository by `scripts/generate_annex_iv.py`. Do not edit.**",
        "CI runs it with `--check`; a hand edit fails the build, which is the point — a document",
        "typed beside the code describes the system somebody meant to build.",
        "",
        "Covers the curtailment path only. It is the one argued to be high-risk under AI Act",
        "Annex III(2); see `docs/REGULATORY.md`, verified against source text 2026-08-09. The",
        "obligations apply from 2 December 2027, so this is documentation written in advance of",
        "a duty rather than in discharge of one.",
        "",
        "---",
        "",
        "## 1 · General description (Annex IV(1))",
        "",
        f"**Intended purpose.** {curtailment.title}.",
        "",
        f"**Decision horizon.** {curtailment.horizon_seconds} seconds.",
        f"**Actuation.** {curtailment.actuation}. There is no human in a five-second loop and",
        "pretending otherwise would be dishonest engineering; everything else in this document",
        "is the weight that carries instead.",
        "",
        f"**Hazard the system mitigates (Art. 9).** {curtailment.hazard.strip()}",
        "",
        "## 2 · Elements and development process (Annex IV(2))",
        "",
        "The system is a stream processor with a bounded model step. Every decision about *when*",
        "a computation is valid — window closure, watermark advance, lateness, freshness — is",
        "deterministic code in `src/watermark/core/`, which imports no framework and no cloud",
        "SDK and reads no clock. The model forecasts; it does not decide whether it may be used.",
        "",
        "| Component | Role |",
        "|---|---|",
        "| `src/watermark/core/` | Windowing, watermarks, deduplication, lateness, "
        "point-in-time joins |",
        "| `streaming/` | PyFlink adapter. Carries no semantic literal (enforced) |",
        "| `src/watermark/features/` | Two independent resolution mechanisms from one contract |",
        "| `src/watermark/decisions/` | The decision engine and the declared fallback rules |",
        "",
        "## 3 · Data and data governance (Annex IV(2)(d), Art. 10)",
        "",
        "**Features used by this decision:**",
        "",
        "| Feature | Window | Freshness budget | Personal data |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| `{feature.id}` | {feature.window.length_seconds}s | "
        f"{feature.freshness_budget_seconds}s | {'yes' if feature.personal_data else 'no'} |"
        for feature in features
    ]
    lines += [
        "",
        "A feature with no declared freshness budget cannot load. That is what makes the",
        "staleness guarantee mechanical rather than a matter of remembering.",
        "",
        f"**Personal data in scope of erasure:** {len(contracts.personal_data_entities)} entities, "
        f"{len(contracts.personal_data_features)} features. Derived from the contracts "
        "rather than from a list.",
        "",
        "## 4 · Human oversight (Annex IV(2)(e), Art. 14)",
        "",
        "Curtailment actuates automatically and is bounded instead: the permitted actions are",
        f"`{', '.join(curtailment.permitted_actions)}` — a throttle, never a disconnection "
        "— and the",
        "fallback may take only a subset of them, so it cannot escalate the system's authority at",
        "the moment the system knows least.",
        "",
        "The anomaly path, which does affect a person, cannot actuate at all: the contract",
        "describing an automatic consequential decision fails to load.",
        "",
        "## 5 · Accuracy, robustness and cybersecurity (Annex IV(2)(g), Art. 15)",
        "",
        "| Property | How it is established |",
        "|---|---|",
        "| No decision from an unclosed window | `evals/watermark/`, 7 labelled situations |",
        "| Reproducibility | `evals/replay/`, shuffled and duplicated delivery, byte-identical |",
        "| Train/serve parity | `evals/parity/`, two independent mechanisms, no tolerance |",
        "| No decision on a stale feature | `evals/freshness/`, 7 cases |",
        "| Recovery | `tests/recovery/`, replay after a mid-burst restart, no double counting |",
        "| Device identity | X.509 per device; a meter may publish only as itself |",
        "| Network | No egress. Interface endpoints only, checked against the granted services |",
        "",
        "## 6 · Record-keeping (Annex IV(2)(f), Art. 12 and 19)",
        "",
        "Every decision records the inputs **as served**, their ages at the moment of the",
        "decision, the model version, the watermark status, and whether it came from the model or",
        "from the declared fallback. Logs are retained for 400 days; Art. 19 sets a floor of six",
        "months and the configuration refuses anything shorter.",
        "",
        "## 7 · Fallback (Art. 9, ADR-0001)",
        "",
        f"**Rule.** `{curtailment.fallback.id}` — {curtailment.fallback.description.strip()}",
        "",
        f"Uses a model: {'yes' if curtailment.fallback.uses_model else 'no'}. "
        f"Reads the feature store: {'yes' if curtailment.fallback.uses_features else 'no'}. "
        "Both are refused at load time, because a fallback that needs either is unavailable in",
        "exactly the conditions the primary path is.",
        "",
        "## 8 · Access control",
        "",
        f"{len(policy.principals())} principals, governed by tags rather than by a list of tables.",
        "The control room has no access to anything personal: a curtailment decision is about a",
        "substation, and the operator does not need to know whose house is behind it.",
        "",
        "## 9 · Post-market monitoring (Art. 72)",
        "",
        "The fallback rate is a first-class metric. A path silently running on fallback for a week",
        "is an outage nothing else reports — every individual decision was correct and",
        "conservative, and the aggregate is the only place the outage is visible.",
        "",
        "---",
        "",
        "*Generated from the contracts and the policy in this repository. Regenerate with*",
        "*`python scripts/generate_annex_iv.py`; CI checks it has not drifted.*",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the document has drifted.")
    arguments = parser.parse_args()

    generated = generate()
    if not arguments.check:
        OUTPUT.write_text(generated, encoding="utf-8")
        print(f"wrote {OUTPUT.name}")
        return 0

    if not OUTPUT.exists():
        print(f"{OUTPUT} does not exist; run without --check", file=sys.stderr)
        return 1
    if OUTPUT.read_text(encoding="utf-8") != generated:
        print(
            f"{OUTPUT.name} has drifted from the contracts it is generated from. Either a "
            "contract changed and the document was not regenerated, or the document was edited "
            "by hand — and a hand-edited generated document is one that describes the system "
            "somebody meant to build.",
            file=sys.stderr,
        )
        return 1
    print("annex-iv: the generated documentation matches the contracts it is generated from")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
