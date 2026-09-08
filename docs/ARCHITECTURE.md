# Architecture

One FastAPI process serves the JSON API and the built React app, owns a single
SQLite file, and runs one background worker. The whole system is one container.

That is a deliberate choice for a one-week prototype, and the last section says
exactly where it stops being the right one.

---

## Components

```
Browser (React SPA, served by the same process)
        │  JSON over HTTP, HTTP Basic
        ▼
FastAPI application  ── middleware: request id, security headers, origin check,
        │                          rate limit, error envelope
        ├── routes/          thin HTTP layer: parse, authorise, serialise
        ├── services/        orchestration: workflow, queue, documents, ledger
        ├── domain/          pure logic: money, normalization, matching, policy
        ├── providers/       extraction behind one interface
        └── db/              SQLAlchemy models and session management
        │
        ▼
SQLite (WAL)  +  uploaded PDFs on the same volume
```

The layering rule is that dependencies point inward: `routes` may call
`services`, `services` may call `domain`, and `domain` calls nothing above it.
`domain/` has no database session, no HTTP client, and no file system access —
it takes values and returns values, which is why the policy is testable without
fixtures.

### The boundary that matters most

`domain/policy.py` is the only place a decision is made. It receives a
`PolicyFacts` value object — already-extracted numbers, already-resolved vendor
and PO, already-computed duplicate hits — and returns a `DecisionOutcome` with a
list of `RuleResult`s. It never sees the PDF, never sees a model response, and
cannot call anything that could be influenced by document content.

This is what makes the prompt-injection sample a non-event: a document can
influence *what values are extracted*, and those values are checked; it has no
route to the code that says APPROVED.

## Data boundaries

| Store | Holds | Notes |
| --- | --- | --- |
| `runtime-data/clearledger.db` | All structured state | WAL mode, one writer |
| `runtime-data/uploads/` | Original PDFs, keyed by SHA-256 | Content-addressed, so identical files are stored once |
| `runtime-data/tmp/` | Page rasters during OCR | Deleted after each page |
| `data/reference/*.csv` | Vendors and purchase orders | Source of truth, seeded into the database |
| `data/samples/` | Generated fixture PDFs and expectations | Regenerable, not runtime state |

Everything under `runtime-data/` is the container's durable volume. Everything
under `data/` ships with the image.

Uploads are content-addressed by SHA-256 and the stored filename is sanitised,
so a hostile filename cannot escape the directory. A run keeps a `document_id`
reference rather than a path.

## Event flow

A run is a durable record, not an in-memory object. Each of the eight stages
opens a `WorkflowEvent`, does its work, and closes it with an outcome and a
duration — written and committed as it happens, not buffered until the end.

```
POST /api/runs  ──▶  case + document + run rows (QUEUED)  ──▶  202 with run id
                                    │
                        asyncio.Queue (maxsize configurable)
                                    │
                     single worker: asyncio.to_thread(execute_run)
                                    │
   intake → read → extract → validate → match → policy → commit → publish
     each stage: INSERT started event, work, UPDATE with outcome, COMMIT
                                    │
   browser polls GET /api/runs/{id}/events?after=<sequence>
```

The frontend polls with a sequence cursor rather than holding a stream open.
Polling survives a refresh, a laptop sleeping, and a proxy that buffers, and
because the events are already durable there is no state to rebuild. Server-sent
events would be a smaller payload and a larger number of failure modes.

*Code:* `backend/app/services/workflow.py`, `frontend/src/features/run/useRunLive.ts`

## Queue and restart behaviour

The worker is a single owned `asyncio.Task`. Work is dispatched with
`asyncio.to_thread` because PDF parsing and OCR are blocking C extensions that
would otherwise stall every other request on the event loop.

One worker, not a pool. The reservation ledger has to be correct under
concurrency, and serialising execution means the interesting concurrency is in
one place — the database transaction — rather than spread across workers. It
also matches the deployment target: a single small container.

On shutdown the task is cancelled and awaited, so a run interrupted mid-flight is
recorded as `INTERRUPTED` with a retryable error rather than left as `RUNNING`
forever.

On startup, `recover_on_startup()`:
- moves any `RUNNING` run to `INTERRUPTED` — no other process can be executing
  it, so it is stale by definition;
- re-enqueues every `QUEUED` run in creation order.

An interrupted run is never silently re-executed. Retry is an explicit action,
because re-running work whose side effects are unknown is how a system
double-commits.

*Code:* `backend/app/services/queue.py`

## Atomic approval

Approval is the only place where a wrong answer costs something, so it does not
rely on having checked earlier in the pipeline.

The commit stage opens its transaction with `BEGIN IMMEDIATE`, taking SQLite's
write lock up front rather than discovering a conflict at COMMIT. Inside that
transaction it re-reads the committed total for the purchase order, re-evaluates
the tolerance against live data, and only then inserts the reservation.

