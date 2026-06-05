"""
Standards / template compliance — the "doesn't follow the quality standard" signal.
"""
from __future__ import annotations

import re

from domain.knowledge import (
    BRAND_CANONICAL_NAME,
    OBSOLETE_CONTENT_MARKERS,
    REQUIRED_SECTIONS,
    RETIRED_STANDARDS,
)

STANDARD_DOC_EXTENSIONS: set[str] = {".pdf", ".docx", ".xlsx", ".pptx"}

# Formats that must have an extractable text layer to be machine-auditable.
# An empty-text result from these formats indicates a scanned image, encryption,
# or a rendering failure — all of which require manual review.
_TEXT_CONTENT_EXPECTED: set[str] = {".pdf", ".docx", ".doc", ".pptx", ".ppt"}

# Penalty weights
_RETIRED_STD_PENALTY  = 0.10   # per retired-standard hit
_PLACEHOLDER_PENALTY  = 0.05   # per placeholder hit
_FORMAT_PENALTY       = 0.15   # non-standard format
_EMPTY_TEXT_PENALTY   = 0.75   # no text in a text-expected format → forces score to 0
_BRAND_PENALTY        = 0.05   # per distinct brand-name misuse

# Matches the brand name case-insensitively, with an optional plural "es".
# The possessive ("BrandName's") is deliberately not captured → not flagged.
# When BRAND_CANONICAL_NAME is empty, brand checking is disabled.
_BRAND_NAME_RE = (
    re.compile(rf"\b{re.escape(BRAND_CANONICAL_NAME)}(es)?\b", re.IGNORECASE)
    if BRAND_CANONICAL_NAME else None
)


def _check_brand_name(text: str) -> list[str]:
    """Flag occurrences of the brand name whose exact spelling is not canonical."""
    if not _BRAND_NAME_RE or not BRAND_CANONICAL_NAME:
        return []
    findings: list[str] = []
    seen: set[str] = set()
    plural = (BRAND_CANONICAL_NAME + "es").lower()
    for m in _BRAND_NAME_RE.finditer(text):
        token = m.group(0)
        if token == BRAND_CANONICAL_NAME or token in seen:
            continue
        seen.add(token)
        if token.lower() == plural:
            findings.append(
                f"Incorrect brand name usage '{token}': the brand name "
                f"'{BRAND_CANONICAL_NAME}' must never be pluralised."
            )
        else:
            findings.append(
                f"Incorrect brand name usage '{token}': write the brand exactly as "
                f"'{BRAND_CANONICAL_NAME}'."
            )
    return findings


def check_standards(doc_type: str, extension: str, text: str) -> dict:
    """Check structural compliance, format standardness, and retired references."""
    text_lower = text.lower()
    findings: list[str] = []

    # ── Empty-text guard ──────────────────────────────────────────────────────
    # Standard text-format documents with no extractable text cannot be
    # content-audited. Flag them for manual review (scanned images, encrypted
    # files, or corrupt documents all present this signature).
    empty_text = extension.lower() in _TEXT_CONTENT_EXPECTED and not text.strip()
    if empty_text:
        findings.append(
            "No text extracted — document may be a scanned image, "
            "password-protected, or have a text-rendering issue. "
            "Content cannot be automatically audited; manual review required."
        )

    # ── Format ────────────────────────────────────────────────────────────────
    is_standard_format = extension.lower() in STANDARD_DOC_EXTENSIONS
    if not is_standard_format:
        findings.append(
            f"Non-standard format '{extension}'; expected one of "
            f"{sorted(STANDARD_DOC_EXTENSIONS)}."
        )

    # ── Required sections ─────────────────────────────────────────────────────
    required_sections = REQUIRED_SECTIONS.get(doc_type, [])
    present_sections: list[str] = []
    missing_sections: list[str] = []

    for section in required_sections:
        if section.lower() in text_lower:
            present_sections.append(section)
        else:
            missing_sections.append(section)
            findings.append(f"Missing required section: '{section}'.")

    # ── Retired standards ─────────────────────────────────────────────────────
    retired_standard_hits: list[dict] = []
    for std_key, std_info in RETIRED_STANDARDS.items():
        for trigger in std_info["triggers"]:
            if trigger.lower() in text_lower:
                hit = {"standard": std_key, "trigger": trigger, "note": std_info["note"]}
                retired_standard_hits.append(hit)
                findings.append(
                    f"References retired standard '{trigger}': {std_info['note']}"
                )
                break  # one hit per standard is enough

    # ── Placeholder / obsolete markers ────────────────────────────────────────
    placeholder_hits: list[str] = []
    for marker in OBSOLETE_CONTENT_MARKERS:
        if marker.lower() in text_lower:
            placeholder_hits.append(marker)
            findings.append(f"Contains placeholder/obsolete marker: '{marker}'.")

    # ── Brand name usage ──────────────────────────────────────────────────────
    brand_violations = _check_brand_name(text)
    findings.extend(brand_violations)

    # ── Score ─────────────────────────────────────────────────────────────────
    # Base: fraction of required sections present
    if required_sections:
        section_ratio = len(present_sections) / len(required_sections)
    else:
        section_ratio = 0.70  # unknown type — neutral

    score = section_ratio
    if not is_standard_format:
        score -= _FORMAT_PENALTY
    score -= len(retired_standard_hits) * _RETIRED_STD_PENALTY
    score -= len(placeholder_hits) * _PLACEHOLDER_PENALTY
    score -= len(brand_violations) * _BRAND_PENALTY
    if empty_text:
        score -= _EMPTY_TEXT_PENALTY   # guaranteed to reach 0 even from 0.70 base

    standards_score = round(max(0.0, min(1.0, score)), 4)

    return {
        "standards_score": standards_score,
        "is_standard_format": is_standard_format,
        "required_sections": required_sections,
        "present_sections": present_sections,
        "missing_sections": missing_sections,
        "retired_standard_hits": retired_standard_hits,
        "placeholder_hits": placeholder_hits,
        "brand_violations": brand_violations,
        "findings": findings,
    }
