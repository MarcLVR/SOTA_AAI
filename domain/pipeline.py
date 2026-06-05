"""
Full document pipeline: connector → mini_critic → DB → Telegram.

Used by the Scan tab after a rule-based audit to write findings to Postgres
and notify document owners via Telegram.
"""
from __future__ import annotations

import re
import sys
import os
from pathlib import Path

# ── Regex patterns (PII + compliance) ─────────────────────────────────────────

_PHONE_RE  = re.compile(
    r'(\+\d{1,3}\s?\d[\d\s\-]{6,14}\d)'
)
_EMAIL_RE  = re.compile(
    r'\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+-internal\.com\b|'
    r'\b[a-zA-Z0-9._%+\-]+@gmail\.com\b'
)
_TBD_RE    = re.compile(
    r'\b(TBD|TODO|\[TBD\]|\[INSERT[^\]]+\]|\[PENDING[^\]]+\])\b'
)
_NAMING_RE = re.compile(r'(FINAL|final|_v\d+|_V\d+)')


# ── Mini-critic ────────────────────────────────────────────────────────────────

def mini_critic(doc: dict) -> list[dict]:
    """Detect PII and compliance issues from a connector DocumentSchema."""
    text = doc["text"] + "\n" + (doc.get("notes") or "")
    findings = []

    for m in _EMAIL_RE.finditer(text):
        findings.append({
            "rule_code":    "pii_email",
            "title":        f"Exposed PII: email address found ({m.group()})",
            "location":     _locate(text, m.start(), doc),
            "suggestion":   "Remove or anonymize email addresses in shareable documents.",
            "severity":     "high",
        })

    for m in _PHONE_RE.finditer(text):
        findings.append({
            "rule_code":    "pii_phone",
            "title":        f"Exposed PII: phone number found ({m.group().strip()})",
            "location":     _locate(text, m.start(), doc),
            "suggestion":   "Remove phone numbers from the document.",
            "severity":     "medium",
        })

    tbds = set(m.group() for m in _TBD_RE.finditer(text))
    if tbds:
        findings.append({
            "rule_code":    "placeholder_values",
            "title":        f"Placeholder values detected: {', '.join(sorted(tbds)[:4])}",
            "location":     "Multiple document sections",
            "suggestion":   "Complete all TBD/TODO/placeholder fields before distributing.",
            "severity":     "medium",
        })

    fname = doc["filename"]
    if _NAMING_RE.search(fname) or (
        not re.match(r'^\d{4}-\d{2}-\d{2}_', fname)
        and doc["format"] in ("pdf",)
    ):
        findings.append({
            "rule_code":    "naming_convention",
            "title":        f"Incorrect naming convention: '{fname}'",
            "location":     "Filename",
            "suggestion":   "Rename following the YYYY-MM-DD_type_name standard.",
            "severity":     "low",
        })

    return findings


def _locate(text: str, pos: int, doc: dict) -> str:
    for s in doc.get("sections", []):
        if s["heading"] and s["heading"] in text[max(0, pos - 300):pos + 300]:
            return f'Section "{s["heading"]}"'
    if doc.get("notes") and pos >= len(doc["text"]):
        return "Speaker notes (not visible in document)"
    return "Document body"


# ── Single-document pipeline ───────────────────────────────────────────────────

def run_document_pipeline(doc_path: str, owner: str = "") -> dict:
    """
    Run connector → mini_critic → DB → Telegram for one document.
    Returns {"run_id", "findings_count", "notified", "error"}.
    """
    _add_chase_to_path()

    from agents.connector import load_document
    from db import create_run
    from notifier import broadcast_finding

    try:
        doc = load_document(doc_path)
    except Exception as exc:
        return {"run_id": None, "findings_count": 0, "notified": 0, "error": str(exc)}

    if doc.get("error"):
        return {"run_id": None, "findings_count": 0, "notified": 0, "error": doc["error"]}

    findings = mini_critic(doc)
    if not findings:
        return {"run_id": None, "findings_count": 0, "notified": 0, "error": None}

    try:
        run_id = create_run(doc["filename"], owner, findings,
                            doc_type=doc.get("format", "unknown"),
                            doc_path=doc_path)
    except Exception as exc:
        return {"run_id": None, "findings_count": len(findings), "notified": 0, "error": str(exc)}

    sent = broadcast_finding(doc["filename"], findings, run_id=run_id)
    return {
        "run_id":         run_id,
        "findings_count": len(findings),
        "notified":       sent,
        "error":          None,
    }


