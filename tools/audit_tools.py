"""
LangChain @tool wrappers for the domain audit pipeline.

Each wrapper calls the corresponding domain function and serialises the dict
result to a JSON string so the LLM can read it. Complex parameters that the
LLM must supply (embedded_metadata, document_results) are accepted as JSON
strings and parsed here — keeps the @tool signatures LLM-friendly.

Pipeline order the auditor agent should follow:
  1. crawl_repository    — inventory files + filesystem metadata
  2. extract_document    — pull text + embedded metadata per file
  3. score_staleness     — age / cold / content-date signal
  4. check_standards     — template compliance + retired-reference signal
  5. check_governance    — metadata completeness signal
  6. aggregate_findings  — roll up into corpus-level dashboard
"""
from __future__ import annotations

import json
from langchain_core.tools import tool
from loguru import logger

from domain.tools.crawler import crawl_repository as _crawl
from domain.tools.extractor import extract_document as _extract
from domain.tools.staleness import score_staleness as _staleness
from domain.tools.standards import check_standards as _standards
from domain.tools.governance import check_governance as _governance
from domain.tools.aggregate import aggregate_findings as _aggregate, compute_trust_score as _trust
from domain.tools.metadata_consistency import check_metadata_consistency as _consistency
from domain.knowledge import doc_type_from_filename


@tool
def crawl_repository(folder_path: str) -> str:
    """Walk a folder recursively and inventory every file with filesystem metadata.

    Returns JSON with keys: files (list), total_files, total_bytes, by_extension.

    Args:
        folder_path: Absolute or relative path to the document repository root.
    """
    logger.info(f"[crawl_repository] folder='{folder_path}'")
    try:
        result = _crawl(folder_path)
        return json.dumps(result)
    except Exception as e:
        logger.error(f"[crawl_repository] {e}")
        return json.dumps({"error": str(e)})


@tool
def extract_document(path: str, max_chars: int = 12000) -> str:
    """Extract body text and embedded metadata from a single document.

    Supports .pdf, .docx, .xlsx, .pptx, .csv, .json, .txt, .md.
    Returns JSON with keys: text, embedded_metadata, page_or_item_count,
    extraction_ok, error.

    Args:
        path: Absolute or relative path to the file.
        max_chars: Maximum characters of body text to return (default 12000).
    """
    logger.info(f"[extract_document] path='{path}' max_chars={max_chars}")
    try:
        result = _extract(path, max_chars=max_chars)
        return json.dumps(result)
    except Exception as e:
        logger.error(f"[extract_document] {e}")
        return json.dumps({"error": str(e)})


@tool
def score_staleness(
    doc_type: str,
    modified_at: str,
    accessed_at: str = "",
    content_text: str = "",
) -> str:
    """Compute a staleness score for a document based on age and access signals.

    Returns JSON with keys: staleness_score (0-1, higher=fresher), age_days,
    threshold_days, is_stale, is_cold, findings.

    Args:
        doc_type: Document type (e.g. 'policy', 'procedure', 'contract').
        modified_at: Last-modified timestamp as ISO 8601 string.
        accessed_at: Last-accessed timestamp as ISO 8601 string (empty string if unknown).
        content_text: Body text of the document to scan for overdue review dates.
    """
    logger.info(f"[score_staleness] doc_type='{doc_type}' modified_at='{modified_at}'")
    try:
        result = _staleness(
            doc_type=doc_type,
            modified_at=modified_at,
            accessed_at=accessed_at or None,
            content_text=content_text or None,
        )
        return json.dumps(result)
    except Exception as e:
        logger.error(f"[score_staleness] {e}")
        return json.dumps({"error": str(e)})


@tool
def check_standards(doc_type: str, extension: str, text: str) -> str:
    """Check structural template compliance, format standardness, and retired references.

    Returns JSON with keys: standards_score (0-1), is_standard_format,
    required_sections, missing_sections, present_sections,
    retired_standard_hits, placeholder_hits, findings.

    Args:
        doc_type: Document type (e.g. 'policy', 'data_dictionary').
        extension: File extension including dot (e.g. '.docx', '.pdf').
        text: First 2000 characters of extracted body text is sufficient.
    """
    logger.info(f"[check_standards] doc_type='{doc_type}' extension='{extension}'")
    try:
        # Cap text to prevent large context window costs when called individually
        result = _standards(doc_type=doc_type, extension=extension, text=text[:8000])
        return json.dumps(result)
    except Exception as e:
        logger.error(f"[check_standards] {e}")
        return json.dumps({"error": str(e)})


@tool
def check_governance(
    doc_type: str,
    embedded_metadata_json: str,
    text: str,
) -> str:
    """Check governance metadata completeness for a document.

    Returns JSON with keys: governance_score (0-1), required_fields,
    present_fields, missing_fields, classification, classification_valid,
    has_owner, findings.

    Args:
        doc_type: Document type (e.g. 'policy', 'data_contract').
        embedded_metadata_json: JSON object of embedded file metadata
            (from extract_document's embedded_metadata field).
        text: First 2000 characters of extracted body text is sufficient.
    """
    logger.info(f"[check_governance] doc_type='{doc_type}'")
    try:
        embedded_metadata = json.loads(embedded_metadata_json) if embedded_metadata_json else {}
        result = _governance(
            doc_type=doc_type,
            embedded_metadata=embedded_metadata,
            text=text[:8000],   # cap to prevent large context costs
        )
        return json.dumps(result)
    except Exception as e:
        logger.error(f"[check_governance] {e}")
        return json.dumps({"error": str(e)})


