"""Erasure, and the refusal that is the actual deliverable.

Claim 6 does not deliver a deletion mechanism. Deleting is easy. It delivers a system that
**will not say "erased" unless every leg confirms** — and one that states, on the face of the
certificate, the leg it cannot complete.

`docs/DECISIONS.md` 11 is the honest position and this package implements it rather than
softening it: crypto-shredding does not reach the weights of a model trained before the
request. The subject's contribution is statistically inside the artefact, no key protects it,
and destroying one does not remove it. That leg is satisfied by quarantining the model and
retraining from the shredded corpus within a declared residual window. **Machine unlearning is
not claimed and is not attempted.**

The certificate says so in words, not in a footnote. A subject told they have been erased when
one leg is outstanding has been told something false, and the residual becomes invisible at
exactly the moment somebody starts relying on the statement.
"""

from __future__ import annotations

from watermark.erasure.certificate import Certificate, Leg, LegOutcome, certify
from watermark.erasure.scope import ErasureScope, scope_from_contracts

__all__ = ["Certificate", "ErasureScope", "Leg", "LegOutcome", "certify", "scope_from_contracts"]
