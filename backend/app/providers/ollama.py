"""Local Ollama extraction provider.

Uses Ollama's schema-constrained ``format`` field so the runtime enforces the
shape of the reply, then validates it again with Pydantic because a constrained
grammar guarantees structure, not truthfulness.

Nothing is downloaded automatically: if the configured model is absent the
provider reports the exact ``ollama pull`` command instead of fetching gigabytes.
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


class OllamaProvider(ExtractionProvider):
    name = "ollama"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client
        self._base_url = settings.ollama_base_url.rstrip("/")
        self._model = settings.ollama_model.strip()

    def _http(self) -> httpx.Client:
        return self._client or httpx.Client(timeout=settings.llm_timeout_seconds)

    def list_models(self) -> list[str]:
        client = self._http()
        try:
            response = client.get(f"{self._base_url}/api/tags", timeout=5.0)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise ExtractionError(
                "provider_unreachable", f"Ollama at {self._base_url} did not respond."
            ) from exc
        finally:
            if self._client is None:
                client.close()
        return [entry.get("name", "") for entry in payload.get("models", []) if entry.get("name")]

    def availability(self) -> ProviderAvailability:
        if not self._model:
            return ProviderAvailability(
                available=False,
                detail="OLLAMA_MODEL is not set. Choose a model already installed locally.",
                setup_command="ollama list",
            )
        try:
            installed = self.list_models()
        except ExtractionError as exc:
            return ProviderAvailability(
                available=False,
                detail=f"{exc.message} Start it with `ollama serve`.",
                model=self._model,
                setup_command="ollama serve",
            )
        # Ollama reports tags as ``name:tag``; treat a bare name as ``name:latest``.
        wanted = self._model if ":" in self._model else f"{self._model}:latest"
        if wanted not in installed and self._model not in installed:
            return ProviderAvailability(
                available=False,
                detail=(
                    f"Model {self._model!r} is not installed. Installed models: "
                    f"{', '.join(installed) or 'none'}."
                ),
                model=self._model,
                setup_command=f"ollama pull {self._model}",
            )
        return ProviderAvailability(
            available=True,
            detail=f"Ollama is reachable at {self._base_url} with model {self._model}.",
            model=self._model,
        )

    def extract(self, page_texts: list[str], extraction_schema: dict) -> ExtractionResult:
        if not self._model:
            raise ExtractionError("provider_not_configured", "OLLAMA_MODEL is not set.")

        system_prompt = load_system_prompt()
        user_message = build_user_message(page_texts, extraction_schema_text())
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        started = time.perf_counter()
        client = self._http()
        repair_attempted = False
        try:
            content = self._chat(client, messages, extraction_schema)
            try:
                invoice = validate_invoice_payload(coerce_json(content))
            except ExtractionError as first_error:
                repair_attempted = True
                messages = [
                    *messages,
                    {"role": "assistant", "content": content[:2000]},
                    {"role": "user", "content": repair_instruction(first_error.message)},
                ]
                content = self._chat(client, messages, extraction_schema)
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
            ),
        )

    def _chat(self, client: httpx.Client, messages: list[dict], schema: dict) -> str:
        body = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "format": schema,
            "options": {"temperature": 0, "num_predict": 2048},
        }
        try:
            response = client.post(
                f"{self._base_url}/api/chat", json=body, timeout=settings.llm_timeout_seconds
            )
        except httpx.TimeoutException as exc:
            raise ExtractionError(
                "provider_timeout",
                f"Ollama did not answer within {settings.llm_timeout_seconds}s.",
            ) from exc
        except httpx.HTTPError as exc:
            raise ExtractionError(
                "provider_unreachable", f"Ollama at {self._base_url} could not be reached."
            ) from exc
        if response.status_code >= 400:
            raise ExtractionError("provider_error", f"Ollama returned HTTP {response.status_code}.")
        payload = response.json()
        content = (payload.get("message") or {}).get("content", "")
        if not content:
            raise ExtractionError("model_invalid_json", "Ollama returned an empty message.")
        return content
