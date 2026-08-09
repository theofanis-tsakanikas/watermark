# Regulatory posture

**Rule for this file, and for every claim derived from it: an obligation goes in only if it
can be traced to a named article of a named instrument, and only after the text has been
read. Record the verification date. If it cannot be traced, delete it.** This project is bound
by that rule more tightly than most, because it makes a *classification* claim rather than a
documentation claim.

> **Status: verified against source text on 2026-08-09.** Every provision quoted below was
> read in the Official Journal or in a full reproduction of the consolidated text on that
> date; the sources are listed at the end. Where something was *not* verified it is in
> [What is not verified](#what-is-not-verified) and nothing in the code depends on it.

---

## Instruments in scope

| Instrument | Why it applies here |
|---|---|
| **EU AI Act** — Reg. (EU) 2024/1689, as amended by Reg. (EU) 2026/1744 (Digital Omnibus on AI, OJ 24.7.2026, in force 27.7.2026) | The curtailment decision is argued to fall in Annex III point 2 |
| **GDPR** — Reg. (EU) 2016/679 | 15-minute consumption data is personal data about a household; the anomaly flag is an automated decision about a person |
| **NIS2** — Dir. (EU) 2022/2555 | Applies to the *operator* as an electricity distribution system operator, an Annex I sector. It says nothing about the AI system, and is not inflated here |
| **Electricity market directive** — Dir. (EU) 2019/944 | Metering data management and non-discriminatory access. Relevant to purpose limitation; the article-level detail is **not** verified and is not relied on |

---

## The classification argument

### Curtailment — argued high-risk under Annex III(2)

**Annex III point 2**, verbatim and unchanged by the 2026 amendment:

> AI systems intended to be used as safety components in the management and operation of
> critical digital infrastructure, road traffic, or in the supply of water, gas, heating or
> electricity.

Curtailment is a system used in the supply of electricity. The whole argument therefore turns
on **"safety component"**, and that is precisely the term the 2026 omnibus rewrote.

**Art. 3(14)**, as amended by Reg. (EU) 2026/1744, verbatim:

> "safety component" means a component of a product or of an AI system which fulfils a safety
> function for that product or AI system, or the failure or malfunctioning of which endangers
> the health and safety of persons or property; **for the purposes of this definition, a
> component fulfils a safety function where its intended purpose is to prevent or mitigate
> risks to health and safety of persons or property**

And two paragraphs newly inserted into **Art. 6**:

> **1a.** For the purposes of this Regulation, including paragraph 1 of this Article, AI
> systems that are solely used for non-safety related aspects of user assistance, performance
> optimisation, service efficiency, automation or convenience or quality control shall not
> qualify as safety components.
>
> **1b.** Notwithstanding paragraph 1a, AI systems the failure or malfunctioning of which
> would endanger health and safety shall qualify as safety components.

The curtailment controller satisfies **both** limbs of the amended definition:

- *Intended purpose.* Its declared purpose is to prevent thermal overload of a substation.
  That is preventing a risk to the safety of persons and property, which is what the amendment
  says a safety function is. The intended purpose is set by the provider, so it is our own
  design decision that decides this — see the warning below.
- *Failure mode.* If it fails or malfunctions, the substation is not protected. Art. 6(1b)
  makes that sufficient on its own, notwithstanding any argument under 6(1a).

**The 6(1a) counter-argument, taken seriously.** A curtailment controller could be described
as *performance optimisation* — allocating a scarce resource, charging capacity, efficiently.
If it were designed as a throughput optimiser that happens to respect a thermal constraint,
6(1a) would be arguable and the classification would weaken. It is not designed that way here:
the path is invoked as a protective response to an approaching limit, its objective is the
limit and not throughput, and its fallback is more conservative than its model rather than
more efficient. **The classification depends on that design choice, and the design choice is
recorded here so that changing it re-opens the question in a diff rather than silently.**

**The Recital 55 tension, not glossed over.** Recital 55 says safety components of critical
infrastructure are systems used to directly protect physical integrity "but which are not
necessary in order for the system to function". Read strictly, that would exclude anything
integral to operating the network. Three things are worth saying rather than hiding:

1. The network functions without automated curtailment — it did before, with manual
   dispatch and cruder protection. The controller is an added protective layer, which fits the
   recital.
2. A recital is an interpretive aid, not an operative provision, and the operative text was
   made *more* precise in 2026 in a direction that does not carry the "necessary to function"
   qualifier at all.
3. Art. 3(14) is drafted around a component *of a product*, while Annex III(2) applies it to
   the management and operation of infrastructure. The fit is imperfect in the instrument
   itself. That is a real interpretive gap, and the honest response is to note it rather than
   to pick the reading that flatters the project.

**Art. 6(3) — the derogation, considered and rejected.** A system listed in Annex III is not
high-risk where it does not pose a significant risk of harm, via one of four conditions:
(a) a narrow procedural task, (b) improving the result of a previously completed human
activity, (c) detecting decision-making patterns without replacing or influencing a human
assessment, (d) a preparatory task. Curtailment matches none of them: it is not procedural,
there is no previously completed human activity to improve, it replaces rather than assists
a human judgement, and it actuates rather than prepares. A provider relying on the derogation
must document the assessment and register the system anyway (Art. 6(4)). **We do not rely on
it**, and this paragraph is the record that it was considered.

### The anomaly classifier is *not* Annex III

It is not a safety component. Its failure does not endanger anybody's physical safety; it
sends an inspector to a house, or fails to. It affects a commercial and legal relationship
with a customer. No other Annex III heading fits either — it is not employment, not credit
scoring, not access to essential services being *denied* on its output, not biometrics.

Asserting it is high-risk would make the project sound more impressive and would be wrong.
Its obligations come from the GDPR, and they are not weaker.

**GDPR Art. 22(1)**: a data subject has the right not to be subject to a decision based solely
on automated processing, including profiling, which produces legal effects concerning them or
similarly significantly affects them. Dispatching an inspection, and what follows from a
tampering finding — back-billing, contract action, referral — is a significant effect. The
system's answer is not to argue about the threshold: **the automated path cannot actuate at
all** (claim 7), so Art. 22(1) is not engaged rather than being satisfied by a safeguard under
Art. 22(3).

**One trap avoided.** Art. 6(3) carries the rule that an Annex III system performing profiling
of natural persons is *always* high-risk. That is a carve-out from the derogation, not an
independent route into Annex III. It does not make a profiling system high-risk if the system
was never in Annex III to begin with. Reading it the other way is an easy and flattering
mistake.

### Why the asymmetry is structural

The two paths must not share an actuation mechanism. Claim 7 is enforced at contract-load
time — a decision contract with `effect: significant_on_person` and `actuation: automatic`
fails to load — precisely so that "we'll automate it later" requires changing a contract, in a
diff, with a reviewer, rather than flipping a flag.

---

## Bias analysis on the anomaly path — the legal basis, and its limits

`docs/SCENARIO.md` names the risk: a non-technical-loss model trained on *confirmed* cases
learns where inspectors historically went, not where tampering historically was, and the
proxies for that correlate with income.

Measuring it needs data about the thing that must not be discriminated on. **Art. 4a**, newly
inserted by Reg. (EU) 2026/1744, is what permits that processing — and paragraph 2 is the one
that reaches this system, because the anomaly classifier is not high-risk:

> **2.** Providers and deployers of other AI systems and models and deployers of high-risk AI
> systems may exceptionally process special categories of personal data to the extent that:
> (a) such processing is strictly necessary to ensure bias detection and correction in view of
> possible biases that are likely to affect the health and safety of persons, have a negative
> impact on fundamental rights or lead to discrimination prohibited pursuant to Union law,
> **especially where data outputs influence inputs for future operations**; and (b) all of the
> conditions and safeguards set out in paragraph 1 are applied.
>
> This paragraph does not create any obligation to conduct such bias detection and correction.

Three consequences, all of which land in code:

1. The phrase *"especially where data outputs influence inputs for future operations"* is a
   description of this exact feedback loop. The scenario's proxy-discrimination risk is not an
   analogy to the provision; it is the case the provision names.
2. The Art. 4a(1) safeguards are conditions, not advice: no other data would do, technical
   limits on re-use, pseudonymisation, strict and documented access control, no transmission
   to other parties, deletion once the bias is corrected or retention ends, and a record of
   processing that states why other data would not have worked. These are testable and they
   belong in the erasure and policy layers, not in a paragraph.
3. **The last sentence is the honest one.** Art. 4a creates no obligation to do bias analysis
   on a system that is not high-risk. This project does it anyway. That is a choice, and it is
   stated as a choice rather than dressed as compliance.

---

## Obligation → control map

Every row ends in a mechanism, not a paragraph. Rows for obligations that do not yet apply are
kept, because the point of building now is to arrive with them already met.

| Obligation | Where it lands |
|---|---|
| AI Act **Art. 9** — risk management system | `docs/adr/` risk register + the fallback rules; the decision contract names the hazard it mitigates |
| AI Act **Art. 10** — data governance, incl. bias examination (Art. 10(2)(f),(g)) | Contract-driven quality gates; the bias analysis on the anomaly path, under Art. 4a(2) |
| AI Act **Art. 11 + Annex IV** — technical documentation | Generated from the repository, CI-failing on drift |
| AI Act **Art. 12** — record-keeping / automatic logs | The decision record: inputs, feature values *as served*, model version, fallback marker, lineage id |
| AI Act **Art. 13** — information to deployers | Generated system documentation; the fallback marker surfaced, not buried |
| AI Act **Art. 14** — human oversight | Claim 7; the inspector queue; the ability to halt curtailment |
| AI Act **Art. 15** — accuracy, robustness, cybersecurity | Claims 1, 2 and 4; the recovery drill; per-device X.509 identity |
| AI Act **Art. 19** — automatically generated logs, retained | Log retention in `infra/`, with the period stated |
| AI Act **Art. 72** — post-market monitoring | Model Monitor, drift thresholds and the auto-rollback path, running rather than described |
| GDPR **Art. 5** — purpose limitation, minimisation | Purpose declared per feature contract; a feature with no declared purpose does not load |
| GDPR **Art. 17** — erasure | Claim 6, to its declared boundary |
| GDPR **Art. 22** — automated individual decisions | Claim 7 |
| GDPR **Art. 25 / 32** — by design, security | Per-subject key hierarchy, no long-lived keys, private networking |
| GDPR **Art. 35** — DPIA | Written for the anomaly path, in the repository, versioned |
| AI Act **Art. 4a** — special-category data for bias detection | The safeguards in (1)(a)–(f) as conditions on the bias harness and on retention |

Articles 9, 12, 13, 14, 15, 19 and 49, and Annexes III and IV, were **not** amended by Reg.
(EU) 2026/1744 — checked against the amending regulation's own enumeration of amendments,
which reaches Articles 1, 2, 3, 4, 4a (new), 5, 6, 10, 11, 17, 25, 27, 28, 29, 30, 40, 42, 43,
50, 56, 57, 58, 60, 60a (new), 63, 64, 69, 70, 72, 75, 76, 77, 95, 96, 97, 99, 111, 113, Annex
I, Annex VIII and a new Annex XIV.

---

## Dates

**Art. 113, third paragraph, point (c)**, as replaced by Reg. (EU) 2026/1744, verbatim:

> (c) Chapter III, Sections 1, 2, and 3, with the exception of Article 6(5), shall apply from:
> (i) **2 December 2027** as regards AI systems classified as high-risk pursuant to Article
> 6(2) and Annex III; and (ii) **2 August 2028** as regards AI systems classified as high-risk
> pursuant to Article 6(1) and Annex I;

What follows for this project, stated plainly because the temptation is to blur it:

- The AI Act's general date of application, 2 August 2026, is **unchanged**. As of the
  verification date the Regulation applies; the prohibitions in Art. 5 have applied since
  2 February 2025 (with the two new prohibitions inserted in 2026 applying from 2 December
  2026), and the general-purpose AI model obligations are unaffected.
- The high-risk obligations this system is built to — Chapter III, Sections 1 to 3 — **do not
  yet apply** to it. They apply from 2 December 2027.
- So the honest sentence is *"built to obligations that take effect on 2 December 2027"*, not
  *"compliant with obligations in force"*. Any README or site copy that says otherwise is
  wrong, and the same date must match the site's copy in `tsakanikas-site/`.

No deadline in this repository may be restated from memory. Each one carries the date it was
verified.

---

## What is not verified

Listed rather than omitted, because an unverified claim that has quietly become a verified-
looking one is the failure mode this file exists to prevent. Nothing below is relied on by any
code, gate or generated document.

| Not verified | Why it is here anyway | What would settle it |
|---|---|---|
| Dir. (EU) 2019/944 article-level obligations on metering data | The directive plainly governs metering data management and non-discriminatory access to it, and that is the whole of what is claimed. The article numbering and the definition of "eligible parties" were not read in the source text | Reading Arts. 20–24 of the consolidated directive and the implementing acts on interoperability |
| NIS2 obligations beyond scope | Confirmed only that electricity distribution is an Annex I sector of high criticality and that a DSO is in scope as an entity. Nothing about Arts. 21 or 23 is stated in this repository | Reading Arts. 21 and 23 and the national transposition |
| Whether a national regulator would accept the Annex III(2) classification | It is an argued position, not a determination. No supervisory authority has been asked | Nothing available to this project. It stays argued |
| Whether the curtailment fallback rule is proportionate under Art. 9 | Proportionality is a judgement about a real network with real customers | Measurement against a real load profile, which synthetic data cannot supply |

---

## Sources consulted, 2026-08-09

- Reg. (EU) 2026/1744, Official Journal L, 24.7.2026 — read as published PDF
  (`http://data.europa.eu/eli/reg/2026/1744/oj`). Recitals 7 and 40; amendments to Art. 3(14),
  the new Art. 4a, the new Art. 6(1a)–(1c), and Art. 113 third paragraph.
- Reg. (EU) 2024/1689, Annex III point 2; Arts. 3(14), 3(62), 6(1)–(5); Recital 55 — read as
  reproduced consolidated text.
- Reg. (EU) 2016/679, Art. 22 — read as reproduced text.
- Dir. (EU) 2022/2555 — scope of the energy sector confirmed at summary level only.

**Re-verification.** These citations are dated, and a date is the only thing that makes them
checkable. When the AI Act is next amended, the affected rows change here first and in the
code second. A future `scripts/check_citations.py` is intended to fail the build on an
undated regulatory statement anywhere in `docs/` or `README.md`; until it exists, the rule is
enforced by review.
