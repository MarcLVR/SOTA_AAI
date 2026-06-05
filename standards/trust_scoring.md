# Trust Score & Supervision Threshold

The auditor assigns every document a **trust score** between 0.0 and 1.0. The score is a
weighted combination of three deterministic checks (no LLM, no tokens). It tells the
owner, at a glance, how much the document can be trusted as-is.

## Dimensions and weights

| Dimension | Weight | What it measures |
|---|---|---|
| Staleness | 0.40 | Is the document within its review threshold? Is it cold (180+ days unaccessed)? See *Document Lifecycle*. |
| Standards compliance | 0.35 | Does it have the required sections? Does it reference retired standards or contain obsolete markers (TODO/TBD/DRAFT/Lorem ipsum)? See *Required Sections* and *Retired Standards*. |
| Governance | 0.25 | Does it carry the required metadata, a valid classification, and no exposed PII? See *Governance & PII*. |

`trust_score = 0.40·staleness + 0.35·standards + 0.25·governance`

## Supervision threshold

- **trust_score ≥ 0.70** → document passes; no action required.
- **trust_score < 0.70** → document **needs supervision**: it is flagged, findings
  are generated, and the owner is notified on Telegram.

## Severity of findings

- **trust_score < 0.50** → high severity.
- **0.50 ≤ trust_score < 0.70** → medium severity.
- Individual rule findings (exposed PII, retired standard) may be raised to high
  severity on their own regardless of the aggregate score.

## What the owner can do

For each flagged document the owner receives the findings with their exact location
and a proposed fix, and can approve an automated fix, mark it for manual handling,
or ignore it — all from a single Telegram message.
