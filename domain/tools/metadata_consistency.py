"""
Deterministic metadata consistency checker — zero LLM calls, zero cost.

Checks:
  1. Author field missing, blank, or a default placeholder ("Administrator",
     "Unknown Author", "user", etc.)
  2. CreationDate == ModDate on a document older than NEVER_REVISED_MIN_DAYS:
     the file has never been revised since initial creation.
  3. Body text contains a date (regex) that differs by more than
     DATE_SKEW_TOLERANCE_DAYS from the metadata CreationDate/ModDate — possible
     content/metadata mismatch.
  4. Body text contains a version string (v1.0, Version 2.3, …) — extracted for
     display; not a finding by itself but useful for the inspector report.

None of these read API keys, call a model, or touch the network.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

# ── Tunable constants ─────────────────────────────────────────────────────────

# Docs this age or older, AND never revised, get flagged.
NEVER_REVISED_MIN_DAYS = 90

# Body-stated date vs metadata date: flag if they differ by more than this.
DATE_SKEW_TOLERANCE_DAYS = 5

# Score penalty per finding.
_PENALTY = 0.20

# Author values that indicate the field was never properly set.
_PLACEHOLDER_AUTHORS = {
    "", "administrator", "unknown", "unknown author", "user", "admin",
    "default user", "microsoft office user", "windows user",
    "python-docx",   # library artifact from auto-generated docs
}

# ── Date / version regex ──────────────────────────────────────────────────────

# ISO dates (2024-09-11, 2024/09/11)
_RE_ISO = re.compile(r"\b(20\d{2})[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])\b")

# European short dates (11.09.2024, 11/09/24)
_RE_EU = re.compile(
    r"\b(0?[1-9]|[12]\d|3[01])[./](0?[1-9]|1[0-2])[./](20\d{2}|\d{2})\b"
)

# Month-name dates ("September 2024", "Sep 2024", "Sep. 2024")
_MONTHS = (
    "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|"
    "January|February|March|April|June|July|August|September|"
    "October|November|December"
)
_RE_MONTHNAME = re.compile(
    rf"\b(?:{_MONTHS})\.?\s+(20\d{{2}})\b",
    re.IGNORECASE,
)

# Version strings
_RE_VERSION = re.compile(
    r"\b(?:[Vv]ersion\s*|[Vv])(\d+\.\d+(?:\.\d+)*)\b"
)

# PDF date format: D:YYYYMMDDHHmmSS
_RE_PDF_DATE = re.compile(r"^D?:?(\d{4})(\d{2})(\d{2})")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _utc(dt: datetime) -> datetime:
    """Ensure a datetime is UTC-aware (attach UTC if naive)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_date(raw: str | None) -> datetime | None:
    """Parse a date string from PDF metadata or filesystem timestamps."""
    if not raw:
        return None
    raw = str(raw).strip()

    # PDF D: format
    m = _RE_PDF_DATE.match(raw)
    if m:
        try:
            return _utc(datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass

    # ISO / isoformat strings
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(raw[:len(fmt)], fmt)
            return _utc(dt)
        except ValueError:
            pass

    # Full isoformat with timezone suffix (+00:00 etc.)
    try:
        return _utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))
    except ValueError:
        pass

    return None


def _dates_in_text(text: str) -> list[datetime]:
    """Extract all plausible dates mentioned in body text, return as datetimes."""
    found: list[datetime] = []

    for m in _RE_ISO.finditer(text):
        try:
            found.append(datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                                  tzinfo=timezone.utc))
        except ValueError:
            pass

    for m in _RE_EU.finditer(text):
        day, mon = int(m.group(1)), int(m.group(2))
        yr_raw = m.group(3)
        yr = int(yr_raw) if len(yr_raw) == 4 else 2000 + int(yr_raw)
        try:
            found.append(datetime(yr, mon, day, tzinfo=timezone.utc))
        except ValueError:
            pass

    for m in _RE_MONTHNAME.finditer(text):
        yr = int(m.group(1))
        # Month-name dates don't include day — use the 1st as proxy
        month_abbr = m.group(0).strip()[:3].title()
        _M = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
              "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
        mon = _M.get(month_abbr)
        if mon:
            try:
                found.append(datetime(yr, mon, 1, tzinfo=timezone.utc))
            except ValueError:
                pass

    return found