Two partial unique indexes make the guarantee structural rather than procedural:

```python
Index("uq_reservation_active_case", ApprovalReservation.case_id,
      unique=True, sqlite_where=ApprovalReservation.active.is_(True))
Index("uq_reservation_active_identity",
      ApprovalReservation.vendor_id, ApprovalReservation.normalized_invoice_number,
      unique=True, sqlite_where=ApprovalReservation.active.is_(True))
```

If application logic ever has a gap, the database refuses the write. Both are
scoped to `active = true`, so a future revoke-and-reapprove flow can deactivate a
reservation without deleting the audit row.

`BEGIN IMMEDIATE` is scoped to this transaction through a context variable —
read paths keep SQLite's deferred begin and are never blocked by it.

*Code:* `backend/app/db/session.py`, `backend/app/services/ledger.py`,
`backend/app/db/models.py`

## Provider abstraction

Extraction sits behind one interface:

```python
class ExtractionProvider(Protocol):
    def extract(self, request: ExtractionRequest) -> ExtractionResult: ...
```

Three implementations: a deterministic rule-based parser (the default, no
network), Ollama, and any OpenAI-compatible chat endpoint. Selection is
configuration, not code.

Every provider returns the same `InvoiceFacts` with per-field evidence: page
number and the exact source text. `services/extraction.py` then verifies each
excerpt against the real page text and labels the field `verified`, `uncertain`
or `missing`.

That verification step is what makes the abstraction safe. A model that invents
a total produces an excerpt that is not on the page, the field is marked
uncertain, and policy routes the invoice to review. The system's correctness does
not depend on the model being honest — only on the evidence check being applied
to whatever it returns.

Model responses are constrained to a JSON schema, and one repair round-trip is
attempted on malformed output. If the model is unreachable, times out, or returns
unusable JSON after the repair attempt, the run fails with a clear error rather
than falling back to a guess.

*Code:* `backend/app/providers/`, `backend/app/services/extraction.py`

## Reading documents

Pages are assessed one at a time. A page with a usable text layer is read
directly, which is exact. A page without one is rasterised with pypdfium2 and
passed to Tesseract. Per-page provenance (`text_layer` or `ocr`) is stored on the
run, so a reviewer can see which numbers came from a scan.

OCR is bounded by a page cap and a per-page timeout. If Tesseract is not
installed, scanned pages are reported as unreadable — explicitly distinct from
"the page was blank", because a missing dependency must not look like a
legitimate extraction result.

*Code:* `backend/app/services/pdf_text.py`, `backend/app/services/ocr.py`

## Frontend

React with TanStack Query. Server state lives in the query cache and is not
duplicated into component state, so a refresh and a first load take the same
path — which is why the "reload and get the same answer" journey works without
any special handling.

The run page polls while a run is live and stops at a terminal state. Every
number displayed is formatted from minor units by one helper; no arithmetic
happens in the browser.

## Why this is enough for a one-week prototype

The properties that actually matter — an auditable decision, a correct ledger,
recoverable runs, evidence for every value — are properties of the schema and
the transaction boundaries, not of the deployment topology. They are already
right, and they stay right when the topology changes.

What single-process SQLite buys is that a reviewer runs `docker compose up` and
has a working system with no external services, no message broker, and no
migration ceremony. For evaluating whether the decisions are correct and
explainable, that is the whole point.

## What would have to change for production

**SQLite → PostgreSQL.** SQLite allows one writer. Two application processes
would serialise on the write lock, and the `busy_timeout` would become visible
latency. The partial unique indexes and `SELECT … FOR UPDATE` semantics port
directly; `BEGIN IMMEDIATE` becomes an explicit row lock on the purchase order.

**In-process queue → a real broker.** The current queue lives in the process, so
its contents are only durable because the run rows are. Multiple workers need
`FOR UPDATE SKIP LOCKED` or a broker with visibility timeouts, plus a dead-letter
path for runs that fail repeatedly.

**Local disk → object storage.** Uploaded PDFs on a container volume do not
survive a rescheduled pod. S3-compatible storage with the same SHA-256 key.

**HTTP Basic → real identity.** Basic auth over one shared credential proves the
reviewer is authorised, not who they are. Corrections already record an actor
label; that field needs to hold a real user id from OIDC, with role separation
between someone who can correct extraction and someone who can select a purchase
order.

**Reference data → the ERP.** The CSVs stand in for a system of record. In
production the purchase orders come from the ERP, and the "committed" figure has
to reconcile with whatever the ERP believes, which is a distributed-consistency
problem this prototype does not have.

**Add: goods receipts.** Real three-way matching needs receipt data. Everything
here is invoice-to-PO.

**Observability.** Structured logs carry a request id today. Production needs
traces across the stages, and alerting on the ratio of review to approved
outcomes, which is the metric that reveals extraction quality drifting.
