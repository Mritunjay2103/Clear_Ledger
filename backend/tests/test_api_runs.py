"""End-to-end tests through the HTTP API.

Runs execute inline here (see ``conftest``), so each request returns once the
workflow has finished. Everything else — intake, extraction, policy, the
commitment ledger — is the production code path.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest


def upload(client, path: Path, *, key: str | None = None) -> dict:
    headers = {"Idempotency-Key": key} if key else {}
    with path.open("rb") as handle:
        response = client.post(
            "/api/runs",
            files={"file": (path.name, handle, "application/pdf")},
            headers=headers,
        )
    assert response.status_code in (200, 202), response.text
    return response.json()


def detail(client, run_id: str) -> dict:
    response = client.get(f"/api/runs/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()


def process(client, samples_dir: Path, sample_id: str) -> dict:
    body = upload(client, samples_dir / f"{sample_id}.pdf")
    return detail(client, body["run"]["id"])


def rule(run: dict, code: str) -> dict | None:
    return next((item for item in run["rule_results"] if item["code"] == code), None)


# --- the happy path ---------------------------------------------------------


def test_a_clean_invoice_is_approved_and_reserves_the_amount(client, samples_dir) -> None:
    run = process(client, samples_dir, "happy-path")

    assert run["execution_status"] == "COMPLETED"
    assert run["decision"] == "APPROVED"
    assert run["invoice_number"] == "INV-1001"
    assert run["gross_total"]["amount"] == "11800.00"
    assert run["matched_po_number"] == "PO-1001"
    assert run["reservation"] is not None
    assert run["reservation"]["active"] is True


def test_every_stage_is_recorded_in_order(client, samples_dir) -> None:
    run = process(client, samples_dir, "happy-path")

    keys = [stage["stage_key"] for stage in run["stages"]]
    assert keys == [
        "intake",
        "read_document",
        "extract_fields",
        "validate_facts",
        "match_references",
        "evaluate_policy",
        "commit_decision",
        "publish_output",
    ]
    assert all(stage["status"] == "completed" for stage in run["stages"])

    sequences = [event["sequence"] for event in run["events"]]
    assert sequences == sorted(sequences)
    assert sequences == list(range(1, len(sequences) + 1))


def test_every_extracted_field_carries_a_quoted_excerpt(client, samples_dir) -> None:
    run = process(client, samples_dir, "happy-path")
    evidence = run["extraction"]["invoice"]["evidence"]

    for field in ("vendor_name", "invoice_number", "invoice_date", "gross_total"):
        assert field in evidence, f"{field} has no evidence"
        assert evidence[field]["excerpt"].strip()
        assert evidence[field]["page"] >= 1
        assert evidence[field]["provenance"] in {"pdf_text", "ocr", "human_correction"}

    quality = {item["field"]: item["label"] for item in run["decision_payload"]["field_quality"]}
    assert quality["gross_total"] == "verified"


def test_an_approved_decision_explains_itself(client, samples_dir) -> None:
    run = process(client, samples_dir, "happy-path")
    outcome = run["decision_payload"]["outcome"]

    assert outcome["headline"]
    assert outcome["explanation"]
    assert outcome["primary_reasons"]
    assert outcome["next_actions"]
    # An approval is a recommendation to pay, never a payment.
    assert "payment" not in outcome["headline"].lower()


# --- edge case 1: duplicates ------------------------------------------------


def test_the_same_bytes_uploaded_twice_are_blocked_as_a_duplicate(client, samples_dir) -> None:
    first = process(client, samples_dir, "happy-path")
    assert first["decision"] == "APPROVED"

    second = process(client, samples_dir, "happy-path")
    assert second["decision"] == "BLOCKED"
    assert rule(second, "DUPLICATE_FILE")["status"] == "fail"
    assert second["reservation"] is None


def test_a_redesigned_reprint_of_a_paid_invoice_is_still_a_duplicate(client, samples_dir) -> None:
    process(client, samples_dir, "happy-path")
    reprint = process(client, samples_dir, "e1-duplicate")

    # Different bytes, different layout, same vendor and invoice number.
    assert rule(reprint, "DUPLICATE_FILE")["status"] != "fail"
    assert rule(reprint, "DUPLICATE_IDENTITY")["status"] == "fail"
    assert reprint["decision"] == "BLOCKED"
    assert reprint["reservation"] is None


def test_a_repeated_idempotency_key_replays_instead_of_creating_a_second_run(
    client, samples_dir
) -> None:
    key = str(uuid.uuid4())
    path = samples_dir / "happy-path.pdf"

    first = upload(client, path, key=key)
    second = upload(client, path, key=key)

    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is True
    assert second["run"]["id"] == first["run"]["id"]

    # A retried submission is not a duplicate invoice: only one run exists.
    listing = client.get("/api/runs").json()
    assert listing["total"] == 1


def test_a_reused_key_with_different_bytes_is_a_conflict(client, samples_dir) -> None:
    key = str(uuid.uuid4())
    upload(client, samples_dir / "happy-path.pdf", key=key)

    with (samples_dir / "e4-arithmetic.pdf").open("rb") as handle:
        response = client.post(
            "/api/runs",
            files={"file": ("other.pdf", handle, "application/pdf")},
            headers={"Idempotency-Key": key},
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_key_reused"


# --- edge case 2: cumulative commitments ------------------------------------


def test_partial_invoices_accumulate_and_the_one_that_breaks_the_budget_stops(
    client, samples_dir
) -> None:
    first = process(client, samples_dir, "e2-part-1")
    second = process(client, samples_dir, "e2-part-2")
    third = process(client, samples_dir, "e2-part-3")

    assert first["decision"] == "APPROVED"
    assert second["decision"] == "APPROVED"
    assert third["decision"] == "REVIEW"

    # The third invoice is small; it is the running total that stops it.
    assert third["gross_total"]["amount"] == "1000.00"
    comparison = third["decision_payload"]["po_comparison"]
    assert comparison["committed_before_display"] == "INR 100,000.00"
    assert comparison["verdict"] == "above_tolerance"
    assert third["reservation"] is None

    # Only the two approvals hold commitments.
    pos = {row["po_number"]: row for row in client.get("/api/purchase-orders").json()["items"]}
    assert pos["PO-2001"]["committed"] == "100000.00"
    assert pos["PO-2001"]["available_nominal"] == "0.00"


def test_a_blocked_run_never_commits_against_the_purchase_order(client, samples_dir) -> None:
    before = {
        row["po_number"]: row["committed"]
        for row in client.get("/api/purchase-orders").json()["items"]
    }
    run = process(client, samples_dir, "r1-blocked-vendor")
    after = {
        row["po_number"]: row["committed"]
        for row in client.get("/api/purchase-orders").json()["items"]
    }

    assert run["decision"] == "BLOCKED"
    assert before == after


# --- edge case 3: a scan with no PO reference -------------------------------


def test_a_scan_without_a_po_reference_goes_to_review_with_candidates(client, samples_dir) -> None:
    run = process(client, samples_dir, "e3-scanned")

    assert run["decision"] == "REVIEW"
    assert rule(run, "PO_REFERENCE_PRESENT")["status"] == "review"
    assert run["decision_payload"]["po_resolution"]["candidates"], "no PO suggestions offered"
    # It was read by OCR, and says so.
    assert "ocr" in run["page_provenance"]
    assert run["extraction"]["metadata"]["pages_ocr"]


# --- edge case 4: arithmetic that does not hold -----------------------------


def test_a_total_its_own_lines_do_not_support_goes_to_review(client, samples_dir) -> None:
    run = process(client, samples_dir, "e4-arithmetic")

    assert run["decision"] == "REVIEW"
    arithmetic = rule(run, "ARITHMETIC_TOTALS")
    assert arithmetic["status"] == "review"
    assert arithmetic["observed_value"] and arithmetic["expected_value"]


# --- refusals ---------------------------------------------------------------


@pytest.mark.parametrize(
    "sample_id,expected,code",
    [
        ("r1-blocked-vendor", "BLOCKED", "VENDOR_STATUS"),
        ("r2-wrong-vendor-po", "BLOCKED", "PO_VENDOR_MATCH"),
        ("r3-missing-number", "REVIEW", "FIELDS_PRESENT"),
        ("r4-currency", "REVIEW", "CURRENCY_SUPPORTED"),
        ("r5-credit-note", "REVIEW", "DOC_KIND_SUPPORTED"),
    ],
)
def test_each_refusal_names_the_rule_that_caused_it(
    client, samples_dir, sample_id, expected, code
) -> None:
    run = process(client, samples_dir, sample_id)
    assert run["decision"] == expected
    assert rule(run, code)["status"] in {"fail", "review"}
    assert run["decision_payload"]["outcome"]["next_actions"]


def test_instructions_hidden_in_the_document_do_not_change_the_decision(
    client, samples_dir
) -> None:
    run = process(client, samples_dir, "r6-prompt-injection")

    assert run["decision"] in {"REVIEW", "BLOCKED"}
    assert run["reservation"] is None
    warnings = " ".join(run["extraction"]["invoice"]["extraction_warnings"]).lower()
    assert "instruction" in warnings


def test_an_unreadable_scan_asks_for_a_human_rather_than_inventing_values(
    client, samples_dir
) -> None:
    run = process(client, samples_dir, "r7-unreadable-scan")

    assert run["execution_status"] in {"COMPLETED", "FAILED"}
    if run["execution_status"] == "COMPLETED":
        assert run["decision"] == "REVIEW"
        assert run["extraction"]["invoice"]["gross_total"] is None


def test_a_non_pdf_upload_is_refused_before_any_run_is_created(client) -> None:
    response = client.post(
        "/api/runs",
        files={"file": ("notes.txt", b"this is not a pdf", "text/plain")},
    )
    assert response.status_code == 415
    assert "pdf" in response.json()["error"]["message"].lower()
    assert client.get("/api/runs").json()["total"] == 0


def test_a_file_that_only_claims_to_be_a_pdf_is_refused(client) -> None:
    response = client.post(
        "/api/runs",
        files={"file": ("invoice.pdf", b"GIF89a not really a pdf", "application/pdf")},
    )
    assert response.status_code in (415, 422)
    assert client.get("/api/runs").json()["total"] == 0


def test_an_empty_request_says_what_is_missing(client) -> None:
    response = client.post("/api/runs")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_document"


# --- corrections ------------------------------------------------------------


def test_a_correction_creates_a_new_run_and_leaves_the_original_intact(client, samples_dir) -> None:
    original = process(client, samples_dir, "e3-scanned")
    assert original["decision"] == "REVIEW"

    po_id = next(
        row["id"]
        for row in client.get("/api/purchase-orders").json()["items"]
        if row["po_number"] == "PO-3001"
    )
    response = client.post(
        f"/api/runs/{original['id']}/corrections",
        json={
            "actor_label": "A. Reviewer",
            "reason": "Confirmed the purchase order with the facilities team.",
            "expected_version": original["version"],
            "selected_po_id": po_id,
        },
    )
    assert response.status_code in (200, 202), response.text
    child = detail(client, response.json()["run"]["id"])

    assert child["parent_run_id"] == original["id"]
    assert child["case_id"] == original["case_id"]
    assert child["trigger"] == "correction"
    assert child["decision"] == "APPROVED"
    assert child["has_human_correction"] is True

    unchanged = detail(client, original["id"])
    assert unchanged["decision"] == "REVIEW"
    assert unchanged["reservation"] is None

    assert unchanged["review_actions"], "the reason a human intervened is not recorded"
    assert unchanged["review_actions"][0]["actor_label"] == "A. Reviewer"


def test_a_corrected_field_is_labelled_as_human_supplied(client, samples_dir) -> None:
    original = process(client, samples_dir, "r3-missing-number")

    response = client.post(
        f"/api/runs/{original['id']}/corrections",
        json={
            "actor_label": "A. Reviewer",
            "reason": "Read the invoice number from the paper copy.",
            "expected_version": original["version"],
            "fields": {"invoice_number": "INV-2200"},
        },
    )
    child = detail(client, response.json()["run"]["id"])

    assert child["extraction"]["invoice"]["invoice_number"] == "INV-2200"
    quality = {item["field"]: item["label"] for item in child["decision_payload"]["field_quality"]}
    assert quality["invoice_number"] == "human_supplied"
    evidence = child["extraction"]["invoice"]["evidence"]["invoice_number"]
    assert evidence["provenance"] == "human_correction"
    assert "A. Reviewer" in evidence["excerpt"]


def test_a_correction_against_a_stale_version_is_rejected(client, samples_dir) -> None:
    original = process(client, samples_dir, "e4-arithmetic")
    response = client.post(
        f"/api/runs/{original['id']}/corrections",
        json={
            "actor_label": "A. Reviewer",
            "reason": "Editing an out-of-date view of this run.",
            "expected_version": original["version"] + 5,
            "fields": {"gross_total": "13000.00"},
        },
    )
    assert response.status_code == 409


def test_an_approved_run_cannot_be_edited_underneath_its_commitment(client, samples_dir) -> None:
    approved = process(client, samples_dir, "happy-path")
    response = client.post(
        f"/api/runs/{approved['id']}/corrections",
        json={
            "actor_label": "A. Reviewer",
            "reason": "Trying to change an invoice that already holds a commitment.",
            "expected_version": approved["version"],
            "fields": {"gross_total": "500.00"},
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["detail"]


def test_a_correction_must_say_who_and_why(client, samples_dir) -> None:
    run = process(client, samples_dir, "e4-arithmetic")
    response = client.post(
        f"/api/runs/{run['id']}/corrections",
        json={
            "actor_label": "A",
            "reason": "typo",
            "expected_version": run["version"],
            "fields": {"gross_total": "13000.00"},
        },
    )
    assert response.status_code == 422


def test_an_unknown_field_cannot_be_smuggled_into_a_correction(client, samples_dir) -> None:
    run = process(client, samples_dir, "e4-arithmetic")
    response = client.post(
        f"/api/runs/{run['id']}/corrections",
        json={
            "actor_label": "A. Reviewer",
            "reason": "Trying to set a field that is not correctable.",
            "expected_version": run["version"],
            "fields": {"decision": "APPROVED"},
        },
    )
    assert response.status_code == 422


# --- retries ----------------------------------------------------------------


def test_a_completed_run_cannot_be_retried(client, samples_dir) -> None:
    # Retry exists for technical failures. Re-running a run that already reached
    # a decision would either duplicate a commitment or add a confusing second
    # decision for the same document.
    original = process(client, samples_dir, "e4-arithmetic")
    response = client.post(f"/api/runs/{original['id']}/retry")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "retry_not_allowed"


def test_a_failed_run_can_be_retried_against_the_same_stored_file(
    client, samples_dir, mark_failed
) -> None:
    original = process(client, samples_dir, "e4-arithmetic")
    mark_failed(original["id"])

    response = client.post(f"/api/runs/{original['id']}/retry")
    assert response.status_code in (200, 202), response.text
    child = detail(client, response.json()["run"]["id"])

    assert child["case_id"] == original["case_id"]
    assert child["parent_run_id"] == original["id"]
    assert child["trigger"] == "retry"
    assert child["document"]["sha256"] == original["document"]["sha256"]
    # A retry of the identical file is not a duplicate submission of an invoice.
    assert rule(child, "DUPLICATE_FILE")["status"] != "fail"
    assert child["decision"] == "REVIEW"


# --- outputs ----------------------------------------------------------------


def test_the_json_export_carries_the_full_provenance(client, samples_dir) -> None:
    run = process(client, samples_dir, "happy-path")
    payload = client.get(f"/api/runs/{run['id']}/export.json").json()

    assert payload["decision"] == "APPROVED"
    assert payload["policy_version"]
    assert payload["input"]["sha256"] == run["document"]["sha256"]
    assert payload["evidence"]
    assert payload["rule_results"]
    assert payload["events"]


def test_the_csv_export_neutralises_spreadsheet_formulas(client, samples_dir) -> None:
    run = process(client, samples_dir, "happy-path")
    response = client.get(f"/api/runs/{run['id']}/export.csv")

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    body = response.text
    for line in body.splitlines():
        for cell in line.split(","):
            bare = cell.strip('"')
            assert not bare.startswith(("=", "+", "-@", "@")), f"unescaped formula: {cell}"


def test_the_source_document_can_be_retrieved_for_the_run(client, samples_dir) -> None:
    run = process(client, samples_dir, "happy-path")
    response = client.get(f"/api/runs/{run['id']}/document")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_events_can_be_polled_incrementally(client, samples_dir) -> None:
    run = process(client, samples_dir, "happy-path")

    everything = client.get(f"/api/runs/{run['id']}/events?after=0").json()
    assert everything["is_terminal"] is True
    assert everything["events"]

    tail = client.get(f"/api/runs/{run['id']}/events?after={everything['last_sequence']}").json()
    assert tail["events"] == []
    assert tail["last_sequence"] == everything["last_sequence"]


def test_an_unknown_run_returns_a_json_not_found(client) -> None:
    response = client.get(f"/api/runs/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"]


def test_an_unknown_api_path_is_json_not_the_html_shell(client) -> None:
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


# --- history and dashboard --------------------------------------------------


def test_history_filters_by_decision_and_reports_totals(client, samples_dir) -> None:
    process(client, samples_dir, "happy-path")
    process(client, samples_dir, "e4-arithmetic")

    approved = client.get("/api/runs?decision=APPROVED").json()
    review = client.get("/api/runs?decision=REVIEW").json()

    assert approved["total"] == 1
    assert review["total"] == 1
    assert approved["items"][0]["decision"] == "APPROVED"


def test_the_dashboard_defines_every_number_it_shows(client, samples_dir) -> None:
    process(client, samples_dir, "happy-path")
    dashboard = client.get("/api/dashboard").json()

    assert dashboard["metrics"]
    for name, metric in dashboard["metrics"].items():
        assert metric["definition"], f"{name} is shown without a definition"
    assert dashboard["decision_breakdown"]["APPROVED"] == 1
