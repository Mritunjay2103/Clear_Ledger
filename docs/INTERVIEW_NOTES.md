# Interview notes

Answers you can defend, each with one real file and function to open. Nothing
here claims a deployment, a customer, a measured accuracy figure or a saving
that was observed — because none of those exist.

---

## Why this problem

Accounts payable is where an operational mistake turns directly into money
leaving the company. The three failure modes are boring and expensive: paying
the same invoice twice, paying more than the purchase order approved, and paying
a vendor you should not be paying. All three are detectable from data the
company already has, and all three survive in practice because checking by hand
is slow and inconsistent.

It is also a good problem for showing judgment, because the tempting answer —
"have a language model read the invoice and tell us what to do" — is the wrong
one, and saying why is more interesting than the code.

## Why rules control approval, not the model

A language model is a good reader and an unaccountable decider. It cannot tell
you why it approved something in terms you can audit, it will produce a
different answer on a different day, and its output is influenced by the
document — which is attacker-controlled input.

So the pipeline splits those two jobs. Extraction may be a model. The decision
is a pure function of extracted values and reference data, with no access to the
document, no network, and no database session.

**Open:** `backend/app/domain/policy.py` → `evaluate()`. It takes a
`PolicyFacts` value object and returns a list of `RuleResult`s plus an outcome.
Everything it uses arrives as an argument. There is nothing in that file that
could be influenced by what a PDF says beyond the parsed values themselves.

The practical consequence: a wrong or hostile extraction can cause a **review**.
It cannot cause an approval.

## What the AI actually does

It turns page text into typed fields — and only if it can show its work. Every
field must come back with the page number and the exact excerpt it came from,
and that excerpt is then checked against the real page text. A value whose quote
is not on the page is labelled `uncertain`, and an uncertain critical field
routes the invoice to review.

**Open:** `backend/app/services/extraction.py` → `verify_evidence()`.

That check is why swapping the deterministic parser for a model is safe. The
system's correctness does not depend on the model being truthful; it depends on
the verification being applied to whatever the model returns.

Three providers behind one interface — a rule-based parser (default, offline),
Ollama, and any OpenAI-compatible endpoint. Model calls are schema-constrained
with one repair attempt on malformed JSON, and a provider failure fails the run
rather than silently falling back to a different extractor.

## What OCR can get wrong

Everything, quietly. It confuses 0 and O, 1 and 7, 5 and S; it merges columns on
a skewed scan; it drops a decimal point. A misread total is the dangerous case
because it looks exactly like a correct one.

Three defences:

1. **Only pages that need it get OCR'd.** A page with a text layer is read
   exactly. Per-page provenance is stored, so a reviewer sees which numbers came
   from a scan.
2. **Evidence still applies.** An OCR'd value must quote OCR'd page text.
3. **Arithmetic is a cross-check.** If subtotal + tax does not equal the printed
   total within a paisa, something was misread and the invoice goes to review —
   which is how a single mangled digit gets caught.

**Open:** `backend/app/services/ocr.py` → `ocr_page()`, and
`backend/app/services/extraction.py` → `read_document()`, which decides page by
page whether the text layer is usable before reaching for OCR.

And the honest bit: if Tesseract is not installed, scanned pages are reported as
**unreadable**, never as empty. A missing dependency must not be able to
masquerade as a blank invoice.

## How duplicates differ from retries

Three different things that all look like "the same thing twice":

- **Duplicate** — the same invoice arriving again, either byte-identical
  (`DUPLICATE_FILE`) or re-rendered with the same vendor and invoice number
  (`DUPLICATE_IDENTITY`). Both are BLOCKED. The second one is the interesting
  case: a checksum cannot catch it, so identity is matched on normalised vendor
  plus normalised invoice number.
- **Idempotent replay** — one submission the client retried, carrying the same
  `Idempotency-Key`. Same key and same payload returns the original run; same
  key with a different payload is a 409, because that means a client bug.
- **Retry** — an explicit action on a run that failed *technically*. It
  reprocesses the stored file, links to the parent run, and is not a duplicate,
  because the invoice was never actually processed.

**Open:** `backend/app/services/ledger.py` → `find_duplicate_file()`. Note that
it skips cases whose only runs failed: if the first attempt crashed, resubmitting
is a legitimate first attempt.

## How split invoices work

An invoice smaller than its purchase order is normal, not a discrepancy. So
approval writes a **reservation** against the PO, and every later invoice is
checked against `already committed + this invoice` versus
`approved + tolerance`.

Tolerance is `min(approved × 1%, INR 500)`. The cap stops a percentage becoming
a large absolute allowance on a large PO.

The demo case: 40,000 approved, then 60,000 approved (100,000 committed), then a
1,000 invoice goes to review. The third invoice is tiny; the running total is
what breaks. That is precisely the failure split invoicing hides.

**Open:** `backend/app/domain/matching.py` → `committed_minor_for_po()`, and
`backend/app/domain/policy.py` → `_budget_rule()`.

## Why a missing purchase order goes to review

Because selecting a purchase order commits budget, and being right most of the
time is not good enough when the wrong answer is a wrong commitment.

The scanned sample makes the case for itself: two open purchase orders for that
vendor, same amount, both plausible. The system ranks them and shows both; it
refuses to pick. Even when there is only one candidate, version 1 still asks —
auto-selecting on a single match is a rule that works until the day two POs
exist.

