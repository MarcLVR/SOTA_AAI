# Document Lifecycle & Staleness Standard

This standard defines how long each type of corporate document stays valid before
it must be reviewed, and when a document is considered stale. The auditor flags documents
that exceed these thresholds. Dates are measured from the document's last-modified
or last-reviewed date.

## Maximum age before review is required (staleness thresholds)

| Document type | Max age (days) | Notes |
|---|---|---|
| Policy | 365 | Policies must be re-approved yearly. |
| Procedure (SOP) | 365 | Operational procedures reviewed yearly. |
| Contract | 730 | Reviewed every two years or at renewal. |
| Data dictionary | 180 | Schema drift is fast; review twice a year. |
| Data contract | 180 | SLAs and schemas reviewed twice a year. |
| Project charter | 365 | Re-baselined yearly while the project runs. |
| Status report | 30 | A status report older than a month is stale. |
| RAID log | 30 | Risk/issue logs must be current within a month. |
| OKR | 90 | Reviewed each quarter. |
| Asana task export | 60 | Task snapshots stale after two months. |
| Business Central record | 365 | Reviewed yearly. |
| Unknown / untyped | 540 | Default fallback when the type cannot be inferred. |

A document older than its threshold loses points on the **staleness** dimension of
the trust score and is surfaced for review.

## Cold access

A document that has not been accessed in **180 days** is considered *cold*. Cold
documents are candidates for archival or deletion regardless of their type. Cold
access is an additional staleness signal independent of the age thresholds above.

## How to stay compliant

- Record a real **last-reviewed** or **effective date** on every document.
- Schedule reviews at or before the thresholds in the table.
- Archive cold documents instead of leaving them in active folders.
