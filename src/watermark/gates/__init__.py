"""The acceptance gates.

One module per thing this repository claims. A gate answers a question with a refusal and a
reason, never with a boolean — a gate that reports `False` teaches its caller to print
"failed" and stop, and the reason is the only part anybody can act on.

Every gate here ships with a mutation in `scripts/gate_proof.py` that breaks it on purpose. A
gate that has never been shown to fail is a comment.
"""

from __future__ import annotations
