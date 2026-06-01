"""
Aggregator — rolls per-document findings into the corpus-level dashboard metrics.
"""
from __future__ import annotations

from domain.knowledge import (
    SCORE_WEIGHTS,
    SUPERVISION_THRESHOLD,
)

_REMEDIATION_MINUTES_PER_DOC = 25.0


def compute_trust_score(
    staleness_score: float,
    standards_score: float,
    governance_score: float,
) -> dict:
    """Weighted combination of the three sub-scores → trust_score + verdict."""
    trust = (
        SCORE_WEIGHTS["staleness"]   * staleness_score
        + SCORE_WEIGHTS["standards"]   * standards_score
        + SCORE_WEIGHTS["governance"]  * governance_score
    )
    trust = round(max(0.0, min(1.0, trust)), 4)
    return {
        "trust_score": trust,
        "needs_supervision": trust < SUPERVISION_THRESHOLD,
    }


def aggregate_findings(document_results: list[dict]) -> dict:
    """Roll up all per-document results into the dashboard payload."""
    if not document_results:
        return {
            "headline": {
                "total_documents": 0,
                "total_size_human": "0 B",
                "total_size_bytes": 0,
                "needs_supervision_count": 0,
                "needs_supervision_pct": 0.0,
            },
            "staleness": {},
            "standards": {},
            "governance": {},
            "by_doc_type": {},
            "estimated_remediation_hours": 0.0,
            "top_offenders": [],
        }

    total_bytes = sum(d.get("size_bytes", 0) for d in document_results)
    flagged = [d for d in document_results if d.get("needs_supervision", False)]

    needs_supervision_count = len(flagged)
    needs_supervision_pct = round(needs_supervision_count / len(document_results) * 100, 1)

    # ── Staleness rollup ──────────────────────────────────────────────────────
    stale_docs = [d for d in document_results if d.get("staleness", {}).get("is_stale")]
    cold_docs  = [d for d in document_results if d.get("staleness", {}).get("is_cold")]
    aged = sorted(
        [d for d in document_results if d.get("staleness", {}).get("age_days", 0) > 0],
        key=lambda d: d["staleness"]["age_days"],
        reverse=True,
    )
    oldest = {"path": aged[0]["path"], "age_days": aged[0]["staleness"]["age_days"]} if aged else None

    staleness_summary = {
        "stale_count": len(stale_docs),
        "cold_count": len(cold_docs),
        "oldest_doc": oldest,
    }

    # ── Standards rollup ─────────────────────────────────────────────────────
    non_std_format = sum(
        1 for d in document_results
        if not d.get("standards", {}).get("is_standard_format", True)
    )
    missing_sections_count = sum(
        len(d.get("standards", {}).get("missing_sections", []))
        for d in document_results
    )
    retired_hits = sum(
        len(d.get("standards", {}).get("retired_standard_hits", []))
        for d in document_results
    )

    standards_summary = {
        "non_standard_format_count": non_std_format,
        "missing_sections_count": missing_sections_count,
        "retired_standard_hits": retired_hits,
    }

    # ── Governance rollup ─────────────────────────────────────────────────────
    no_owner_count = sum(
        1 for d in document_results
        if not d.get("governance", {}).get("has_owner", True)
    )
    unclassified_count = sum(
        1 for d in document_results
        if not d.get("governance", {}).get("classification_valid", True)
    )

    governance_summary = {
        "no_owner_count": no_owner_count,
        "unclassified_count": unclassified_count,
    }

    # ── By doc type ───────────────────────────────────────────────────────────
    by_doc_type: dict[str, dict] = {}
    for d in document_results:
        dt = d.get("doc_type", "unknown")
        bucket = by_doc_type.setdefault(dt, {"count": 0, "flagged": 0})
        bucket["count"] += 1
        if d.get("needs_supervision", False):
            bucket["flagged"] += 1

    # ── Remediation estimate ──────────────────────────────────────────────────
    estimated_remediation_hours = round(
        needs_supervision_count * _REMEDIATION_MINUTES_PER_DOC / 60, 1
    )

    # ── Top offenders (worst trust_score first) ───────────────────────────────
    sorted_docs = sorted(document_results, key=lambda d: d.get("trust_score", 1.0))
    top_offenders = []
    for d in sorted_docs[:10]:
        # Pick the single worst finding across all three signals
        all_findings = (
            d.get("staleness", {}).get("findings", [])
            + d.get("standards", {}).get("findings", [])
            + d.get("governance", {}).get("findings", [])
        )
        top_reason = all_findings[0] if all_findings else "No specific finding."
        top_offenders.append({
            "path": d.get("path", ""),
            "doc_type": d.get("doc_type", "unknown"),
            "trust_score": d.get("trust_score", 0.0),
            "top_reason": top_reason,
        })

    return {
        "headline": {
            "total_documents": len(document_results),
            "total_size_human": human_bytes(total_bytes),
            "total_size_bytes": total_bytes,
            "needs_supervision_count": needs_supervision_count,
            "needs_supervision_pct": needs_supervision_pct,
        },
        "staleness": staleness_summary,
        "standards": standards_summary,
        "governance": governance_summary,
        "by_doc_type": by_doc_type,
        "estimated_remediation_hours": estimated_remediation_hours,
        "top_offenders": top_offenders,
    }


def human_bytes(n: int) -> str:
    """1234567890 → '1.1 GB'."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"
