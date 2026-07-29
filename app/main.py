import json
import os
import shutil
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .actions import apply_normalized, commit_record, record_to_dict
from .database import Base, engine, get_db
from .extraction import extract_record
from .models import Document, ExtractedRecord
from .schema_migrate import ensure_schema
from .text_extract import (
    ALLOWED_EXTENSIONS,
    extract_text_from_file,
    file_type_from_name,
    is_allowed_filename,
)
from .validation import extraction_from_dict, source_has_line_items, validate_and_score

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
Path("output").mkdir(parents=True, exist_ok=True)

app = FastAPI(title="doc-automation")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    ensure_schema()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/system/status")
def system_status():
    """LLM + OCR status for Settings panel."""
    from .extraction import OLLAMA_MODEL, OLLAMA_URL
    from .text_extract import ALLOWED_EXTENSIONS

    ollama_ok = False
    ollama_error = None
    try:
        tags_url = OLLAMA_URL.replace("/api/generate", "/api/tags")
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(tags_url)
            ollama_ok = resp.status_code == 200
            if not ollama_ok:
                ollama_error = f"HTTP {resp.status_code}"
    except Exception as e:
        ollama_error = str(e)

    return {
        "app_name": "Clearpost",
        "llm": {
            "provider": "Ollama",
            "model": OLLAMA_MODEL,
            "url": OLLAMA_URL,
            "online": ollama_ok,
            "status": "online" if ollama_ok else "offline",
            "error": ollama_error,
        },
        "ocr": {
            "engine": "EasyOCR",
            "license": "Apache-2.0",
            "notes": "Images and scanned PDFs; digital PDFs use embedded text first.",
        },
        "allowed_extensions": sorted(ALLOWED_EXTENSIONS.keys()),
    }


# MIME for inline preview (FileResponse with filename= defaults to attachment → browser download)
_FILE_MEDIA_TYPES = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "tiff": "image/tiff",
    "bmp": "image/bmp",
    "txt": "text/plain",
    "csv": "text/csv",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@app.get("/documents/{document_id}/file")
def get_document_file(document_id: int, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    path = Path(doc.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="File missing on disk")
    name = doc.original_filename or path.name
    ft = (doc.file_type or Path(name).suffix.lstrip(".")).lower()
    media_type = _FILE_MEDIA_TYPES.get(ft)
    # inline so PDF/image preview in the UI does NOT trigger a download
    return FileResponse(
        path,
        filename=name,
        media_type=media_type,
        content_disposition_type="inline",
    )


def _original_filename(file_path: str, stored: str | None = None) -> str:
    if stored:
        return stored
    name = Path(file_path).name
    if "_" in name:
        return name.split("_", 1)[1]
    return name


def extract_text_job(document_id: int):
    from .database import SessionLocal

    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            return
        try:
            doc.raw_text = extract_text_from_file(doc.file_path, doc.file_type)
            doc.status = "processing"
            db.commit()
        except Exception:
            doc.status = "failed"
            db.commit()
    finally:
        db.close()


def _ensure_document_text(doc: Document, db: Session) -> None:
    if doc.raw_text:
        return
    try:
        doc.raw_text = extract_text_from_file(doc.file_path, doc.file_type)
        doc.status = "processing"
        db.commit()
    except Exception as e:
        doc.status = "failed"
        db.commit()
        raise HTTPException(status_code=500, detail=f"Text extraction failed: {e}")


@app.get("/files/allowed")
def allowed_files():
    return {
        "extensions": sorted(ALLOWED_EXTENSIONS.keys()),
        "types": sorted(set(ALLOWED_EXTENSIONS.values())),
        "ocr": "easyocr",
        "notes": "Images and scanned PDFs use EasyOCR; digital PDFs use embedded text first.",
    }


@app.post("/documents/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not file.filename or not is_allowed_filename(file.filename):
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS.keys()))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Allowed: {allowed}",
        )

    safe_name = Path(file.filename).name
    ftype = file_type_from_name(safe_name) or "pdf"
    dest = UPLOAD_DIR / f"{os.urandom(8).hex()}_{safe_name}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    doc = Document(
        file_path=str(dest),
        original_filename=safe_name,
        file_type=ftype,
        status="pending",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    background_tasks.add_task(extract_text_job, doc.id)

    return {
        "id": doc.id,
        "filename": safe_name,
        "file_type": doc.file_type,
        "status": doc.status,
        "file_path": doc.file_path,
    }


