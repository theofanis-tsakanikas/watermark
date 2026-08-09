# The scenario

Everything in this repository is built for one fictional but realistically-shaped operator.
Read this before designing anything: most of the engineering difficulty in the project comes
from properties of this domain, not from the AWS services used to serve it.

## The operator

**Elektra Dianomi** — a distribution system operator and EV charging provider in a single EU
member state.

| | |
|---|---|
| Smart meters | ~250,000 residential + ~8,000 commercial |
| Public EV chargers | ~2,000 across ~600 sites (22 kW AC and 150 kW DC) |
| Substations | 400, each with a declared thermal limit |
| Meter interval | 15 minutes, uploaded in bursts, not evenly |
| Charger telemetry | 1 Hz while a session is active |
| Peak ingest | ~4,000 events/s; typical ~900 events/s |

Sizing matters because it decides which problems are real. At 250k meters on 15-minute
intervals the steady state is modest; the *bursts* are the problem — most meters upload
within the same few minutes after the interval boundary, and a firmware cohort that retries
on failure can double that spike.

## The three decision paths

### 1. Curtailment (seconds)

A substation approaches its thermal limit. A short-horizon load forecast per substation,
combined with the live charging sessions attached to it, decides which sessions to throttle
and by how much. The signal goes back out to the charger.

- Automatic. There is no human in a five-second loop, and pretending otherwise would be
  dishonest engineering.
- Therefore everything else has to carry the weight: bounded action (a throttle, never a
  disconnection), a conservative deterministic fallback, and an audited record.
- **The fallback rule** is the interesting part: when the forecast is unavailable or its
  features are stale, curtailment falls back to a proportional rule on measured load alone —
  more aggressive than the model, and marked as fallback for its whole life. It costs
  customers charging speed. It does not cost the substation.

### 2. Meter anomaly (hours)

A classifier scores meters for tampering, faults and non-technical loss. High scores queue
for an inspector.

- **Never automatic.** The output is a ranked queue with the reasons attached; a named
  inspector accepts or rejects, and the rejection is a training signal.
- This is the fairness-sensitive path. Non-technical loss models are a documented case of
  proxy discrimination: old installations, irregular consumption patterns and prepayment
  correlate with lower-income areas, and a model trained on *confirmed* cases learns where
  inspectors historically went, not where tampering historically was. That feedback loop is
  the thing the bias analysis must actually look for, and it must be written down as the
  risk it is.

### 3. Settlement (days, with restatement)

Hourly consumption totals per meter and per balancing group, feeding billing and market
settlement.

- No model anywhere near it.
- Its entire difficulty is **late data**. A meter that uploads three days late changes an
  already-published total.

## The data

| Stream / table | Source | Grain | Notes |
|---|---|---|---|
| `meter_reading` | IoT Core (MQTT, X.509) | meter × 15 min | duplicates on retry, clock skew, firmware-version schema variants |
| `charge_session_tick` | IoT Core | session × 1 s | session start/stop can arrive out of order relative to ticks |
| `substation_telemetry` | IoT Core | substation × 1 s | the safety-relevant signal |
| `meter_batch_drop` | S3 (SFTP from a legacy AMI head-end) | meter × 15 min | up to **3 days** late, the reason claim 1 and claim 2 exist |
| `customer`, `contract`, `tariff`, `meter_assignment` | CDC via DMS from the operational DB | SCD-2 | a meter changes customer; a tariff changes mid-period |
| `substation_limit` | CDC | SCD-2 | limits change seasonally |
| `inspection_outcome` | internal app | inspection | the label source, and the feedback loop |

### The properties that make it hard

1. **Late data with real consequences.** A three-day-late batch restates a published
   settlement total. It must restate, not overwrite (doctrine 4).
2. **Duplicates.** Meters retry. Deduplication is on `(meter_id, interval_start,
   payload_hash)` and must survive replay.
3. **Clock skew.** Device clocks drift. Event time comes from the device; a reading whose
   event time is in the future beyond tolerance is quarantined with the reason, not clamped.
4. **Idle sources.** A quiet substation stops emitting. In Flink, an idle partition holds
   the watermark back and every other substation's windows stop closing — silently, with no
   error anywhere. This is claim 1's sharpest case and it must be in the eval set.
5. **Schema evolution.** Three firmware generations emit three payload shapes. Glue Schema
   Registry with enforced compatibility, and the core logic normalises before anything else
   sees a record.
6. **Point-in-time correctness.** A feature computed today with today's tariff is wrong for a
   reading from last month. Every join against SCD-2 reference data resolves as of the
   event's time. Getting this wrong is also the classic route to label leakage in training.
7. **Backfill.** Thirty days must replay through the identical code path and produce
   identical output. No "backfill script" that is a second implementation.
8. **Erasure on an append-only lakehouse.** A customer exercises Art. 17. The data is in
   Iceberg, in the offline feature store, in the online store, in training sets, and
   statistically inside model artefacts. See claim 6 — this is the hardest thing in the
   project and the most valuable to have solved.
9. **Cold start.** A newly installed meter has no history. Rules, not a model.
10. **Feature freshness.** The forecast is only as good as its most recent input. A feature
    past its budget is not served (claim 4).
11. **Recovery.** Kill the job mid-window, restore from savepoint, prove no double counting.
12. **Consent and purpose.** Consumption data at 15-minute resolution is behavioural: it
    shows when a household is empty. Purpose limitation is not decorative here.

## Synthetic data

Generated, seeded and deterministic — committed under `data/`. It must contain, on purpose:

- a firmware cohort that duplicates ~2% of readings
- a meter population with clock skew, including two meters skewed beyond tolerance
- a batch drop that lands 3 days late and changes a published total
- one substation that goes idle for 40 minutes
- a tariff change mid-period and a meter that changes customer
- a genuine tampering signature and two lookalikes that are *not* tampering
- a demographic proxy correlation strong enough that a careless model picks it up — this is
  what makes the bias analysis a real result rather than a green tick
- N meters with a deliberate evidence gap, so abstention/fallback counts are exact

The generator is part of the argument. A synthetic dataset with no pathology proves nothing.
