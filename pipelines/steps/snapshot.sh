#!/usr/bin/env bash
# Pin the snapshot. Run by the pipeline's first Processing step.
#
# A file, not an inline string in the pipeline definition. SageMaker validates
# `ContainerArguments` against a pattern that a shell one-liner with quotes does not satisfy —
# "Unable to parse pipeline definition" — and a script that lives in the repository can be read,
# reviewed and run by hand, which a JSON string inside HCL cannot.
set -euo pipefail

python3 -c "import zipfile; zipfile.ZipFile('/opt/ml/processing/input/code/code.zip').extractall('/tmp/code')"
pip install --no-deps --quiet /tmp/code/*.whl

python3 -m watermark.models.snapshot \
  --snapshot "$SNAPSHOT_ID" \
  --as-of "$AS_OF" \
  --labels "$LABEL_SOURCE" \
  --source /tmp/code/population.csv
