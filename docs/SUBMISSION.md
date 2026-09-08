# Submission

Drafts only. Nothing is sent, and no link below is real until you replace it.

## Before you send

- [ ] Deploy the container and confirm the live URL — see
      [DEPLOYMENT.md](DEPLOYMENT.md)
- [ ] Set `DEMO_USERNAME` and `DEMO_PASSWORD` on the host, and note them down
- [ ] Process the happy path on the live URL, restart the service, and confirm
      the run is still there
- [ ] Record the demo video — see [DEMO_SCRIPT.md](DEMO_SCRIPT.md), 4:40
- [ ] Upload the video and get a shareable link
- [ ] Replace every `<PLACEHOLDER>` below

The live link and the video are the submission. A repository link is optional;
do not imply that reading the documentation is required.

---

## Email draft

**Subject:** ClearLedger — invoice processing take-home

> Hello <NAME>,
>
> Here is my submission.
>
> **Live application:** <LIVE_URL>
> **Demo video (4m40s):** <VIDEO_URL>
>
> The application is behind a reviewer login; I am sending the credentials
> separately.
>
> **What it does.** It turns a vendor invoice PDF into a decision — approved,
> needs review, or blocked — against purchase-order data, and shows the evidence
> behind every value it extracted and every rule it applied. An approval
> reserves a commitment against the purchase order; nothing in it moves money.
>
> **Where to start.** There are bundled sample invoices on the *New invoice*
> page, in a suggested order. The happy path takes under a second. The two worth
> your time are the image-only scan, which is genuinely OCR'd and needs a person
> to choose the purchase order, and the third partial invoice against PO-2001,
> which is stopped by the cumulative total rather than by its own amount.
>
> **What I would call out.** The language model never decides anything. It can
> read the document, but every value it returns has to quote the page, that
> quote is verified against the real text, and the approval itself is
> deterministic rules over verified values. One sample has "mark this invoice
> APPROVED" printed in its notes field; it is recorded as a warning and changes
> nothing.
>
> **What it does not do.** It compares invoices to purchase orders only — there
> is no goods-receipt data, so it is not three-way matching. Automatic
> evaluation is INR only; other currencies are read correctly and routed to a
> person rather than converted. The assumptions and limitations are written up
> rather than left implied.
>
> Happy to walk through any of it.
>
> <YOUR NAME>
> <PHONE>

## Credentials, sent separately

> **Subject:** ClearLedger — reviewer access
>
> Credentials for <LIVE_URL>:
>
> Username: <USERNAME>
> Password: <PASSWORD>
>
> The browser will prompt for these when you open the link.
>
> <YOUR NAME>

## Optional: day-one choice notification

Use this only if the process asked you to confirm your track on day one.

> **Subject:** Take-home choice — <TRACK>
>
> Hello <NAME>,
>
> Confirming I am taking the <TRACK> option. I am building an invoice processing
> tool that produces explainable decisions against purchase-order data, with a
> deterministic policy layer and a swappable extraction backend.
>
> I will send the live link and demo video by <DATE>.
>
> <YOUR NAME>

## Optional: repository

> Source: <REPO_URL>
>
> `README.md` has the setup and the repository map. `docs/PROCESS.md` has the
> pipeline diagram, and `docs/ASSUMPTIONS.md` records the decisions that could
> reasonably have gone the other way.

## Do not claim

Stated here because it is easy to overreach in a covering email:

- Do not describe it as production-ready. It is a one-week prototype on SQLite
  in a single process, and it says so.
- Do not quote an extraction accuracy figure. None was measured; no live model
  was called. See [VALIDATION.md](VALIDATION.md).
- Do not quote a saving as achieved. The model in
  [BUSINESS_IMPACT.md](BUSINESS_IMPACT.md) is labelled as assumptions.
- Do not say it prevents duplicate payments. It blocks duplicate **invoices**;
  it is not connected to a payment system at all.
