"""
Standards / template compliance — the "doesn't follow the quality standard" signal.
"""
from __future__ import annotations

from domain.knowledge import (
    OBSOLETE_CONTENT_MARKERS,
    REQUIRED_SECTIONS,
    RETIRED_STANDARDS,
)

STANDARD_DOC_EXTENSIONS: set[str] = {".pdf", ".docx", ".xlsx", ".pptx"}

# Penalty weights
_RETIRED_STD_PENALTY = 0.10   # per retired-standard hit
_PLACEHOLDER_PENALTY = 0.05   # per placeholder hit
_FORMAT_PENALTY      = 0.15   # non-standard format


def check_standards(doc_type: str, extension: str, text: str) -> dict:
    """Check structural compliance, format standardness, and retired references."""
    text_lower = text.lower()
    findings: list[str] = []

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

    standards_score = round(max(0.0, min(1.0, score)), 4)

    return {
        "standards_score": standards_score,
        "is_standard_format": is_standard_format,
        "required_sections": required_sections,
        "present_sections": present_sections,
        "missing_sections": missing_sections,
        "retired_standard_hits": retired_standard_hits,
        "placeholder_hits": placeholder_hits,
        "findings": findings,
    }
