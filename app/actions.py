import csv
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from .models import ActionLog, Document, ExtractedRecord

OUTPUT_DIR = Path("output")
TECHNICAL_PATH = OUTPUT_DIR / "financial_summary_technical.csv"
READABLE_PATH = OUTPUT_DIR / "summary_readable.csv"

TECHNICAL_HEADERS = [
    "record_id",
    "document_id",
    "original_filename",
    "file_type",
    "status",
    "vendor",
    "invoice_number",
    "invoice_date",
    "currency",
    "total_amount",
    "subtotal",
    "tax_amount",
    "due_date",
    "po_number",
    "payment_terms",
    "line_items_json",
    "confidence_score",
    "validation_notes",
    "created_at",
    "extracted_at",
    "approved_at",
    "committed_at",
]

READABLE_HEADERS = [
    "File",
    "Type",
    "Vendor",
    "Invoice #",
    "Date",
    "Amount",
    "Status",
    "Confidence",
    "Notes",
    "Approved",
    "Posted",
    "Ref",
]


def _parse_notes(record: ExtractedRecord) -> list[str]:
    if not record.validation_notes:
        return []
    try:
        data = json.loads(record.validation_notes)
        return data if isinstance(data, list) else [str(data)]
    except json.JSONDecodeError:
        return [record.validation_notes]


def _ts(dt: datetime | None) -> str:
    return dt.isoformat() if dt else ""


def _money(amount: float | None, currency: str | None) -> str:
    cur = (currency or "USD").upper()
    if amount is None:
        return ""
    return f"{cur} {amount:,.2f}"


def _doc_meta(db: Session, record: ExtractedRecord) -> dict:
    doc = db.query(Document).filter(Document.id == record.document_id).first()
    filename = ""
    file_type = "pdf"
    status = ""
    if doc:
        filename = doc.original_filename or Path(doc.file_path).name
        if not doc.original_filename:
            name = Path(doc.file_path).name
            if "_" in name:
                filename = name.split("_", 1)[1]
        file_type = doc.file_type or "pdf"
        status = doc.status or ""
    return {"original_filename": filename, "file_type": file_type, "status": status}


def _append_csv(path: Path, headers: list[str], row: dict) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def write_exports(db: Session, record: ExtractedRecord) -> None:
    meta = _doc_meta(db, record)
    notes = _parse_notes(record)
    notes_str = "; ".join(notes)

    tech = {
        "record_id": record.id,
        "document_id": record.document_id,
        "original_filename": meta["original_filename"],
        "file_type": meta["file_type"],
        "status": "committed",
        "vendor": record.vendor or "",
        "invoice_number": record.invoice_number or "",
        "invoice_date": record.invoice_date or "",
        "currency": record.currency or "USD",
        "total_amount": f"{record.total_amount:.2f}" if record.total_amount is not None else "",
        "subtotal": f"{record.subtotal:.2f}" if record.subtotal is not None else "",
        "tax_amount": f"{record.tax_amount:.2f}" if record.tax_amount is not None else "",
        "due_date": record.due_date or "",
        "po_number": record.po_number or "",
        "payment_terms": record.payment_terms or "",
        "line_items_json": record.line_items or "[]",
        "confidence_score": record.confidence_score if record.confidence_score is not None else "",
        "validation_notes": notes_str,
        "created_at": _ts(record.created_at),
        "extracted_at": _ts(record.extracted_at),
        "approved_at": _ts(record.approved_at),
        "committed_at": _ts(record.committed_at),
    }
    _append_csv(TECHNICAL_PATH, TECHNICAL_HEADERS, tech)

    conf = record.confidence_score
    if conf is None:
        conf_label = "—"
    elif conf >= 0.95:
        conf_label = f"High ({conf:.2f})"
    elif conf >= 0.75:
        conf_label = f"Medium ({conf:.2f})"
    else:
        conf_label = f"Low ({conf:.2f})"

    readable = {
        "File": meta["original_filename"],
        "Type": meta["file_type"],
        "Vendor": record.vendor or "",
        "Invoice #": record.invoice_number or "",
        "Date": record.invoice_date or "",
        "Amount": _money(record.total_amount, record.currency),
        "Status": "committed",
        "Confidence": conf_label,
        "Notes": notes_str or "OK",
        "Approved": "Yes" if record.approved_at else "No",
        "Posted": "Yes" if record.committed_at else "No",
        "Ref": record.id,
    }
    _append_csv(READABLE_PATH, READABLE_HEADERS, readable)


def record_to_dict(record: ExtractedRecord, document: Document | None = None) -> dict:
    try:
        line_items = json.loads(record.line_items) if record.line_items else []
    except json.JSONDecodeError:
        line_items = []
    notes = _parse_notes(record)
    out = {
        "id": record.id,
        "document_id": record.document_id,
        "vendor": record.vendor,
        "invoice_number": record.invoice_number,
        "invoice_date": record.invoice_date,
        "date": record.invoice_date,  # legacy alias for UI
        "total_amount": record.total_amount,
        "currency": record.currency or "USD",
        "due_date": record.due_date,
        "subtotal": record.subtotal,
        "tax_amount": record.tax_amount,
        "po_number": record.po_number,
        "payment_terms": record.payment_terms,
        "line_items": line_items,
        "lines_required": bool(record.lines_required),
        "confidence_score": record.confidence_score,
        "validation_notes": notes,
        "created_at": _ts(record.created_at) or None,
        "extracted_at": _ts(record.extracted_at) or None,
        "approved_at": _ts(record.approved_at) or None,
        "committed_at": _ts(record.committed_at) or None,
    }
    if document:
        name = document.original_filename
        if not name:
            name = Path(document.file_path).name
            if "_" in name:
                name = name.split("_", 1)[1]
        out["original_filename"] = name
        out["file_type"] = document.file_type or "pdf"
        out["document_status"] = document.status
    return out


def apply_normalized(record: ExtractedRecord, normalized: dict, notes: list[str], score: float) -> None:
    record.vendor = normalized.get("vendor")
    record.invoice_number = normalized.get("invoice_number")
    record.invoice_date = normalized.get("invoice_date")
    record.total_amount = normalized.get("total_amount")
    record.currency = normalized.get("currency") or "USD"
    record.due_date = normalized.get("due_date")
    record.subtotal = normalized.get("subtotal")
    record.tax_amount = normalized.get("tax_amount")
    record.po_number = normalized.get("po_number")
    record.payment_terms = normalized.get("payment_terms")
    record.line_items = json.dumps(normalized.get("line_items") or [])
    record.lines_required = int(normalized.get("lines_required") or 0)
    record.confidence_score = score
    record.validation_notes = json.dumps(notes)


def commit_record(db: Session, record: ExtractedRecord) -> ActionLog:
    if record.committed_at:
        log = ActionLog(
            record_id=record.id,
            action_type="export_summaries",
            status="skipped: already committed",
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        return log

    try:
        now = datetime.utcnow()
        if not record.approved_at:
            record.approved_at = now
        record.committed_at = now
        write_exports(db, record)
        log = ActionLog(
            record_id=record.id,
            action_type="export_summaries",
            status="success",
        )
    except Exception as e:
        record.committed_at = None
        log = ActionLog(
            record_id=record.id,
            action_type="export_summaries",
            status=f"failed: {e}",
        )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log
