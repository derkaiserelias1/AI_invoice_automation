# Clearpost — invoice automation with confidence gating

A local document pipeline for accounts payable: intake invoices as PDFs, scans or
spreadsheets, extract the fields with an LLM, score every result against
deterministic rules, and route anything uncertain to human review before export.

Runs entirely on your machine. No cloud service sees the documents.

## The design decision that matters

**Confidence is computed by rules, not self-reported by the model.**

The first version asked the model how confident it was. It would return a clean high
score on invoices where it had quietly mis-parsed a total or invented a line item — so
wrong records flowed into the approved queue looking healthy. Silent wrong values are
the failure mode that actually costs money in AP; a visibly failed extraction is
harmless by comparison.

So the model now only returns fields. Scoring is separate and deterministic
(`app/validation.py`):

- **Arithmetic reconciliation** — line items must sum to the total within **$0.05**,
  with a second check allowing `line_sum + tax == total`
- **Required-field checks** — vendor, invoice number, date, total
- **Date parsing across 10 formats** — `%Y-%m-%d`, `%m/%d/%Y`, `%d/%m/%Y`, `%b %d, %Y`
  and others, so a valid date in an unusual format is not treated as missing
- **Scoring tiers** — incomplete extractions are floored at 0.35 and capped at 0.50;
  complete ones start at 0.85 and rise with each corroborating field

A record reaches `ready` only if it is complete **and** the arithmetic balances
**and** it scores ≥ 0.95. Everything else becomes `needs_review`, flagged with a
specific note naming what failed — for example
`line items sum 1240.00 does not match total 1340.00`.

Extraction is wrapped in a **strict-JSON retry**: malformed model output re-prompts
instead of killing the batch.

## Pipeline

```
upload -> text extraction -> LLM field extraction -> rule-based validation
                                                            |
                                              score >= 0.95 and balanced?
                                                   /                \
                                                 yes                 no
                                                  |                   |
                                               ready            needs_review
                                                  |                   |
                                                  |            human edit / approve
                                                  \                   /
                                                   commit -> CSV exports
```

**Text extraction** (`app/text_extract.py`) handles embedded PDF text via `pdfplumber`,
falls back to **OCR with EasyOCR** for scanned images, and reads CSV/spreadsheet input
directly.

## API

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/documents/upload` | intake a file |
| `POST` | `/documents/{id}/extract` | run extraction + scoring |
| `GET` | `/documents` | list documents |
| `GET` | `/documents/{id}/file` | retrieve the original |
| `GET` | `/records/queue` | all records |
| `GET` | `/records/needs-review` | the review queue |
| `POST` | `/documents/{id}/edit` | correct extracted fields |
| `POST` | `/records/{id}/approve` | approve a reviewed record |
| `POST` | `/records/{id}/commit` | commit and write exports |
| `GET` | `/exports/technical` | full-fidelity CSV |
| `GET` | `/exports/readable` | human-readable CSV |
| `GET` | `/system/status` | health and model availability |

Every commit writes an `ActionLog` entry, so each record has an audit trail from
upload through approval.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
.\run.bat
```

Then open `http://127.0.0.1:8000`. Sample invoices are in `test_invoices/`.

Extraction uses a local model through Ollama (`qwen2.5:7b-instruct` by default):

```bash
ollama pull qwen2.5:7b-instruct
```

## Layout

```
app/
  main.py          FastAPI routes (570 lines)
  validation.py    confidence rules and scoring (267)
  actions.py       exports, commit, audit log (251)
  text_extract.py  PDF text, OCR, spreadsheet intake (197)
  extraction.py    LLM prompt, strict-JSON retry (120)
  models.py        SQLAlchemy models (69)
  schema_migrate.py  lightweight migrations (58)
  database.py      session setup (18)
static/            review UI
test_invoices/     sample documents
```

Stack: FastAPI, SQLAlchemy, pydantic, pdfplumber, EasyOCR, vanilla JS. SQLite by default.

## Scope

A working prototype, run locally. Not deployed, not multi-tenant, no authentication —
it assumes a single trusted operator on one machine.

## License

MIT — see `LICENSE`.