def _version_in_text(text: str) -> str | None:
    m = _RE_VERSION.search(text)
    return m.group(1) if m else None


# ── Public API ────────────────────────────────────────────────────────────────

def check_metadata_consistency(
    embedded_metadata: dict,
    text: str,
    modified_at: str,
) -> dict:
    """
    Deterministic metadata consistency checks — no LLM, no API calls.

    Args:
        embedded_metadata: dict from extract_document() (Author, CreationDate, …)
        text:              extracted body text (up to max_chars)
        modified_at:       filesystem last-modified ISO timestamp

    Returns dict with keys:
        consistency_score   float [0, 1]  higher = more consistent
        findings            list[str]
        dates_in_body       list[str]     ISO date strings found in text
        version_in_body     str | None    e.g. "2.3"
    """
    now = datetime.now(tz=timezone.utc)
    findings: list[str] = []

    # ── 1. Author check ───────────────────────────────────────────────────────
    author = str(embedded_metadata.get("Author") or
                 embedded_metadata.get("author") or "").strip()
    if author.lower() in _PLACEHOLDER_AUTHORS:
        findings.append(
            f"Author field is {'missing' if not author else repr(author)!s} — "
            "document may be unattributed or was created with a generic user account."
        )

    # ── 2. Never-revised check ────────────────────────────────────────────────
    raw_creation = (
        embedded_metadata.get("CreationDate")
        or embedded_metadata.get("creation_date")
        or embedded_metadata.get("created")
    )
    raw_moddate = (
        embedded_metadata.get("ModDate")
        or embedded_metadata.get("ModificationDate")
        or embedded_metadata.get("modified")
        or modified_at
    )
    dt_created = _parse_date(raw_creation)
    dt_modified = _parse_date(raw_moddate)

    if dt_created and dt_modified:
        diff_sec = abs((dt_modified - dt_created).total_seconds())
        # Ensure both datetimes are timezone-aware for subtraction
        dt_c_aware = dt_created.replace(tzinfo=timezone.utc) if dt_created.tzinfo is None else dt_created
        age_days = (now - dt_c_aware).days
        if diff_sec < 60 and age_days >= NEVER_REVISED_MIN_DAYS:
            findings.append(
                f"Document was never revised since creation ({dt_created.date()}) "
                f"— {age_days} days ago. Review cadence may not have been met."
            )

    # ── 3. Body-date vs metadata skew ─────────────────────────────────────────
    # A body date is "mismatched" only if it differs from BOTH CreationDate AND
    # ModDate beyond tolerance. A body date matching either anchor is fine.
    body_dates = _dates_in_text(text[:8000])  # scan first ~8k chars
    anchors = [d for d in (dt_created, dt_modified) if d is not None]
    skewed: list[tuple[datetime, datetime, int]] = []

    for bd in body_dates:
        # Skip if the body date matches any anchor within tolerance
        if any(abs((bd - a).days) <= DATE_SKEW_TOLERANCE_DAYS for a in anchors):
            continue
        # Flag against the closest anchor
        if anchors:
            closest = min(anchors, key=lambda a: abs((bd - a).days))
            skew = abs((bd - closest).days)
            if bd.year == closest.year:
                skewed.append((bd, closest, skew))

    if skewed:
        bd, rd, sk = max(skewed, key=lambda t: t[2])  # worst mismatch
        findings.append(
            f"Body text contains date {bd.date()} but file metadata records "
            f"{rd.date()} — {sk}-day discrepancy. Possible content/metadata mismatch."
        )

    # ── 4. Version string (informational, not a finding) ─────────────────────
    version = _version_in_text(text[:4000])

    # ── Score ─────────────────────────────────────────────────────────────────
    score = round(max(0.0, 1.0 - len(findings) * _PENALTY), 4)

    return {
        "consistency_score": score,
        "findings": findings,
        "dates_in_body": [d.date().isoformat() for d in body_dates[:10]],
        "version_in_body": version,
    }
