"""Run the documented demo scenarios against a live ClearLedger server.

This exercises the real HTTP surface in the same order a reviewer would click
through it, and prints the decision, driving rule codes and purchase-order
arithmetic for each step.

    python scripts/smoke_demo.py --base-url http://127.0.0.1:8000
    python scripts/smoke_demo.py --only happy-path,e1-duplicate

CLEARLEDGER_BASE_URL, CLEARLEDGER_USERNAME and CLEARLEDGER_PASSWORD supply the
same values, which is easier when pointing at a container.

The scenarios build on each other, so this expects a database with no prior
runs. Stop the server and run `python scripts/manage.py reset-demo --confirm
RESET` first, or point it at a fresh container.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PATH = PROJECT_ROOT / "data" / "samples" / "expected_results.json"

MUTATION_HEADERS = {"X-ClearLedger-Request": "1"}
DEMO_ORDER = [
    "happy-path",
    "e1-duplicate",
    "e2-part-1",
    "e2-part-2",
    "e2-part-3",
    "e3-scanned",
    "e4-arithmetic",
    "r1-blocked-vendor",
    "r2-wrong-vendor-po",
    "r3-missing-number",
    "r4-currency",
    "r5-credit-note",
    "r6-prompt-injection",
    "r7-unreadable-scan",
]


def expected_for(sample_id: str) -> dict:
    if not EXPECTED_PATH.exists():
        return {}
    payload = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    for entry in payload.get("samples", []):
        if entry["id"] == sample_id:
            return entry
    return {}


def wait_for_run(client: httpx.Client, run_id: str, timeout: float = 180.0) -> dict:
    deadline = time.monotonic() + timeout
    after = 0
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}/events", params={"after": after})
        response.raise_for_status()
        payload = response.json()
        after = payload["last_sequence"]
        if payload["is_terminal"]:
            break
        time.sleep(0.4)
    detail = client.get(f"/api/runs/{run_id}")
    detail.raise_for_status()
    return detail.json()


def run_sample(client: httpx.Client, sample_id: str) -> dict:
    response = client.post(
        "/api/runs",
        data={"sample_id": sample_id},
        headers={**MUTATION_HEADERS, "Idempotency-Key": str(uuid.uuid4())},
    )
    if response.status_code not in (200, 202):
        raise SystemExit(f"{sample_id}: intake failed {response.status_code} {response.text[:300]}")
    return wait_for_run(client, response.json()["run"]["id"])


def describe(detail: dict, expected: dict) -> bool:
    decision = detail.get("decision")
    status = detail.get("execution_status")
    expected_decision = expected.get("expected_decision", "?")
    acceptable = {value.strip() for value in expected_decision.split("_OR_")}
    observed = status if status in {"FAILED", "INTERRUPTED"} else decision or status
    ok = observed in acceptable
    comparison = (detail.get("decision_payload") or {}).get("po_comparison") or {}
    driving = [
        f"{item['code']}:{item['status']}"
        for item in detail.get("rule_results") or []
        if item["status"] in {"fail", "review"}
    ]
    print(f"  execution : {status}")
    verdict = "OK" if ok else "MISMATCH"
    print(f"  decision  : {decision or '-'}   expected {expected_decision}   {verdict}")
    print(f"  invoice   : {detail.get('invoice_number')} / {detail.get('vendor_display_name')}")
    gross = detail.get("gross_total")
    print(f"  amount    : {gross['amount'] + ' ' + gross['currency'] if gross else '-'}")
    if comparison.get("po_number"):
        print(
            f"  po math   : {comparison['po_number']} committed "
            f"{comparison.get('committed_before_display')} + invoice "
            f"{comparison.get('invoice_gross_display')} = "
            f"{comparison.get('projected_total_display')} vs allowed "
            f"{comparison.get('allowed_total_display')} -> {comparison.get('verdict')}"
        )
    if detail.get("reservation"):
        print(f"  reserved  : {detail['reservation']['amount']['amount']}")
    else:
        print("  reserved  : nothing")
    print(f"  driving   : {', '.join(driving) or 'none'}")
    if detail.get("error_code"):
        print(f"  error     : {detail['error_code']} - {detail.get('error_message')}")
    headline = (detail.get("decision_payload") or {}).get("outcome", {}).get("headline")
    if headline:
        print(f"  headline  : {headline}")
    return ok


def correct_e3(client: httpx.Client, detail: dict) -> bool:
    """Select a candidate purchase order and confirm the child run approves."""
    candidates = (detail.get("decision_payload") or {}).get("po_resolution", {}).get(
        "candidates"
    ) or []
    if not candidates:
        print("  correction: no candidate purchase orders were offered")
        return False
    chosen = candidates[0]
    response = client.post(
        f"/api/runs/{detail['id']}/corrections",
        json={
            "actor_label": "A. Analyst (demo)",
            "reason": "Confirmed with the site manager that this covers the Bengaluru contract.",
            "expected_version": detail["version"],
            "selected_po_id": chosen["po_id"],
        },
        headers={**MUTATION_HEADERS, "Idempotency-Key": str(uuid.uuid4())},
    )
    if response.status_code != 202:
        print(f"  correction failed: {response.status_code} {response.text[:300]}")
        return False
    child = wait_for_run(client, response.json()["run"]["id"])
    print(f"  correction: selected {chosen['po_number']} -> child run {child['decision']}")
    parent = client.get(f"/api/runs/{detail['id']}").json()
    unchanged = parent["decision"] == "REVIEW"
    print(f"  original  : still {parent['decision']} ({'unchanged' if unchanged else 'CHANGED'})")
    return child["decision"] == "APPROVED" and unchanged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", default=os.environ.get("CLEARLEDGER_BASE_URL", "http://127.0.0.1:8000")
    )
    parser.add_argument("--only", default="")
    parser.add_argument("--username", default=os.environ.get("CLEARLEDGER_USERNAME", ""))
    parser.add_argument("--password", default=os.environ.get("CLEARLEDGER_PASSWORD", ""))
    args = parser.parse_args()
    print(f"target: {args.base_url}")

    auth = (args.username, args.password) if args.username else None
    targets = [item.strip() for item in args.only.split(",") if item.strip()] or DEMO_ORDER

    failures: list[str] = []
    with httpx.Client(base_url=args.base_url, timeout=120.0, auth=auth) as client:
        capabilities = client.get("/api/capabilities")
        capabilities.raise_for_status()
        info = capabilities.json()
        ocr = info["ocr"]
        ocr_line = f"available - {ocr['engine']}" if ocr["available"] else "unavailable"
        print(f"extraction: {info['extraction']['configured_provider_label']} | OCR: {ocr_line}")
        print()

        for sample_id in targets:
            print(f"[{sample_id}]")
            detail = run_sample(client, sample_id)
            if not describe(detail, expected_for(sample_id)):
                failures.append(sample_id)
            needs_correction = sample_id == "e3-scanned" and detail.get("decision") == "REVIEW"
            if needs_correction and not correct_e3(client, detail):
                failures.append("e3-scanned-correction")
            print()

        if "happy-path" in targets:
            print("[happy-path re-upload: identical bytes]")
            detail = run_sample(client, "happy-path")
            observed = detail.get("decision")
            codes = [
                item["code"]
                for item in detail.get("rule_results") or []
                if item["status"] in {"fail", "review"}
            ]
            print(f"  decision  : {observed}   driving: {', '.join(codes)}")
            if observed != "BLOCKED" or "DUPLICATE_FILE" not in codes:
                failures.append("happy-path-reupload")
            print()

    if failures:
        print(f"MISMATCHES: {', '.join(failures)}")
        return 1
    print("All scenarios matched their documented expectations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
