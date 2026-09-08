# Business impact

Two sections, kept apart deliberately: what was **measured** on this prototype,
and what a deployment **could** be measured against. Nothing here is a proven
return. Every estimate states its assumptions and shows its formula so you can
disagree with the inputs rather than the conclusion.

---

## Measured locally

These come from the container run recorded in [VALIDATION.md](VALIDATION.md),
against 14 synthetic fixtures on one developer machine.

| Observation | Value | What it does and does not mean |
| --- | --- | --- |
| Median run duration | 48 ms | Wall clock on this machine, for these fixtures, most of which have a text layer. Not a throughput claim. |
| Slowest run | 836 ms | The image-only scan, genuinely OCR'd. This is the realistic figure for scanned intake. |
| Cases processed | 15 | Across the demo suite |
| Approved without human input | 4 of 15 | On a fixture set built to be roughly half exceptions. **This is not an automation rate for real invoices** — the sample was chosen to exercise edge cases, not to be representative. |
| Reserved commitments | INR 135,400.00 | Sum of active reservations. Not money moved. |
| Scenario expectations met | 14 of 14 | Every documented outcome reproduced |

The auto-approval percentage on the dashboard is always shown with its
numerator and denominator, precisely so it cannot be quoted as an accuracy
figure.

## What would be worth measuring in production

Each of these is instrumentable from data the system already stores.

| KPI | Definition | Source |
| --- | --- | --- |
| Straight-through rate | Invoices reaching APPROVED with no human correction ÷ invoices with a decision | `WorkflowRun.decision`, `has_human_correction` |
| Review rate by cause | Share of reviews driven by each rule code | `WorkflowRun.rule_results` |
| Time to decision | Submission to terminal state | `created_at` → `completed_at` |
| Duplicate catch count | BLOCKED runs with a duplicate rule failing | `rule_results` |
| Tolerance catches | REVIEW runs driven by `PO_BUDGET_TOLERANCE` | `rule_results` |
| Correction rate by field | Which field reviewers fix most | `ReviewAction.proposed_values` |
| Reviewer override agreement | Corrections that then approve ÷ all corrections | run lineage |
| Extraction drift | Fields labelled `uncertain` per 100 invoices, over time | `decision_payload.field_quality` |

The last two are the ones worth building a dashboard for. **Correction rate by
field** tells you where extraction is weak and where to spend engineering time.
**Extraction drift** is the early-warning signal: if the share of uncertain
fields climbs after a vendor changes their invoice template or a model version
changes, you see it before anyone notices wrong decisions.

## An illustrative time model

Everything in this section is an **assumption**, not an observation. Substitute
your own numbers; the arithmetic is the point, not the result.

**Assumed inputs — none of these were measured here:**

| Assumption | Placeholder | Where a real figure comes from |
| --- | --- | --- |
| Manual handling per invoice | 6 minutes | Time-and-motion sample of the current AP process |
| Review handling per invoice | 3 minutes | Time to read a decision and act, once the evidence is presented |
| Straight-through share | 60% | Only knowable after a pilot on real invoices |
| Monthly invoice volume | 2,000 | The organisation's own figure |

**Formula**

```
manual_minutes    = volume × manual_minutes_each
assisted_minutes  = volume × (1 − straight_through) × review_minutes_each
minutes_saved     = manual_minutes − assisted_minutes
```

**Worked with the placeholders above**

```
manual    = 2000 × 6                = 12,000 minutes  (200 hours)
assisted  = 2000 × 0.40 × 3         =  2,400 minutes  ( 40 hours)
saved     = 9,600 minutes/month     = 160 hours/month
```

Read that as "if these four assumptions hold, the model predicts 160 hours".
Every one of them is unverified. The straight-through share in particular is the
number most likely to be wrong, and it dominates the result: at 30%
straight-through the saving falls to 116 hours, and at 80% it rises to 180.

**Not included, and material:** exception handling that takes longer than three
minutes, the cost of reviewing the system's own mistakes, onboarding time,
reference-data maintenance, and hosting. A saving model that omits the cost of
being wrong is marketing, not analysis.

## Where the value actually is

Time saved is the easy number to put in a slide. It is not the interesting one.

**Avoided duplicate payments.** A single duplicate payment is often worth more
than a month of time savings, and duplicates are exactly what a tired human
misses and a normalised-identity check does not. The demo shows the version that
matters: an invoice re-rendered in a different layout, with different bytes,
caught on vendor plus invoice number.

**Cumulative overage caught before commitment.** Split invoicing is where budget
quietly overruns, because each invoice looks fine on its own. This is checkable
only if something is tracking the running total against the purchase order —
which is what the reservation ledger is for.

**Audit answerability.** Every decision carries the evidence, the rules, the
policy version, and a snapshot of the reference data as it was. The value shows
up when someone asks about a decision from three months ago and the answer takes
a minute instead of an afternoon.

**Consistency.** Two reviewers apply a tolerance differently; one policy file
does not. That matters more at scale than the per-invoice minutes.

## How to establish the real numbers

1. Run the current process and the system side by side on real invoices for two
   weeks. Do not let the system decide anything; just record what it would have
   decided.
2. Compare its decisions against what the team actually did. Disagreements are
   the finding — in both directions.
3. From that, the straight-through share and the review time are measurements
   rather than assumptions, and the model above produces a number worth quoting.
4. Only then enable automatic approval, and only for the categories where the
   pilot showed agreement.

Until step 3, every figure in this document that is not in the "Measured
locally" table is a hypothesis.
