"""
scripts/assign_owners.py — stamp document author metadata with a real team owner.

Why: the chase pipeline resolves a document's owner from its author metadata
(see domain/pipeline.py:_resolve_owner, which substring-matches the team
usernames augusto/luca/marc/nacho). The demo/test corpora ship with fictional
corporate authors ("Jane Smith", "COO Office — David Chen", …) that match no
one, so every finding falls back to the default_owner. This script rewrites the
`author` core-property of each corpus file to a realistic name that *embeds* the
assigned teammate's username, so audit_and_persist() routes each document's
findings to the right person's Telegram.

Usage:
    python -m scripts.assign_owners            # stamp all files
    python -m scripts.assign_owners --check    # report current vs target, no writes

Idempotent: re-running just re-stamps the same author. Office formats only
(.docx/.pptx/.xlsx + .pdf when pypdf is present). Plain .txt/.csv/.json carry no
author metadata and therefore stay on the default_owner fallback — listed under
UNSTAMPABLE below so the team can decide how to handle them.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Realistic display names that embed the platform_username as a substring so
# domain/pipeline.py:_resolve_owner maps author → owner.
AUTHOR_NAME = {
    "augusto": "Augusto Marín — Security & Compliance",
    "luca":    "Luca Cesari — Data Governance Office",
    "marc":    "Marc Pérez — Project Management Office",
    "nacho":   "Nacho Carballal — Office of the COO",
}

# ── Owner assignment (thematic) ───────────────────────────────────────────────
# augusto = security/compliance/risk · luca = data · marc = projects/specs/status
# nacho = direction/legal/board/privacy
OWNERS: dict[str, str] = {
    # ── demo_corpus/files ─────────────────────────────────────────────────────
    "domain/demo_corpus/files/security_policy_2021.docx":            "augusto",
    "domain/demo_corpus/files/pci_compliance_checklist_2020.xlsx":   "augusto",
    "domain/demo_corpus/files/incident_response_procedure.docx":     "augusto",
    "domain/demo_corpus/files/third_party_risk_policy.docx":         "augusto",
    "domain/demo_corpus/files/password_policy.docx":                 "augusto",
    "domain/demo_corpus/files/it_acceptable_use_policy_2018.docx":   "augusto",
    "domain/demo_corpus/files/remote_work_policy.docx":              "augusto",
    "domain/demo_corpus/files/fraud_risk_assessment.txt":            "augusto",
    "domain/demo_corpus/files/backup_recovery_procedure.docx":       "augusto",
    "domain/demo_corpus/files/change_management_procedure.docx":     "augusto",
    "domain/demo_corpus/files/software_development_lifecycle.docx":  "augusto",

    "domain/demo_corpus/files/data_classification_policy.docx":           "luca",
    "domain/demo_corpus/files/data_governance_framework_draft_2020.docx": "luca",
    "domain/demo_corpus/files/data_retention_procedure.docx":            "luca",
    "domain/demo_corpus/files/data_strategy_q1_2025.pptx":              "luca",
    "domain/demo_corpus/files/crm_data_dictionary.xlsx":               "luca",
    "domain/demo_corpus/files/customer_data_dictionary_v1.xlsx":       "luca",
    "domain/demo_corpus/files/employee_data_dictionary_draft.xlsx":    "luca",
    "domain/demo_corpus/files/finance_data_dictionary.xlsx":           "luca",
    "domain/demo_corpus/files/hr_data_contract.xlsx":                  "luca",
    "domain/demo_corpus/files/sales_analytics_data_contract.xlsx":     "luca",
    "domain/demo_corpus/files/api_data_contract_draft.txt":            "luca",
    "domain/demo_corpus/files/bc_vendor_master_extract.csv":           "luca",

    "domain/demo_corpus/files/project_charter_crm_refresh.docx":     "marc",
    "domain/demo_corpus/files/project_charter_data_platform.docx":   "marc",
    "domain/demo_corpus/files/project_charter_legacy_bi_2020.docx":  "marc",
    "domain/demo_corpus/files/raid_log_data_platform.docx":          "marc",
    "domain/demo_corpus/files/okr_q2_2025.docx":                     "marc",
    "domain/demo_corpus/files/okr_q3_2022.docx":                     "marc",
    "domain/demo_corpus/files/status_report_2020_w11.docx":          "marc",
    "domain/demo_corpus/files/status_report_jun_2025.docx":          "marc",
    "domain/demo_corpus/files/status_report_may_2025.docx":          "marc",
    "domain/demo_corpus/files/asana_project_dump_q1_2025.json":      "marc",

    "domain/demo_corpus/files/board_fy2025_data_overview.pptx":      "nacho",
    "domain/demo_corpus/files/business_continuity_plan.docx":        "nacho",
    "domain/demo_corpus/files/privacy_policy.docx":                  "nacho",
    "domain/demo_corpus/files/gdpr_compliance_policy_2018.docx":     "nacho",
    "domain/demo_corpus/files/ai_regulation_briefing_2021.pptx":     "nacho",
    "domain/demo_corpus/files/saas_master_agreement_2024.docx":      "nacho",
    "domain/demo_corpus/files/supplier_agreement_acme_2024.docx":    "nacho",
    "domain/demo_corpus/files/vendor_nda_template_2019.docx":        "nacho",

    # ── test_docs ─────────────────────────────────────────────────────────────
    "test_docs/contrato_juan_garcia_2024.docx":   "nacho",
    "test_docs/2026-06-01_contrato_limpio.docx":  "nacho",
    "test_docs/gdpr_policy_draft.docx":           "nacho",
    "test_docs/Q1_Results_Deck.pptx":             "nacho",
    "test_docs/spec_autenticacion_v0.docx":       "marc",
    "test_docs/api_design_FINAL_v2.pdf":          "marc",
    "test_docs/kickoff_proyecto_alpha.pptx":      "marc",
    "test_docs/informe_ventas_Q1_2026.pdf":       "marc",
}

# Deliberately left OUT of OWNERS so the demo shows the "ownerless document"
# path: a real Office doc (not just the odd .txt/.csv) that resolves to no
# teammate → escalated to the default_owner (admin) by audit_and_persist.
DELIBERATELY_UNOWNED = {
    "domain/demo_corpus/files/onboarding_procedure_draft.docx",  # orphan draft → admin
}

# Formats with no author metadata → _resolve_owner can't read them; they stay on
# the default_owner fallback regardless of the mapping above.
UNSTAMPABLE_EXT = {".txt", ".csv", ".json"}


def _set_author(path: Path, author: str) -> str:
    """Write `author` into the file's metadata. Returns a status string."""
    ext = path.suffix.lower()
    try:
        if ext == ".docx":
            from docx import Document
            d = Document(str(path)); d.core_properties.author = author; d.save(str(path))
        elif ext == ".pptx":
            from pptx import Presentation
            p = Presentation(str(path)); p.core_properties.author = author; p.save(str(path))
        elif ext == ".xlsx":
            from openpyxl import load_workbook
            wb = load_workbook(str(path)); wb.properties.creator = author; wb.save(str(path))
        elif ext == ".pdf":
            try:
                from pypdf import PdfReader, PdfWriter
            except ImportError:
                return "skip (pypdf not installed)"
            r = PdfReader(str(path)); w = PdfWriter(); w.append_pages_from_reader(r)
            w.add_metadata({"/Author": author})
            with open(path, "wb") as fh:
                w.write(fh)
        elif ext in UNSTAMPABLE_EXT:
            return "unstampable (no author metadata)"
        else:
            return f"skip (unsupported {ext})"
    except Exception as exc:  # noqa: BLE001 — report, never crash the batch
        return f"ERROR: {exc}"
    return "ok"


def _current_author(path: Path) -> str:
    ext = path.suffix.lower()
    try:
        if ext == ".docx":
            from docx import Document
            return Document(str(path)).core_properties.author or ""
        if ext == ".pptx":
            from pptx import Presentation
            return Presentation(str(path)).core_properties.author or ""
        if ext == ".xlsx":
            from openpyxl import load_workbook
            return load_workbook(str(path)).properties.creator or ""
        if ext == ".pdf":
            try:
                from pypdf import PdfReader
                return (PdfReader(str(path)).metadata or {}).get("/Author", "") or ""
            except Exception:
                return ""
    except Exception:
        return "?"
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report only, no writes")
    args = ap.parse_args()

    counts: dict[str, int] = {}
    missing = []
    for rel, owner in OWNERS.items():
        path = REPO / rel
        author = AUTHOR_NAME[owner]
        if not path.exists():
            missing.append(rel)
            continue
        if args.check:
            cur = _current_author(path)
            hit = owner if owner in cur.lower() else "—"
            print(f"  [{owner:7}] {rel}\n            now: {cur!r}  → resolves: {hit}")
        else:
            status = _set_author(path, author)
            counts[status] = counts.get(status, 0) + 1
            print(f"  [{owner:7}] {rel}: {status}")

    print()
    if missing:
        print(f"⚠️  {len(missing)} file(s) in the map not found on disk:")
        for m in missing:
            print(f"     - {m}")
    if not args.check:
        print("Summary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
