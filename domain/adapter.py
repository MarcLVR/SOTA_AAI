"""
domain/adapter.py — bridge between audit_repository() per-document output
and the chase/notifier.py + chase/db.py expected finding format.

Input:  one document dict from audit_repository()["documents"]
Output: list of finding dicts, each with all keys required by both consumers:

    title       str   short human label         "Stale document"
    location    str   signal + specific field   "staleness · modified date"
    suggestion  str   actionable remediation    "Schedule a review and update content."
    severity    str   "high" | "medium" | "low"
    rule_code   str   stable machine code       "stale"
    detail      str   full finding string       "Modified 540 days ago; ..."

Severity rule (document-level, based on trust_score):
    trust_score < 0.50                        → "high"
    rule_code in {retired_standard,
                  extraction_failed}          → always "high" (can mislead RAG)
    trust_score < 0.70                        → "medium"
    otherwise                                 → "low"

notifier.py uses: title, location, suggestion, severity
db.py uses:       rule_code, detail, proposed_fix (=suggestion), location, severity
Both use all six fields — the adapter always populates all of them.
"""
from __future__ import annotations

# ── Lookup tables ─────────────────────────────────────────────────────────────

_TITLES: dict[str, str] = {
    "stale":                "Stale document",
    "cold":                 "Abandoned document",
    "overdue_review":       "Overdue review date",
    "non_standard_format":  "Non-standard file format",
    "missing_section":      "Missing required section",
    "retired_standard":     "References retired standard",
    "placeholder":          "Contains placeholder content",
    "brand_misuse":         "Incorrect brand name usage",
    "missing_metadata":     "Missing governance metadata",
    "unclassified":         "Unclassified document",
    "no_owner":             "No document owner",
    "extraction_failed":    "Document extraction failed",
}

_SUGGESTIONS: dict[str, str] = {
    "stale":
        "Schedule a review, update content, and reset the modification date.",
    "cold":
        "Confirm the document is still needed; archive or delete if obsolete.",
    "overdue_review":
        "Conduct the overdue review, update the review date in the document body.",
    "non_standard_format":
        "Convert to the standard format (.docx / .xlsx / .pptx / .pdf) for this document type.",
    "missing_section":
        "Add the missing section with appropriate content before loading into a RAG pipeline.",
    "retired_standard":
        "Replace the retired standard reference with the current version.",
    "placeholder":
        "Complete or remove placeholder content (TBD / TODO / PENDING) before production use.",
    "brand_misuse":
        "Write the brand name with the exact canonical casing; never pluralise it.",
    "missing_metadata":
        "Add the missing metadata field to the document properties or header.",
    "unclassified":
        "Assign a valid data classification label (e.g. Internal, Confidential, Public).",
    "no_owner":
        "Assign a named owner responsible for maintaining and reviewing this document.",
    "extraction_failed":
        "Check the file is not corrupted; re-export from the source application.",
}

# Rule codes that are always high severity regardless of trust_score
_ALWAYS_HIGH: set[str] = {"retired_standard", "extraction_failed"}


# ── Rule-code inference ───────────────────────────────────────────────────────

def _infer_rule_code(finding_text: str, signal: str) -> str:
    """Infer a stable rule_code from the free-text finding string."""
    t = finding_text.lower()

    if signal == "staleness":
        if "not accessed" in t:
            return "cold"
        if "overdue" in t or "review/expiry" in t or "expiry date" in t:
            return "overdue_review"
        # "Modified N days ago; ..." — the default staleness finding
        return "stale"

    if signal == "standards":
        if "non-standard format" in t:
            return "non_standard_format"
        if "missing required section" in t:
            return "missing_section"
        if "retired standard" in t:
            return "retired_standard"
        if "placeholder" in t or "obsolete marker" in t:
            return "placeholder"
        if "brand name" in t:
            return "brand_misuse"
        return "non_standard_format"  # safest fallback for standards

    if signal == "governance":
        if "no owner" in t or "ungoverned" in t:
            return "no_owner"
        if "classification" in t:
            return "unclassified"
        if "missing governance field" in t:
            return "missing_metadata"
        return "missing_metadata"  # safest fallback for governance

    return signal  # unknown signal — use as-is


# ── Location extraction ───────────────────────────────────────────────────────

