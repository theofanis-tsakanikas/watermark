# The container the pipeline's processing steps run in.
#
# **Why this exists rather than a stock AWS image.** Every prebuilt SageMaker processing image
# ships Python 3.9, and this package requires 3.12 — not arbitrarily: `slots=True` dataclasses
# are 3.10, `datetime.UTC` is 3.11, and unions in `isinstance` are 3.10. The first live run
# said so plainly:
#
#     ERROR: Package 'watermark' requires a different Python: 3.9.21 not in '>=3.12'
#
# The alternatives were both worse. A public-ECR image cannot be pulled: this VPC has no NAT,
# and the `ecr.api`/`ecr.dkr` endpoints reach the *private* registry only. Lowering the
# package's floor would mean changing shipped code to suit a container, and losing language
# features used throughout the codebase to fix two modules.
#
# So: our own image, in our own registry, pulled through the endpoints that already exist.
#
# **It is deliberately almost empty.** The two modules that run here — `watermark.models.snapshot`
# and `watermark.models.examine` — import nothing but the standard library and this package.
# Adding scikit-learn or pandas "because a processing container usually has them" would be
# installing a dependency to satisfy an import nobody makes, and every megabyte is a megabyte
# pulled through a VPC endpoint on every step of every run.
FROM public.ecr.aws/docker/library/python:3.12-slim

# SageMaker runs the entrypoint as root and mounts inputs at /opt/ml/processing. Nothing here
# needs to be writable outside that, so the image stays as the base image left it.
WORKDIR /opt/ml/processing

# The wheel is not baked in. It arrives on the `code` channel with the population and the step
# scripts, which is what keeps the image stable while the code changes: a new commit produces a
# new wheel and reuses this image, and only a change to *this file* produces a new image.
# Baking the code in would mean a container build on every commit and a registry full of
# near-identical layers.
RUN pip install --no-cache-dir --upgrade pip

# A sanity check at build time rather than a surprise at run time. If the base image ever moves
# below the floor this package needs, the build fails here — on a runner, in seconds — instead
# of failing inside a processing job after the cluster has been paid for.
RUN python3 -c "import sys; assert sys.version_info >= (3, 12), sys.version; print(sys.version)"

ENTRYPOINT []
