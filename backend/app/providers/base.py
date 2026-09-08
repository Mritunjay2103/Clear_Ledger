"""The extraction provider contract.

A provider turns page text into typed invoice facts. It never decides anything:
approval, matching and money are the exclusive job of the deterministic domain
layer, so a wrong or hostile model output can only cause a review, never a
payment commitment.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from app.schemas.invoice import ExtractedInvoice, ExtractionResult

PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "invoice_extraction.md"


class ExtractionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ProviderAvailability:
    available: bool
    detail: str
    model: str | None = None
    setup_command: str | None = None


def extraction_json_schema() -> dict:
    """JSON Schema handed to schema-constrained model backends."""
    schema = ExtractedInvoice.model_json_schema()
    schema["additionalProperties"] = False
    return schema


def extraction_schema_text() -> str:
    return json.dumps(extraction_json_schema(), indent=2, sort_keys=True)


class ExtractionProvider(ABC):
    """extract(page_texts, extraction_schema) -> typed result + metadata."""

    name: str = "base"

    @abstractmethod
    def availability(self) -> ProviderAvailability:
        """Report whether this provider can actually run right now."""

    @abstractmethod
    def extract(self, page_texts: list[str], extraction_schema: dict) -> ExtractionResult:
        """Produce invoice facts from page text."""
