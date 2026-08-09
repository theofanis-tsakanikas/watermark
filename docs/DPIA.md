# Data protection impact assessment — the meter anomaly path

**GDPR Art. 35.** Written for the anomaly path only. The curtailment path processes substation
telemetry and takes no decision about a person; the settlement path processes personal data but
takes no automated decision with a significant effect. This one does both, which is what makes
an assessment necessary rather than decorative.

**Status: written 2026-08-09, against a system that has never processed a real person's data.**
Nothing in this repository has been applied (`docs/DECISIONS.md` 15), so every risk below is
assessed against a design and synthetic data. A DPIA is a living document in a system that
runs; this one is the version that exists before anything does.

---

## 1 · The processing

15-minute consumption per meter, joined point-in-time to the customer responsible for it, is
scored for tampering, faults and non-technical loss. High scores enter a queue an inspector
works. The inspector's accept or reject is recorded and becomes a training signal.

**Necessity.** Non-technical loss is real and is ultimately paid for by other customers. The
alternative to scoring is inspecting at random or inspecting on complaint, and both send more
inspectors to more homes for fewer findings.

**Proportionality.** The output is a *ranking*, not a decision. Nothing follows from a high
score except that somebody looks sooner. The processing that would be disproportionate —
automatic disconnection, automatic back-billing — is not merely unimplemented: the contract
describing it fails to load (claim 7).

## 2 · What makes it high-risk under Art. 35(3)

- **Systematic evaluation of personal aspects**, on a large scale (250,000 households).
- **Behavioural data.** Consumption at 15-minute resolution shows when a house is empty, when
  somebody wakes, whether the pattern changed. It is not "meter readings".
- **A decision with a significant effect**: an inspection, and what follows a finding.

Not Annex III of the AI Act. `docs/REGULATORY.md` argues that at length and declines to claim
otherwise, because asserting high-risk status to sound more rigorous would be the failure this
project exists to argue against.

## 3 · The risks, and what actually mitigates each

| Risk | Mitigation | Where it is enforced |
|---|---|---|
| An automated decision with a legal or similarly significant effect (Art. 22) | The path cannot actuate. The contract fails to load with `actuation: automatic`, and `Actuation` cannot be constructed without a `Review` | claim 7, 8/8 |
| **Proxy discrimination through the inspection feedback loop** | Measured, found, and the model **refused promotion** because of it | `docs/BIAS-FINDING.md`; claim 5 |
| Function creep — consumption collected for settlement used to investigate | Purpose is a Lake Formation tag, and the settlement role cannot read inspection outcomes or vice versa | `scripts/check_policy_access.py`, 24 pairs |
| A decision taken on data the system had not seen | No decision from an unclosed window; no decision on a stale feature | claims 1 and 4 |
| A number that cannot be explained to the person it is about | The decision record carries the inputs *as served*, their ages, the model version and the fallback marker | AI Act Art. 12; claim 4 |
| Erasure that quietly does not happen | The system refuses to certify unless every leg confirms, and names the leg deletion cannot reach | claim 6, 9/9 |
| Excessive retention | Log retention 400 days (Art. 19 floor is six months); Athena results expire; noncurrent object versions expire at 90 days | `infra/foundation` |

## 4 · The residual risk, stated plainly

**The feedback loop is not fixed.** `docs/BIAS-FINDING.md` measures it, the promotion gate
refuses the model because of it, and neither of those breaks the loop. A model trained on
inspector confirmations learns where inspectors went; the only mitigation that changes that is
allocating a fraction of inspections independently of the model, which costs wasted visits and
is a decision for the operator rather than for the platform.

Until that happens, the honest position is that this path **has no promotable model**. The
queue would be populated by the deterministic rules the cold-start case already uses. That is a
worse product and a true statement, and the repository ships the true statement.

**Erasure has a declared residual.** A model trained before an erasure request retains the
subject statistically. Crypto-shredding does not reach it. The window is 30 days, printed on
the certificate, and machine unlearning is not claimed.

## 5 · Rights

| Right | How it is served |
|---|---|
| Art. 15, access | The decision record is per subject and self-contained: inputs as served, ages, model version, origin |
| Art. 16, rectification | A corrected reading restates rather than overwrites, and the prior value survives (doctrine 4) |
| Art. 17, erasure | Claim 6, to the declared boundary |
| Art. 22(3), human intervention | Not a safeguard bolted on: the human decision is the *only* path to actuation |

## 6 · What would change this assessment

- An operator deciding to act automatically on a score. That is a contract change, in a diff,
  with a reviewer — and it would make the path Art. 22(1) processing, requiring the safeguards
  this design currently avoids needing.
- Randomised inspection allocation, which would break the feedback loop and change section 4.
- A supervisory authority disagreeing with the Annex III reading in `docs/REGULATORY.md`. That
  is argued, not determined, and the repository says so.
