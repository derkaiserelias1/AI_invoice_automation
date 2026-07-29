"""Rule-based validation and confidence for extracted invoice fields."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%m-%d-%Y",
    "%d-%m-%Y",
    "%Y/%m/%d",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d %b %Y",
    "%d %B %Y",
)

LINE_HINTS = re.compile(
    r"(line\s*items?|description\s+amount|qty\s+.*price|quantity\s+.*total|"
    r"item\s+description|unit\s*price|\bqty\b)",
    re.I,
)


@dataclass
class LineItemData:
    description: str = ""
    amount: float | None = None
    quantity: float | None = None
    unit_price: float | None = None


@dataclass
class ExtractionData:
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
    line_items: list[LineItemData] = field(default_factory=list)
    lines_required: bool = False


@dataclass
class ValidationResult:
    main_complete: bool
    confidence_score: float
    validation_notes: list[str]
    status: str  # ready | needs_review
    normalized: dict[str, Any]


def source_has_line_items(raw_text: str) -> bool:
    if not raw_text:
        return False
    if LINE_HINTS.search(raw_text):
        return True
    # Multiple amount-like rows often indicate a line table
    money_lines = re.findall(r"^.*\$?\d+[.,]\d{2}.*$", raw_text, re.M)
    return len(money_lines) >= 4


def parse_date(value: str | None) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # Already ISO
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Try dateutil-free loose: YYYY-MM-DD substring
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def _nonempty(s: str | None) -> bool:
    return bool(s and str(s).strip())


def _as_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def validate_and_score(data: ExtractionData) -> ValidationResult:
    notes: list[str] = []
    currency = (data.currency or "USD").strip().upper() or "USD"
    invoice_date = parse_date(data.invoice_date)
    due_date = parse_date(data.due_date)
    total = _as_float(data.total_amount)
    subtotal = _as_float(data.subtotal)
    tax = _as_float(data.tax_amount)

    clean_lines: list[dict[str, Any]] = []
    for li in data.line_items or []:
        amt = _as_float(getattr(li, "amount", None) if not isinstance(li, dict) else li.get("amount"))
        desc = (
            getattr(li, "description", "")
            if not isinstance(li, dict)
            else (li.get("description") or "")
        )
        if amt is None and not str(desc).strip():
            continue
        item: dict[str, Any] = {
            "description": str(desc).strip(),
            "amount": amt if amt is not None else 0.0,
        }
        qty = _as_float(getattr(li, "quantity", None) if not isinstance(li, dict) else li.get("quantity"))
        up = _as_float(
            getattr(li, "unit_price", None) if not isinstance(li, dict) else li.get("unit_price")
        )
        if qty is not None:
            item["quantity"] = qty
        if up is not None:
            item["unit_price"] = up
        clean_lines.append(item)

    # --- main checks ---
    main_ok = True
    if not _nonempty(data.vendor):
        notes.append("missing vendor")
        main_ok = False
    if not _nonempty(data.invoice_number):
        notes.append("missing invoice_number")
        main_ok = False
    if not invoice_date:
        notes.append("missing or invalid invoice_date")
        main_ok = False
    if total is None:
        notes.append("missing total_amount")
        main_ok = False
    elif total < 0:
        notes.append("total_amount is negative")
        main_ok = False
    if not _nonempty(currency):
        notes.append("missing currency")
        main_ok = False

    lines_ok = True
    if data.lines_required:
        if not clean_lines:
            notes.append("source has line items but none extracted")
            lines_ok = False
            main_ok = False
        elif total is not None:
            line_sum = sum(float(x["amount"]) for x in clean_lines)
            if abs(line_sum - total) > 0.05:
                # Allow total = subtotal + tax if tax present
                if tax is not None and abs(line_sum + tax - total) <= 0.05:
                    pass
                else:
                    notes.append(
                        f"line items sum {line_sum:.2f} does not match total {total:.2f}"
                    )
                    lines_ok = False
                    main_ok = False

    # --- confidence ---
    if not main_ok:
        score = 0.35
        if _nonempty(data.vendor):
            score += 0.05
        if invoice_date:
            score += 0.05
        if total is not None:
            score += 0.05
        score = min(score, 0.5)
        status = "needs_review"
    else:
        score = 0.85
        if data.lines_required and lines_ok:
            score = 0.95
        status = "ready"

        # Bonus bumps only when present and valid
        if due_date:
            score = min(1.0, score + 0.02)
        if subtotal is not None:
            score = min(1.0, score + 0.02)
        if tax is not None:
            score = min(1.0, score + 0.02)
        if _nonempty(data.po_number):
            score = min(1.0, score + 0.02)
        if _nonempty(data.payment_terms):
            score = min(1.0, score + 0.02)
        if clean_lines and not data.lines_required:
            score = min(1.0, score + 0.02)
        if main_ok and (not data.lines_required or lines_ok) and score >= 0.95:
            # Full main + lines = perfect floor already applied
            pass

    if due_date is None and _nonempty(data.due_date):
        notes.append("due_date present but unparseable (ignored for boost)")

    normalized = {
        "vendor": (data.vendor or "").strip() or None,
        "invoice_number": (data.invoice_number or "").strip() or None,
        "invoice_date": invoice_date,
        "total_amount": total,
        "currency": currency,
        "due_date": due_date,
        "subtotal": subtotal,
        "tax_amount": tax,
        "po_number": (data.po_number or "").strip() or None,
        "payment_terms": (data.payment_terms or "").strip() or None,
        "line_items": clean_lines,
        "lines_required": 1 if data.lines_required else 0,
    }

    return ValidationResult(
        main_complete=main_ok,
        confidence_score=round(score, 3),
        validation_notes=notes,
        status=status,
        normalized=normalized,
    )


def extraction_from_dict(payload: dict[str, Any], lines_required: bool) -> ExtractionData:
    items = []
    for li in payload.get("line_items") or []:
        if isinstance(li, dict):
            items.append(
                LineItemData(
                    description=str(li.get("description") or ""),
                    amount=_as_float(li.get("amount")),
                    quantity=_as_float(li.get("quantity")),
                    unit_price=_as_float(li.get("unit_price")),
                )
            )
    # Accept legacy "date" key
    inv_date = payload.get("invoice_date") or payload.get("date")
    return ExtractionData(
        vendor=payload.get("vendor"),
        invoice_number=payload.get("invoice_number"),
        invoice_date=str(inv_date) if inv_date is not None else None,
        total_amount=_as_float(payload.get("total_amount")),
        currency=payload.get("currency") or "USD",
        due_date=str(payload["due_date"]) if payload.get("due_date") else None,
        subtotal=_as_float(payload.get("subtotal")),
        tax_amount=_as_float(payload.get("tax_amount")),
        po_number=payload.get("po_number"),
        payment_terms=payload.get("payment_terms"),
        line_items=items,
        lines_required=lines_required,
    )
