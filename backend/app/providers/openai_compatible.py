"""One OpenAI-compatible chat-completions adapter.

Schema support varies between "OpenAI-compatible" servers, so the adapter tests
it rather than assuming: it asks for ``json_schema`` first and, only if the
server rejects that mode, retries with ``json_object`` and records the downgrade
in extraction metadata. Credentials stay server-side.
"""

from __future__ import annotations

import time

import httpx

from app.config import settings
from app.providers.base import (
    ExtractionError,
    ExtractionProvider,
    ProviderAvailability,
    extraction_schema_text,
)
from app.providers.model_common import (
    build_user_message,
    coerce_json,
    load_system_prompt,
    prompt_hash,
    repair_instruction,
    validate_invoice_payload,
)
from app.schemas.invoice import ExtractionMetadata, ExtractionResult

_SCHEMA_REJECTION_MARKERS = (
    "response_format",
    "json_schema",
    "unsupported",
    "not supported",
    "invalid_request_error",
)


class OpenAICompatibleProvider(ExtractionProvider):
    name = "openai_compatible"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client
        self._base_url = settings.llm_base_url.rstrip("/")
        self._model = settings.llm_model.strip()
        self._api_key = settings.llm_api_key

    def _http(self) -> httpx.Client:
        return self._client or httpx.Client(timeout=settings.llm_timeout_seconds)

    def availability(self) -> ProviderAvailability:
        missing = [
            name
            for name, value in (
                ("LLM_BASE_URL", self._base_url),
                ("LLM_MODEL", self._model),
                ("LLM_API_KEY", self._api_key),
            )
            if not value
        ]
        if missing:
            return ProviderAvailability(
                available=False,
                detail=f"Not configured: {', '.join(missing)} must be set server-side.",
                model=self._model or None,
            )
        return ProviderAvailability(
            available=True,
            detail=f"Configured for {self._base_url} with model {self._model}.",
            model=self._model,
        )

    def extract(self, page_texts: list[str], extraction_schema: dict) -> ExtractionResult:
        availability = self.availability()
        if not availability.available:
            raise ExtractionError("provider_not_configured", availability.detail)

        system_prompt = load_system_prompt()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": build_user_message(page_texts, extraction_schema_text())},
        ]
        started = time.perf_counter()
        client = self._http()
        notes: list[str] = []
        repair_attempted = False
        try:
            content, used_schema_mode = self._complete(client, messages, extraction_schema)
            if not used_schema_mode:
                notes.append(
                    "The endpoint rejected json_schema response_format; json_object mode was "
                    "used and the reply was validated with Pydantic instead."
                )
            try:
                invoice = validate_invoice_payload(coerce_json(content))
            except ExtractionError as first_error:
                repair_attempted = True
                messages = [
                    *messages,
                    {"role": "assistant", "content": content[:2000]},
                    {"role": "user", "content": repair_instruction(first_error.message)},
                ]
                content, _ = self._complete(
                    client, messages, extraction_schema, force_plain=not used_schema_mode
                )
                invoice = validate_invoice_payload(coerce_json(content))
        finally:
            if self._client is None:
                client.close()

        return ExtractionResult(
            invoice=invoice,
            metadata=ExtractionMetadata(
                provider=self.name,
                model=self._model,
                prompt_hash=prompt_hash(system_prompt),
                duration_ms=int((time.perf_counter() - started) * 1000),
                repair_attempted=repair_attempted,
                pages_total=len(page_texts),
                notes=notes,
            ),
        )

    def _complete(
        self,
        client: httpx.Client,
        messages: list[dict],
        schema: dict,
        *,
        force_plain: bool = False,
    ) -> tuple[str, bool]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        base_body = {
            "model": self._model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 2048,
        }
        attempts: list[tuple[dict, bool]] = []
        if not force_plain:
            attempts.append(
                (
                    {
                        **base_body,
                        "response_format": {
                            "type": "json_schema",
                            "json_schema": {
                                "name": "extracted_invoice",
                                "strict": True,
                                "schema": schema,
                            },
                        },
                    },
                    True,
                )
            )
        attempts.append(({**base_body, "response_format": {"type": "json_object"}}, False))

        last_error: str = "no attempt was made"
        for body, is_schema_mode in attempts:
            try:
                response = client.post(
                    f"{self._base_url}/chat/completions",
                    json=body,
                    headers=headers,
                    timeout=settings.llm_timeout_seconds,
                )
            except httpx.TimeoutException as exc:
                raise ExtractionError(
                    "provider_timeout",
                    f"The provider did not answer within {settings.llm_timeout_seconds}s.",
                ) from exc
            except httpx.HTTPError as exc:
                raise ExtractionError(
                    "provider_unreachable", "The configured provider could not be reached."
                ) from exc

            if response.status_code == 400 and is_schema_mode:
                detail = response.text[:400].lower()
                if any(marker in detail for marker in _SCHEMA_REJECTION_MARKERS):
                    last_error = "endpoint rejected json_schema response_format"
                    continue
            if response.status_code == 401 or response.status_code == 403:
                raise ExtractionError(
                    "provider_unauthorized", "The provider rejected the configured credentials."
                )
            if response.status_code == 429:
                raise ExtractionError("provider_rate_limited", "The provider rate limited us.")
            if response.status_code >= 400:
                raise ExtractionError(
                    "provider_error", f"The provider returned HTTP {response.status_code}."
                )
            payload = response.json()
            choices = payload.get("choices") or []
            if not choices:
                raise ExtractionError("model_invalid_json", "The provider returned no choices.")
            content = (choices[0].get("message") or {}).get("content") or ""
            if not content:
                raise ExtractionError("model_invalid_json", "The provider returned empty content.")
            return content, is_schema_mode

        raise ExtractionError("provider_error", f"Extraction failed: {last_error}.")
