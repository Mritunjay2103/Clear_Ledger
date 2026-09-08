"""Contract tests for the two model adapters, over a stubbed HTTP transport.

These assert how the adapters speak to a server and how they behave when the
server misbehaves. They deliberately say nothing about the quality of a real
model's extraction, which is not deterministic and is not something a test can
honestly assert. Whether a live model was exercised is recorded in
docs/VALIDATION.md.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import settings
from app.providers.base import ExtractionError, extraction_json_schema
from app.providers.ollama import OllamaProvider
from app.providers.openai_compatible import OpenAICompatibleProvider

PAGES = ["Saffron Office Systems Pvt. Ltd.\nInvoice INV-1001\nTotal INR 11,800.00"]

GOOD_PAYLOAD = {
    "vendor_name": "Saffron Office Systems Pvt. Ltd.",
    "invoice_number": "INV-1001",
    "invoice_date": "2026-08-24",
    "currency": "INR",
    "gross_total": "11800.00",
    "document_kind": "invoice",
    "evidence": {
        "gross_total": {
            "page": 1,
            "excerpt": "Total INR 11,800.00",
            "provenance": "pdf_text",
        }
    },
}


def transport(handler) -> httpx.Client:
    """An httpx client whose requests never leave the process."""
    return httpx.Client(transport=httpx.MockTransport(handler))


def ollama_reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})


def openai_reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


# --- Ollama -----------------------------------------------------------------


def test_ollama_sends_the_schema_so_the_runtime_constrains_the_reply(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ollama_model", "qwen2.5:7b-instruct")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        seen["path"] = request.url.path
        return ollama_reply(json.dumps(GOOD_PAYLOAD))

    with transport(handler) as client:
        result = OllamaProvider(client).extract(PAGES, extraction_json_schema())

    assert seen["path"] == "/api/chat"
    assert seen["model"] == "qwen2.5:7b-instruct"
    assert seen["stream"] is False
    # Constraining the grammar is what stops most malformed replies existing.
    assert seen["format"]["properties"]["invoice_number"]
    # Temperature 0: the same document should not produce different numbers.
    assert seen["options"]["temperature"] == 0
    assert result.invoice.invoice_number == "INV-1001"
    assert result.metadata.provider == "ollama"
    assert result.metadata.repair_attempted is False


def test_ollama_repairs_one_malformed_reply_and_records_that_it_did(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ollama_model", "qwen2.5:7b-instruct")
    replies = ["not json at all", json.dumps(GOOD_PAYLOAD)]
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return ollama_reply(replies[len(sent) - 1])

    with transport(handler) as client:
        result = OllamaProvider(client).extract(PAGES, extraction_json_schema())

    assert len(sent) == 2
    # The repair turn carries the bad reply back, so the model can see its error.
    assert sent[1]["messages"][-2]["role"] == "assistant"
    assert result.metadata.repair_attempted is True
    assert result.invoice.gross_total == "11800.00"


def test_ollama_gives_up_rather_than_guessing_after_a_failed_repair(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ollama_model", "qwen2.5:7b-instruct")

    def handler(_request: httpx.Request) -> httpx.Response:
        return ollama_reply("still not json")

    with transport(handler) as client, pytest.raises(ExtractionError) as caught:
        OllamaProvider(client).extract(PAGES, extraction_json_schema())

    assert caught.value.code == "model_invalid_json"


def test_ollama_reports_a_timeout_as_a_timeout(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ollama_model", "qwen2.5:7b-instruct")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("too slow", request=request)

    with transport(handler) as client, pytest.raises(ExtractionError) as caught:
        OllamaProvider(client).extract(PAGES, extraction_json_schema())

    assert caught.value.code == "provider_timeout"


def test_ollama_names_the_pull_command_instead_of_downloading_a_model(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ollama_model", "llama3.2")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b-instruct"}]})

    with transport(handler) as client:
        availability = OllamaProvider(client).availability()

    assert availability.available is False
    assert availability.setup_command == "ollama pull llama3.2"


def test_ollama_that_is_not_running_is_reported_as_unreachable(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ollama_model", "qwen2.5:7b-instruct")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with transport(handler) as client:
        availability = OllamaProvider(client).availability()

    assert availability.available is False
    assert availability.setup_command == "ollama serve"


# --- OpenAI-compatible ------------------------------------------------------


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setattr(settings, "llm_base_url", "https://models.example/v1")
    monkeypatch.setattr(settings, "llm_model", "some-model")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")


def test_openai_adapter_asks_for_a_json_schema_first(configured) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        seen["auth"] = request.headers.get("authorization")
        seen["path"] = request.url.path
        return openai_reply(json.dumps(GOOD_PAYLOAD))

    with transport(handler) as client:
        result = OpenAICompatibleProvider(client).extract(PAGES, extraction_json_schema())

    assert seen["path"] == "/v1/chat/completions"
    assert seen["auth"] == "Bearer test-key"
    assert seen["response_format"]["type"] == "json_schema"
    assert seen["response_format"]["json_schema"]["strict"] is True
    assert result.invoice.vendor_name == "Saffron Office Systems Pvt. Ltd."
    assert result.metadata.notes == []


def test_openai_adapter_falls_back_to_json_object_and_says_so(configured) -> None:
    """Many 'OpenAI-compatible' servers do not implement json_schema."""
    modes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        modes.append(body["response_format"]["type"])
        if body["response_format"]["type"] == "json_schema":
            return httpx.Response(
                400, json={"error": {"message": "response_format json_schema is not supported"}}
            )
        return openai_reply(json.dumps(GOOD_PAYLOAD))

    with transport(handler) as client:
        result = OpenAICompatibleProvider(client).extract(PAGES, extraction_json_schema())

    assert modes == ["json_schema", "json_object"]
    # The downgrade is recorded on the run rather than hidden.
    assert any("json_object" in note for note in result.metadata.notes)
    assert result.invoice.invoice_number == "INV-1001"


def test_openai_adapter_distinguishes_the_errors_that_need_different_fixes(configured) -> None:
    cases = {
        401: "provider_unauthorized",
        403: "provider_unauthorized",
        429: "provider_rate_limited",
        500: "provider_error",
    }
    for status, expected_code in cases.items():

        def handler(_request: httpx.Request, status: int = status) -> httpx.Response:
            return httpx.Response(status, json={"error": "no"})

        with transport(handler) as client, pytest.raises(ExtractionError) as caught:
            OpenAICompatibleProvider(client).extract(PAGES, extraction_json_schema())
        assert caught.value.code == expected_code


def test_openai_adapter_refuses_to_run_when_it_is_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_base_url", "")
    monkeypatch.setattr(settings, "llm_model", "")
    monkeypatch.setattr(settings, "llm_api_key", "")

    with pytest.raises(ExtractionError) as caught:
        OpenAICompatibleProvider().extract(PAGES, extraction_json_schema())

    assert caught.value.code == "provider_not_configured"
    assert "LLM_BASE_URL" in caught.value.message


def test_a_model_reply_that_invents_a_field_cannot_smuggle_it_through(configured) -> None:
    """Extra keys are dropped by the schema, so a model cannot add a decision."""
    payload = {**GOOD_PAYLOAD, "decision": "APPROVED", "approved": True}

    def handler(_request: httpx.Request) -> httpx.Response:
        return openai_reply(json.dumps(payload))

    with transport(handler) as client:
        result = OpenAICompatibleProvider(client).extract(PAGES, extraction_json_schema())

    assert not hasattr(result.invoice, "decision")
    assert "APPROVED" not in result.invoice.model_dump_json()
