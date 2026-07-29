from datetime import datetime

from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship

from .database import Base


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    file_path = Column(String, nullable=False)
    original_filename = Column(String, nullable=True)
    file_type = Column(String, default="pdf")
    raw_text = Column(Text, nullable=True)
    # pending | processing | ready | needs_review | approved | committed | failed
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)

    extracted_records = relationship("ExtractedRecord", back_populates="document")


class ExtractedRecord(Base):
    __tablename__ = "extracted_records"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)

    # Main
    vendor = Column(String, nullable=True)
    invoice_number = Column(String, nullable=True)
    invoice_date = Column(String, nullable=True)  # YYYY-MM-DD
    total_amount = Column(Float, nullable=True)
    currency = Column(String, default="USD")

    # Bonus
    due_date = Column(String, nullable=True)
    subtotal = Column(Float, nullable=True)
    tax_amount = Column(Float, nullable=True)
    po_number = Column(String, nullable=True)
    payment_terms = Column(String, nullable=True)
    line_items = Column(Text, nullable=True)  # JSON

    # Detection / scoring
    lines_required = Column(Integer, default=0)  # 1 if source had line table
    confidence_score = Column(Float, nullable=True)
    validation_notes = Column(Text, nullable=True)  # JSON list of strings

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    extracted_at = Column(DateTime, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    committed_at = Column(DateTime, nullable=True)

    document = relationship("Document", back_populates="extracted_records")
    action_logs = relationship("ActionLog", back_populates="record")


class ActionLog(Base):
    __tablename__ = "action_logs"

    id = Column(Integer, primary_key=True, index=True)
    record_id = Column(Integer, ForeignKey("extracted_records.id"), nullable=False)
    action_type = Column(String, nullable=False)
    status = Column(String, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)

    record = relationship("ExtractedRecord", back_populates="action_logs")
