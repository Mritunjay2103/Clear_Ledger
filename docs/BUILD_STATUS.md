# Build status

Current as of **8 September 2026**. Written so another session can pick this up
without re-deriving what already works. Observed results are in
[VALIDATION.md](VALIDATION.md); this file is the state of the build.

**All seven phases are complete.** What remains is external: deploy, record,
send.

---

## Phase gates

| Phase | Gate | State |
| --- | --- | --- |
| 1 — Grounding and skeleton | Backend starts, frontend renders, reference data loads from the database | done |
| 2 — Happy path slice | An invoice runs from bytes to APPROVED and survives a refresh | done |
| 3 — Document variability and models | A scan is actually read; both model adapters exist with tests | done — no live model was called, see below |
| 4 — Business exceptions | The four cases, duplicate vs idempotency, cumulative ledger, correction lineage, race protection | done |
| 5 — Dashboard and visual quality | Browser journeys pass, every control does something, metrics come from stored facts | done |
| 6 — Runtime and deployment | Container smoke test and restart persistence pass locally | done — no hosted deployment |
| 7 — Submission preparation | A fresh reviewer can start and operate the project from the README | done |

## Capabilities

**Pipeline.** Eight durable stages, each writing its event as it happens. Runs
survive process death: `RUNNING` becomes `INTERRUPTED` on startup, `QUEUED` is
re-enqueued oldest first, and neither can be mistaken for a decision.

**Extraction.** Deterministic rule parser (default, offline), Ollama, and any
OpenAI-compatible endpoint, behind one interface. Every field carries a page and
an excerpt, and the excerpt is verified against real page text before the value
is trusted. Text-layer pages are read exactly; only pages that need it are
OCR'd, with per-page provenance recorded.

**Decision.** Deterministic rules over verified values, in `domain/policy.py`,
with no I/O and no access to document text. Three outcomes plus a separate
technical `FAILED` state. Every run stores its rule results, a policy snapshot
and a reference-data snapshot.

**Ledger.** Approvals write reservations; commitments accumulate per purchase
order; tolerance is `min(1%, INR 500)`. Two partial unique indexes plus
`BEGIN IMMEDIATE` make the concurrency guarantee structural.

**Corrections.** A correction creates a linked child run and a `ReviewAction`
holding actor, reason, and old and new values. Optimistic concurrency on the run
version. Runs holding a live reservation are not correctable.

**API.** Versioned JSON with a structured error envelope, OpenAPI, idempotency
keys, HTTP Basic, origin checks, rate limiting, upload limits, JSON and CSV
exports with formula-injection neutralised.

**Frontend.** Overview, new invoice, run detail, history, reference and policy.
Live stage polling with a sequence cursor. Verified at 1440, 1024 and 390 px.

**Packaging.** One multi-stage image serving API and frontend, with Tesseract,
a non-root user, a healthcheck on `/api/readiness`, and `/data` as the only
writable path.

## Verified

- 147 backend tests, ruff clean, frontend typecheck/lint/build clean
- 10 browser journeys against the **container**, including the authenticated path
- All 14 scenarios matching their documented expectations on a fresh volume
- Restart persistence: dashboard identical across `docker restart`
- Auth covering the SPA shell and the JS bundle, not just the API
- Fixture regeneration byte-identical

## Known gaps

Each of these is deliberate and documented, not forgotten.

| Gap | Where it is written up |
| --- | --- |
| No live model has ever been called — Ollama is installed with no models pulled | [VALIDATION.md](VALIDATION.md#what-was-not-verified) |
| No hosted deployment exists | [DEPLOYMENT.md](DEPLOYMENT.md) |
| No demo video recorded | [DEMO_SCRIPT.md](DEMO_SCRIPT.md) |
| Approvals cannot be revoked, so approved runs are not correctable | [ASSUMPTIONS.md](ASSUMPTIONS.md#corrections-and-history) |
| Invoice-to-PO only; no goods receipts | [ASSUMPTIONS.md](ASSUMPTIONS.md#scope) |
| INR only for automatic evaluation | [ASSUMPTIONS.md](ASSUMPTIONS.md#money) |
| Credit notes recognised, not processed | [ASSUMPTIONS.md](ASSUMPTIONS.md#documents-and-extraction) |
| Single process, single SQLite writer | [ARCHITECTURE.md](ARCHITECTURE.md#what-would-have-to-change-for-production) |
| One shared reviewer login, not identity | [ARCHITECTURE.md](ARCHITECTURE.md#what-would-have-to-change-for-production) |
| Chromium only; no screen-reader or axe audit | [VALIDATION.md](VALIDATION.md#what-was-not-verified) |

## Decisions worth not re-litigating

Changing any of these means changing behaviour a test asserts and a document
explains.

1. **The model never decides.** Extraction is swappable; policy is not.
2. **A value without a verified excerpt is never trusted.** This is what makes
   the model swappable safely.
3. **A missing PO reference always goes to review**, even with one plausible
   candidate.
4. **Blocked vendors, duplicate identities and the cumulative limit are BLOCKED,
   not overridable.** There is no "approve anyway".
5. **Corrections create runs; they never edit them.**
6. **A provider failure fails the run.** No silent fallback unless explicitly
   enabled, and then it is recorded.
7. **Money is integer minor units with `ROUND_HALF_UP`.** Never a float.
8. **Retry is explicit**, never automatic, for both interrupted and failed runs.

## Next concrete steps

All external — the build itself needs nothing.

1. Deploy the container to a host with a persistent disk at `/data`, set
   `APP_ENV=production` and the reviewer credentials. Follow the restart check in
   [DEPLOYMENT.md](DEPLOYMENT.md), which is the step that catches a
   mis-mounted disk.
2. Reset the demo database, run E2 parts 1 and 2, and record the 4:40 walkthrough
   in [DEMO_SCRIPT.md](DEMO_SCRIPT.md).
3. Fill the placeholders in [SUBMISSION.md](SUBMISSION.md) and send the
   credentials separately from the link.

If a live model is wanted first: `ollama pull qwen2.5:7b-instruct`, set
`EXTRACTION_PROVIDER=ollama` and `OLLAMA_MODEL`, re-run
`python scripts/smoke_demo.py`, and record what actually happened in
VALIDATION.md — including the scenarios that come out differently, which is the
honest result rather than a failure.