@app.get("/documents")
def list_documents(db: Session = Depends(get_db)):
    docs = db.query(Document).order_by(Document.id.desc()).all()
    result = []
    for doc in docs:
        filename = _original_filename(doc.file_path, doc.original_filename)
        rec = (
            db.query(ExtractedRecord)
            .filter(ExtractedRecord.document_id == doc.id)
            .order_by(ExtractedRecord.id.desc())
            .first()
        )
        item = {
            "id": doc.id,
            "filename": filename,
            "file_type": doc.file_type or "pdf",
            "status": doc.status,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
        }
        if rec:
            item["confidence_score"] = rec.confidence_score
            item["validation_notes"] = record_to_dict(rec).get("validation_notes")
            item["record"] = record_to_dict(rec, doc)
        result.append(item)
    return result


@app.post("/documents/{document_id}/extract")
def run_extract(document_id: int, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    _ensure_document_text(doc, db)

    if not (doc.raw_text or "").strip():
        doc.status = "failed"
        db.commit()
        raise HTTPException(
            status_code=400,
            detail="No text could be extracted from this file (empty OCR/PDF text)",
        )

    try:
        extracted = extract_record(doc.raw_text or "")
    except Exception as e:
        doc.status = "failed"
        db.commit()
        raise HTTPException(status_code=500, detail=str(e))

    result = validate_and_score(extracted)
    now = datetime.utcnow()
    record = ExtractedRecord(document_id=doc.id, extracted_at=now)
    apply_normalized(
        record,
        result.normalized,
        result.validation_notes,
        result.confidence_score,
    )
    record.extracted_at = now
    db.add(record)
    doc.status = result.status  # ready | needs_review
    db.commit()
    db.refresh(record)
    return record_to_dict(record, doc)


class RecordUpdate(BaseModel):
    vendor: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    date: str | None = None  # legacy
    total_amount: float | None = None
    currency: str | None = None
    due_date: str | None = None
    subtotal: float | None = None
    tax_amount: float | None = None
    po_number: str | None = None
    payment_terms: str | None = None
    line_items: list | None = None


def _revalidate_record(record: ExtractedRecord, doc: Document | None, force_status: bool = False) -> None:
    payload = {
        "vendor": record.vendor,
        "invoice_number": record.invoice_number,
        "invoice_date": record.invoice_date,
        "total_amount": record.total_amount,
        "currency": record.currency,
        "due_date": record.due_date,
        "subtotal": record.subtotal,
        "tax_amount": record.tax_amount,
        "po_number": record.po_number,
        "payment_terms": record.payment_terms,
    }
    try:
        payload["line_items"] = json.loads(record.line_items) if record.line_items else []
    except json.JSONDecodeError:
        payload["line_items"] = []

    lines_required = bool(record.lines_required)
    if doc and doc.raw_text:
        lines_required = source_has_line_items(doc.raw_text)

    data = extraction_from_dict(payload, lines_required=lines_required)
    result = validate_and_score(data)
    apply_normalized(
        record,
        result.normalized,
        result.validation_notes,
        result.confidence_score,
    )
    if doc and (force_status or doc.status not in ("committed", "approved")):
        doc.status = result.status


@app.get("/records/queue")
def review_queue(db: Session = Depends(get_db)):
    """Records open for review/edit (not committed). Includes legacy 'done'."""
    records = (
        db.query(ExtractedRecord)
        .join(Document)
        .filter(
            Document.status.in_(
                ["ready", "needs_review", "approved", "done", "processing"]
            )
        )
        .order_by(ExtractedRecord.id.desc())
        .all()
    )
    out = []
    for r in records:
        if r.committed_at:
            continue
        doc = db.query(Document).filter(Document.id == r.document_id).first()
        out.append(record_to_dict(r, doc))
    return out


@app.get("/records/needs-review")
def needs_review(db: Session = Depends(get_db)):
    """Backward-compatible: full review queue."""
    return review_queue(db)


@app.post("/documents/{document_id}/edit")
def open_for_edit(document_id: int, db: Session = Depends(get_db)):
    """Send a document back to the review queue for reassess / edit."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.status == "failed" and not doc.raw_text:
        raise HTTPException(status_code=400, detail="Document failed and has no text to edit")

    record = (
        db.query(ExtractedRecord)
        .filter(ExtractedRecord.document_id == doc.id)
        .order_by(ExtractedRecord.id.desc())
        .first()
    )
    if not record:
        raise HTTPException(
            status_code=400,
            detail="No extraction yet — run extract first or re-upload",
        )

    # Unlock so user can save / approve / commit again
    record.approved_at = None
    record.committed_at = None
    _revalidate_record(record, doc, force_status=True)
    db.commit()
    db.refresh(record)
    db.refresh(doc)
    return record_to_dict(record, doc)


@app.patch("/records/{record_id}")
def update_record(record_id: int, body: RecordUpdate, db: Session = Depends(get_db)):
    record = db.query(ExtractedRecord).filter(ExtractedRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    data = body.model_dump(exclude_unset=True)
    if "date" in data and "invoice_date" not in data:
        data["invoice_date"] = data.pop("date")
    else:
        data.pop("date", None)

    if "line_items" in data and data["line_items"] is not None:
        data["line_items"] = json.dumps(data["line_items"])

    for key, value in data.items():
        if hasattr(record, key):
            setattr(record, key, value)

    # Editing unlocks a prior commit so Save/Approve work after Edit from Records
    record.committed_at = None
    record.approved_at = None

    doc = db.query(Document).filter(Document.id == record.document_id).first()
    _revalidate_record(record, doc)
    if doc and doc.status in ("committed", "done", "approved"):
        doc.status = "needs_review"
        _revalidate_record(record, doc)
    db.commit()
    db.refresh(record)
    return record_to_dict(record, doc)


@app.post("/records/{record_id}/approve")
def approve_record(record_id: int, db: Session = Depends(get_db)):
    record = db.query(ExtractedRecord).filter(ExtractedRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    if record.committed_at:
        raise HTTPException(status_code=400, detail="Record already committed")

    doc = db.query(Document).filter(Document.id == record.document_id).first()
    _revalidate_record(record, doc)
    if not (
        record.vendor
        and record.invoice_number
        and record.invoice_date
        and record.total_amount is not None
    ):
        raise HTTPException(
            status_code=400,
            detail="Main fields incomplete: fix vendor, invoice #, date, and total before approve",
        )

    # Human sign-off: main fields required; line heuristic not blocking.
    from .validation import ExtractionData, LineItemData

    try:
        items_raw = json.loads(record.line_items or "[]")
    except json.JSONDecodeError:
        items_raw = []
    items = [
        LineItemData(
            description=i.get("description", ""),
            amount=i.get("amount"),
            quantity=i.get("quantity"),
            unit_price=i.get("unit_price"),
        )
        for i in items_raw
        if isinstance(i, dict)
    ]
    data = ExtractionData(
        vendor=record.vendor,
        invoice_number=record.invoice_number,
        invoice_date=record.invoice_date,
        total_amount=record.total_amount,
        currency=record.currency or "USD",
        due_date=record.due_date,
        subtotal=record.subtotal,
        tax_amount=record.tax_amount,
        po_number=record.po_number,
        payment_terms=record.payment_terms,
        line_items=items,
        lines_required=False,  # human sign-off: don't block on line heuristic
    )
    result = validate_and_score(data)
    if not result.main_complete:
        raise HTTPException(
            status_code=400,
            detail="Main fields invalid: " + "; ".join(result.validation_notes),
        )
    apply_normalized(record, result.normalized, result.validation_notes, result.confidence_score)
    record.approved_at = datetime.utcnow()
    if doc:
        doc.status = "approved"
    db.commit()
    db.refresh(record)
    return record_to_dict(record, doc)


@app.post("/records/{record_id}/commit")
def commit(record_id: int, db: Session = Depends(get_db)):
    record = db.query(ExtractedRecord).filter(ExtractedRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")

    doc = db.query(Document).filter(Document.id == record.document_id).first()

    # Require approval first (or approve inline if main fields complete)
    if not record.approved_at:
        approve_record(record_id, db)
        record = db.query(ExtractedRecord).filter(ExtractedRecord.id == record_id).first()
        doc = db.query(Document).filter(Document.id == record.document_id).first()

    log = commit_record(db, record)
    if doc and log.status == "success":
        doc.status = "committed"
        db.commit()
        db.refresh(record)

    return {
        "log_id": log.id,
        "action_type": log.action_type,
        "status": log.status,
        "exports": {
            "technical": "output/financial_summary_technical.csv",
            "readable": "output/summary_readable.csv",
        },
        "record": record_to_dict(record, doc),
    }


@app.get("/exports/technical")
def download_technical():
    path = Path("output/financial_summary_technical.csv")
    if not path.exists():
        raise HTTPException(status_code=404, detail="No technical summary yet")
    return FileResponse(path, filename="financial_summary_technical.csv")


@app.get("/exports/readable")
def download_readable():
    path = Path("output/summary_readable.csv")
    if not path.exists():
        raise HTTPException(status_code=404, detail="No readable summary yet")
    return FileResponse(path, filename="summary_readable.csv")


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def no_cache_ui(request, call_next):
    """Prevent browsers from serving stale Clearpost CSS/JS/HTML after UI fixes."""
    response = await call_next(request)
    path = request.url.path or ""
    if path == "/" or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/")
def index():
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend not found")
    return FileResponse(
        index_path,
        media_type="text/html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )
