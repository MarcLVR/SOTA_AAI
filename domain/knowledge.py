"""
Domain knowledge for the AI-Readiness Auditor.

Tunable facts and config — staleness thresholds, retired standards, required
sections per doc type, governance metadata requirements, scoring weights.
Nothing here calls an LLM.
"""
from __future__ import annotations

# ── Staleness thresholds (days) ───────────────────────────────────────────────
STALENESS_THRESHOLDS_DAYS: dict[str, int] = {
    "policy":          365,
    "procedure":       365,
    "contract":        730,
    "data_dictionary": 180,
    "data_contract":   180,
    "project_charter": 365,
    "status_report":    30,
    "raid_log":         30,
    "okr":              90,
    "asana_task":       60,
    "bc_record":       365,
    "unknown":         540,
}

COLD_ACCESS_THRESHOLD_DAYS: int = 180


# ── Retired / superseded standards ───────────────────────────────────────────
RETIRED_STANDARDS: dict[str, dict] = {
    "iso_27001_2013": {
        "triggers": ["ISO 27001:2013", "ISO/IEC 27001:2013"],
        "note": "Superseded by ISO/IEC 27001:2022.",
    },
    "iso_9001_2008": {
        "triggers": ["ISO 9001:2008"],
        "note": "Superseded by ISO 9001:2015.",
    },
    "gdpr_transition": {
        "triggers": ["GDPR transition period", "Data Protection Directive 95/46/EC"],
        "note": "Directive 95/46/EC repealed by GDPR; transition period ended 2018.",
    },
    "eu_ai_act_draft": {
        "triggers": ["proposed AI Act", "draft AI Regulation", "AI Act proposal 2021"],
        "note": "EU AI Act entered into force 2024; 'draft/proposed' references are stale.",
    },
    "pci_dss_3": {
        "triggers": ["PCI DSS 3.2.1", "PCI-DSS v3"],
        "note": "Superseded by PCI DSS v4.0.",
    },
    "windows_server_2012": {
        "triggers": ["Windows Server 2012", "Server 2008"],
        "note": "End of support; references indicate outdated IT procedures.",
    },
}

OBSOLETE_CONTENT_MARKERS: list[str] = [
    "DRAFT — DO NOT DISTRIBUTE",
    "CONFIDENTIAL DRAFT",
    "for review by",
    "TODO",
    "TBD",
    "[INSERT",
    "Lorem ipsum",
]


# ── Brand name usage ──────────────────────────────────────────────────────────
# Canonical spelling of the company/brand name. Any token matching it
# case-insensitively but not exactly is flagged as a branding violation.
# Set via BRAND_NAME env var; empty string disables brand checking entirely.
# Detection lives in tools/standards.py; this is the single tunable fact.
from config import settings as _settings
BRAND_CANONICAL_NAME: str = _settings.brand_name


# ── Required sections per doc type ────────────────────────────────────────────
REQUIRED_SECTIONS: dict[str, list[str]] = {
    "policy":          ["Purpose", "Scope", "Owner", "Effective Date", "Review Date"],
    "procedure":       ["Purpose", "Scope", "Steps", "Owner", "Last Updated"],
    "contract":        ["Parties", "Term", "Effective Date", "Termination", "Signatures"],
    "data_dictionary": ["Field Name", "Data Type", "Description", "Owner", "Classification"],
    "data_contract":   ["Schema", "Owner", "SLA", "Classification", "Retention"],
    "project_charter": ["Objective", "Scope", "Sponsor", "Milestones", "Budget"],
    "status_report":   ["Period", "Status", "Risks", "Next Steps"],
    "raid_log":        ["Risks", "Assumptions", "Issues", "Dependencies"],
    "okr":             ["Objective", "Key Results", "Owner", "Target"],
}


# ── Required governance metadata per doc type ─────────────────────────────────
REQUIRED_METADATA: dict[str, list[str]] = {
    "default":         ["owner", "classification", "review_date"],
    "policy":          ["owner", "classification", "review_date", "effective_date"],
    "data_dictionary": ["owner", "classification", "retention", "steward"],
    "data_contract":   ["owner", "classification", "retention", "sla"],
    "contract":        ["owner", "counterparty", "expiry_date"],
    "bc_record":       ["owner", "last_validated", "responsibility_center"],
}

VALID_CLASSIFICATIONS: set[str] = {
    "public", "internal", "confidential", "restricted", "secret",
}


# ── Doc-type detection from filename ─────────────────────────────────────────
DOCTYPE_FILENAME_HINTS: dict[str, list[str]] = {
    "policy":          ["policy"],
    "procedure":       ["procedure", "sop"],
    "contract":        ["contract", "agreement", "nda", "msa"],
    "data_dictionary": ["data dictionary", "data_dictionary"],
    "data_contract":   ["data contract", "data_contract"],
    "project_charter": ["charter"],
    "status_report":   ["status", "weekly", "report"],
    "raid_log":        ["raid", "risk log"],
    "okr":             ["okr", "objectives"],
    "asana_task":      ["asana", "task_export"],
    "bc_record":       ["business central", "bc_", "navision", "dynamics"],
}


# ── Trust score weights and supervision threshold ─────────────────────────────
SCORE_WEIGHTS: dict[str, float] = {
    "staleness":  0.40,
    "standards":  0.35,
    "governance": 0.25,
}
SUPERVISION_THRESHOLD: float = 0.70


def doc_type_from_filename(filename: str) -> str:
    """Best-effort doc_type from filename. Returns 'unknown' if no hint matches."""
    low = filename.lower()
    for doc_type, hints in DOCTYPE_FILENAME_HINTS.items():
        if any(h in low for h in hints):
            return doc_type
    return "unknown"
