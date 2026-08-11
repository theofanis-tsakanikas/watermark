.DEFAULT_GOAL := help
SHELL := /bin/bash

# The venv when there is one, the ambient interpreter when there is not.
#
# A hard-coded `.venv/bin/…` is true on the laptop that wrote it and false on a CI runner,
# where the package is installed into the runner's own Python. A target that only runs where
# it was written is a target nobody has tested — and it fails in the expensive place, four
# minutes into a deploy, on `No such file or directory`.
VENV := .venv
PY   := $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python3)
PYTHON := $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python3)
PIP  := $(if $(wildcard $(VENV)/bin/pip),$(VENV)/bin/pip,python3 -m pip)
RUFF := $(if $(wildcard $(VENV)/bin/ruff),$(VENV)/bin/ruff,ruff)
# Its own environment: checkov pins boto3 exactly, and the application's floor is higher.
# Created on demand by `iac-scan`.
CHECKOV_VENV := .venv-checkov
CHECKOV := $(if $(wildcard $(CHECKOV_VENV)/bin/checkov),$(CHECKOV_VENV)/bin/checkov,checkov)

LINT_PATHS := src tests tests_flink scripts data evals streaming pipelines/jobs

# The suffix must match the Flink minor version in infra/streaming/variables.tf. The connector
# is built per Flink release and a mismatched one fails at job start rather than at package
# time, which is the expensive place — the application reports READY and reads nothing.
# `scripts/check_flink_versions_agree.py` compares the two.
CONNECTOR_VERSION := 5.1.0-1.20

# ─────────────────────────────────────────────────────────────────────────────
# Everything above the "cloud" section runs with NO AWS account and NO
# credentials. That is the point: no claim in this repository needs a cloud in
# order to be checked.
# ─────────────────────────────────────────────────────────────────────────────

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

.venv:
	python3.12 -m venv .venv
	$(PIP) install --upgrade pip

.PHONY: install
install: .venv ## Create the venv and install the package with dev extras
	$(PIP) install -e ".[dev]"

.PHONY: test
test: ## Full test suite — offline, no credentials, no JVM, under a second
	$(PY) -m pytest

# Tier two (ADR-0003). A separate directory rather than a marker, so that `make test` cannot
# pick it up by accident and so a reader can see which claims cost a JVM. It is not optional:
# in CI, WATERMARK_REQUIRE_FLINK=1 turns a missing JVM from a skip into a failure, because a
# suite that quietly skips is a suite reporting green for one thing less than it says.
.PHONY: test-flink
test-flink: ## Core↔Flink equivalence on a local MiniCluster — slow, needs a JVM
	$(PY) -m pytest tests_flink

.PHONY: lint
lint: ## ruff check + format check (the exact command CI runs)
	$(RUFF) check $(LINT_PATHS)
	$(RUFF) format --check $(LINT_PATHS)

.PHONY: fmt
fmt: ## Apply ruff formatting
	$(RUFF) format $(LINT_PATHS)
	$(RUFF) check --fix $(LINT_PATHS)

# ── The seven claims ─────────────────────────────────────────────────────────
#
# One target per claim, added by the phase that earns it. A target here whose claim is not
# yet provable would be a green tick for work that has not happened.
#
#   claim 1  no decision from a window that has not closed   — phase 1
#   claim 2  replay is identical                             — phase 1
#   claim 3  train/serve parity                              — phase 2
#   claim 4  no decision on a stale feature                  — phase 2
#   claim 5  no model reaches an endpoint ungated            — phase 3
#   claim 6  erasure is complete, and proved                 — phase 4
#   claim 7  no automatic consequential decision on a person — phase 3

.PHONY: claims
claims: core-pure adapter-thin parity-independent flink-versions contracts-validate seed-check claim-1 claim-2 claim-3 claim-4 claim-5 claim-6 claim-7 policy cost ## Every claim gate that exists today

