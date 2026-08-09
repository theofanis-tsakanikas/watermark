# Portfolio context — why this project exists and what it changes

Context a session working inside this repository would otherwise not have. It is not needed
to write code, but it decides several judgement calls, so read it once.

## The author

Theofanis Tsakanikas — AI data engineer, Athens, remote-only (EU/CET), targeting AWS AI data
engineering roles and running a consulting venture ("the trust layer") at **tsakanikas.io**.
Certifications: AWS GenAI Developer (Professional), AWS Data Engineer (Associate), Databricks
GenAI Engineer and Data Engineer (Associate), HashiCorp Terraform (Associate); IAPP AIGP in
progress. MEng ECE, NTUA. The portfolio lives at `~/portfolio/projects/`.

**Conversation is always in Greek. Repository content is always in English.**

## What already exists

| Project | What it is | AWS depth |
|---|---|---|
| **FintelliGuard** | Enterprise RAG + compliance agent on Bedrock, 592 tests | Bedrock, OpenSearch Serverless, Lambda, MSK, ECS, VPC |
| **Attestor** | Multi-tenant regulated report factory on Bedrock AgentCore, 293 tests, deploy-ready | AgentCore, Iceberg/Athena, Cedar, Cognito |
| **Self-Healing Multi-Cloud Agents** | LangGraph supervisor/medic across four platforms, 380 tests | EKS, RDS, DynamoDB |
| **Multi-Cloud Governance Platform** | One contract → Unity Catalog + Snowflake, 137 tests | ECS, RDS, VPN, Route53 |
| **Fleet Risk Lakehouse** | Databricks medallion + GDPR Art. 9 column masks, 165 tests | Managed Grafana, S3 |
| **Real-Time Telemetry Pipeline** | GCP-native streaming, 102 tests | — |
| **Contract-Driven Data Pipeline** | Airflow/PySpark ETL, 47 tests | Glue, Athena, S3 |

**Attestor is the current quality bar.** Its conventions — the claim scoreboard, `gate-proof`,
`preflight`, the doctrine as numbered rules, offline-first, the ADR habit, README numbers that
are command output — are what this project should match or beat. When in doubt about a
convention, open `../attestor/` and copy the thinking rather than the file.

## The gap Watermark fills

Measured from the actual Terraform across every repository, the portfolio has **no**
SageMaker, **no** Kinesis or Flink, **no** Lake Formation, **no** Step Functions, **no** Glue
Spark jobs. That is the core of every AWS AI data engineering job description, and it is
absent. Watermark closes all of it in one coherent system.

Two further things it fixes:

1. **The Readiness Framework's dimension 02 has an unproven item** — *"train/serve feature
   parity is enforced and proven by test"*. No existing build demonstrates it. Claim 3 does.
2. **The portfolio reads as generative-first.** Three of seven projects are RAG or agents.
   Watermark rebalances toward the title actually being applied for.

## Where it will rub, and what to do about it

**Dimension 03 of the framework is written LLM-first.** Three of its five checklist items
(prompt injection, jailbreak, grounding against an approved source) do not apply to a
forecasting or classification system. Watermark will therefore score low on 03 unless the
framework grows a non-generative reading: fail-closed on stale or missing input, bounded
model judgement, an adversarial set of poisoned and out-of-distribution inputs, bias
thresholds that block a release. Either outcome is publishable — the site already publishes
a deliberate 81/100 — but it is a decision for the site, in `tsakanikas-site/tools/dimensions.py`,
**not** something to solve by bending this project.

**The site is heading toward eight build cards.** Attestor is not on it yet either. Past
four, an extra card dilutes rather than persuades. The open editorial decision is three
flagships in front (FintelliGuard, Attestor, Watermark) with the rest in a compact index.
Decide before adding a card, not after.

**Numbers live in five places.** "Six public platforms" and "~1,400 tests" appear in
`job-application/CV.md`, `CV-detailed.md`, `portfolio-one-pager.md`, `tsakanikas-site/FRAMEWORK.md`,
`tsakanikas-site/CLAUDE.md` and the LinkedIn assets. Cheap to update, expensive to forget.

## What to do when Phase 4 is done

1. Public GitHub repository, same account, README scoreboard as the front door.
2. Add to `CV.md`, `CV-detailed.md` and `portfolio-one-pager.md`; update the test total and
   the platform count everywhere listed above.
3. Add the row to the framework-mapping table in `tsakanikas-site/FRAMEWORK.md`.
4. Site card + evidence screenshots + a 16:9 video walkthrough via the existing tooling
   (`tools/video/build_wide.py`), and the long-form article via `tools/build_writing.py`.
5. **The second worked example** — the Readiness Framework scored against Watermark, beside
   the FintelliGuard one. A framework with one worked example on a RAG system looks like a
   framework for RAG systems.

## The wider plan

Watermark is the first of two or three projects of this weight. The intent is that the newer,
deeper builds carry the portfolio and the earliest, simplest ones (Contract-Driven Data
Pipeline, Real-Time Telemetry Pipeline) recede to a compact index or move out of the front
line entirely. Build accordingly: this repository is meant to be a flagship, and its standard
of proof is what justifies retiring the older ones.
