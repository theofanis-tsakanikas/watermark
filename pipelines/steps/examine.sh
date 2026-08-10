#!/usr/bin/env bash
# Our own bias analysis, the model card and the monitoring baseline. See snapshot.sh for why
# this is a file rather than an inline argument.
set -euo pipefail

python3 -c "import zipfile; zipfile.ZipFile('/opt/ml/processing/input/code/code.zip').extractall('/tmp/code')"
pip install --no-deps --quiet /tmp/code/*.whl

python3 -m watermark.models.examine --threshold "$THRESHOLD"
