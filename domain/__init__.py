"""
Domain layer for the AI-Readiness Auditor.

Scans a document repository and answers: "How much of this data is trustworthy
enough to feed AI / base decisions on?"

Three detection signals:
    1. Staleness    — not modified/opened in N years/months; content references
                      expired dates or retired standards.
    2. Standards    — does not follow the template / quality standard for its type
                      (missing required sections, wrong structure).
    3. Governance   — missing required metadata (owner, classification, retention,
                      review date).

These roll up into a per-document trust_score and a corpus-level
"% needs supervision" metric.
"""
