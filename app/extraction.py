import json
import re

import httpx
from pydantic import BaseModel, Field

from .validation import ExtractionData, LineItemData, source_has_line_items

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b-instruct"


class LineItem(BaseModel):
    description: str = ""
    amount: float = 0.0
    quantity: float | None = None
    unit_price: float | None = None


class RawExtraction(BaseModel):
    """Fields the model returns. Confidence is computed by validation, not the LLM."""

    vendor: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    total_amount: float | None = None
    currency: str | None = "USD"
    due_date: str | None = None
    subtotal: float | None = None
    tax_amount: float | None = None
    po_number: str | None = None
    payment_terms: str | None = None
    line_items: list[LineItem] = Field(default_factory=list)


PROMPT_TEMPLATE = """Extract invoice/receipt fields from the text below.
Return ONLY valid JSON with no markdown, no explanation. Schema:
{{
  "vendor": "string or null",
  "invoice_number": "string or null",
  "invoice_date": "YYYY-MM-DD or original date string or null",
  "total_amount": 0.0 or null,
  "currency": "USD",
  "due_date": "string or null",
  "subtotal": 0.0 or null,
  "tax_amount": 0.0 or null,
  "po_number": "string or null",
  "payment_terms": "string or null",
  "line_items": [{{"description": "string", "amount": 0.0, "quantity": null, "unit_price": null}}]
}}
Rules:
- Always try to fill vendor, invoice_number, invoice_date, total_amount, currency.
- Only include line_items if the document lists individual items; otherwise use [].
- Only fill due_date, subtotal, tax_amount, po_number, payment_terms if clearly present.
- Do not invent values. Use null when unknown.

TEXT:
{text}
"""


def _strip_json(raw: str) -> str:
    raw = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if fence:
        return fence.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        return raw[start : end + 1]
    return raw


def _call_ollama(raw_text: str) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": PROMPT_TEMPLATE.format(text=raw_text[:12000]),
        "stream": False,
        "options": {"temperature": 0.1},
    }
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(OLLAMA_URL, json=payload)
        resp.raise_for_status()
        return resp.json().get("response", "")


def extract_record(raw_text: str) -> ExtractionData:
    """Call LLM and return ExtractionData (validation applied separately)."""
    lines_required = source_has_line_items(raw_text or "")
    last_error: Exception | None = None
    for _ in range(2):
        try:
            response_text = _call_ollama(raw_text or "")
            data = json.loads(_strip_json(response_text))
            raw = RawExtraction.model_validate(data)
            items = [
                LineItemData(
                    description=li.description,
                    amount=li.amount,
                    quantity=li.quantity,
                    unit_price=li.unit_price,
                )
                for li in raw.line_items
            ]
            return ExtractionData(
                vendor=raw.vendor,
                invoice_number=raw.invoice_number,
                invoice_date=raw.invoice_date,
                total_amount=raw.total_amount,
                currency=raw.currency or "USD",
                due_date=raw.due_date,
                subtotal=raw.subtotal,
                tax_amount=raw.tax_amount,
                po_number=raw.po_number,
                payment_terms=raw.payment_terms,
                line_items=items,
                lines_required=lines_required,
            )
        except (json.JSONDecodeError, httpx.HTTPError, KeyError, ValueError) as e:
            last_error = e
    raise ValueError(f"Extraction failed after retry: {last_error}")
