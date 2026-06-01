"""
Governance metadata completeness — the "ungoverned data" signal.
"""
from __future__ import annotations

import re

from domain.knowledge import (
    REQUIRED_METADATA,
    VALID_CLASSIFICATIONS,
)

# Inline governance block patterns — looks for "Key: value" in the body text.
_INLINE_PATTERNS: dict[str, re.Pattern] = {
    "owner":         re.compile(r"(?:owner|document owner|data owner)\s*[:\-]\s*(.+)", re.IGNORECASE),
    "classification": re.compile(r"(?:classification|data classification|sensitivity)\s*[:\-]\s*(.+)", re.IGNORECASE),
    "review_date":   re.compile(r"(?:review\s*date|next\s*review|valid\s*until)\s*[:\-]\s*(.+)", re.IGNORECASE),
    "effective_date": re.compile(r"(?:effective\s*date|in\s*force\s*from)\s*[:\-]\s*(.+)", re.IGNORECASE),
    "retention":     re.compile(r"(?:retention|retention\s*period|keep\s*for)\s*[:\-]\s*(.+)", re.IGNORECASE),
    "steward":       re.compile(r"(?:steward|data\s*steward)\s*[:\-]\s*(.+)", re.IGNORECASE),
    "sla":           re.compile(r"(?:sla|service\s*level)\s*[:\-]\s*(.+)", re.IGNORECASE),
    "counterparty":  re.compile(r"(?:counterparty|counter\s*party|second\s*party)\s*[:\-]\s*(.+)", re.IGNORECASE),
    "expiry_date":   re.compile(r"(?:expiry|expiry\s*date|expires\s*on)\s*[:\-]\s*(.+)", re.IGNORECASE),
    "last_validated": re.compile(r"(?:last\s*validated|last\s*verified)\s*[:\-]\s*(.+)", re.IGNORECASE),
    "responsibility_center": re.compile(r"(?:responsibility\s*center|cost\s*center)\s*[:\-]\s*(.+)", re.IGNORECASE),
}

# Heuristic: treat embedded author/creator as owner proxy
_AUTHOR_KEYS = {"author", "creator", "owner", "data owner"}


def check_governance(
    doc_type: str,
    embedded_metadata: dict,
    text: str,
) -> dict:
    """Check governance metadata completeness."""
    findings: list[str] = []

    required_fields = REQUIRED_METADATA.get(doc_type, REQUIRED_METADATA["default"])

    # ── Build a merged metadata view: embedded + inline ───────────────────────
    merged: dict[str, str] = {}

    # 1. Normalise embedded metadata keys to lowercase
    for k, v in embedded_metadata.items():
        if v:
            merged[k.lower()] = str(v).strip()

    # Author/creator fields map to "owner"
    for key in _AUTHOR_KEYS:
        if key in merged and "owner" not in merged:
            merged["owner"] = merged[key]

    # 2. Scan inline governance block in body text
    for field_name, pattern in _INLINE_PATTERNS.items():
        if field_name not in merged:
            m = pattern.search(text)
            if m:
                value = m.group(1).strip().rstrip(".")
                if value:
                    merged[field_name] = value

    # ── Check presence ────────────────────────────────────────────────────────
    present_fields: list[str] = []
    missing_fields: list[str] = []

    for field in required_fields:
        if field in merged:
            present_fields.append(field)
        else:
            missing_fields.append(field)
            findings.append(f"Missing governance field: '{field}'.")

    # ── Classification validity ───────────────────────────────────────────────
    raw_classification = merged.get("classification", "").lower().strip()
    classification: str | None = raw_classification if raw_classification else None
    classification_valid = classification in VALID_CLASSIFICATIONS if classification else False

    if not classification:
        findings.append("Classification missing or not found.")
    elif not classification_valid:
        findings.append(
            f"Classification '{classification}' is not a recognised label "
            f"(valid: {sorted(VALID_CLASSIFICATIONS)})."
        )

    # ── Owner signal ──────────────────────────────────────────────────────────
    has_owner = "owner" in merged and bool(merged["owner"])
    if not has_owner:
        findings.append("No owner assigned — document is ungoverned.")

    # ── Score: fraction of required fields present, penalty if no owner ───────
    if required_fields:
        score = len(present_fields) / len(required_fields)
    else:
        score = 1.0

    if not has_owner:
        score = max(0.0, score - 0.20)
    if not classification_valid:
        score = max(0.0, score - 0.10)

    governance_score = round(max(0.0, min(1.0, score)), 4)

    return {
        "governance_score": governance_score,
        "required_fields": required_fields,
        "present_fields": present_fields,
        "missing_fields": missing_fields,
        "classification": classification,
        "classification_valid": classification_valid,
        "has_owner": has_owner,
        "findings": findings,
    }