**Open:** `backend/app/domain/matching.py` → `resolve_purchase_order()`. Note
that fuzzy similarity only ever produces *candidates*; it never resolves.

## How a correction preserves history

It does not edit anything. A correction creates a **new run** on the same case,
with the reviewer's values marked `human_supplied`, and stores a `ReviewAction`
holding who, why, the old values and the new ones. Both runs stay readable
forever.

Two guards:

- A run holding an active reservation **cannot** be corrected, because that
  would either replace or double-count a commitment. Revoke-and-reapprove is a
  documented gap, not a half-built feature.
- The client sends the version it was looking at. If the run has moved on, the
  correction is rejected with a 409 instead of applying to a state the reviewer
  never saw.

**Open:** `backend/app/services/workflow.py` → `_apply_overrides()` and the
correction route in `backend/app/api/routes/runs.py`.

## What a crash does

Nothing is buffered. Each stage writes its event and commits as it happens, so a
process that dies mid-run leaves a partial but truthful record.

On startup, `recover_on_startup()` marks any `RUNNING` run as `INTERRUPTED` —
this process is the only worker and has just started, so RUNNING means the
previous process died — and re-enqueues anything still `QUEUED`, oldest first. An
interrupted run holds **no decision**, so it can never be mistaken for one.

Re-running is an explicit choice, never automatic: re-executing work whose side
effects are unknown is how systems double-commit.

**Open:** `backend/app/services/queue.py` → `recover_on_startup()`, and
`backend/tests/test_recovery.py`, which forces the states rather than hoping.

## The one place concurrency actually matters

Two approvals racing on the same purchase order.

The commit stage opens its transaction with `BEGIN IMMEDIATE`, taking SQLite's
write lock before reading, then re-reads the committed total and re-evaluates
the tolerance inside that transaction. It does not trust the check made earlier
in the pipeline.

Underneath, two partial unique indexes — one on the case, one on
vendor + normalised invoice number, both scoped to active reservations — make it
a database guarantee rather than an application promise.

**Open:** `backend/app/db/session.py` → `immediate_transaction()`, and the two
`Index(...)` declarations at the end of `backend/app/db/models.py`.

## Why money is never a float

`0.1 + 0.2 != 0.3` is a curiosity in most code and an audit finding here.
Amounts are parsed with `Decimal` and stored as integer minor units — 11,800.00
is 1,180,000 paise. Rounding is `ROUND_HALF_UP`, which is what a person means by
rounding, not Python's banker's rounding.

**Open:** `backend/app/domain/money.py` → `to_minor()` and `quantize()`.

## Where the prototype stops

- **Invoice-to-PO only.** No goods receipts, so it is not three-way matching.
  Saying otherwise would be a lie about capability.
- **APPROVED reserves; it never pays.** Nothing here touches a payment rail.
- **INR only for automatic evaluation.** Other currencies parse and display
  correctly, then route to review. Converting needs a rate, a date and a policy
  about which rate applies, none of which exist.
- **Credit notes are recognised, not processed.** They need offsetting rules
  against the original invoice.
- **No revoke-and-reapprove**, so an approved run cannot be corrected.
- **Reference data is read-only in the UI.** The CSVs are the source of truth.
- **One shared reviewer login.** Corrections record an actor label the reviewer
  types, not an authenticated identity.

## What changes for multi-user production

| Now | Then | Why |
| --- | --- | --- |
| SQLite, one writer | PostgreSQL | Two app processes would serialise on the write lock. The partial indexes port directly; `BEGIN IMMEDIATE` becomes a row lock on the purchase order. |
| In-process queue | A broker, or `FOR UPDATE SKIP LOCKED` | Multiple workers need visibility timeouts and a dead-letter path. |
| PDFs on a volume | Object storage | A container volume does not survive rescheduling. Same SHA-256 key. |
| HTTP Basic | OIDC with roles | The actor field needs a real user id, and "may correct extraction" and "may select a purchase order" should be different permissions. |
| Reference CSVs | The ERP | And then "committed" has to reconcile with what the ERP believes, which is a consistency problem this prototype does not have. |
| Request-id logs | Traces and alerting | The metric to watch is the review-to-approved ratio: when extraction quality drifts, that moves first. |

The reason the current design is defensible is that none of these changes touch
the decision logic. `domain/` has no session, no client and no I/O — it would be
lifted into the new system unchanged.

## Questions worth being asked

**"Why not let the model pick the purchase order when there's only one match?"**
Because the rule you write for the single-candidate case is the rule that fires
on the day there are two and one of them is wrong. The cost of asking is one
click; the cost of a wrong commitment is a wrong commitment.

**"Isn't blocking a re-rendered duplicate too aggressive?"** It is a deliberate
asymmetry. A false block costs someone a conversation; a false approval costs
money. Where the two are not symmetric, the system should not behave as if they
are.

**"How do you know the extraction is right?"** For a critical field, I do not
have to trust it: the value has to quote the document, and the quote is checked
against the page. What I can say is that an unverifiable value never reaches an
approval. What I cannot say — and do not claim anywhere — is an accuracy
percentage, because no live model has been measured against this data.

**"What would you do next, with another week?"** Revoke-and-reapprove, so an
approved run can be corrected safely; then goods receipts, because
invoice-to-PO is the half of matching that catches billing errors and not the
half that catches "we never received it".