.PHONY: core-pure
core-pure: ## The stream core imports no framework and no cloud SDK
	$(PY) scripts/check_core_is_pure.py

.PHONY: adapter-thin
adapter-thin: ## The streaming adapter carries no semantic literal (ADR-0003)
	$(PY) scripts/check_adapter_is_thin.py

.PHONY: parity-independent
parity-independent: ## The two feature mechanisms share the contract and nothing else (ADR-0004)
	$(PY) scripts/check_parity_paths_are_independent.py

.PHONY: contracts-validate
contracts-validate: ## Every entity contract loads and cross-checks
	$(PY) scripts/check_contracts.py

.PHONY: seed-check
seed-check: ## The generated day reproduces its recording, exactly
	$(PY) scripts/seed_check.py

.PHONY: claim-1
claim-1: ## CLAIM 1 — no decision comes out of a window that has not closed
	$(PY) -m evals.watermark

.PHONY: claim-2
claim-2: ## CLAIM 2 — replay is identical
	$(PY) -m evals.replay

.PHONY: flink-versions
flink-versions: ## The equivalence tier and the deployment run the same Flink
	$(PY) scripts/check_flink_versions_agree.py

.PHONY: package
package-ml: ## Build the wheel the SageMaker processing steps install before running our code
	@# The stock AWS images have never heard of this package. Every pipeline step that runs
	@# `watermark.models.*` installs this first from the pipeline's `code` channel; a step whose
	@# entrypoint names a module the image does not contain fails after the cluster is paid for.
	rm -rf infra/ml/.package
	mkdir -p infra/ml/.package
	$(PIP) wheel --quiet --no-deps --wheel-dir infra/ml/.package .
	@test -n "$$(ls infra/ml/.package/*.whl 2>/dev/null)" || { \
		echo "no wheel produced in infra/ml/.package"; exit 1; }
	@# The rows the snapshot step pins, written out rather than imported: `data/` is not in
	@# the wheel, and a step that imports it dies on ImportError inside a paid cluster.
	$(PYTHON) -c "import csv,sys; sys.path.insert(0,'.'); from data.labels import labels; \
	w=csv.writer(open('infra/ml/.package/population.csv','w',newline='')); \
	w.writerow(['entity_id','deprivation_decile','score','confirmed']); \
	[w.writerow([i.meter_id,i.deprivation_decile,i.score,int(i.confirmed)]) for i in labels()]"
	cp pipelines/steps/*.sh infra/ml/.package/
	@echo "packaged $$(ls infra/ml/.package/*.whl) + population.csv + step scripts"

package: connector ## Vendor the package into infra/streaming/.package so terraform can zip it
	rm -rf infra/streaming/.package
	mkdir -p infra/streaming/.package
	$(PIP) install --quiet --target infra/streaming/.package --no-compile --no-deps .
	cp -R streaming contracts infra/streaming/.package/
	@# The Kinesis connector is a JAR the job cannot start without, and it is not vendored: a
	@# 20 MB binary in a repository whose whole claim is that everything in it is checkable is
	@# the one file nobody would ever verify. `make connector` fetches it; this refuses to
	@# package without it, rather than producing an archive that deploys and never reads.
	@test -f infra/streaming/lib/flink-sql-connector-kinesis.jar || { \
		echo "missing infra/streaming/lib/flink-sql-connector-kinesis.jar — run 'make connector'"; \
		exit 1; \
	}
	@# One connector, one jar. The Iceberg runtime lived here briefly and is gone: the lakehouse
	@# is written by `pipelines/jobs/land_to_silver.py`, where Glue supplies Iceberg natively.
	mkdir -p infra/streaming/.package/lib
	cp infra/streaming/lib/flink-sql-connector-kinesis.jar infra/streaming/.package/lib/
	@echo "packaged infra/streaming/.package ($$(du -sh infra/streaming/.package | cut -f1))"

.PHONY: claim-3
claim-3: ## CLAIM 3 — train/serve parity, between two mechanisms
	$(PY) -m evals.parity

.PHONY: claim-4
claim-4: ## CLAIM 4 — no decision on a stale feature
	$(PY) -m evals.freshness

.PHONY: claim-5
claim-5: ## CLAIM 5 — no model reaches an endpoint ungated
	$(PY) -m evals.promotion

.PHONY: claim-6
claim-6: ## CLAIM 6 — erasure is complete to a declared boundary
	$(PY) -m evals.erasure

.PHONY: policy
policy: ## The Lake Formation access suite: every reachable set, and every closed path
	$(PY) scripts/check_policy_access.py

.PHONY: cost
cost: ## A full capture stays inside the design's cost envelope
	$(PY) scripts/check_cost_envelope.py

.PHONY: claim-7
claim-7: ## CLAIM 7 — no automatic consequential decision about a person
	$(PY) -m evals.oversight

.PHONY: annex-iv
annex-iv: ## Regenerate the Annex IV technical documentation from the contracts
	$(PY) scripts/generate_annex_iv.py

.PHONY: connector
connector: ## Fetch the Kinesis connector JAR the Flink job cannot start without
	@test -f infra/streaming/lib/flink-sql-connector-kinesis.jar && { \
		echo "  connector already present"; exit 0; \
	} || true
	@mkdir -p infra/streaming/lib
	@echo "Fetching the Flink Kinesis connector for the runtime in infra/streaming/variables.tf."
	@echo "Not vendored: a 20 MB binary in a repository whose claim is that everything in it is"
	@echo "checkable is the one file nobody would verify. The download is a deliberate act."
	curl -fsSL -o infra/streaming/lib/flink-sql-connector-kinesis.jar \
		"https://repo1.maven.org/maven2/org/apache/flink/flink-sql-connector-kinesis/$(CONNECTOR_VERSION)/flink-sql-connector-kinesis-$(CONNECTOR_VERSION).jar"
	@echo "fetched $$(du -h infra/streaming/lib/flink-sql-connector-kinesis.jar | cut -f1)"

.PHONY: gate-proof
gate-proof: ## Break every gate on purpose; each must be refused, for the right reason
	$(PY) scripts/gate_proof.py

# ── Infrastructure (offline validation only — no cloud calls) ────────────────

.PHONY: wiring
wiring: ## The offline stand-ins for a plan nobody can run without credentials
	$(PY) scripts/check_lakehouse_wiring.py
	$(PY) scripts/check_vpc_endpoints.py
	$(PY) scripts/check_oidc_subjects.py
	$(PY) scripts/check_deploy_inputs.py

.PHONY: tf-fmt
tf-fmt: ## terraform fmt across every layer
	terraform fmt -recursive infra

.PHONY: tf-validate
tf-validate: ## terraform validate per layer, offline (no backend, no provider creds)
	$(PY) scripts/tf_validate.py

.PHONY: checkov-venv
checkov-venv:
	@test -x $(CHECKOV_VENV)/bin/checkov || { \
		echo "  creating $(CHECKOV_VENV) — checkov pins boto3 and cannot share ours"; \
		python3 -m venv $(CHECKOV_VENV) && $(CHECKOV_VENV)/bin/pip install -q --upgrade pip checkov; \
	}

.PHONY: iac-scan
iac-scan: checkov-venv ## checkov over the Terraform layers
	$(CHECKOV) -d infra --quiet --compact

# ── Cloud (never run implicitly; always a deliberate act) ────────────────────

.PHONY: preflight
preflight: ## Everything that must be true before the estate is stood up
	$(PY) scripts/preflight.py

.PHONY: preflight-fast
preflight-fast: ## The same, without gate-proof, terraform and checkov
	$(PY) scripts/preflight.py --fast

.PHONY: ci
ci: preflight ## Everything CI runs, in one command
