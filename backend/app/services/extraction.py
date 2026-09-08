"""Document reading, provider selection and evidence verification.

Evidence verification is the hinge between "a model said so" and "the document
says so". Every quoted excerpt is checked against the real page text, and the
claimed value must be derivable from the excerpt it cites. A field that fails
either check is marked uncertain, and an uncertain critical field cannot be
approved automatically.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from app.config import settings
from app.domain.money import MoneyError, parse_decimal
from app.providers.base import (
    ExtractionError,
    ExtractionProvider,
    ProviderAvailability,
    extraction_json_schema,
)
from app.providers.ollama import OllamaProvider
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.rules import RulesProvider
from app.schemas.invoice import (
    CRITICAL_FIELDS,
    ExtractedInvoice,
    ExtractionResult,
    FieldQuality,
)
from app.services import ocr as ocr_service
from app.services.ocr import OcrError, OcrStatus
from app.services.pdf_text import DocumentText, extract_text, normalize_for_evidence

EVIDENCE_FIELDS: tuple[str, ...] = (*CRITICAL_FIELDS, "explicit_po_number")
_AMOUNT_TOKEN_RE = re.compile(r"-?[\d,]+(?:\.\d{1,2})?")


@dataclass
class ReadDocument:
    page_texts: list[str] = field(default_factory=list)
    page_provenance: list[str] = field(default_factory=list)
    ocr_pages: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ocr_attempted: bool = False
    ocr_engine: str | None = None
    readable: bool = True

    def provenance_for_page(self, page_number: int) -> str:
        if 1 <= page_number <= len(self.page_provenance):
            return self.page_provenance[page_number - 1]
        return "pdf_text"

    @property
    def total_characters(self) -> int:
        return sum(len(text.strip()) for text in self.page_texts)


def build_provider(name: str) -> ExtractionProvider:
    if name == "rules":
        return RulesProvider()
    if name == "ollama":
        return OllamaProvider()
    if name == "openai_compatible":
        return OpenAICompatibleProvider()
    raise ExtractionError("provider_not_configured", f"Unknown extraction provider {name!r}.")


def provider_availability(name: str) -> ProviderAvailability:
    try:
        return build_provider(name).availability()
    except ExtractionError as exc:
        return ProviderAvailability(available=False, detail=exc.message)


def read_document(path: Path, document_text: DocumentText | None = None) -> ReadDocument:
    """Read every page, invoking OCR only on pages whose text layer is unusable."""
    parsed = document_text or extract_text(path)
    result = ReadDocument()
    ocr_status: OcrStatus | None = None

    for page in parsed.pages:
        if not page.needs_ocr:
            result.page_texts.append(page.text)
            result.page_provenance.append("pdf_text")
            continue

        result.ocr_attempted = True
        if ocr_status is None:
            ocr_status = ocr_service.probe()
            result.ocr_engine = ocr_status.version if ocr_status.available else None
        if not ocr_status.available:
            result.page_texts.append(page.text)
            result.page_provenance.append("pdf_text")
            result.warnings.append(
                f"Page {page.page_number} has no usable text layer and OCR is unavailable. "
                + ocr_status.detail
            )
            continue
        try:
            text = ocr_service.ocr_page(path, page.page_number)
        except OcrError as exc:
            result.page_texts.append(page.text)
            result.page_provenance.append("pdf_text")
            result.warnings.append(f"OCR failed on page {page.page_number}: {exc.message}")
            continue
        result.page_texts.append(text)
        result.page_provenance.append("ocr")
        result.ocr_pages.append(page.page_number)

    result.readable = result.total_characters > 0
    return result


def run_extraction(document: ReadDocument, provider_name: str | None = None) -> ExtractionResult:
    """Run the configured provider, honouring the explicit fallback setting.

    A provider failure is never silently converted into a different provider's
    output. Fallback happens only when ALLOW_RULES_FALLBACK is on, and the
    original failure is recorded on the run.
    """
    name = provider_name or settings.extraction_provider
    provider = build_provider(name)
    schema = extraction_json_schema()
    try:
        result = provider.extract(document.page_texts, schema)
    except ExtractionError as exc:
        if name == "rules" or not settings.allow_rules_fallback:
            raise
        fallback = RulesProvider()
        result = fallback.extract(document.page_texts, schema)
        result.metadata.fallback_from = name
        result.metadata.fallback_reason = f"{exc.code}: {exc.message}"
        result.invoice.extraction_warnings.append(
            f"Primary provider {name!r} failed ({exc.code}); the deterministic parser produced "
            "these facts instead. Model-only fields may be missing."
        )

    result.metadata.pages_total = len(document.page_texts)
    result.metadata.pages_ocr = list(document.ocr_pages)
    result.metadata.ocr_engine = document.ocr_engine
    _stamp_provenance(result.invoice, document)
    return result


def _stamp_provenance(invoice: ExtractedInvoice, document: ReadDocument) -> None:
    """Label evidence with how its page was read, unless a human supplied it."""
    for evidence in invoice.evidence.values():
        if evidence.provenance == "human_correction":
            continue
        evidence.provenance = document.provenance_for_page(evidence.page)


def _numeric_forms(value: str) -> set[str]:
    """String forms an amount may legitimately take in the printed document."""
    forms = {value.strip()}
    try:
        number = parse_decimal(value)
    except MoneyError:
        return forms
    forms.add(str(number))
    forms.add(str(number.normalize()))
    quantised = number.quantize(Decimal("0.01"))
    forms.add(str(quantised))
    whole, _, fraction = str(quantised).partition(".")
    with contextlib.suppress(ValueError):
        forms.add(f"{int(whole):,}.{fraction}")
    if quantised == quantised.to_integral_value():
        forms.add(str(quantised.to_integral_value()))
    return {form for form in forms if form}


def verify_evidence(
    invoice: ExtractedInvoice, document: ReadDocument
) -> tuple[list[FieldQuality], list[str]]:
    """Check every cited excerpt against the document; label each critical field."""
    normalized_pages = [normalize_for_evidence(text) for text in document.page_texts]
    whole_document = " ".join(normalized_pages)
    qualities: list[FieldQuality] = []
    warnings: list[str] = []

    for field_name in EVIDENCE_FIELDS:
        raw_value = getattr(invoice, field_name, None)
        if raw_value in (None, ""):
            qualities.append(
                FieldQuality(
                    field=field_name, label="missing", reason="Not present in the document."
                )
            )
            continue

        value = str(raw_value)
        evidence = invoice.evidence.get(field_name)
        if evidence is None:
            qualities.append(
                FieldQuality(
                    field=field_name,
                    label="uncertain",
                    reason="No source excerpt was cited for this value.",
                )
            )
            warnings.append(f"{field_name}: value supplied without a source excerpt.")
            continue

        if evidence.provenance == "human_correction":
            qualities.append(
                FieldQuality(
                    field=field_name,
                    label="human_supplied",
                    reason=evidence.excerpt,
                    evidence=evidence,
                )
            )
            continue

        normalized_excerpt = normalize_for_evidence(evidence.excerpt)
        page_index = evidence.page - 1
        page_text = normalized_pages[page_index] if 0 <= page_index < len(normalized_pages) else ""

        if not normalized_excerpt or normalized_excerpt not in page_text:
            if normalized_excerpt and normalized_excerpt in whole_document:
                qualities.append(
                    FieldQuality(
                        field=field_name,
                        label="uncertain",
                        reason=(
                            f"The cited excerpt exists but on a different page than the claimed "
                            f"page {evidence.page}."
                        ),
                        evidence=evidence,
                    )
                )
                warnings.append(f"{field_name}: excerpt cited against the wrong page.")
            else:
                qualities.append(
                    FieldQuality(
                        field=field_name,
                        label="uncertain",
                        reason="The cited excerpt does not appear in the document text.",
                        evidence=evidence,
                    )
                )
                warnings.append(
                    f"{field_name}: cited excerpt was not found in the document; the value is "
                    "not supported by evidence."
                )
            continue

        if not _value_supported(field_name, value, normalized_excerpt):
            qualities.append(
                FieldQuality(
                    field=field_name,
                    label="uncertain",
                    reason=(f"The value {value!r} cannot be derived from the cited excerpt."),
                    evidence=evidence,
                )
            )
            warnings.append(f"{field_name}: cited excerpt does not contain the claimed value.")
            continue

        conflict = _conflicting_amount(field_name, value, normalized_excerpt)
        if conflict:
            qualities.append(
                FieldQuality(
                    field=field_name, label="uncertain", reason=conflict, evidence=evidence
                )
            )
            warnings.append(f"{field_name}: {conflict}")
            continue

        qualities.append(FieldQuality(field=field_name, label="verified", evidence=evidence))

    return qualities, warnings


def _value_supported(field_name: str, value: str, normalized_excerpt: str) -> bool:
    if field_name in {"subtotal", "tax_total", "gross_total"}:
        return any(form.casefold() in normalized_excerpt for form in _numeric_forms(value))
    lowered = value.strip().casefold()
    if lowered in normalized_excerpt:
        return True
    # Identifiers survive presentation differences in separators.
    squashed_value = re.sub(r"[\s\-_./]", "", lowered)
    squashed_excerpt = re.sub(r"[\s\-_./]", "", normalized_excerpt)
    if squashed_value and squashed_value in squashed_excerpt:
        return True
    if field_name in {"vendor_name", "invoice_date"}:
        tokens = [token for token in re.split(r"\W+", lowered) if len(token) > 2]
        if tokens and all(token in normalized_excerpt for token in tokens):
            return True
    return False


def _conflicting_amount(field_name: str, value: str, normalized_excerpt: str) -> str | None:
    """Flag an excerpt that carries several amounts, only one of which was used."""
    if field_name not in {"subtotal", "tax_total", "gross_total"}:
        return None
    candidates = {token.replace(",", "") for token in _AMOUNT_TOKEN_RE.findall(normalized_excerpt)}
    try:
        claimed = parse_decimal(value)
    except MoneyError:
        return "The value is not a valid decimal amount."
    distinct: set[Decimal] = set()
    for token in candidates:
        try:
            parsed = parse_decimal(token)
        except MoneyError:
            continue
        distinct.add(parsed)
    conflicting = {number for number in distinct if number != claimed}
    if len(conflicting) >= 2:
        return (
            "The cited excerpt contains several different amounts, so it does not "
            "identify this value unambiguously."
        )
    return None