def _extract_location(finding_text: str, signal: str, rule_code: str) -> str:
    """Build a human-readable location string from the finding text."""
    t = finding_text.lower()
    prefix = signal.capitalize()

    if rule_code == "stale":
        return f"{prefix} · modified date"
    if rule_code == "cold":
        return f"{prefix} · last access date"
    if rule_code == "overdue_review":
        return f"{prefix} · review date in body"
    if rule_code == "non_standard_format":
        # "Non-standard format '.txt'; expected one of [...]"
        try:
            ext = finding_text.split("'")[1]
            return f"{prefix} · file extension ({ext})"
        except IndexError:
            return f"{prefix} · file extension"
    if rule_code == "missing_section":
        # "Missing required section: 'Owner'."
        try:
            section = finding_text.split("'")[1]
            return f"{prefix} · section: {section}"
        except IndexError:
            return f"{prefix} · required section"
    if rule_code == "retired_standard":
        # "References retired standard 'ISO/IEC 27001:2013': ..."
        try:
            std = finding_text.split("'")[1]
            return f"{prefix} · reference: {std}"
        except IndexError:
            return f"{prefix} · retired standard reference"
    if rule_code == "placeholder":
        # "Contains placeholder/obsolete marker: 'TBD'."
        try:
            marker = finding_text.split("'")[1]
            return f"{prefix} · placeholder: {marker}"
        except IndexError:
            return f"{prefix} · placeholder content"
    if rule_code == "brand_misuse":
        # "Incorrect brand name usage 'OMNIACCESS': ..."
        try:
            variant = finding_text.split("'")[1]
            return f"{prefix} · brand name: {variant}"
        except IndexError:
            return f"{prefix} · brand name"
    if rule_code == "missing_metadata":
        # "Missing governance field: 'owner'."
        try:
            field = finding_text.split("'")[1]
            return f"{prefix} · field: {field}"
        except IndexError:
            return f"{prefix} · metadata fields"
    if rule_code == "unclassified":
        return f"{prefix} · classification"
    if rule_code == "no_owner":
        return f"{prefix} · owner field"
    if rule_code == "extraction_failed":
        return "extraction · file parse"

    return f"{prefix} · general"


# ── Severity ──────────────────────────────────────────────────────────────────

def _severity(trust_score: float, rule_code: str) -> str:
    if rule_code in _ALWAYS_HIGH:
        return "high"
    if trust_score < 0.50:
        return "high"
    if trust_score < 0.70:
        return "medium"
    return "low"


# ── Core adapter ──────────────────────────────────────────────────────────────

def audit_doc_to_findings(doc_result: dict) -> list[dict]:
    """
    Convert one document result from audit_repository()["documents"] into a
    list of finding dicts compatible with chase/notifier.py and chase/db.py.

    Returns an empty list if the document has no findings.
    """
    trust = doc_result.get("trust_score", 1.0)
    findings_out: list[dict] = []

    # Extraction failure is not emitted as a finding string by the domain tools —
    # handle it explicitly here.
    if not doc_result.get("extraction_ok", True):
        findings_out.append({
            "title":      _TITLES["extraction_failed"],
            "location":   "extraction · file parse",
            "suggestion": _SUGGESTIONS["extraction_failed"],
            "severity":   "high",
            "rule_code":  "extraction_failed",
            "detail":     "File could not be fully parsed; text and metadata may be incomplete.",
        })

    # Iterate the three signal lists in order
    for signal, findings_list in (
        ("staleness",  doc_result.get("staleness",  {}).get("findings", [])),
        ("standards",  doc_result.get("standards",  {}).get("findings", [])),
        ("governance", doc_result.get("governance", {}).get("findings", [])),
    ):
        for detail_text in findings_list:
            if not detail_text:
                continue
            rule_code = _infer_rule_code(detail_text, signal)
            sev       = _severity(trust, rule_code)
            location  = _extract_location(detail_text, signal, rule_code)
            findings_out.append({
                "title":      _TITLES.get(rule_code, rule_code.replace("_", " ").title()),
                "location":   location,
                "suggestion": _SUGGESTIONS.get(rule_code, "Review and remediate this finding."),
                "severity":   sev,
                "rule_code":  rule_code,
                "detail":     detail_text,
            })

    return findings_out


# ── Convenience: run audit + adapt all flagged documents ─────────────────────

def audit_repository_to_findings(folder: str) -> dict[str, list[dict]]:
    """
    Run audit_repository() on *folder* and return a mapping
        doc_path → list[finding_dict]
    for every document that needs supervision (trust_score < 0.70).

    Documents with no findings are omitted from the result.
    """
    from domain.run_audit import audit_repository

    result = audit_repository(folder)
    out: dict[str, list[dict]] = {}

    for doc in result.get("documents", []):
        if not doc.get("needs_supervision", False):
            continue
        findings = audit_doc_to_findings(doc)
        if findings:
            out[doc["path"]] = findings

    return out


