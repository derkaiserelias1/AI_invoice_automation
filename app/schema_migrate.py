"""Add missing SQLite columns for evolving schema without wiping the DB."""

from sqlalchemy import inspect, text

from .database import engine


DOCUMENT_COLUMNS = {
    "original_filename": "VARCHAR",
    "file_type": "VARCHAR",
}

RECORD_COLUMNS = {
    "invoice_number": "VARCHAR",
    "invoice_date": "VARCHAR",
    "currency": "VARCHAR",
    "due_date": "VARCHAR",
    "subtotal": "FLOAT",
    "tax_amount": "FLOAT",
    "po_number": "VARCHAR",
    "payment_terms": "VARCHAR",
    "lines_required": "INTEGER DEFAULT 0",
    "validation_notes": "TEXT",
    "extracted_at": "DATETIME",
    "approved_at": "DATETIME",
    "committed_at": "DATETIME",
}


def _existing_columns(table: str) -> set[str]:
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def ensure_schema() -> None:
    with engine.begin() as conn:
        docs = _existing_columns("documents")
        for name, coltype in DOCUMENT_COLUMNS.items():
            if name not in docs:
                conn.execute(text(f"ALTER TABLE documents ADD COLUMN {name} {coltype}"))

        recs = _existing_columns("extracted_records")
        for name, coltype in RECORD_COLUMNS.items():
            if name not in recs:
                conn.execute(text(f"ALTER TABLE extracted_records ADD COLUMN {name} {coltype}"))

        # Migrate legacy `date` → invoice_date when present
        recs = _existing_columns("extracted_records")
        if "date" in recs and "invoice_date" in recs:
            conn.execute(
                text(
                    "UPDATE extracted_records SET invoice_date = date "
                    "WHERE (invoice_date IS NULL OR invoice_date = '') "
                    "AND date IS NOT NULL AND date != ''"
                )
            )