# ── Batch pipeline (for audit results) ────────────────────────────────────────

def _pipeline_from_doc(doc: dict, owner: str) -> dict:
    """Run mini_critic → DB → Telegram on an already-loaded doc dict."""
    _add_chase_to_path()
    from db import create_run
    from notifier import broadcast_finding

    findings = mini_critic(doc)
    if not findings:
        return {"run_id": None, "findings_count": 0, "notified": 0, "error": None}
    try:
        run_id = create_run(doc["filename"], owner, findings,
                            doc_type=doc.get("format", "unknown"))
    except Exception as exc:
        return {"run_id": None, "findings_count": len(findings), "notified": 0, "error": str(exc)}

    sent = broadcast_finding(doc["filename"], findings, run_id=run_id)
    return {"run_id": run_id, "findings_count": len(findings), "notified": sent, "error": None}


def run_audit_pipeline(document_results: list[dict], default_owner: str = "") -> dict:
    """
    Run the full pipeline for all documents flagged as needing supervision.

    document_results: list of dicts from audit_repository() (each has 'path',
                      'needs_supervision', 'embedded_metadata')
    Returns a summary dict with per-document results.
    """
    flagged = [d for d in document_results if d.get("needs_supervision")]
    if not flagged:
        return {"processed": 0, "runs_created": 0, "notified": 0, "errors": []}

    processed = runs_created = notified_count = 0
    errors = []

    for doc in flagged:
        path  = doc.get("path", "")
        owner = _resolve_owner(doc, default_owner)

        # If the original file is gone (e.g. Gradio temp upload cleaned up)
        # but the audit already captured the text, run mini_critic directly.
        if doc.get("text") and not Path(path).exists():
            pre_loaded = {
                "text":     doc["text"],
                "filename": doc.get("name", Path(path).name),
                "format":   doc.get("extension", "").lstrip(".") or "unknown",
                "sections": [],
                "notes":    None,
            }
            result = _pipeline_from_doc(pre_loaded, owner)
        else:
            result = run_document_pipeline(path, owner)
        processed += 1

        if result["error"]:
            errors.append(f"{Path(path).name}: {result['error']}")
        else:
            if result["run_id"] is not None:
                runs_created += 1
            notified_count += result.get("notified", 0)

    return {
        "processed":    processed,
        "runs_created": runs_created,
        "notified":     notified_count,
        "errors":       errors,
    }


def _resolve_owner(doc: dict, default: str) -> str:
    """Map the document author/creator metadata to a known owner, else default.

    Each format exposes the author under a different key: .docx/.pptx use
    "author", .xlsx uses "creator", .pdf uses "Author" (capitalised). Match the
    key case-insensitively so every format routes, not just .docx/.pptx.
    """
    meta = doc.get("embedded_metadata") or {}
    author = ""
    for key, val in meta.items():
        if val and key.lower() in ("author", "creator", "last_modified_by"):
            author = str(val)
            break
    if not author:
        return default
    author_lower = author.lower()
    from config import settings as _settings
    owner_list = [o.strip() for o in _settings.owner_usernames.split(",") if o.strip()]
    for name in owner_list:
        if name in author_lower:
            return name
    return default


def _add_chase_to_path() -> None:
    chase = str(Path(__file__).resolve().parent.parent / "chase")
    if chase not in sys.path:
        sys.path.insert(0, chase)