@tool
def aggregate_findings(document_results_json: str) -> str:
    """Roll up per-document audit results into corpus-level dashboard metrics.

    Returns JSON with keys: headline (total_documents, total_size_human,
    needs_supervision_pct), staleness, standards, governance, by_doc_type,
    estimated_remediation_hours, top_offenders.

    Args:
        document_results_json: JSON array of per-document result objects. Each
            object must contain: path, doc_type, size_bytes, trust_score,
            needs_supervision, and the staleness/standards/governance sub-results.
    """
    logger.info("[aggregate_findings] rolling up corpus results")
    try:
        document_results = json.loads(document_results_json)
        result = _aggregate(document_results)
        return json.dumps(result)
    except Exception as e:
        logger.error(f"[aggregate_findings] {e}")
        return json.dumps({"error": str(e)})


@tool
def run_full_audit(folder_path: str) -> str:
    """Run the complete AI-readiness audit pipeline on a folder in one call.

    Executes crawl → extract → staleness/standards/governance per file →
    aggregate, and returns a JSON dashboard with headline metrics and top
    offenders. Use this for any corpus-level audit request. Use the individual
    tools only when inspecting a single document.

    Returns JSON with keys: headline (total_documents, total_size_human,
    needs_supervision_count, needs_supervision_pct), staleness, standards,
    governance, by_doc_type, estimated_remediation_hours, top_offenders.

    Args:
        folder_path: Path to the document repository folder to audit.
    """
    logger.info(f"[run_full_audit] folder='{folder_path}'")
    try:
        crawl = _crawl(folder_path)
        files = crawl.get("files", [])
        if not files:
            return json.dumps({"error": f"No files found in {folder_path!r}"})

        document_results = []
        for file_record in files:
            path = file_record["path"]
            doc_type = file_record["doc_type"]
            extension = file_record["extension"]
            modified_at = file_record["modified_at"]
            accessed_at = file_record.get("accessed_at") or None

            extracted = _extract(path)
            text = extracted.get("text", "")
            embedded_metadata = extracted.get("embedded_metadata", {})

            staleness = _staleness(doc_type, modified_at, accessed_at, content_text=text)
            standards = _standards(doc_type, extension, text)
            governance = _governance(doc_type, embedded_metadata, text)
            trust = _trust(staleness["staleness_score"],
                           standards["standards_score"],
                           governance["governance_score"])

            document_results.append({
                "path":             path,
                "name":             file_record["name"],
                "doc_type":         doc_type,
                "extension":        extension,
                "size_bytes":       file_record["size_bytes"],
                "modified_at":      modified_at,
                "extraction_ok":    extracted.get("extraction_ok", True),
                "staleness":        staleness,
                "standards":        standards,
                "governance":       governance,
                "trust_score":      trust["trust_score"],
                "needs_supervision": trust["needs_supervision"],
            })

        return json.dumps(_aggregate(document_results))
    except Exception as e:
        logger.error(f"[run_full_audit] {e}")
        return json.dumps({"error": str(e)})


@tool
def inspect_document(path: str) -> str:
    """Run all audit checks on a single document and return structured findings.

    ALWAYS use this (not the individual tools) when inspecting one specific file.
    It runs extract → staleness → standards → governance → metadata_consistency
    internally and returns only the scored findings — no raw document text is
    included in the response, keeping this call cheap regardless of file size.

    Returns JSON with: name, doc_type, extension, size_bytes, modified_at,
    extraction_ok, embedded_metadata, trust_score, needs_supervision,
    staleness, standards, governance, consistency.

    Args:
        path: Absolute or relative path to the document file.
    """
    logger.info(f"[inspect_document] path='{path}'")
    import os
    from pathlib import Path as _Path
    try:
        p = _Path(path)
        ext = p.suffix.lower()
        doc_type = doc_type_from_filename(p.name)

        stat = os.stat(path)
        from datetime import datetime, timezone
        modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        accessed_at = datetime.fromtimestamp(stat.st_atime, tz=timezone.utc).isoformat()

        extracted        = _extract(path)
        text             = extracted.get("text", "")
        embedded_meta    = extracted.get("embedded_metadata", {})
        extraction_ok    = extracted.get("extraction_ok", True)

        staleness    = _staleness(doc_type, modified_at, accessed_at, content_text=text)
        standards    = _standards(doc_type, ext, text)
        governance   = _governance(doc_type, embedded_meta, text)
        consistency  = _consistency(embedded_meta, text, modified_at)
        trust        = _trust(staleness["staleness_score"],
                              standards["standards_score"],
                              governance["governance_score"])

        # Return structured findings — deliberately omit raw text to keep
        # the LLM context small regardless of document length.
        return json.dumps({
            "name":              p.name,
            "doc_type":          doc_type,
            "extension":         ext,
            "size_bytes":        stat.st_size,
            "modified_at":       modified_at,
            "extraction_ok":     extraction_ok,
            "embedded_metadata": embedded_meta,
            "trust_score":       trust["trust_score"],
            "needs_supervision": trust["needs_supervision"],
            "staleness":         staleness,
            "standards":         standards,
            "governance":        governance,
            "consistency":       consistency,
        })
    except Exception as e:
        logger.error(f"[inspect_document] {e}")
        return json.dumps({"error": str(e), "path": path})


AUDITOR_TOOLS = [
    run_full_audit,
    inspect_document,        # single-doc: zero raw text in LLM context
    crawl_repository,
    extract_document,
    score_staleness,
    check_standards,
    check_governance,
    aggregate_findings,
]