# ── Scan + persist (drop-in replacement for audit_repository) ────────────────

def audit_and_persist(folder: str, default_owner: str = "") -> dict:
    """
    Run audit_repository(folder), persist flagged findings to PostgreSQL, and
    return the same result dict — drop-in replacement for audit_repository().

    Callers (e.g. ui/app.py) only need to swap the function name; the return
    value is identical so all downstream UI code keeps working unchanged.

    default_owner: escalation/admin owner assigned to findings whose owner cannot
    be inferred from the document's author metadata (e.g. orphan documents,
    formats with no author metadata). Routes "ownerless" documents to a real
    inbox instead of dropping them silently.

    When DB_ENABLED=false (default), the DB write is skipped entirely — no
    connection attempts, no timeouts, no UI freeze.
    """
    from domain.run_audit import audit_repository
    from domain.pipeline import _resolve_owner   # author-metadata → team owner
    from config import settings

    if not default_owner:
        default_owner = settings.default_owner

    result = audit_repository(folder)

    # Fast-path: skip ALL DB work when the database is not configured.
    # Without this guard every flagged document would attempt a TCP connection
    # (connect_timeout=3s) that blocks the scan handler for up to
    # N_flagged × 3s — freezing the Gradio "processing" spinner.
    if not settings.db_enabled:
        return result

    import sys as _sys, os as _os
    _chase = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "chase")
    if _chase not in _sys.path:
        _sys.path.insert(0, _chase)
    from db import create_run

    for doc in result.get("documents", []):
        if not doc.get("needs_supervision", False):
            continue
        findings = audit_doc_to_findings(doc)
        if not findings:
            continue
        # Resolve the real owner from the document's author metadata; fall back
        # to default_owner only when the author is unknown / unmapped.
        owner = _resolve_owner(doc, default_owner)
        try:
            create_run(
                document=doc.get("name", "unknown"),
                owner=owner,
                findings=findings,
                doc_type=doc.get("doc_type", "unknown"),
                doc_path=doc.get("path", ""),
            )
        except Exception as exc:
            print(f"[adapter] DB persist skipped for {doc.get('name')!r}: {exc}")

    return result


# ── Smoke-test CLI ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import sys

    folder = sys.argv[1] if len(sys.argv) > 1 else "domain/demo_corpus/files"
    print(f"Auditing {folder!r} …\n")

    from domain.run_audit import audit_repository

    result = audit_repository(folder)
    documents = result.get("documents", [])

    # Show findings for the 3 lowest-trust documents
    worst = sorted(documents, key=lambda d: d.get("trust_score", 1.0))[:3]

    REQUIRED_KEYS = {"title", "location", "suggestion", "severity", "rule_code", "detail"}
    VALID_SEVERITIES = {"high", "medium", "low"}
    all_ok = True

    for doc in worst:
        findings = audit_doc_to_findings(doc)
        print(f"{'='*65}")
        print(f"  {doc['name']}")
        print(f"  trust_score={doc['trust_score']:.3f}  needs_supervision={doc['needs_supervision']}")
        print(f"  findings: {len(findings)}")
        print()

        for i, f in enumerate(findings, 1):
            missing = REQUIRED_KEYS - f.keys()
            invalid_sev = f.get("severity") not in VALID_SEVERITIES

            if missing or invalid_sev:
                all_ok = False

            status = "✅" if not missing and not invalid_sev else "❌"
            print(f"  {status} [{i}] rule_code={f['rule_code']!r}  severity={f['severity']!r}")
            print(f"       title:      {f['title']}")
            print(f"       location:   {f['location']}")
            print(f"       suggestion: {f['suggestion'][:80]}")
            print(f"       detail:     {f['detail'][:80]}")
            if missing:
                print(f"       MISSING KEYS: {missing}")
            print()

    print(f"{'='*65}")
    totals = audit_repository_to_findings(folder)
    total_docs    = len(totals)
    total_findings = sum(len(v) for v in totals.values())
    print(f"\naudit_repository_to_findings() → {total_docs} flagged docs, "
          f"{total_findings} total findings")
    print(f"\nAll findings valid: {'✅ YES' if all_ok else '❌ NO'}")
