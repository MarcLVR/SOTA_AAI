"""
Staleness scoring — the "too old / not touched" signal.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from domain.knowledge import (
    COLD_ACCESS_THRESHOLD_DAYS,
    STALENESS_THRESHOLDS_DAYS,
)

# Matches ISO dates (2022-03-15) or slash dates (15/03/2022, 03/15/2022)
_ISO_DATE   = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_SLASH_DATE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
# "review by / review date / next review / expires / valid until" followed by a date
_REVIEW_TRIGGER = re.compile(
    r"(?:review\s+(?:by|date|before)|next\s+review|expir(?:es?|y)|valid\s+until"
    r"|effective\s+(?:until|to)|sunset\s+date)",
    re.IGNORECASE,
)

_DATE_FORMATS = [
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
]


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _parse_content_date(s: str) -> datetime | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _find_overdue_review_dates(text: str, now: datetime) -> list[str]:
    """Return human-readable findings for any overdue review/expiry dates in text."""
    findings: list[str] = []
    # Only look in the 200 chars after a review-trigger phrase
    for trigger_match in _REVIEW_TRIGGER.finditer(text):
        window = text[trigger_match.start(): trigger_match.start() + 200]
        for pattern in (_ISO_DATE, _SLASH_DATE):
            for date_match in pattern.finditer(window):
                dt = _parse_content_date(date_match.group(1))
                if dt and dt < now:
                    overdue_days = (now - dt).days
                    findings.append(
                        f"Review/expiry date '{date_match.group(1)}' in body "
                        f"is {overdue_days} days overdue."
                    )
    return findings


def score_staleness(
    doc_type: str,
    modified_at: str,
    accessed_at: str | None = None,
    content_text: str | None = None,
) -> dict:
    """Compute a staleness score and findings that explain it.

    Args:
        doc_type:     Document type (e.g. 'policy', 'contract').
        modified_at:  Last-modified timestamp as ISO 8601 string.
        accessed_at:  Last-accessed timestamp as ISO 8601 (optional).
        content_text: Body text to scan for overdue review dates (optional).

    Returns dict with staleness_score (0-1, higher=fresher), age_days,
    threshold_days, is_stale, is_cold, findings.
    """
    now = _now()
    findings: list[str] = []

    modified_dt = _parse_iso(modified_at)
    if modified_dt is None:
        return {
            "staleness_score": 0.5,
            "age_days": -1,
            "threshold_days": -1,
            "is_stale": False,
            "is_cold": False,
            "findings": [f"Could not parse modified_at: {modified_at!r}"],
        }

    age_days = (now - modified_dt).days
    threshold_days = STALENESS_THRESHOLDS_DAYS.get(doc_type, STALENESS_THRESHOLDS_DAYS["unknown"])
    is_stale = age_days > threshold_days

    if is_stale:
        findings.append(
            f"Modified {age_days} days ago; '{doc_type}' review cadence is {threshold_days} days."
        )

    # Cold access check
    is_cold = False
    if accessed_at:
        accessed_dt = _parse_iso(accessed_at)
        if accessed_dt:
            cold_days = (now - accessed_dt).days
            is_cold = cold_days > COLD_ACCESS_THRESHOLD_DAYS
            if is_cold:
                findings.append(
                    f"Not accessed in {cold_days} days "
                    f"(threshold: {COLD_ACCESS_THRESHOLD_DAYS} days) — possible abandonment."
                )

    # Overdue review dates in body
    if content_text:
        findings.extend(_find_overdue_review_dates(content_text, now))

    # Score: linear decay — 1.0 at age 0, ~0.5 at threshold, 0.0 at 2×threshold
    raw = 1.0 - (age_days / (2.0 * threshold_days)) if threshold_days > 0 else 0.0
    staleness_score = max(0.0, min(1.0, raw))
    if is_cold:
        staleness_score = max(0.0, staleness_score - 0.15)

    return {
        "staleness_score": round(staleness_score, 4),
        "age_days": age_days,
        "threshold_days": threshold_days,
        "is_stale": is_stale,
        "is_cold": is_cold,
        "findings": findings,
    }


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)
